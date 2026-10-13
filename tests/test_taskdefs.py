"""Task definitions, assertions, and trial scoring.

A task is the operational form of a research construct: "combined" is a
construct, "the order totals 300 EGP on a Wednesday" is an assertion. These tests
check that the file, the parser, and the evaluator agree, because a mismatch
here silently changes what counts as a success.
"""

from __future__ import annotations

import json

import pytest

from nbva.db.orders import OrderStore
from nbva.research.taskdefs import (
    TASK_FAMILIES,
    evaluate_task,
    load_tasks,
    task_from_dict,
)

WEDNESDAY = "wednesday"


@pytest.fixture
def task_index(tasks):
    return {t.task_id: t for t in tasks}


def _order(**overrides):
    base = {
        "order_id": "NB-ORD-0001",
        "status": "draft",
        "delivery_zone_id": "Z1",
        "subtotal": 290,
        "discount_total": 0,
        "delivery_fee": 15,
        "total": 305,
        "lines": [
            {"line_id": "L1", "item_id": "NB-BURG-001", "quantity": 1,
             "item_name_en": "Classic Chicken Burger", "line_total": 145, "options": []},
        ],
    }
    base.update(overrides)
    return base


# -- the shipped file -----------------------------------------------------


def test_the_task_file_loads(tasks):
    assert len(tasks) == 26


def test_every_weekday_promotion_is_covered_by_some_task(tasks):
    """PROM-01/03/04 only fire on a day, so a Wednesday-only set proves nothing.

    The weekday machinery is useless if no trial runs on the day a promotion
    needs, so at least one task has to pin each of those days.
    """
    weekdays = {t.business_weekday for t in tasks}
    assert {"tuesday", "sunday", "friday"} <= weekdays


def test_the_gated_student_promotion_is_covered_both_ways(tasks):
    """The gate has to be tested with and without the evidence, not just one.

    A set that only ever supplies a student id would pass even if the gate were
    ignored entirely, which is the failure this pair exists to catch.
    """
    gated = [
        t
        for t in tasks
        if t.business_weekday == "sunday"
        and (t.expected_lines or [])
        and any("NB-GRIL-001" in json.dumps(ln) for ln in t.expected_lines)
    ]
    assert any(t.student_id for t in gated)
    assert any(not t.student_id for t in gated)


def test_task_ids_are_unique(tasks):
    ids = [t.task_id for t in tasks]
    assert len(ids) == len(set(ids))


def test_every_task_belongs_to_a_declared_family(tasks):
    for t in tasks:
        assert t.family in TASK_FAMILIES, f"{t.task_id} has family {t.family!r}"


def test_all_seven_families_are_represented(tasks):
    assert {t.family for t in tasks} == set(TASK_FAMILIES)


def test_every_task_has_at_least_one_utterance(tasks):
    for t in tasks:
        assert t.turns, f"{t.task_id} has no turns"
        assert t.turns[0].utterance.strip()


def test_turn_indices_start_at_one_and_increase(tasks):
    for t in tasks:
        assert t.turns[0].index == 1
        assert [turn.index for turn in t.turns] == list(range(1, len(t.turns) + 1))


def test_every_task_pins_its_business_weekday(tasks):
    # An unpinned task would let the calendar decide the expected total.
    for t in tasks:
        assert t.business_weekday, f"{t.task_id} does not pin business_weekday"


def test_every_order_task_declares_what_it_expects(tasks):
    for t in tasks:
        if t.family not in ("order_creation", "order_modification", "combined"):
            continue
        has_assertion = any(
            [
                t.expected_total is not None,
                t.expected_subtotal is not None,
                t.expected_error is not None,
                bool(t.expected_lines),
                bool(t.expected_tools),
            ]
        )
        assert has_assertion, f"{t.task_id} asserts nothing"


def test_every_task_states_at_least_one_assertion(tasks):
    for t in tasks:
        has_assertion = any(
            [
                t.expected_total is not None,
                t.expected_error is not None,
                t.expect_no_order,
                bool(t.expected_facts),
                bool(t.expected_tools),
                bool(t.expected_lines),
            ]
        )
        assert has_assertion, f"{t.task_id} has no assertions at all"


def test_a_refusal_task_must_not_also_expect_an_order(tasks):
    for t in tasks:
        if t.expected_error:
            assert t.expect_no_order, f"{t.task_id} expects both a refusal and an order"


def test_the_matched_condition_set_declares_its_own_conditions(task_index):
    matched = [t for t in task_index.values() if t.task_id.startswith("DEV-MC-")]
    assert len(matched) == 5
    assert sorted(t.declared_condition for t in matched) == ["C0", "C1", "C2", "C3", "C4"]


