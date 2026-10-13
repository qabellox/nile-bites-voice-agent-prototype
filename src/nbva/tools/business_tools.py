"""Implementations of the deterministic business tools.

Every function here reads from :class:`RestaurantRepository` or
:class:`OrderStore` and returns plain JSON-serialisable data. No language model
is involved, and no function invents a business fact: when a reference cannot be
resolved, the result is a structured error plus the known valid values, not a
guess.
"""

from __future__ import annotations

import re
from typing import Any

from ..db.orders import OrderStore
from ..db.repository import normalize_text
from ..db.schema import Order

# --- small deterministic helpers ------------------------------------------

# Determiners and articles are deliberately NOT counts. "the burger" says which
# kind of thing, not how many, so treating "the" as 1 made an ambiguous
# reference look resolvable and the resolver silently picked a line. The
# resolver is meant to ask instead of guessing, so only real quantities count.
_NUM_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "first": 1, "second": 2, "third": 3,
    "ones": 1, "one's": 1,
    # Egyptian Arabic numerals. The study is conducted in Egyptian Arabic, so
    # "اتنين برجر" must resolve to a quantity of two just as "two burgers" does.
    "واحد": 1, "واحدة": 1, "واحده": 1, "unit": 1,
    "اتنين": 2, "اثنين": 2, "اتنينه": 2, "اثنان": 2, "اثنتين": 2,
    "تلات": 3, "تلاته": 3, "تلاتة": 3, "ثلاث": 3, "ثلاثه": 3,
    "اربعه": 4, "اربعة": 4, "اربع": 4,
    "خمسه": 5, "خمسة": 5, "خمس": 5,
    "سته": 6, "ستة": 6, "ست": 6,
    "سبعه": 7, "سبعة": 7, "سبع": 7,
    "تمنه": 8, "ثمانية": 8, "تمن": 8,
    "تسعه": 9, "تسعة": 9, "تسع": 9,
    "عشره": 10, "عشرة": 10, "عشر": 10,
}

_ORDINALS = {"l1": 0, "l2": 1, "l3": 2, "l4": 3, "l5": 4, "l6": 5, "l7": 6, "l8": 7, "l9": 8, "l10": 9}

# Positional words. Customers point at a line by its position far more often than
# by its name ("the second one", "التاني", "the last"), and those are exactly
# the references the multi-turn context tasks depend on. "last" is resolved
# against the live line count rather than being a fixed index.
_POSITIONAL: dict[str, int | None] = {
    "first": 0, "1st": 0, "one": 0,
    "second": 1, "2nd": 1,
    "third": 2, "3rd": 2,
    "fourth": 3, "4th": 3,
    "fifth": 4, "5th": 4,
    "last": None, "final": None, "other": None,
    "الاول": 0, "الأول": 0, "الاولي": 0, "الأولي": 0,
    "التاني": 1, "الثاني": 1, "التانية": 1, "الثانية": 1,
    "التالت": 2, "الثالث": 2, "التالتة": 2, "الثالثة": 2,
    "الرابع": 3, "الرابعة": 3,
    "الخامس": 4, "الخامسة": 4,
    "الاخر": None, "الأخر": None, "اخر": None, "أخر": None,
    "الاخير": None, "الأخير": None, "تاني": 1, "اول": 0,
}

# Reference words that mean "the whole group", used by the context resolver.
_ALL_WORDS = {"all", "everything", "both", "الكل", "كل", "الكل ده"}
_CATEGORY_HINTS = {
    "burger": "burgers", "burgers": "burgers", "برجر": "burgers", "برجرات": "burgers",
    "wrap": "wraps", "wraps": "wraps", "سندوتش": "wraps", "سندوتشات": "wraps",
    "grill": "grills", "grills": "grills", "مشاوي": "grills", "main": "grills",
    "side": "sides", "sides": "sides", "جنب": "sides", "الجنب": "sides", "جنبات": "sides",
    "salad": "salads", "salads": "salads", "سلطة": "salads", "سلطات": "salads",
    "drink": "drinks", "drinks": "drinks", "مشروب": "drinks", "مشروبات": "drinks",
    "dessert": "desserts", "desserts": "desserts", "حلوى": "desserts", "حلويات": "desserts",
    "breakfast": "breakfast", "فطار": "breakfast",
}


