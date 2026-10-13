"""Tool registry: schema declaration, argument coercion, dispatch, and tracing.

Two independent things happen here:

1. ``llm_schemas()`` renders OpenAI-style function-calling schemas that the
   language model sees. The model may only choose among these.
2. ``dispatch()`` validates and executes the call against the deterministic
   business layer. Anything the model invents is rejected here, before it can
   influence the spoken answer.

Every executed call is recorded in :class:`ToolCallRecord` so the trial log
carries the full tool trace, which is required for the database/tool accuracy
and failure-category metrics.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..db.orders import OrderStore
from ..db.repository import RestaurantRepository

JSONSchema = dict[str, Any]


@dataclass
class ToolCallRecord:
    """One executed tool call, in order."""

    index: int
    name: str
    arguments: dict[str, Any]
    ok: bool
    result: Any
    started_perf: float
    ended_perf: float
    error_code: str | None = None

    @property
    def duration_ms(self) -> float:
        return (self.ended_perf - self.started_perf) * 1000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "arguments": self.arguments,
            "ok": self.ok,
            "error_code": self.error_code,
            "result": self.result,
            "duration_ms": round(self.duration_ms, 3),
        }


@dataclass
class ToolRegistry:
    repo: RestaurantRepository
    orders: OrderStore
    tools: dict[str, Callable[..., Any]] = field(default_factory=dict)
    schemas: dict[str, JSONSchema] = field(default_factory=dict)
    calls: list[ToolCallRecord] = field(default_factory=list)
    strict_arguments: bool = True
    log_calls: bool = True
    # Weekday used for weekday-dependent business rules (promotions, hours).
    # Set from the session so that results do not drift with the wall clock.
    business_weekday: str | None = None

    # -- registration ---------------------------------------------------
    def register(
        self,
        name: str,
        fn: Callable[..., Any],
        description: str,
        parameters: JSONSchema,
    ) -> None:
        self.tools[name] = fn
        self.schemas[name] = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        }

    def tool_names(self) -> list[str]:
        return list(self.tools)

    def llm_schemas(self) -> list[JSONSchema]:
        return [self.schemas[n] for n in self.tools]

    # -- dispatch ---------------------------------------------------------
    def dispatch(self, name: str, arguments: dict[str, Any] | None) -> Any:
        fn = self.tools.get(name)
        started = time.perf_counter()
        if fn is None:
            record = ToolCallRecord(
                index=len(self.calls),
                name=name,
                arguments=arguments or {},
                ok=False,
                result={
                    "ok": False,
                    "error": "UNKNOWN_TOOL",
                    "detail": f"no tool named {name!r}",
                    "available_tools": self.tool_names(),
                },
                started_perf=started,
                ended_perf=time.perf_counter(),
                error_code="UNKNOWN_TOOL",
            )
            self._record(record)
            return record.result

        args = dict(arguments or {})
        try:
            result = fn(**args)
        except TypeError as exc:
            result = {
                "ok": False,
                "error": "BAD_ARGUMENT",
                "detail": str(exc),
                "received": args,
            }
        except Exception as exc:  # defensive: a tool bug must not kill the session
            result = {"ok": False, "error": "TOOL_EXCEPTION", "detail": f"{type(exc).__name__}: {exc}"}

        error_code = None
        if isinstance(result, dict) and result.get("ok") is False:
            error_code = str(result.get("error", "TOOL_ERROR"))

        record = ToolCallRecord(
            index=len(self.calls),
            name=name,
            arguments=args,
            ok=error_code is None,
            result=result,
            started_perf=started,
            ended_perf=time.perf_counter(),
            error_code=error_code,
        )
        self._record(record)
        return result

    def _record(self, record: ToolCallRecord) -> None:
        if self.log_calls:
            self.calls.append(record)

    # -- tracing helpers ---------------------------------------------------
    def take_calls(self) -> list[dict[str, Any]]:
        """Return the calls recorded so far and clear the buffer."""
        out = [c.to_dict() for c in self.calls]
        self.calls.clear()
        return out

    def peek_calls(self) -> list[ToolCallRecord]:
        return list(self.calls)

    def allowed_fact_tokens(self) -> set[str]:
        """Every token the model is allowed to state as a fact.

        Built from tool output only. Used by the grounding check so that a price
        the model produced from memory rather than from a tool is detectable.
        """
        import re

        allowed: set[str] = set()
        for call in self.calls:
            if not call.ok:
                continue
            for match in re.findall(r"\d+(?:\.\d+)?", _stringify(call.result)):
                allowed.add(match.rstrip("0").rstrip(".") if "." in match else match)
        return allowed


def _stringify(value: Any, depth: int = 0) -> str:
    if depth > 6:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        return " ".join(_stringify(v, depth + 1) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_stringify(v, depth + 1) for v in value)
    return ""


# ---------------------------------------------------------------------------
# JSON Schema fragments reused by the tool definitions
# ---------------------------------------------------------------------------

STRING = {"type": "string"}


def _array_of_string(desc: str) -> JSONSchema:
    return {"type": "array", "items": {"type": "string"}, "description": desc}


def _obj(props: JSONSchema, required: list[str] | None = None) -> JSONSchema:
    return {
        "type": "object",
        "properties": props,
        "required": required or [],
        "additionalProperties": False,
    }


LINE_SPEC = _obj(
    {
        "item": {"type": "string", "description": "Menu item id, English name, or Egyptian Arabic name. Use search_menu first if unsure."},
        "quantity": {"type": "integer", "minimum": 1, "description": "How many of the item."},
        "options": _array_of_string("Customization option ids or names, e.g. 'extra_cheese', 'no_onion', 'side_fries'."),
        "special_instructions": STRING,
    },
    required=["item"],
)


def build_default_registry(
    repo: RestaurantRepository,
    orders: OrderStore,
    strict_arguments: bool = True,
    log_calls: bool = True,
    business_weekday: str | None = None,
) -> ToolRegistry:
    """Wire the deterministic tool set. Explicit construction, no globals."""
    from . import business_tools as bt

    reg = ToolRegistry(
        repo=repo,
        orders=orders,
        strict_arguments=strict_arguments,
        log_calls=log_calls,
        business_weekday=business_weekday,
    )

    reg.register(
        "search_menu",
        lambda **kw: bt.search_menu(reg, **kw),
        "Search the Nile Bites menu by free text, in English or Egyptian Arabic. Returns matching items with prices and availability. Use this whenever you do not already know the exact item id.",
        _obj(
            {
                "query": {"type": "string", "description": "What the customer asked for. May be Arabic, English, or mixed."},
                "category": STRING,
                "only_available": {"type": "boolean", "description": "If true, hide unavailable items."},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
            }
        ),
    )

    reg.register(
        "get_menu_item",
        lambda **kw: bt.get_menu_item(reg, **kw),
        "Get one menu item by id, name, or alias. Returns price, ingredients, allergens, and available customization groups.",
        _obj({"item": {"type": "string", "description": "Item id, English name, or Egyptian Arabic alias."}}, required=["item"]),
    )

    reg.register(
        "get_price",
        lambda **kw: bt.get_price(reg, **kw),
        "Get the price of an item, optionally including customization options. Returns the unit price and the price for a given quantity.",
        _obj(
            {
                "item": STRING,
                "quantity": {"type": "integer", "minimum": 1},
                "options": _array_of_string("Customization option ids or names to price in."),
            },
            required=["item"],
        ),
    )

    reg.register(
        "check_availability",
        lambda **kw: bt.check_availability(reg, **kw),
        "Check whether a menu item is available right now. Returns the reason and available alternatives when it is not.",
        _obj({"item": STRING}, required=["item"]),
    )

    reg.register(
        "get_delivery_info",
        lambda **kw: bt.get_delivery_info(reg, **kw),
        "Get delivery fee, minimum order, and estimated delivery time for a zone. Also returns the full zone list when the zone is unknown or omitted.",
        _obj({"zone": STRING, "order_total": {"type": "integer", "minimum": 0, "description": "Order total after discounts, to check the free-delivery threshold."}}),
    )

    reg.register(
        "get_business_hours",
        lambda **kw: bt.get_business_hours(reg, **kw),
        "Get opening hours, closing time, and last order time, for today or for a named weekday.",
        _obj({"day": {"type": "string", "description": "Weekday name, e.g. 'friday'. Omit for the full weekly schedule."}}),
    )

    reg.register(
        "get_promotions",
        lambda **kw: bt.get_promotions(reg, **kw),
        "List active promotions, optionally filtered by whether they can apply to a given order.",
        _obj(
            {
                "zone": STRING,
                "order_total": {"type": "integer", "minimum": 0},
                "weekday": STRING,
            }
        ),
    )

    reg.register(
        "create_order",
        lambda **kw: bt.create_order(reg, **kw),
        "Create a new order with one or more lines. Use this when the customer is placing an order. Returns the full order including totals. Only use this once per order; use modify_order for later changes.",
        _obj(
            {
                "lines": {"type": "array", "items": LINE_SPEC, "minItems": 1},
                "order_mode": {"type": "string", "enum": ["delivery", "pickup"]},
                "zone": STRING,
                "special_instructions": STRING,
                "weekday": {"type": "string", "description": "Current weekday name, used for weekday promotions."},
                "student_id": {
                    "type": "string",
                    "description": "Student ID the customer gave you. Only pass it after they "
                    "have actually stated it, because it unlocks the student discount.",
                },
            },
            required=["lines"],
        ),
    )

    reg.register(
        "modify_order",
        lambda **kw: bt.modify_order(reg, **kw),
        "Change the current order: add lines, remove lines, set quantities, or replace the options on a line. Use resolve_order_reference first when the customer says 'one of the burgers' or 'it' and the order has more than one candidate line.",
        _obj(
            {
                "order_id": STRING,
                "add": {"type": "array", "items": LINE_SPEC},
                "set_quantities": {
                    "type": "array",
                    "items": _obj({"line": STRING, "quantity": {"type": "integer"}}, required=["line", "quantity"]),
                },
                "remove": {
                    "type": "array",
                    "items": _obj({"line": STRING, "quantity": {"type": "integer", "minimum": 1}}, required=["line"]),
                },
                "set_options": {
                    "type": "array",
                    "items": _obj({"line": STRING, "options": _array_of_string("Replacement option ids or names.")}, required=["line", "options"]),
                },
                "special_instructions": STRING,
                "weekday": STRING,
            }
        ),
    )

    reg.register(
        "resolve_order_reference",
        lambda **kw: bt.resolve_order_reference(reg, **kw),
        "Resolve what the customer means by a reference such as 'the burgers', 'the fries', 'the first one', 'the drinks', or 'it' into exact order line ids. Call this before modify_order when the reference is ambiguous.",
        _obj({"reference": STRING, "order_id": STRING}, required=["reference"]),
    )

    reg.register(
        "get_current_order",
        lambda **kw: bt.get_current_order(reg, **kw),
        "Return the current order with all lines, quantities, options, and totals. Use before answering any question about what is in the order.",
        _obj({"order_id": STRING}),
    )

    reg.register(
        "calculate_order_total",
        lambda **kw: bt.calculate_order_total(reg, **kw),
        "Recompute and return the order total as a full breakdown: subtotal, discounts, delivery fee, and total.",
        _obj({"order_id": STRING}),
    )

    reg.register(
        "cancel_order",
        lambda **kw: bt.cancel_order(reg, **kw),
        "Cancel the current order.",
        _obj({"order_id": STRING, "reason": STRING}),
    )

    reg.register(
        "confirm_order",
        lambda **kw: bt.confirm_order(reg, **kw),
        "Confirm the current order, for example when the customer says 'that's all, confirm it'.",
        _obj({"order_id": STRING}),
    )

    return reg


TOOL_REGISTRY = build_default_registry  # factory alias, kept for discoverability
_DEFAULT_REGISTRY: ToolRegistry | None = None


def get_tool(name: str) -> Callable[..., Any] | None:
    return _DEFAULT_REGISTRY.tools.get(name) if _DEFAULT_REGISTRY else None


def tool_names() -> list[str]:
    return _DEFAULT_REGISTRY.tool_names() if _DEFAULT_REGISTRY else []
