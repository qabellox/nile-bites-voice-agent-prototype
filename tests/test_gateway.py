"""A1 tool gateway: parity, validation, idempotency, measured cost.

The gateway is only allowed to add *structure*. These tests pin that boundary:
the tool list must be byte-identical to the baseline's, a disabled gateway must
be exactly the baseline path, and nothing may be invented on the way through.
"""

from __future__ import annotations

import pytest

from nbva.arch.gateway import (
    IDEMPOTENT_TOOLS,
    MUTATING_TOOLS,
    ToolGateway,
    validate_arguments,
)
from nbva.tools.registry import build_default_registry


def _registry(repo, orders, weekday="wednesday"):
    return build_default_registry(repo, orders, business_weekday=weekday)


def _item_id(repo, orders) -> str:
    probe = _registry(repo, orders)
    result = probe.dispatch("search_menu", {"query": "burger", "max_results": 1})
    assert result["ok"] is True and result["items"], "dataset must expose a burger"
    return result["items"][0]["item_id"]


def _order_args(item_id: str) -> dict:
    return {"lines": [{"item": item_id, "quantity": 2}], "order_mode": "pickup"}


# -- parity ----------------------------------------------------------------


def test_tool_list_is_identical_to_the_baseline(repo, orders):
    registry = _registry(repo, orders)
    gateway = ToolGateway(registry)
    assert gateway.llm_schemas() == registry.llm_schemas()
    assert gateway.tool_names() == registry.tool_names()


def test_disabled_gateway_is_exactly_the_baseline_path(repo, orders):
    plain = _registry(repo, orders)
    gated = ToolGateway(_registry(repo, orders), enabled=False)

    a = plain.dispatch("search_menu", {"query": "burger", "max_results": 2})
    b = gated.dispatch("search_menu", {"query": "burger", "max_results": 2})

    assert a == b
    # Nothing is recorded when the component is off: an ablation must not add
    # bookkeeping that the baseline does not have.
    assert gated.take_decisions() == []


def test_unknown_tool_keeps_the_registry_error(repo, orders):
    gateway = ToolGateway(_registry(repo, orders))
    result = gateway.dispatch("delete_everything", {})
    assert result["error"] == "UNKNOWN_TOOL"
    decision = gateway.take_decisions()[0]
    assert decision["accepted"] is False
    assert decision["error_code"] == "UNKNOWN_TOOL"


# -- validation ------------------------------------------------------------


def test_missing_required_argument_is_rejected_before_dispatch(repo, orders):
    registry = _registry(repo, orders)
    gateway = ToolGateway(registry)

    result = gateway.dispatch("create_order", {})

    assert result["ok"] is False
    assert result["error"] == "VALIDATION_FAILED"
    assert "lines" in result["detail"]
    # The tool never ran, so no business state was touched.
    assert registry.calls == []


def test_wrong_quantity_type_is_rejected_not_guessed(repo, orders):
    gateway = ToolGateway(_registry(repo, orders))
    item = _item_id(repo, orders)

    result = gateway.dispatch(
        "create_order",
        {"lines": [{"item": item, "quantity": "two"}], "order_mode": "pickup"},
    )

    assert result["error"] == "VALIDATION_FAILED"
    assert "arguments.lines[0].quantity" in result["detail"]


def test_numeric_string_quantity_is_coerced(repo, orders):
    registry = _registry(repo, orders)
    gateway = ToolGateway(registry)
    item = _item_id(repo, orders)

    result = gateway.dispatch(
        "create_order",
        {"lines": [{"item": item, "quantity": "2"}], "order_mode": "pickup"},
    )

    assert result["ok"] is True
    decision = gateway.take_decisions()[0]
    assert decision["normalized_arguments"]["lines"][0]["quantity"] == 2
    assert isinstance(decision["normalized_arguments"]["lines"][0]["quantity"], int)


def test_unexpected_argument_is_rejected(repo, orders):
    gateway = ToolGateway(_registry(repo, orders))
    result = gateway.dispatch(
        "search_menu", {"query": "burger", "free_delivery": True}
    )
    assert result["error"] == "VALIDATION_FAILED"
    assert "free_delivery" in result["detail"]