def _item_payload(item, repo, include_options: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "item_id": item.id,
        "name_en": item.name_en,
        "name_ar_eg": item.name_ar_eg,
        "category": item.category,
        "price_egp": item.price,
        "available": item.available,
        "prep_time_min": item.prep_time_min,
        "ingredients": item.ingredients,
        "allergens": item.allergens,
    }
    if not item.available:
        payload["unavailable_reason"] = repo.unavailable_reason(item.id)
        payload["alternatives"] = [
            {"item_id": a, "name_en": repo.items_by_id[a].name_en, "price_egp": repo.items_by_id[a].price}
            for a in repo.unavailable_alternatives(item)
        ]
    if include_options:
        payload["customization_groups"] = [
            {
                "group_id": g.id,
                "name_en": g.name_en,
                "selection": g.selection,
                "required": g.required,
                "options": [
                    {"option_id": o.id, "name_en": o.name_en, "name_ar_eg": o.name_ar_eg, "price_egp": o.price}
                    for o in g.options
                ],
            }
            for g in repo.groups_for_item(item)
        ]
    return payload


def _order_payload(order: Order) -> dict[str, Any]:
    return order.to_public_dict()


def _fail(error: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "error": error}
    out.update(extra)
    return out


def _default_weekday(reg, pinned: str | None = None) -> str | None:
    """Business weekday for promotion rules.

    Resolution order: the weekday the order was already priced under, then the
    weekday the session was configured with, and only then the wall clock.

    Preferring the order's own weekday is what stops a recalculated total from
    disagreeing with the total the customer was already quoted. The wall-clock
    fallback keeps ad-hoc use sane; research runs must pin the weekday so that
    a total cannot change with the calendar.
    """
    if pinned:
        return str(pinned).lower()
    configured = getattr(reg, "business_weekday", None)
    if configured and str(configured).lower() != "auto":
        return str(configured).lower()
    import datetime as _dt

    return _dt.datetime.now().strftime("%A").lower()


# --- menu tools -------------------------------------------------------------

def search_menu(
    reg,
    query: str = "",
    category: str | None = None,
    only_available: bool = False,
    max_results: int = 10,
) -> dict[str, Any]:
    items = reg.repo.search_menu(
        query=query or "",
        category=category,
        only_available=bool(only_available),
        max_results=max(1, min(int(max_results or 10), 20)),
    )
    if not items:
        return _fail(
            "NO_MENU_MATCH",
            query=query,
            detail="no menu item matched that request",
            suggestion="ask the customer to name a dish more specifically, or offer the category list",
            available_categories=reg.repo.categories(),
        )
    return {
        "ok": True,
        "query": query,
        "match_count": len(items),
        "items": [
            {
                "item_id": i.id,
                "name_en": i.name_en,
                "name_ar_eg": i.name_ar_eg,
                "category": i.category,
                "price_egp": i.price,
                "available": i.available,
            }
            for i in items
        ],
    }


def get_menu_item(reg, item: str) -> dict[str, Any]:
    found = reg.repo.try_get_item(item)
    if found is None:
        return _fail(
            "UNKNOWN_ITEM",
            reference=item,
            suggestions=reg.repo.suggest_items(item),
            hint="use search_menu to find the right item",
        )
    return {"ok": True, **_item_payload(found, reg.repo)}


