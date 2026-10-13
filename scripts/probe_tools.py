"""Ad-hoc integration probe for the deterministic tool layer.

Not a research measurement. Run with:  python scripts/probe_tools.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nbva.db.orders import OrderStore  # noqa: E402
from nbva.db.repository import RestaurantRepository  # noqa: E402
from nbva.tools.registry import build_default_registry  # noqa: E402


def show(label, value):
    print(f"\n--- {label} ---")
    print(json.dumps(value, ensure_ascii=False, indent=2)[:1400])


repo = RestaurantRepository.from_path(ROOT / "data" / "restaurant" / "nile_bites.json")
orders = OrderStore(repo, "probe-session")
reg = build_default_registry(repo, orders)

print("tools:", reg.tool_names())
print("schemas:", len(reg.llm_schemas()))

show("search_menu chicken", reg.dispatch("search_menu", {"query": "chicken"}))
show("get_price fries x3", reg.dispatch("get_price", {"item": "بطاطس", "quantity": 3}))
show("availability fish", reg.dispatch("check_availability", {"item": "fish"}))
show("delivery heliopolis 250", reg.dispatch("get_delivery_info", {"zone": "heliopolis", "order_total": 250}))
show("delivery heliopolis 350", reg.dispatch("get_delivery_info", {"zone": "heliopolis", "order_total": 350}))
show("unknown zone", reg.dispatch("get_delivery_info", {"zone": "aswan"}))
show("friday hours", reg.dispatch("get_business_hours", {"day": "friday"}))
show("promotions", reg.dispatch("get_promotions", {"weekday": "tuesday", "order_total": 500}))

r = reg.dispatch(
    "create_order",
    {
        "lines": [
            {"item": "chicken burger", "quantity": 2, "options": ["no_onion", "side_fries"]},
            {"item": "coke", "quantity": 1},
        ],
        "order_mode": "delivery",
        "zone": "zamalek",
        "weekday": "tuesday",
    },
)
show("create_order", r)

show("current order", reg.dispatch("get_current_order", {}))
show("resolve 'the burgers'", reg.dispatch("resolve_order_reference", {"reference": "the burgers"}))
show("resolve 'one of the burgers'", reg.dispatch("resolve_order_reference", {"reference": "one of the burgers"}))
show("resolve 'the fries'", reg.dispatch("resolve_order_reference", {"reference": "fries"}))
show("resolve 'it'", reg.dispatch("resolve_order_reference", {"reference": "it"}))
show(
    "modify L1 no cheese",
    reg.dispatch("modify_order", {"set_options": [{"line": "L1", "options": ["no_onion", "side_fries", "lettuce_wrap"]}], "weekday": "tuesday"}),
)
show("total", reg.dispatch("calculate_order_total", {}))
show("bad tool", reg.dispatch("no_such_tool", {}))
show("bad argument", reg.dispatch("create_order", {"lines": "not-a-list"}))

print("\ntool calls recorded:", len(reg.take_calls()))
