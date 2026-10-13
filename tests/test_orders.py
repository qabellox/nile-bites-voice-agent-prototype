"""Order arithmetic against the real dataset.

Every assertion here is a business fact the agent is expected to state, so a
regression in this file means the agent would start lying about money. The
weekday is pinned because promotions and hours depend on it.
"""

from __future__ import annotations

import pytest

from tests.conftest import WEDNESDAY

BURGER = {"item_id": "NB-BURG-001", "quantity": 2}  # 145 each
COKE = {"item_id": "NB-DRNK-001", "quantity": 1}  # 25
FRIES = {"item_id": "NB-SIDE-001", "quantity": 1}  # 55
PLATTER = {"item_id": "NB-GRIL-001", "quantity": 1}  # 320
FALAFEL_WRAP = {"item_id": "NB-WRAP-002", "quantity": 1}  # 70


def create(orders, lines, zone="Z1", mode="delivery", weekday=WEDNESDAY):
    result = orders.create_order(lines, order_mode=mode, zone_id=zone, weekday=weekday)
    assert result.get("ok"), result
    return result["order"]


def test_line_and_order_totals_are_consistent(orders):
    order = create(orders, [BURGER, COKE])
    assert order["subtotal"] == 2 * 145 + 25
    assert order["discount_total"] == sum(d["amount"] for d in order["discounts"])
    assert order["total"] == order["subtotal"] - order["discount_total"] + order["delivery_fee"]


def test_sum_of_line_totals_equals_subtotal(orders):
    order = create(orders, [BURGER, COKE, FRIES])
    assert sum(l["line_total"] for l in order["lines"]) == order["subtotal"]


def test_options_change_the_unit_price(orders):
    plain = create(orders, [{"item_id": "NB-BURG-001", "quantity": 1}], weekday="tuesday")
    loaded = create(
        orders,
        [{"item_id": "NB-BURG-001", "quantity": 1, "options": ["extra_cheese", "bacon"]}],
        weekday="tuesday",
    )
    assert loaded["lines"][0]["unit_price"] - plain["lines"][0]["unit_price"] == 25 + 30


def test_zero_price_option_does_not_change_money(orders):
    with_option = create(
        orders, [{"item_id": "NB-BURG-001", "quantity": 2, "options": ["no_cheese"]}]
    )
    without = create(orders, [{"item_id": "NB-BURG-001", "quantity": 2}])
    assert with_option["subtotal"] == without["subtotal"] == 290


# -- promotions -----------------------------------------------------------


def test_promotion_applies_on_a_freshly_created_order(orders):
    """Regression: eligibility used to read a stale subtotal of 0.

    Nothing had called rebuild_totals() before the promotion check, so every
    newly created order saw subtotal 0, no promotion could ever match, and
    every total was silently too high.
    """
    order = create(orders, [BURGER, COKE])
    assert [d["promotion_id"] for d in order["discounts"]] == ["PROM-05"]
    assert order["discount_total"] == 40


def test_promotion_recomputes_when_a_line_is_removed(orders):
    first = create(orders, [BURGER, COKE])
    assert first["discount_total"] == 40
    # Dropping below the 2-item threshold must withdraw the discount.
    result = orders.modify_order(remove=[{"line_id": "L1", "quantity": 1}], weekday=WEDNESDAY)
    order = result["order"]
    assert order["discounts"] == []
    assert order["discount_total"] == 0


def test_discount_total_is_current_when_the_delivery_fee_is_computed(orders):
    """Regression: the free-delivery waiver read a stale discount_total.

    315 - 40 = 275 is below the 300 threshold, so the fee must be charged. With
    a stale discount_total of 0 the waiver compared 315 against 300 and
    wrongly waived the fee.
    """
    order = create(orders, [BURGER, COKE])
    assert order["subtotal"] - order["discount_total"] == 275
    assert order["delivery_fee"] == 15
    assert order["total"] == 290


def test_free_delivery_threshold_uses_post_discount_money(orders):
    # 375 post-discount-equivalent is over 300 with no promotion involved.
    order = create(orders, [PLATTER, FRIES])
    assert order["discounts"] == []
    assert order["delivery_fee"] == 0
    assert order["total"] == 375


def test_weekday_promotion_only_fires_on_its_day(orders):
    tuesday = create(orders, [{"item_id": "NB-BURG-002", "quantity": 1}], weekday="tuesday")
    wednesday = create(orders, [{"item_id": "NB-BURG-002", "quantity": 1}], weekday="wednesday")
    assert [d["promotion_id"] for d in tuesday["discounts"]] == ["PROM-01"]
    assert wednesday["discounts"] == []


