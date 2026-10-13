"""Conversational state for one session.

Holds the business state (order store), the short dialogue history the language
model needs for anaphora, and the trial bookkeeping. Multi-turn context
resolution is measured against this object, so its contents are logged in full at
the end of each turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..db.orders import OrderStore
from ..db.repository import RestaurantRepository
from ..obs.logger import new_session_id
from ..tools.registry import ToolRegistry, build_default_registry


@dataclass
class DialogueTurn:
    turn_index: int
    role: str            # "user" | "assistant"
    text: str
    timestamp_utc: str


@dataclass
class Session:
    """One voice conversation."""

    session_id: str
    repository: RestaurantRepository
    experiment_id: str = "unknown"
    is_research_run: bool = False
    participant_id: str = ""
    business_weekday: str | None = None
    max_history_turns: int = 12

    orders: OrderStore = field(init=False)
    tools: ToolRegistry = field(init=False)
    history: list[DialogueTurn] = field(default_factory=list)
    turn_index: int = 0
    current_trial_id: str | None = None
    trial_sequence: int = 0
    current_task: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.orders = OrderStore(self.repository, self.session_id)
        self.tools = build_default_registry(
            repo=self.repository,
            orders=self.orders,
            strict_arguments=True,
            log_calls=True,
            business_weekday=self.business_weekday,
        )

    # -- history ---------------------------------------------------------
    def add_turn(self, role: str, text: str, timestamp_utc: str) -> None:
        self.history.append(
            DialogueTurn(
                turn_index=self.turn_index, role=role, text=text, timestamp_utc=timestamp_utc
            )
        )
        self.history = self.history[-self.max_history_turns :]

    def next_turn_index(self) -> int:
        self.turn_index += 1
        return self.turn_index

    def message_history(self) -> list[dict[str, str]]:
        """Chat-format history for the language model."""
        return [{"role": t.role, "content": t.text} for t in self.history]

    # -- business state ---------------------------------------------------
    def current_order(self) -> dict[str, Any] | None:
        order = self.orders.current_order()
        return order.to_public_dict() if order else None

    def take_tool_calls(self) -> list[dict[str, Any]]:
        return self.tools.take_calls()

    def peek_tool_calls(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self.tools.peek_calls()]

    def grounding_allowed_numbers(self) -> set[str]:
        return self.tools.allowed_fact_tokens()

    # -- trial lifecycle ---------------------------------------------------
    def set_business_weekday(self, weekday: str | None) -> None:
        """Pin the business weekday for the trials that follow.

        A task may carry its own weekday so that an order total is a function of
        the trial, not of the day it happened to be run. Passing ``None`` restores
        the session default.
        """
        self.business_weekday = (weekday or None)
        self.tools.business_weekday = self.business_weekday

    def begin_trial(self, task, trial_id: str | None = None) -> str:
        self.current_task = task
        task_id = getattr(task, "task_id", None)
        if trial_id:
            self.current_trial_id = trial_id
        else:
            # A trial is one presentation of a task, so the id has to stay unique
            # when the same task is shown twice. `task_id` remains a separate
            # field on every record, so this does not change how records join.
            self.trial_sequence += 1
            self.current_trial_id = f"{task_id or 'unassigned'}-{self.trial_sequence:03d}"
        # A task-level weekday wins so a scripted set can be reproducible
        # regardless of when it is run.
        task_weekday = getattr(task, "business_weekday", None)
        if task_weekday:
            self.set_business_weekday(task_weekday)
        return self.current_trial_id

    def state_snapshot(self) -> dict[str, Any]:
        order = self.orders.current_order()
        return {
            "session_id": self.session_id,
            "experiment_id": self.experiment_id,
            "participant_id": self.participant_id,
            "turn_index": self.turn_index,
            "trial_id": self.current_trial_id,
            "orders_created": len(self.orders.all_orders()),
            "current_order_id": order.order_id if order else None,
            "order_status": order.status if order else None,
            "order_total": order.total if order else None,
            "order_line_count": len(order.lines) if order else 0,
            "history_turns": len(self.history),
        }

    def reset_for_new_trial(self) -> None:
        """Start a fresh trial while keeping the same session id.

        Business state is intentionally NOT cleared: a new trial that expects no
        order should assert it, rather than have the harness hide a leak.
        """
        self.current_task = None
        self.current_trial_id = None
        self.set_business_weekday(self.business_weekday)
        self.take_tool_calls()


def create_session(
    repository: RestaurantRepository,
    experiment_id: str = "unknown",
    is_research_run: bool = False,
    participant_id: str = "",
    business_weekday: str | None = None,
    session_id: str | None = None,
) -> Session:
    return Session(
        session_id=session_id or new_session_id("s"),
        repository=repository,
        experiment_id=experiment_id,
        is_research_run=is_research_run,
        participant_id=participant_id,
        business_weekday=business_weekday,
    )
