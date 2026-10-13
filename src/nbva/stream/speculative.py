"""Commit-gated speculative tool preparation.

When a committed prefix is ready, this stage warms the read-only business
lookups the final turn will most likely need (menu search over the committed
text) in a background worker, *while* the user is still speaking. Results are
cached by committed prefix and handed to the responder at endpoint time, so
overlapped work converts directly into saved latency. Only read-only tools
(``search_``/``get_``/``check_`` family) ever run speculatively; anything
that mutates state waits for the final interpretation.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any


# Tools that may run on partial/committed hypotheses: read-only lookups
# that cannot create, modify, or cancel anything. Anything not in this set
# (create_order, modify_order, cancel_order, confirm_order) requires the
# final interpretation and must go through the normal final path.
READONLY_TOOLS = frozenset({
    "search_menu",
    "get_menu_item",
    "get_price",
    "check_availability",
    "get_delivery_info",
    "get_business_hours",
    "get_promotions",
    "get_current_order",
    "calculate_order_total",
    "resolve_order_reference",
})

MUTATING_TOOLS = frozenset({
    "create_order",
    "modify_order",
    "cancel_order",
    "confirm_order",
})


def can_run_speculatively(name: str) -> bool:
    """Code-level safety gate: only READONLY tools may run speculatively."""
    return str(name) in READONLY_TOOLS


@dataclass
class SpeculativeTools:
    registry: Any = None
    max_workers: int = 1

    _pool: Any = field(default=None, repr=False)
    _cache: dict = field(default_factory=dict, repr=False)
    _lock: Any = field(default_factory=threading.Lock, repr=False)
    prepared_prefixes: int = 0
    reused_prefixes: int = 0

    def __post_init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=self.max_workers,
                                        thread_name_prefix="nbva-spec")

    def prepare(self, committed_text: str) -> None:
        """Fire-and-forget warm-up for one committed prefix (no-op if cached)."""
        text = (committed_text or "").strip()
        if not text or self.registry is None:
            return
        with self._lock:
            if text in self._cache:
                return
            self._cache[text] = None  # placeholder: in flight
        self._pool.submit(self._run_search, text)

    def run_guarded(self, name: str, arguments: dict[str, Any] | None) -> Any:
        """Dispatch only if the tool is read-only; refuse otherwise.

        This is the single choke point for all speculative execution: a
        mutating tool can never reach the registry through this path, even
        if a caller passes the wrong name.
        """
        if not can_run_speculatively(name):
            raise PermissionError(
                f"refusing speculative execution of state-changing tool {name!r}; "
                "mutating tools require the final interpretation"
            )
        return self.registry.dispatch(name, arguments or {})

    def _run_search(self, text: str) -> None:
        try:
            result = self.run_guarded("search_menu", {"query": text})
        except Exception as exc:
            result = {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:200]}
        with self._lock:
            self._cache[text] = result
            self.prepared_prefixes += 1

    def await_prefix(self, text: str, timeout_s: float = 10.0) -> Any | None:
        """Block (briefly) until the cached result for a prefix is ready."""
        deadline = time.monotonic() + max(0.0, timeout_s)
        while time.monotonic() < deadline:
            with self._lock:
                if text in self._cache and self._cache[text] is not None:
                    self.reused_prefixes += 1
                    return self._cache[text]
            time.sleep(0.02)
        with self._lock:
            return self._cache.get(text)

    def shutdown(self) -> None:
        try:
            self._pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