def test_enum_violation_is_rejected(repo, orders):
    gateway = ToolGateway(_registry(repo, orders))
    item = _item_id(repo, orders)
    result = gateway.dispatch(
        "create_order",
        {"lines": [{"item": item}], "order_mode": "teleport"},
    )
    assert result["error"] == "VALIDATION_FAILED"
    assert "order_mode" in result["detail"]


def test_gateway_does_not_add_a_missing_quantity(repo, orders):
    """The safety rule the whole project rests on: never default a quantity."""
    gateway = ToolGateway(_registry(repo, orders))
    item = _item_id(repo, orders)

    gateway.dispatch(
        "create_order", {"lines": [{"item": item}], "order_mode": "pickup"}
    )

    normalized = gateway.take_decisions()[0]["normalized_arguments"]
    assert "quantity" not in normalized["lines"][0]


def test_empty_line_list_is_rejected(repo, orders):
    gateway = ToolGateway(_registry(repo, orders))
    result = gateway.dispatch("create_order", {"lines": []})
    assert result["error"] == "VALIDATION_FAILED"
    assert "at least 1" in result["detail"]


# -- idempotency -----------------------------------------------------------


def test_duplicate_create_order_is_suppressed(repo, orders):
    registry = _registry(repo, orders)
    gateway = ToolGateway(registry)
    args = _order_args(_item_id(repo, orders))

    first = gateway.dispatch("create_order", args)
    second = gateway.dispatch("create_order", args)

    assert first["ok"] is True
    assert second.get("reused") is True
    assert len(registry.calls) == 1, "the second call must not reach the tool"
    assert gateway.take_decisions()[1]["reasons"] == [
        "duplicate mutation suppressed"
    ]


def test_idempotency_can_be_disabled(repo, orders):
    registry = _registry(repo, orders)
    gateway = ToolGateway(registry, idempotency=False)
    args = _order_args(_item_id(repo, orders))

    gateway.dispatch("create_order", args)
    second = gateway.dispatch("create_order", args)

    assert second.get("reused") is not True
    assert len(registry.calls) == 2


def test_only_create_order_is_deduplicated():
    # modify_order is excluded on purpose: two identical "add one more" calls in
    # one turn are a legitimate pair, and suppressing the second loses a line.
    assert IDEMPOTENT_TOOLS == {"create_order"}
    assert "modify_order" not in IDEMPOTENT_TOOLS
    assert IDEMPOTENT_TOOLS <= MUTATING_TOOLS


# -- measurement -----------------------------------------------------------


def test_each_call_records_its_own_cost(repo, orders):
    gateway = ToolGateway(_registry(repo, orders))
    gateway.dispatch("search_menu", {"query": "burger"})
    gateway.dispatch("get_menu_item", {"item": "does-not-exist"})

    decisions = gateway.take_decisions()
    assert len(decisions) == 2
    for decision in decisions:
        assert decision["validation_ms"] >= 0.0
        assert decision["dispatch_ms"] >= 0.0
        assert decision["arrival"] in (0, 1)


def test_rejected_call_still_costs_time(repo, orders):
    """A validator that rejects everything is not free, and must not look free."""
    gateway = ToolGateway(_registry(repo, orders))
    gateway.dispatch("create_order", {})
    decision = gateway.take_decisions()[0]
    assert decision["dispatch_ms"] == 0.0
    assert decision["validation_ms"] >= 0.0


# -- validator unit surface -------------------------------------------------


def test_validator_reports_every_error_not_just_the_first():
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "integer", "minimum": 1},
            "b": {"type": "string", "enum": ["x", "y"]},
        },
        "required": ["a", "b"],
        "additionalProperties": False,
    }
    normalized, errors = validate_arguments(schema, {"a": 0, "b": "z"})
    assert normalized["a"] == 0
    assert any("minimum" in e for e in errors)
    assert any("one of" in e for e in errors)