def get_price(
    reg, item: str, quantity: int = 1, options: list[str] | None = None
) -> dict[str, Any]:
    found = reg.repo.try_get_item(item)
    if found is None:
        return _fail("UNKNOWN_ITEM", reference=item, suggestions=reg.repo.suggest_items(item))
    qty = max(1, int(quantity or 1))
    groups = reg.repo.groups_for_item(found)
    priced: list[dict[str, Any]] = []
    options_total = 0
    for ref in options or []:
        match = reg.repo.find_option(str(ref), groups)
        if match is None:
            return _fail(
                "UNKNOWN_OPTION",
                reference=ref,
                item_id=found.id,
                valid_options=[
                    {"group_id": g.id, "option_id": o.id, "name_en": o.name_en, "price_egp": o.price}
                    for g in groups
                    for o in g.options
                ],
            )
        group, option = match
        priced.append(
            {
                "group_id": group.id,
                "option_id": option.id,
                "name_en": option.name_en,
                "name_ar_eg": option.name_ar_eg,
                "price_egp": option.price,
            }
        )
        options_total += option.price

    unit = found.price + options_total
    return {
        "ok": True,
        "item_id": found.id,
        "name_en": found.name_en,
        "name_ar_eg": found.name_ar_eg,
        "base_price_egp": found.price,
        "options": priced,
        "options_total_egp": options_total,
        "unit_price_egp": unit,
        "quantity": qty,
        "line_total_egp": unit * qty,
        "currency": "EGP",
        "available": found.available,
    }


def check_availability(reg, item: str) -> dict[str, Any]:
    found = reg.repo.try_get_item(item)
    if found is None:
        return _fail("UNKNOWN_ITEM", reference=item, suggestions=reg.repo.suggest_items(item))
    payload = {
        "ok": True,
        "item_id": found.id,
        "name_en": found.name_en,
        "name_ar_eg": found.name_ar_eg,
        "available": found.available,
    }
    if not found.available:
        payload["reason"] = reg.repo.unavailable_reason(found.id)
        payload["alternatives"] = [
            {
                "item_id": a,
                "name_en": reg.repo.items_by_id[a].name_en,
                "name_ar_eg": reg.repo.items_by_id[a].name_ar_eg,
                "price_egp": reg.repo.items_by_id[a].price,
            }
            for a in reg.repo.unavailable_alternatives(found)
        ]
    else:
        payload["prep_time_min"] = found.prep_time_min
    return payload


# --- delivery / hours / promotions ------------------------------------------

def get_delivery_info(reg, zone: str | None = None, order_total: int | None = None) -> dict[str, Any]:
    policy = reg.repo.delivery
    zone_list = [
        {
            "zone_id": z.id,
            "name_en": z.name_en,
            "name_ar_eg": z.name_ar_eg,
            "delivery_fee_egp": z.fee,
            "min_order_egp": z.min_order,
            "eta_minutes": f"{z.eta_min_minutes}-{z.eta_max_minutes}",
        }
        for z in reg.repo.zones()
    ]
    base = {
        "currency": "EGP",
        "policy": policy.policy,
        "free_delivery_threshold_egp": policy.free_delivery_threshold,
        "free_delivery_note": policy.free_delivery_note,
        "pickup_address": policy.pickup_address,
        "pickup_ready_minutes": policy.pickup_ready_minutes,
        "all_zones": zone_list,
    }

    if not zone or not str(zone).strip():
        return {"ok": True, "zone": None, "resolved": False, **base}

    resolved = reg.repo.zone(str(zone))
    if resolved is None:
        return _fail(
            "UNKNOWN_ZONE",
            reference=zone,
            detail="that area is not in the delivery list; delivery is unavailable there and the order must be pickup",
            **base,
        )

    fee = resolved.fee
    free_applies = order_total is not None and int(order_total) >= policy.free_delivery_threshold
    if free_applies:
        fee = 0
    return {
        "ok": True,
        "zone": {
            "zone_id": resolved.id,
            "name_en": resolved.name_en,
            "name_ar_eg": resolved.name_ar_eg,
            "delivery_fee_egp": fee,
            "standard_delivery_fee_egp": resolved.fee,
            "free_delivery_applies": bool(free_applies),
            "min_order_egp": resolved.min_order,
            "eta_minutes": f"{resolved.eta_min_minutes}-{resolved.eta_max_minutes}",
        },
        "resolved": True,
        **base,
    }


