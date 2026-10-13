"""The deterministic tool layer the LLM is allowed to see.

The agent must never state a fact that did not come from one of these, so each
tool is pinned to the value the shipped dataset actually holds. Field names here
mirror the real tool payloads, because a mismatch would mean the tests were
passing against a shape the model never receives.
"""

from __future__ import annotations

import pytest

from nbva.db.orders import OrderStore
from nbva.tools.registry import build_default_registry
from tests.conftest import WEDNESDAY

EXPECTED_TOOL_COUNT = 14

TOOL_NAMES = {
    "search_menu",
    "get_menu_item",
    "get_price",
    "check_availability",
    "get_delivery_info",
    "get_business_hours",
    "get_promotions",
    "create_order",
    "modify_order",
    "cancel_order",
    "confirm_order",
    "get_current_order",
    "calculate_order_total",
    "resolve_order_reference",
}


@pytest.fixture
def registry(repo):
    return build_default_registry(repo, OrderStore(repo, "tool-tests"))


def call(registry, name, **kwargs):
    return registry.dispatch(name, kwargs)


def two_line_order(registry):
    return call(
        registry,
        "create_order",
        lines=[{"item_id": "NB-BURG-001", "quantity": 2}, {"item_id": "NB-SIDE-001", "quantity": 1}],
        order_mode="pickup",
        weekday=WEDNESDAY,
    )


# -- registry shape -------------------------------------------------------


def test_all_expected_tools_are_exposed(registry):
    names = {t["function"]["name"] for t in registry.llm_schemas()}
    assert names == TOOL_NAMES
    assert len(names) == EXPECTED_TOOL_COUNT


def test_every_schema_declares_a_description_and_parameters(registry):
    for schema in registry.llm_schemas():
        assert schema["function"]["description"], schema["name"]
        assert isinstance(schema["function"]["parameters"], dict)


def test_unknown_tool_returns_an_error_instead_of_raising(registry):
    result = registry.dispatch("teleport", {})
    assert result.get("error")


# -- menu lookups ---------------------------------------------------------


def test_search_finds_an_item_by_arabic_name(registry):
    result = call(registry, "search_menu", query="تشيكن برجر")
    assert result["match_count"] >= 1
    assert result["items"][0]["item_id"] == "NB-BURG-001"


def test_search_finds_an_item_by_english_name(registry):
    result = call(registry, "search_menu", query="koshari")
    assert result["items"][0]["item_id"] == "NB-GRIL-002"


@pytest.mark.parametrize("alias", ["chicken-burger", "برجر فراخ", "chickenburger", "chicken burger"])
def test_search_finds_an_item_by_alias(registry, alias):
    result = call(registry, "search_menu", query=alias)
    assert result["items"], alias
    assert result["items"][0]["item_id"] == "NB-BURG-001", alias


def test_search_for_something_absent_reports_no_match(registry):
    result = call(registry, "search_menu", query="sushi")
    assert result["ok"] is False
    assert result["error"] == "NO_MENU_MATCH"
    assert result["suggestion"]


def test_menu_payloads_report_prices_in_egp(registry):
    result = call(registry, "get_menu_item", item="NB-BURG-001")
    assert result["price_egp"] == 145
    assert result["available"] is True


def test_get_menu_item_can_resolve_by_arabic_name(registry):
    assert call(registry, "get_menu_item", item="تشيكن برجر")["item_id"] == "NB-BURG-001"


def test_get_price_returns_the_shipped_price(registry):
    result = call(registry, "get_price", item="NB-GRIL-002")
    assert result["base_price_egp"] == 85
    assert result["unit_price_egp"] == 85
    assert result["currency"] == "EGP"


def test_get_price_adds_option_costs(registry):
    result = call(registry, "get_price", item="NB-BURG-001", options=["extra_cheese", "bacon"])
    assert result["options_total_egp"] == 55
    assert result["unit_price_egp"] == 145 + 55


def test_availability_reflects_the_out_of_stock_list(registry):
    assert call(registry, "check_availability", item="NB-GRIL-005")["available"] is False
    assert call(registry, "check_availability", item="NB-BURG-001")["available"] is True


def test_availability_explains_why_an_item_is_unavailable(registry):
    result = call(registry, "check_availability", item="NB-GRIL-005")
    assert result.get("reason")


# -- delivery, hours, promotions -----------------------------------------