def test_promotions_are_not_stackable(orders):
    # Large enough for PROM-02, and also 2+ of a PROM-05 eligible item.
    order = create(
        orders,
        [{"item_id": "NB-GRIL-001", "quantity": 1}, {"item_id": "NB-BURG-001", "quantity": 2}],
    )
    assert len(order["discounts"]) == 1


def test_pickup_never_charges_delivery(orders):
    order = create(orders, [BURGER, COKE], zone=None, mode="pickup")
    assert order["delivery_fee"] == 0
    assert order["delivery_zone_id"] is None


# -- the free delivery promotion ------------------------------------------


def test_the_free_delivery_promotion_waives_the_fee_on_its_day(orders):
    # 210 EGP is over PROM-04's 200 minimum but under the general 300
    # threshold, so only the promotion can explain a zero fee.
    order = create(orders, [{"item_id": "NB-WRAP-002", "quantity": 3}], weekday="friday")
    assert order["subtotal"] == 210
    assert [d["promotion_id"] for d in order["discounts"]] == ["PROM-04"]
    assert order["discounts"][0]["reason"] == "free_delivery"
    assert order["delivery_fee"] == 0
    assert order["total"] == 210


def test_the_same_order_on_another_day_still_pays_the_fee(orders):
    order = create(orders, [{"item_id": "NB-WRAP-002", "quantity": 3}], weekday="thursday")
    assert order["discounts"] == []
    assert order["delivery_fee"] == 15
    assert order["total"] == 225


def test_the_free_delivery_promotion_respects_its_zone_list(orders):
    order = create(
        orders,
        [{"item_id": "NB-WRAP-002", "quantity": 3}],
        zone="Z3",
        weekday="friday",
    )
    assert order["discounts"] == []
    assert order["delivery_fee"] == 25


def test_the_free_delivery_promotion_respects_its_minimum(orders):
    order = create(orders, [{"item_id": "NB-WRAP-002", "quantity": 2}], weekday="friday")
    assert order["subtotal"] == 140
    assert order["discounts"] == []
    assert order["delivery_fee"] == 15


def test_a_free_delivery_promotion_takes_the_single_discount_slot(orders):
    # Friday and Z1, and over PROM-05's minimum with 2 of an eligible item, so a
    # money promotion would also qualify. PROM-04 comes first in the dataset, so
    # it takes the one non-stackable slot and blocks PROM-05.
    order = create(
        orders,
        [{"item_id": "NB-BURG-001", "quantity": 2}, {"item_id": "NB-SIDE-001", "quantity": 1}],
        weekday="friday",
    )
    assert order["subtotal"] == 345
    assert [d["promotion_id"] for d in order["discounts"]] == ["PROM-04"]
    assert order["discount_total"] == 0
    assert order["delivery_fee"] == 0


def test_the_first_applicable_promotion_wins_regardless_of_type(orders):
    # PROM-02 precedes PROM-04 in the dataset, so the money promotion takes the
    # slot on a Friday order that also qualifies for free delivery.
    order = create(
        orders,
        [{"item_id": "NB-GRIL-001", "quantity": 1}, {"item_id": "NB-BURG-001", "quantity": 2}],
        weekday="friday",
    )
    assert [d["promotion_id"] for d in order["discounts"]] == ["PROM-02"]
    assert order["discount_total"] == 50
    # The fee is waived by the general 300 threshold, not by PROM-04.
    assert order["subtotal"] - order["discount_total"] >= 300
    assert order["delivery_fee"] == 0


def test_pickup_does_not_claim_a_free_delivery_promotion(orders):
    order = create(
        orders,
        [{"item_id": "NB-WRAP-002", "quantity": 3}],
        zone=None,
        mode="pickup",
        weekday="friday",
    )
    assert order["discounts"] == []
    assert order["delivery_fee"] == 0


def test_the_general_threshold_still_waives_the_fee_on_any_day(orders):
    order = create(orders, [PLATTER], weekday=WEDNESDAY)  # 320 EGP
    assert order["subtotal"] >= 300
    assert order["discounts"] == []
    assert order["delivery_fee"] == 0


# -- the student discount gate --------------------------------------------


def test_the_student_discount_is_withheld_until_the_customer_states_an_id(orders):
    # Sunday, over PROM-03's 150 minimum, but nothing has been asked yet.
    order = create(orders, [PLATTER], zone=None, mode="pickup", weekday="sunday")
    assert order["subtotal"] == 320
    assert order["discounts"] == []
    assert order["total"] == 320