def get_business_hours(reg, day: str | None = None) -> dict[str, Any]:
    hours = reg.repo.business_hours
    if day and str(day).strip():
        found = reg.repo.hours_for_day(str(day))
        if found is None:
            return _fail(
                "UNKNOWN_DAY",
                reference=day,
                detail="unknown weekday name",
                known_days=list(hours.schedule),
            )
        return {
            "ok": True,
            "timezone": hours.timezone,
            "day": found,
            "closed_dates": hours.closed_dates,
        }
    return {
        "ok": True,
        "timezone": hours.timezone,
        "weekly_schedule": {
            d: {"open": v.open, "close": v.close, "last_order": v.last_order}
            for d, v in hours.schedule.items()
        },
        "closed_dates": hours.closed_dates,
    }


def get_promotions(
    reg, zone: str | None = None, order_total: int | None = None, weekday: str | None = None
) -> dict[str, Any]:
    zone_id = None
    if zone:
        z = reg.repo.zone(str(zone))
        zone_id = z.id if z else None
    day = (weekday or _default_weekday(reg) or "").lower()

    out = []
    for promo in reg.repo.active_promotions():
        day_ok = (not promo.days) or (day in [d.lower() for d in promo.days])
        min_ok = order_total is None or int(order_total) >= promo.min_subtotal
        zone_ok = (not promo.eligible_zone_ids) or (zone_id in promo.eligible_zone_ids)
        applies = bool(day_ok and min_ok and zone_ok)
        if promo.type == "free_delivery":
            applies = bool(
                applies
                and order_total is not None
                and int(order_total) >= reg.repo.delivery.free_delivery_threshold
            )
        out.append(
            {
                "promotion_id": promo.id,
                "name_en": promo.name_en,
                "name_ar_eg": promo.name_ar_eg,
                "type": promo.type,
                "value": promo.value,
                "min_subtotal_egp": promo.min_subtotal,
                "days": promo.days,
                "eligible_zone_ids": promo.eligible_zone_ids,
                "requires_mention": promo.requires_mention,
                "applies_to_current_context": applies,
                "not_applicable_reason": None
                if applies
                else _promo_reason(promo, day_ok, min_ok, zone_ok),
            }
        )
    return {
        "ok": True,
        "evaluated_weekday": day or None,
        "evaluated_zone": zone_id,
        "evaluated_order_total": order_total,
        "stackable": reg.repo.promotions.stackable,
        "promotions": out,
    }


def _promo_reason(promo, day_ok: bool, min_ok: bool, zone_ok: bool) -> str:
    if not min_ok:
        return f"requires a subtotal of at least {promo.min_subtotal} EGP"
    if not day_ok:
        return f"only on {', '.join(promo.days)}"
    if not zone_ok:
        return f"only in zones {', '.join(promo.eligible_zone_ids)}"
    return "conditions not met"


# --- order tools ------------------------------------------------------------

def create_order(
    reg,
    lines: list[dict[str, Any]],
    order_mode: str = "delivery",
    zone: str | None = None,
    special_instructions: str | None = None,
    weekday: str | None = None,
    student_id: str | None = None,
) -> dict[str, Any]:
    if not reg.repo.rules.delivery_requires_zone and order_mode == "pickup":
        zone = None
    if not isinstance(lines, list):
        return _fail(
            "BAD_ARGUMENT",
            detail="'lines' must be a list of line objects",
            received_type=type(lines).__name__,
        )
    return reg.orders.create_order(
        lines=lines or [],
        order_mode=order_mode,
        zone_id=zone,
        special_instructions=special_instructions,
        weekday=(weekday or _default_weekday(reg)),
        student_id=student_id,
    )


def modify_order(
    reg,
    order_id: str | None = None,
    add: list[dict[str, Any]] | None = None,
    set_quantities: list[dict[str, Any]] | None = None,
    remove: list[dict[str, Any]] | None = None,
    set_options: list[dict[str, Any]] | None = None,
    special_instructions: str | None = None,
    weekday: str | None = None,
) -> dict[str, Any]:
    return reg.orders.modify_order(
        order_id=order_id,
        add=add,
        set_quantities=set_quantities,
        remove=remove,
        set_options=set_options,
        special_instructions=special_instructions,
        # Left as None when the caller did not name a weekday, so the order
        # keeps the weekday it was already priced under. Eagerly substituting
        # the session default here would silently re-price the order.
        weekday=weekday,
    )