@pytest.mark.parametrize("zone,fee", [("Z1", 15), ("Z2", 20), ("Z3", 25), ("Z6", 45)])
def test_delivery_fee_matches_the_zone_table(registry, zone, fee):
    result = call(registry, "get_delivery_info", zone=zone)
    assert result["zone"]["delivery_fee_egp"] == fee


def test_delivery_accepts_an_arabic_zone_name(registry):
    assert call(registry, "get_delivery_info", zone="المعادي")["zone"]["zone_id"] == "Z2"


def test_delivery_states_the_free_delivery_threshold(registry):
    assert call(registry, "get_delivery_info", zone="Z1")["free_delivery_threshold_egp"] == 300


def test_delivery_flags_when_the_free_threshold_is_already_met(registry):
    below = call(registry, "get_delivery_info", zone="Z1", order_total=299)
    above = call(registry, "get_delivery_info", zone="Z1", order_total=300)
    assert below["zone"]["free_delivery_applies"] is False
    assert above["zone"]["free_delivery_applies"] is True


def test_delivery_outside_any_zone_explains_that_it_must_be_pickup(registry):
    result = call(registry, "get_delivery_info", zone="الأقصر")
    assert result["ok"] is False
    assert result["error"] == "UNKNOWN_ZONE"
    assert "pickup" in result["detail"].lower()


def test_hours_are_reported_for_the_requested_day(registry):
    result = call(registry, "get_business_hours", day="friday")
    assert result["day"]["open"] == "12:00"
    assert result["day"]["close"] == "00:30"
    assert result["timezone"] == "Africa/Cairo"


def test_hours_list_the_fictional_closure_dates(registry):
    assert "2026-01-07" in call(registry, "get_business_hours")["closed_dates"]


def test_promotions_can_be_filtered_to_a_day(registry):
    friday = call(registry, "get_promotions", weekday="friday")
    assert any(p["promotion_id"] == "PROM-04" for p in friday["promotions"])
    assert friday["evaluated_weekday"] == "friday"


def test_promotions_list_states_they_do_not_stack(registry):
    assert call(registry, "get_promotions")["stackable"] is False


# -- order lifecycle through the tools -----------------------------------


def test_tool_order_round_trip(registry):
    created = call(
        registry,
        "create_order",
        lines=[{"item_id": "NB-BURG-001", "quantity": 2}, {"item_id": "NB-DRNK-001", "quantity": 1}],
        order_mode="delivery",
        zone="Z1",
        weekday=WEDNESDAY,
    )
    assert created["ok"], created
    order_id = created["order"]["order_id"]
    assert created["order"]["total"] == 290

    total = call(registry, "calculate_order_total", order_id=order_id)
    assert total["breakdown"]["total_egp"] == 290

    assert call(registry, "confirm_order", order_id=order_id)["order"]["status"] == "confirmed"
    assert call(registry, "cancel_order", order_id=order_id, reason="test")["order"]["status"] == "cancelled"


def test_get_current_order_reflects_the_active_order(registry):
    call(
        registry,
        "create_order",
        lines=[{"item_id": "NB-BURG-001", "quantity": 1}],
        order_mode="pickup",
        weekday=WEDNESDAY,
    )
    current = call(registry, "get_current_order")
    assert current["order"]["lines"][0]["item_id"] == "NB-BURG-001"
    assert "EGP" in current["summary"]


def test_get_current_order_is_empty_before_anything_is_ordered(registry):
    result = call(registry, "get_current_order")
    assert result["ok"] is True
    assert result["order"] is None


def test_refusal_reaches_the_model_as_a_structured_error(registry):
    result = call(
        registry,
        "create_order",
        lines=[{"item_id": "NB-WRAP-002", "quantity": 1}],
        order_mode="delivery",
        zone="Z2",
        weekday=WEDNESDAY,
    )
    assert result["ok"] is False
    assert result["error"] == "BELOW_MIN_ORDER"
    assert result["short_by"] == 30


def test_recalculating_a_total_does_not_change_it_with_the_calendar(registry):
    """Regression: the total must not depend on the real day of the week.

    Re-totalling used to re-price the order under the wall-clock weekday. An
    order created on a Wednesday came back at a different total on a Sunday,
    because the Sunday promotion had been applied to an order the customer had
    already been quoted. The weekday is now pinned to the order.
    """
    created = call(
        registry,
        "create_order",
        lines=[{"item_id": "NB-BURG-001", "quantity": 2}, {"item_id": "NB-DRNK-001", "quantity": 1}],
        order_mode="delivery",
        zone="Z1",
        weekday=WEDNESDAY,
    )
    quoted = created["order"]["total"]
    assert created["order"]["priced_weekday"] == WEDNESDAY

    for _ in range(3):
        again = call(registry, "calculate_order_total", order_id=created["order"]["order_id"])
        assert again["breakdown"]["total_egp"] == quoted

    modified = call(registry, "modify_order", add=[{"item_id": "NB-DRNK-001", "quantity": 1}])
    assert modified["order"]["priced_weekday"] == WEDNESDAY
    assert call(registry, "calculate_order_total")["breakdown"]["total_egp"] == modified["order"]["total"]


