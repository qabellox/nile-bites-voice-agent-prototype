"""Check every task's expected money against the real order engine.

The task file is authored by hand, so its expected totals are a claim, not a
fact. This script replays each task's final expected line set through the actual
deterministic OrderStore and reports where the claim and the engine disagree.
Hand arithmetic is exactly the kind of thing that silently rots a task bank.

Not a research measurement.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nbva.db.orders import OrderStore  # noqa: E402
from nbva.db.repository import RestaurantRepository  # noqa: E402
from nbva.db.schema import RestaurantData  # noqa: E402
from nbva.research.taskdefs import load_tasks  # noqa: E402

DB = ROOT / "data" / "restaurant" / "nile_bites.json"
TASKS = ROOT / "data" / "tasks" / "tasks.yaml"

# Which zone each task delivers to, when the task expects a total that
# includes a delivery fee.
ZONE_FOR_TASK = {
    "DEV-MC-C0": "Z1", "DEV-MC-C1": "Z1", "DEV-MC-C2": "Z1", "DEV-MC-C3": "Z1", "DEV-MC-C4": "Z1",
    "DEV-MOD-01": "Z1",
    "DEV-MOD-02": "Z1",
    "DEV-COMB-01": "Z3",
    "DEV-COMB-02": "Z2",
}


def main() -> int:
    repo = RestaurantRepository(RestaurantData.model_validate_json(DB.read_text(encoding="utf-8")))
    tasks = load_tasks(TASKS)
    print(f"loaded {len(tasks)} dev tasks from {TASKS.name}\n")

    problems = 0
    checked = 0

    for spec in tasks:
        # Only tasks that declare a final expected order can be replayed.
        # Multi-turn and negation tasks end in agent dialogue, not in a state
        # the file pins, so they are reported as manual-review instead.
        if not spec.expected_lines or spec.expect_no_order is True:
            print(f"[manual] {spec.task_id:<12} no pinned final order; verify by hand")
            continue
        if len(spec.turns) > 1:
            print(f"[manual] {spec.task_id:<12} multi-turn; final state not pinned by the file")
            continue

        weekday = spec.business_weekday or "wednesday"
        # The task file wins where it declares a zone or a mode; the map below
        # only covers the older tasks that expect a delivery fee without
        # declaring where they deliver to.
        zone = spec.expected_zone_id or ZONE_FOR_TASK.get(spec.task_id)
        order_mode = spec.expected_order_mode or ("delivery" if zone else "pickup")

        lines = [
            {
                "item_id": ln["item_id"],
                "quantity": int(ln["quantity"]),
                "options": list(ln.get("options") or []),
            }
            for ln in spec.expected_lines
        ]
        store = OrderStore(repo, "verify")
        result = store.create_order(
            lines,
            order_mode=order_mode,
            zone_id=zone,
            weekday=weekday,
            student_id=spec.student_id,
        )
        checked += 1

        if "error" in result:
            print(f"[FAIL  ] {spec.task_id:<12} engine refused the order: {result['error']}")
            problems += 1
            continue

        order = result.get("order", result)
        actual = {
            "subtotal": order["subtotal"],
            "delivery_fee": order["delivery_fee"],
            "total": order["total"],
        }

        claims = {
            "subtotal": spec.expected_subtotal,
            "delivery_fee": spec.expected_delivery_fee,
            "total": spec.expected_total,
        }

        mismatches = [
            f"{k}: file={v} engine={actual[k]}"
            for k, v in claims.items()
            if v is not None and v != actual[k]
        ]

        discounts = order.get("discounts") or []
        promo = f"{discounts[0]['promotion_id']} " if discounts else "no-promo "
        if mismatches:
            print(f"[FAIL  ] {spec.task_id:<12} {promo} " + " | ".join(mismatches))
            print(
                f"           engine: subtotal={actual['subtotal']} "
                f"discount={order.get('discount_total')} "
                f"fee={actual['delivery_fee']} total={actual['total']}"
            )
            problems += 1
        else:
            print(
                f"[ok    ] {spec.task_id:<12} {promo} "
                f"subtotal={actual['subtotal']} fee={actual['delivery_fee']} total={actual['total']}"
            )

    print(f"\nreplayed {checked} single-turn tasks, {problems} disagree with the engine")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