def cancel_order(reg, order_id: str | None = None, reason: str | None = None) -> dict[str, Any]:
    return reg.orders.cancel_order(order_id=order_id, reason=reason)


def confirm_order(reg, order_id: str | None = None) -> dict[str, Any]:
    return reg.orders.confirm_order(order_id=order_id)


def get_current_order(reg, order_id: str | None = None) -> dict[str, Any]:
    order = reg.orders.get(order_id or "")
    if order is None:
        return {
            "ok": True,
            "order": None,
            "detail": "the customer has no order in this session yet",
        }
    return {"ok": True, "order": _order_payload(order), "summary": _summarize(order)}


def calculate_order_total(reg, order_id: str | None = None) -> dict[str, Any]:
    order = reg.orders.get(order_id or "")
    if order is None:
        return _fail("NO_CURRENT_ORDER", detail="there is no order to total")
    reg.orders._refresh_order(order, _default_weekday(reg, order.priced_weekday))
    return {
        "ok": True,
        "order_id": order.order_id,
        "currency": "EGP",
        "breakdown": {
            "subtotal_egp": order.subtotal,
            "discount_total_egp": order.discount_total,
            "delivery_fee_egp": order.delivery_fee,
            "total_egp": order.total,
        },
        "discounts": [d.model_dump(mode="json") for d in order.discounts],
        "lines": [
            {
                "line_id": l.line_id,
                "item_id": l.item_id,
                "name_en": l.item_name_en,
                "quantity": l.quantity,
                "unit_price_egp": l.unit_price,
                "line_total_egp": l.line_total,
            }
            for l in order.lines
        ],
    }


def _summarize(order: Order) -> str:
    parts = [
        f"{l.quantity}x {l.item_name_en} ({l.line_total} EGP)"
        for l in order.lines
    ]
    return f"Order {order.order_id}: " + ", ".join(parts) + f". Total {order.total} EGP."


# --- context resolution -----------------------------------------------------