def test_an_article_is_not_treated_as_a_quantity(registry):
    """Regression: "the burger" must not resolve by pretending "the" means one.

    With two different burgers in the order, the reference is genuinely
    ambiguous. Treating the article as a count of 1 made it look resolvable and
    the resolver silently chose the first line instead of asking.
    """
    call(
        registry,
        "create_order",
        lines=[{"item_id": "NB-BURG-001", "quantity": 1}, {"item_id": "NB-BURG-002", "quantity": 1}],
        order_mode="pickup",
        weekday=WEDNESDAY,
    )
    result = call(registry, "resolve_order_reference", reference="the burger")
    assert result["ambiguous"] is True
    assert set(result["line_ids"]) == {"L1", "L2"}


def test_egyptian_arabic_numerals_resolve_quantities(registry):
    two_line_order(registry)
    result = call(registry, "resolve_order_reference", reference="اتنين من البرجر")
    assert result["resolved"] is True
    assert result["line_ids"] == ["L1"]
    assert result["quantity"] == 2


def test_definite_article_does_not_block_category_resolution(registry):
    two_line_order(registry)
    result = call(registry, "resolve_order_reference", reference="شيل واحد من البرجر")
    assert result["resolved"] is True
    assert result["line_ids"] == ["L1"]
    assert result["quantity"] == 1


def test_modify_reports_the_change_and_the_new_total(registry):
    created = call(
        registry,
        "create_order",
        lines=[{"item_id": "NB-BURG-001", "quantity": 2}],
        order_mode="pickup",
        weekday=WEDNESDAY,
    )
    result = call(
        registry,
        "modify_order",
        set_quantities=[{"line_id": "L1", "quantity": 1}],
        weekday=WEDNESDAY,
    )
    assert result["ok"] is True
    assert result["order"]["lines"][0]["quantity"] == 1
    assert result["order"]["discounts"] == []
    assert result["order"]["total"] == 145


# -- reference resolution -------------------------------------------------


@pytest.mark.parametrize(
    "reference,expected",
    [
        ("fries", "L2"),
        ("بطاطس", "L2"),
        ("the burger", "L1"),
        ("برجر", "L1"),
        ("L2", "L2"),
        # Positional anaphora, the form customers actually use.
        ("the second item", "L2"),
        ("the second one", "L2"),
        ("التاني", "L2"),
        ("the first", "L1"),
        ("الأول", "L1"),
        ("the last one", "L2"),
        ("الاخر", "L2"),
    ],
)
def test_resolve_reference_maps_onto_the_right_line(registry, reference, expected):
    two_line_order(registry)
    result = call(registry, "resolve_order_reference", reference=reference)
    assert result["resolved"] is True, reference
    assert result["line_ids"] == [expected], reference


def test_resolve_reference_handles_the_whole_order(registry):
    two_line_order(registry)
    result = call(registry, "resolve_order_reference", reference="الكل")
    assert result["line_ids"] == ["L1", "L2"]


def test_resolve_reference_reports_ambiguity_instead_of_guessing(registry):
    call(
        registry,
        "create_order",
        lines=[{"item_id": "NB-BURG-001", "quantity": 1}, {"item_id": "NB-BURG-002", "quantity": 1}],
        order_mode="pickup",
        weekday=WEDNESDAY,
    )
    result = call(registry, "resolve_order_reference", reference="the burger")
    assert result.get("ambiguous") is True
    assert result.get("line_ids")
    assert result.get("recommended_action")


def test_resolve_reference_with_nothing_in_the_order(registry):
    result = call(registry, "resolve_order_reference", reference="the fries")
    assert result["error"] == "NO_CURRENT_ORDER"


def test_unresolvable_reference_returns_the_lines_for_a_clarifying_question(registry):
    two_line_order(registry)
    result = call(registry, "resolve_order_reference", reference="the sushi")
    assert result["resolved"] is False
    assert result["order_lines"]