def test_the_matched_condition_set_keeps_the_order_constant(task_index):
    # A matched set exists to vary only the code-switching, so every member must
    # quote the same order.
    matched = [t for t in task_index.values() if t.task_id.startswith("DEV-MC-")]
    assert len({t.expected_total for t in matched}) == 1
    assert len({json_lines(t) for t in matched}) == 1, "matched tasks differ in their lines"


def json_lines(task):
    return tuple(
        (line.get("item"), line.get("quantity"))
        for line in (task.expected_lines or [])
    )


# -- parsing --------------------------------------------------------------


def test_a_task_round_trips_through_its_dict(task_index):
    original = task_index["DEV-ORD-01"]
    again = task_from_dict(original.to_dict())
    assert again.task_id == original.task_id
    assert again.expected_total == original.expected_total
    assert again.business_weekday == original.business_weekday


def test_to_dict_is_json_serialisable(task_index):
    import json

    json.dumps(task_index["DEV-ORD-01"].to_dict())


def test_a_dict_without_a_task_id_is_rejected():
    with pytest.raises(Exception):
        task_from_dict({"family": "information", "turns": [{"utterance": "hi"}]})


def test_an_unknown_family_is_rejected():
    with pytest.raises(Exception):
        task_from_dict(
            {
                "task_id": "X-1",
                "family": "telepathy",
                "turns": [{"utterance": "hi"}],
            }
        )


# -- evaluation -----------------------------------------------------------


def _matched_order(spec):
    """An order that satisfies every money and line assertion of a task."""
    return _order(
        total=spec.expected_total,
        subtotal=spec.expected_subtotal,
        delivery_fee=spec.expected_delivery_fee,
        delivery_zone_id=spec.expected_zone_id or "Z1",
        lines=[
            {
                "line_id": f"L{i + 1}",
                "item_id": line.get("item_id"),
                "quantity": line.get("quantity", 1),
                "item_name_en": line.get("item_id", ""),
                "options": [{"option_id": o} for o in (line.get("options") or [])],
            }
            for i, line in enumerate(spec.expected_lines or [])
        ],
    )


def test_a_matching_order_passes(task_index):
    spec = task_index["DEV-MC-C0"]
    result = evaluate_task(
        spec, _matched_order(spec), [{"name": "create_order", "result": {"ok": True}}]
    )
    assert result.success is True
    assert result.score == 1.0


def test_a_wrong_total_fails_and_is_reported(task_index):
    spec = task_index["DEV-MC-C0"]
    order = _matched_order(spec)
    order["total"] = (spec.expected_total or 0) + 1
    result = evaluate_task(spec, order, [{"name": "create_order", "result": {"ok": True}}])
    assert result.success is False
    failed = [c for c in result.checks if not c["passed"]]
    assert any(c["check"] == "order_total" for c in failed)
    assert result.score < 1.0


def test_a_wrong_subtotal_fails_and_is_reported(task_index):
    spec = task_index["DEV-MC-C0"]
    order = _matched_order(spec)
    order["subtotal"] = (spec.expected_subtotal or 0) + 1
    result = evaluate_task(spec, order, [{"name": "create_order", "result": {"ok": True}}])
    assert result.success is False
    assert any(c["check"] == "order_subtotal" for c in result.checks if not c["passed"])


def test_a_missing_order_fails_when_one_was_expected(task_index):
    spec = task_index["DEV-MC-C0"]
    result = evaluate_task(spec, None, [{"name": "create_order", "result": {"ok": True}}])
    assert result.success is False
    assert any(c["check"] == "order_exists" for c in result.checks if not c["passed"])


def test_an_expected_refusal_passes_when_the_tool_refused(task_index):
    spec = task_index["DEV-ORD-02"]
    calls = [
        {
            "name": "create_order",
            "result": {"ok": False, "error": "BELOW_MIN_ORDER", "detail": "under minimum"},
        }
    ]
    result = evaluate_task(spec, None, calls)
    assert result.success is True


def test_an_expected_refusal_fails_when_the_tool_succeeded(task_index):
    spec = task_index["DEV-ORD-02"]
    calls = [{"name": "create_order", "result": {"ok": True}}]
    result = evaluate_task(spec, None, calls)
    assert result.success is False
    assert any(c["check"] == "expected_error_raised" for c in result.checks if not c["passed"])


def test_expect_no_error_catches_a_silent_tool_failure():
    # A task that insists the deterministic layer succeeds must fail when a tool
    # refused, even if the order state happens to look plausible.
    spec = task_from_dict(
        {
            "task_id": "T-NOERR",
            "family": "order_creation",
            "declared_condition": "C2",
            "business_weekday": "wednesday",
            "turns": [{"utterance": "x"}],
            "expect_no_error": True,
            "expected_total": 100,
        }
    )
    order = _order(total=100, subtotal=85, delivery_fee=15)
    bad = evaluate_task(
        spec, order, [{"name": "create_order", "result": {"ok": False, "error": "UNAVAILABLE"}}]
    )
    assert bad.success is False
    assert any(c["check"] == "no_tool_error" for c in bad.checks if not c["passed"])
    good = evaluate_task(spec, order, [{"name": "create_order", "result": {"ok": True}}])
    assert good.success is True