def test_the_student_discount_applies_once_an_id_is_supplied(orders):
    order = orders.create_order(
        [PLATTER],
        order_mode="pickup",
        zone_id=None,
        weekday="sunday",
        student_id="STU-4471",
    )
    assert order["ok"], order
    summary = order["order"]
    assert [d["promotion_id"] for d in summary["discounts"]] == ["PROM-03"]
    assert summary["discount_total"] == 32
    assert summary["total"] == 288


def test_a_blank_student_id_does_not_unlock_the_discount(orders):
    order = orders.create_order(
        [PLATTER],
        order_mode="pickup",
        zone_id=None,
        weekday="sunday",
        student_id="   ",
    )
    assert order["ok"], order
    assert order["order"]["discounts"] == []


def test_the_student_id_is_kept_on_the_order_for_reproducibility(orders):
    order = orders.create_order(
        [PLATTER],
        order_mode="pickup",
        zone_id=None,
        weekday="sunday",
        student_id="STU-4471",
    )
    assert order["order"]["student_id"] == "STU-4471"
    # Re-totalling the same order keeps the same discount, because the evidence
    # travels with the order rather than with the dialogue.
    recalculated = orders.modify_order(
        order_id=order["order"]["order_id"],
        add=[{"item_id": "NB-DRNK-001", "quantity": 1}],
        weekday="sunday",
    )
    assert recalculated["ok"], recalculated
    assert [d["promotion_id"] for d in recalculated["order"]["discounts"]] == ["PROM-03"]


def test_the_student_discount_still_respects_its_minimum(orders):
    order = orders.create_order(
        [{"item_id": "NB-WRAP-002", "quantity": 2}],  # 140, under the minimum
        order_mode="pickup",
        zone_id=None,
        weekday="sunday",
        student_id="STU-4471",
    )
    assert order["ok"], order
    assert order["order"]["discounts"] == []


def test_the_student_discount_still_respects_its_day(orders):
    order = orders.create_order(
        [PLATTER],
        order_mode="pickup",
        zone_id=None,
        weekday="wednesday",
        student_id="STU-4471",
    )
    assert order["ok"], order
    assert order["order"]["discounts"] == []


# -- refusals -------------------------------------------------------------


def test_zone_minimum_is_enforced_against_post_discount_money(orders):
    result = orders.create_order(
        [FALAFEL_WRAP], order_mode="delivery", zone_id="Z2", weekday=WEDNESDAY
    )
    assert not result.get("ok")
    assert result["error"] == "BELOW_MIN_ORDER"
    assert result["short_by"] == 30
    assert result["current_total"] == 70


def test_delivery_without_a_zone_is_refused(orders):
    result = orders.create_order([COKE], order_mode="delivery", zone_id=None, weekday=WEDNESDAY)
    assert not result.get("ok")
    assert result["error"] == "UNKNOWN_ZONE"
    assert result["known_zones"]


def test_unknown_zone_lists_the_known_ones(orders):
    result = orders.create_order([COKE], order_mode="delivery", zone_id="ZZ", weekday=WEDNESDAY)
    assert not result.get("ok")
    assert result["error"] == "UNKNOWN_ZONE"
    assert {z["zone_id"] for z in result["known_zones"]} == {"Z1", "Z2", "Z3", "Z4", "Z5", "Z6"}


def test_unavailable_item_is_refused(orders):
    result = orders.create_order(
        [{"item_id": "NB-GRIL-005", "quantity": 1}], order_mode="pickup", weekday=WEDNESDAY
    )
    assert not result.get("ok")
    assert result["error"] == "ITEM_UNAVAILABLE"


def test_unknown_item_is_refused(orders):
    result = orders.create_order(
        [{"item_id": "NB-NOPE-999", "quantity": 1}], order_mode="pickup", weekday=WEDNESDAY
    )
    assert not result.get("ok")
    assert result["error"] == "UNKNOWN_ITEM"


def test_invalid_option_is_refused(orders):
    result = orders.create_order(
        [{"item_id": "NB-BURG-001", "quantity": 1, "options": ["unobtainium"]}],
        order_mode="pickup",
        weekday=WEDNESDAY,
    )
    assert not result.get("ok")
    assert result["error"] == "UNKNOWN_OPTION"
    assert result["reference"] == "unobtainium"