def resolve_order_reference(reg, reference: str, order_id: str | None = None) -> dict[str, Any]:
    """Deterministically map a conversational reference onto concrete lines.

    This is the measurable backbone of the context-resolution diagnostic. It is
    intentionally conservative: when a reference matches more than one line it
    reports ``ambiguous`` instead of guessing, so the agent learns to ask.
    """
    order = reg.orders.get(order_id or "")
    if order is None:
        return _fail("NO_CURRENT_ORDER", detail="there is no order to refer to")

    ref = normalize_text(reference)
    lines = order.lines
    if not ref:
        return _fail("BAD_ARGUMENT", reference=reference, detail="empty reference")

    if ref in _ALL_WORDS:
        return {
            "ok": True,
            "resolved": True,
            "reference": reference,
            "line_ids": [l.line_id for l in lines],
            "interpretation": "all lines in the current order",
            "total_quantity": sum(l.quantity for l in lines),
        }

    # 1. explicit line id
    compact = ref.replace(" ", "")
    if compact in _ORDINALS:
        idx = _ORDINALS[compact]
        if idx < len(lines):
            return {
                "ok": True,
                "resolved": True,
                "reference": reference,
                "line_ids": [lines[idx].line_id],
                "interpretation": f"line {lines[idx].line_id}",
                "total_quantity": lines[idx].quantity,
            }

    # 2. a positional word: "the second one", "التاني", "the last"
    tokens = [t for t in ref.split() if t]
    for token in tokens:
        if token in _POSITIONAL:
            wanted = _POSITIONAL[token]
            idx = len(lines) - 1 if wanted is None else wanted
            if 0 <= idx < len(lines):
                hit = lines[idx]
                return {
                    "ok": True,
                    "resolved": True,
                    "reference": reference,
                    "line_ids": [hit.line_id],
                    "item_id": hit.item_id,
                    "interpretation": f"line {hit.line_id} ({hit.item_name_en})",
                    "matched_position": token,
                    "quantity": 1,
                    "total_quantity": hit.quantity,
                }

    # 3. a count plus a category noun: "one of the burgers"
    count = _extract_count(ref)
    wanted_cat = _extract_category(ref)

    candidates: list = []
    if wanted_cat:
        candidates = [l for l in lines if l.category == wanted_cat]
    else:
        item_hits = []
        for line in lines:
            item = reg.repo.items_by_id.get(line.item_id)
            keys = {normalize_text(line.item_name_en), normalize_text(line.item_name_ar_eg)}
            if item:
                keys |= {normalize_text(k) for k in [item.name_en, item.name_ar_eg, *item.aliases]}
            if any(k and (k in ref or ref in k) for k in keys):
                item_hits.append(line)
        candidates = item_hits

    if not candidates:
        return {
            "ok": True,
            "resolved": False,
            "reference": reference,
            "line_ids": [],
            "reason": "no order line matches that reference",
            "order_lines": [_line_view(l) for l in lines],
            "recommended_action": "ask the customer which item they mean",
        }

    if len(candidates) == 1:
        only = candidates[0]
        qty = count if count else 1
        qty = min(qty, only.quantity)
        return {
            "ok": True,
            "resolved": True,
            "reference": reference,
            "line_ids": [only.line_id],
            "item_id": only.item_id,
            "interpretation": f"line {only.line_id} ({only.item_name_en})",
            "quantity": qty,
            "total_quantity": only.quantity,
        }

    # multiple candidates: apply a count if it disambiguates
    if count is not None and count <= sum(l.quantity for l in candidates):
        # "two of the burgers" -> the first candidate that has >= count
        for line in candidates:
            if line.quantity >= count:
                return {
                    "ok": True,
                    "resolved": True,
                    "reference": reference,
                    "line_ids": [line.line_id],
                    "item_id": line.item_id,
                    "interpretation": f"line {line.line_id} ({line.item_name_en})",
                    "quantity": count,
                    "total_quantity": line.quantity,
                    "note": "resolved by quantity against the first matching line",
                }
        total_qty = sum(l.quantity for l in candidates)
        return {
            "ok": True,
            "resolved": True,
            "reference": reference,
            "line_ids": [l.line_id for l in candidates],
            "interpretation": f"all {len(candidates)} matching lines, total quantity {total_qty}",
            "quantity": count,
            "total_quantity": total_qty,
        }

    return {
        "ok": True,
        "resolved": False,
        "ambiguous": True,
        "reference": reference,
        "line_ids": [l.line_id for l in candidates],
        "reason": "the reference matches more than one order line",
        "candidates": [_line_view(l) for l in candidates],
        "recommended_action": "ask a short clarifying question before modifying the order",
    }


def _line_view(line) -> dict[str, Any]:
    return {
        "line_id": line.line_id,
        "item_id": line.item_id,
        "name_en": line.item_name_en,
        "name_ar_eg": line.item_name_ar_eg,
        "category": line.category,
        "quantity": line.quantity,
        "line_total_egp": line.line_total,
        "options": [o.option_id for o in line.options],
    }


def _extract_count(ref: str) -> int | None:
    # Arabic tokens have to be captured too, otherwise the Egyptian Arabic
    # numerals in _NUM_WORDS are never reached.
    tokens = re.findall(r"\d+|[^\W\d_]+", ref, flags=re.UNICODE)
    for tok in tokens:
        if tok.isdigit():
            val = int(tok)
            if val > 0:
                return val
        elif tok in _NUM_WORDS:
            return _NUM_WORDS[tok]
    return None


def _extract_category(ref: str) -> str | None:
    for token in ref.split():
        if token in _CATEGORY_HINTS:
            return _CATEGORY_HINTS[token]
        # Egyptian Arabic marks definiteness with a leading "ال", so "البرجر"
        # and "برجر" must resolve to the same category. Stripping the article
        # here covers every noun at once instead of listing prefixed variants.
        if token.startswith("ال") and len(token) > 2:
            stripped = token[2:]
            if stripped in _CATEGORY_HINTS:
                return _CATEGORY_HINTS[stripped]
    return None