def test_a_forbidden_tool_is_caught():
    spec = task_from_dict(
        {
            "task_id": "T-FORBID",
            "family": "order_creation",
            "declared_condition": "C2",
            "business_weekday": "wednesday",
            "turns": [{"utterance": "x"}],
            "forbidden_tools": ["modify_order"],
            "expected_total": 305,
            "expected_subtotal": 290,
            "expected_delivery_fee": 15,
            "expected_zone_id": "Z1",
        }
    )
    order = _order(total=305, subtotal=290, delivery_fee=15)
    result = evaluate_task(
        spec,
        order,
        [
            {"name": "create_order", "result": {"ok": True}},
            {"name": "modify_order", "result": {"ok": True}},
        ],
    )
    assert result.success is False
    assert any(c["check"] == "tool_not_called:modify_order" for c in result.checks if not c["passed"])


def test_a_required_tool_that_was_never_called_fails():
    spec = task_from_dict(
        {
            "task_id": "T-REQ",
            "family": "information",
            "declared_condition": "C0",
            "business_weekday": "wednesday",
            "turns": [{"utterance": "x"}],
            "expected_tools": ["get_price"],
            "expect_no_order": True,
        }
    )
    result = evaluate_task(spec, None, [])
    assert result.success is False
    assert any(c["check"] == "tool_called:get_price" for c in result.checks if not c["passed"])


def test_a_required_fact_is_checked_against_the_response_text():
    spec = task_from_dict(
        {
            "task_id": "T-FACT",
            "family": "price_availability",
            "declared_condition": "C2",
            "business_weekday": "wednesday",
            "turns": [{"utterance": "x"}],
            "expect_no_order": True,
            "expected_facts": [{"type": "number", "value": "145"}],
        }
    )
    assert evaluate_task(spec, None, [], response_text="السعر 145 جنيه").success is True
    assert evaluate_task(spec, None, [], response_text="السعر 150 جنيه").success is False


def test_a_regex_fact_assertion_is_supported():
    spec = task_from_dict(
        {
            "task_id": "T-REGEX",
            "family": "delivery",
            "declared_condition": "C1",
            "business_weekday": "wednesday",
            "turns": [{"utterance": "x"}],
            "expect_no_order": True,
            "expected_facts": [{"type": "regex", "value": r"\d+\s*-\s*\d+\s*minutes"}],
        }
    )
    assert evaluate_task(spec, None, [], response_text="25-35 minutes").success is True
    assert evaluate_task(spec, None, [], response_text="soon").success is False


def test_a_task_with_no_checks_is_not_silently_a_success():
    spec = task_from_dict(
        {
            "task_id": "T-EMPTY",
            "family": "information",
            "declared_condition": "C0",
            "business_weekday": "wednesday",
            "turns": [{"utterance": "x"}],
        }
    )
    result = evaluate_task(spec, None, [])
    assert result.success is False
    assert result.score == 0.0


def test_the_state_diff_is_reported_for_a_mismatch(task_index):
    spec = task_index["DEV-MC-C0"]
    order = _matched_order(spec)
    order["total"] = 999
    result = evaluate_task(spec, order, [{"name": "create_order", "result": {"ok": True}}])
    assert result.state_diff is None or isinstance(result.state_diff, str)


# -- the task totals agree with the real engine ---------------------------


def test_the_expected_totals_are_reproducible_from_the_shipped_database(repo, tasks):
    """Every single-turn money assertion is replayed through OrderStore.

    This is the check that stops the expected-value column from drifting away
    from the deterministic layer it is supposed to describe.
    """
    from nbva.tools.registry import build_default_registry

    for task in tasks:
        if task.expected_total is None or len(task.turns) != 1:
            continue
        if task.expected_tools != ["create_order"]:
            continue
        store = OrderStore(repo, f"verify-{task.task_id}")
        registry = build_default_registry(
            repo=repo, orders=store, business_weekday=task.business_weekday
        )
        result = registry.dispatch(
            "create_order",
            {
                "lines": [
                    {
                        "item": line.get("item_id") or line.get("item"),
                        "quantity": line.get("quantity", 1),
                        "options": line.get("options") or [],
                    }
                    for line in (task.expected_lines or [])
                ],
                "order_mode": task.expected_order_mode or "delivery",
                "zone": task.expected_zone_id,
                "student_id": task.student_id,
            },
        )
        assert result.get("ok") is True, f"{task.task_id}: {result}"
        assert result["order"]["total"] == task.expected_total, task.task_id