@pytest.mark.parametrize("quantity", [0, -1])
def test_non_positive_quantity_is_refused(orders, quantity):
    result = orders.create_order(
        [{"item_id": "NB-BURG-001", "quantity": quantity}],
        order_mode="pickup",
        weekday=WEDNESDAY,
    )
    assert not result.get("ok")


def test_quantity_above_the_cap_is_refused(orders, repo):
    over = repo.rules.max_quantity_per_line + 1
    result = orders.create_order(
        [{"item_id": "NB-BURG-001", "quantity": over}], order_mode="pickup", weekday=WEDNESDAY
    )
    assert not result.get("ok")
    assert result["error"] == "MAX_QUANTITY_EXCEEDED"


def test_a_refused_order_is_not_partially_saved(orders):
    before = len(orders.all_orders())
    orders.create_order(
        [BURGER, {"item_id": "NB-GRIL-005", "quantity": 1}],
        order_mode="pickup",
        weekday=WEDNESDAY,
    )
    assert len(orders.all_orders()) == before
    assert orders.current_order() is None


def test_session_order_limit_is_enforced(orders, repo):
    for _ in range(repo.rules.max_orders_per_session):
        orders.create_order([COKE], order_mode="pickup", weekday=WEDNESDAY)
    result = orders.create_order([COKE], order_mode="pickup", weekday=WEDNESDAY)
    assert not result.get("ok")
    assert "MAX" in result["error"]


# -- state transitions ----------------------------------------------------


def test_confirm_and_cancel_move_the_order_through_its_states(orders):
    order = create(orders, [BURGER], zone=None, mode="pickup")
    order_id = order["order_id"]
    assert order["status"] == "draft"

    confirmed = orders.confirm_order(order_id)["order"]
    assert confirmed["status"] == "confirmed"

    cancelled = orders.cancel_order(order_id, reason="customer changed mind")["order"]
    assert cancelled["status"] == "cancelled"
    assert orders.current_order() is None


def test_a_confirmed_order_can_no_longer_be_edited(orders):
    """A confirmed order has gone to the kitchen, so edits are refused.

    Allowing them would let the engine reprice a total the customer was already
    told, with no acknowledgement from them. The agent is told to place a new
    order instead.
    """
    order = create(orders, [BURGER], zone=None, mode="pickup")
    orders.confirm_order(order["order_id"])
    result = orders.modify_order(
        order_id=order["order_id"],
        set_quantities=[{"line_id": "L1", "quantity": 5}],
        weekday=WEDNESDAY,
    )
    assert not result.get("ok")
    assert result["error"] == "ORDER_ALREADY_CONFIRMED"
    assert result["order_status"] == "confirmed"
    assert "new order" in result["detail"]
    # The confirmed order is left exactly as the customer agreed it.
    unchanged = orders.get(order["order_id"]).to_public_dict()
    assert unchanged["lines"][0]["quantity"] == 2
    assert unchanged["status"] == "confirmed"


def test_editing_a_draft_order_still_reprices_it(orders):
    order = create(orders, [BURGER], zone=None, mode="pickup")
    result = orders.modify_order(
        order_id=order["order_id"],
        set_quantities=[{"line_id": "L1", "quantity": 5}],
        weekday=WEDNESDAY,
    )
    assert result.get("ok")
    updated = result["order"]
    assert updated["lines"][0]["quantity"] == 5
    assert updated["subtotal"] == 5 * 145
    # 5 burgers still satisfies PROM-05's two-item rule.
    assert [d["promotion_id"] for d in updated["discounts"]] == ["PROM-05"]
    assert updated["total"] == 5 * 145 - 40


def test_identical_lines_merge_into_one(orders):
    order = create(
        orders,
        [
            {"item_id": "NB-BURG-001", "quantity": 1},
            {"item_id": "NB-BURG-001", "quantity": 1},
        ],
        zone=None,
        mode="pickup",
    )
    assert len(order["lines"]) == 1
    assert order["lines"][0]["quantity"] == 2


def test_differing_options_do_not_merge(orders):
    order = create(
        orders,
        [
            {"item_id": "NB-BURG-001", "quantity": 1},
            {"item_id": "NB-BURG-001", "quantity": 1, "options": ["bacon"]},
        ],
        zone=None,
        mode="pickup",
    )
    assert len(order["lines"]) == 2


def test_order_history_records_each_transition(orders):
    order = create(orders, [BURGER], zone=None, mode="pickup")
    orders.confirm_order(order["order_id"])
    events = [e["event"] for e in orders.get(order["order_id"]).to_public_dict()["history"]]
    assert "order_created" in events
    assert "order_confirmed" in events
