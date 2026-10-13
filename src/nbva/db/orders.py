"""Order creation, modification, and retrieval. Fully deterministic.

All money arithmetic happens here in integer EGP. No language model ever
computes a total, and no order state is ever inferred from free text by the
model: the model may only call these functions with structured arguments.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from .repository import RestaurantRepository, normalize_text
from .schema import (
    AppliedDiscount,
    Order,
    OrderHistoryEvent,
    OrderLine,
    SelectedOption,
)

# Tools never raise for business-level problems; they return structured errors
# so the model can read them and the log can record them.
ERR_UNKNOWN_ITEM = "UNKNOWN_ITEM"
ERR_UNKNOWN_OPTION = "UNKNOWN_OPTION"
ERR_UNAVAILABLE = "ITEM_UNAVAILABLE"
ERR_MAX_LINES = "MAX_LINES_EXCEEDED"
ERR_MAX_QTY = "MAX_QUANTITY_EXCEEDED"
ERR_NO_ORDER = "NO_CURRENT_ORDER"
ERR_UNKNOWN_LINE = "UNKNOWN_LINE"
ERR_UNKNOWN_ZONE = "UNKNOWN_ZONE"
ERR_MIN_ORDER = "BELOW_MIN_ORDER"
ERR_MAX_ORDERS = "MAX_ORDERS_EXCEEDED"
ERR_BAD_ARGUMENT = "BAD_ARGUMENT"
ERR_DUPLICATE_OPTION = "DUPLICATE_OPTION"
ERR_ORDER_LOCKED = "ORDER_ALREADY_CONFIRMED"


class OrderStore:
    """Owns every order created during a session."""

    def __init__(self, repository: RestaurantRepository, session_id: str):
        self.repo = repository
        self.session_id = session_id
        self._orders: dict[str, Order] = {}
        self._sequence: int = 0
        self._current_order_id: str | None = None
        self._history_sequence: int = 0

    # -- helpers ---------------------------------------------------------
    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def _next_history(self) -> int:
        self._history_sequence += 1
        return self._history_sequence

    def _log(self, order: Order, event: str, detail: str = "") -> None:
        order.history.append(
            OrderHistoryEvent(event=event, detail=detail, sequence=self._next_history())
        )
        order.updated_at = self._now()

    def _next_order_id(self) -> str:
        self._sequence += 1
        return f"NB-ORD-{self._sequence:04d}"

    def _next_line_id(self, order: Order) -> str:
        used = {line.line_id for line in order.lines}
        n = 1
        while f"L{n}" in used:
            n += 1
        return f"L{n}"

    def current_order(self) -> Order | None:
        if self._current_order_id is None:
            return None
        return self._orders.get(self._current_order_id)

    def all_orders(self) -> list[Order]:
        return [self._orders[k] for k in sorted(self._orders)]

    def get(self, order_id: str) -> Order | None:
        if not order_id:
            return self.current_order()
        return self._orders.get(order_id.strip())

    # -- options ---------------------------------------------------------
    def _resolve_options(
        self, item_id: str, option_refs: Iterable[str]
    ) -> tuple[list[SelectedOption] | None, dict[str, Any] | None]:
        item = self.repo.items_by_id[item_id]
        groups = self.repo.groups_for_item(item)
        resolved: list[SelectedOption] = []
        seen: set[tuple[str, str]] = set()
        for ref in option_refs or []:
            if not str(ref).strip():
                continue
            found = self.repo.find_option(str(ref), groups)
            if found is None:
                return None, {
                    "ok": False,
                    "error": ERR_UNKNOWN_OPTION,
                    "reference": str(ref),
                    "valid_options_for_item": [
                        {
                            "group_id": g.id,
                            "option_id": o.id,
                            "name_en": o.name_en,
                            "name_ar_eg": o.name_ar_eg,
                            "price": o.price,
                        }
                        for g in groups
                        for o in g.options
                    ],
                }
            group, option = found
            key = (group.id, option.id)
            if key in seen:
                continue
            seen.add(key)
            resolved.append(
                SelectedOption(
                    group_id=group.id,
                    option_id=option.id,
                    name_en=option.name_en,
                    name_ar_eg=option.name_ar_eg,
                    price=option.price,
                )
            )
        return resolved, None

    def _validate_single_select(self, item_id: str, options: list[SelectedOption]) -> dict[str, Any] | None:
        groups = {g.id: g for g in self.repo.groups_for_item(self.repo.items_by_id[item_id])}
        counts: dict[str, int] = {}
        for opt in options:
            counts[opt.group_id] = counts.get(opt.group_id, 0) + 1
        for gid, count in counts.items():
            group = groups.get(gid)
            if group and group.selection == "single" and count > 1:
                return {
                    "ok": False,
                    "error": ERR_BAD_ARGUMENT,
                    "detail": f"group {gid} accepts only one option but {count} were given",
                }
        return None

    # -- pricing ---------------------------------------------------------
    def _compute_delivery_fee(self, order: Order) -> int:
        zone = None
        if order.delivery_zone_id:
            zone = self.repo.zone(order.delivery_zone_id)
        if order.order_mode != "delivery" or zone is None:
            return 0
        if self._has_free_delivery_promotion(order):
            return 0
        subtotal_after_discount = order.subtotal - order.discount_total
        if subtotal_after_discount >= self.repo.delivery.free_delivery_threshold:
            return 0
        return zone.fee

    def _has_free_delivery_promotion(self, order: Order) -> bool:
        return any(d.reason == "free_delivery" for d in order.discounts)

    def _promotion_applies(self, promo, order: Order, weekday: str | None) -> bool:
        """Day, money, zone and evidence eligibility, in the dataset's order."""
        if not promo.active:
            return False
        if promo.days and weekday and weekday.lower() not in [d.lower() for d in promo.days]:
            return False
        if order.subtotal < promo.min_subtotal:
            return False
        if promo.eligible_zone_ids:
            if order.order_mode != "delivery" or order.delivery_zone_id not in promo.eligible_zone_ids:
                return False
        if promo.requires_mention and not self._requirement_met(promo.requires_mention, order):
            return False
        return True

    @staticmethod
    def _requirement_met(requirement: str, order: Order) -> bool:
        """Whether the order carries the evidence a gated promotion needs.

        A requirement with no implementation fails closed, so a promotion can
        never be granted because nobody wrote the check for it.
        """
        key = requirement.strip().lower()
        if key == "student id":
            return bool((order.student_id or "").strip())
        return False

    def _apply_promotions(self, order: Order, weekday: str | None) -> list[AppliedDiscount]:
        """Deterministic promotion selection: at most one, dataset order wins."""
        applied: list[AppliedDiscount] = []
        promo_book = self.repo.promotions
        if promo_book.stackable:
            return applied
        for promo in self.repo.active_promotions():
            if not self._promotion_applies(promo, order, weekday):
                continue
            amount = self._promotion_amount(promo, order)
            if promo.type == "free_delivery":
                # Real benefit worth zero money, so it is recorded for
                # provenance and takes the single non-stackable slot.
                applied.append(
                    AppliedDiscount(
                        promotion_id=promo.id,
                        name_en=promo.name_en,
                        amount=0,
                        reason=promo.type,
                    )
                )
                break
            if amount > 0:
                applied.append(
                    AppliedDiscount(
                        promotion_id=promo.id,
                        name_en=promo.name_en,
                        amount=amount,
                        reason=promo.type,
                    )
                )
                break
        return applied

    def _promotion_amount(self, promo, order: Order) -> int:
        if promo.type == "free_delivery":
            return 0
        if promo.type == "amount_off_order":
            if promo.min_qty_of_item:
                qty = sum(
                    line.quantity for line in order.lines if line.item_id in promo.eligible_item_ids
                )
                if qty < promo.min_qty_of_item:
                    return 0
            return min(promo.value, order.subtotal)
        if promo.type == "percent_off_order":
            if promo.eligible_item_ids:
                base = sum(
                    line.line_total for line in order.lines if line.item_id in promo.eligible_item_ids
                )
            else:
                base = order.subtotal
            return (base * promo.value) // 100
        if promo.type == "percent_off_item":
            base = sum(
                line.line_total for line in order.lines if line.item_id in promo.eligible_item_ids
            )
            if base <= 0:
                return 0
            return (base * promo.value) // 100
        return 0

    def _refresh_order(self, order: Order, weekday: str | None = None) -> None:
        # Promotion eligibility reads order.subtotal and the delivery-fee
        # waiver reads order.subtotal - order.discount_total, so both derived
        # fields must be current before either rule is evaluated. Computing
        # them here makes the ordering explicit instead of depending on a
        # previous rebuild_totals() call; without it a freshly built order has
        # subtotal 0 and no promotion can ever match.
        order.subtotal = sum(line.line_total for line in order.lines)
        order.discounts = self._apply_promotions(order, self._resolve_weekday(order, weekday))
        order.discount_total = sum(d.amount for d in order.discounts)
        fee = self._compute_delivery_fee(order)
        order.rebuild_totals(fee)

    @staticmethod
    def _resolve_weekday(order: Order, weekday: str | None) -> str | None:
        """The weekday an order is priced under.

        An explicit weekday wins and is remembered. Otherwise the order keeps
        whatever it was originally priced under, so recalculating a total later
        in the session cannot change the number the customer was already told.
        """
        if weekday:
            order.priced_weekday = weekday.lower()
        return order.priced_weekday or (weekday.lower() if weekday else None)

    # -- public operations ------------------------------------------------
    def create_order(
        self,
        lines: list[dict[str, Any]],
        order_mode: str = "delivery",
        zone_id: str | None = None,
        special_instructions: str | None = None,
        weekday: str | None = None,
        student_id: str | None = None,
    ) -> dict[str, Any]:
        if len(self._orders) >= self.repo.rules.max_orders_per_session:
            return {
                "ok": False,
                "error": ERR_MAX_ORDERS,
                "detail": f"session limit of {self.repo.rules.max_orders_per_session} orders reached",
            }
        mode = (order_mode or "delivery").strip().lower()
        if mode not in ("delivery", "pickup"):
            return {"ok": False, "error": ERR_BAD_ARGUMENT, "detail": f"unknown order_mode {order_mode!r}"}

        resolved_zone = None
        if mode == "delivery":
            if not zone_id:
                return {
                    "ok": False,
                    "error": ERR_UNKNOWN_ZONE,
                    "detail": "delivery requires a delivery zone; ask the customer for the area",
                    "known_zones": [
                        {"zone_id": z.id, "name_en": z.name_en, "name_ar_eg": z.name_ar_eg}
                        for z in self.repo.zones()
                    ],
                }
            resolved_zone = self.repo.zone(zone_id)
            if resolved_zone is None:
                return {
                    "ok": False,
                    "error": ERR_UNKNOWN_ZONE,
                    "reference": zone_id,
                    "known_zones": [
                        {"zone_id": z.id, "name_en": z.name_en, "name_ar_eg": z.name_ar_eg}
                        for z in self.repo.zones()
                    ],
                }

        order = Order(
            order_id=self._next_order_id(),
            session_id=self.session_id,
            status="draft",
            order_mode=mode,  # type: ignore[arg-type]
            delivery_zone_id=resolved_zone.id if resolved_zone else None,
            special_instructions=special_instructions,
            student_id=(student_id.strip() or None) if isinstance(student_id, str) else None,
            created_at=self._now(),
            updated_at=self._now(),
        )

        for spec in lines or []:
            outcome = self._add_line_to(order, spec, merging_enabled=True)
            if not outcome.get("ok"):
                return {
                    "ok": False,
                    "error": outcome.get("error", ERR_BAD_ARGUMENT),
                    "detail": outcome.get("detail", ""),
                    "reference": outcome.get("reference"),
                    "order_discarded": order.order_id,
                }

        if not order.lines:
            return {"ok": False, "error": ERR_BAD_ARGUMENT, "detail": "order has no lines"}

        if len(order.lines) > self.repo.rules.max_lines_per_order:
            return {
                "ok": False,
                "error": ERR_MAX_LINES,
                "detail": f"max {self.repo.rules.max_lines_per_order} lines per order",
            }

        self._refresh_order(order, weekday)

        if mode == "delivery" and resolved_zone is not None:
            after_discount = order.subtotal - order.discount_total
            if after_discount < resolved_zone.min_order:
                return {
                    "ok": False,
                    "error": ERR_MIN_ORDER,
                    "detail": (
                        f"zone {resolved_zone.id} minimum order is {resolved_zone.min_order} EGP; "
                        f"current total after discounts is {after_discount} EGP"
                    ),
                    "short_by": resolved_zone.min_order - after_discount,
                    "zone_id": resolved_zone.id,
                    "zone_min_order": resolved_zone.min_order,
                    "current_total": after_discount,
                }

        self._orders[order.order_id] = order
        self._current_order_id = order.order_id
        self._log(order, "order_created", f"{len(order.lines)} line(s)")
        return {"ok": True, "order": order.to_public_dict()}

    def _add_line_to(self, order: Order, spec: dict[str, Any], merging_enabled: bool) -> dict[str, Any]:
        item_ref = str(spec.get("item") or spec.get("item_id") or "").strip()
        if not item_ref:
            return {"ok": False, "error": ERR_BAD_ARGUMENT, "detail": "line is missing 'item'"}
        item = self.repo.try_get_item(item_ref)
        if item is None:
            return {
                "ok": False,
                "error": ERR_UNKNOWN_ITEM,
                "reference": item_ref,
                "suggestions": self.repo.suggest_items(item_ref),
            }
        if not item.available:
            return {
                "ok": False,
                "error": ERR_UNAVAILABLE,
                "reference": item_ref,
                "item_id": item.id,
                "item_name": item.name_en,
                "reason": self.repo.unavailable_reason(item.id),
                "alternatives": self.repo.unavailable_alternatives(item),
            }

        qty_raw = spec.get("quantity", 1)
        try:
            qty = int(qty_raw)
        except (TypeError, ValueError):
            return {"ok": False, "error": ERR_BAD_ARGUMENT, "detail": f"quantity {qty_raw!r} is not an integer"}
        if qty <= 0:
            return {"ok": False, "error": ERR_BAD_ARGUMENT, "detail": "quantity must be positive"}
        if qty > self.repo.rules.max_quantity_per_line:
            return {
                "ok": False,
                "error": ERR_MAX_QTY,
                "detail": f"max quantity per line is {self.repo.rules.max_quantity_per_line}",
            }

        options, err = self._resolve_options(item.id, spec.get("options") or [])
        if err is not None:
            return err
        options = options or []
        single_err = self._validate_single_select(item.id, options)
        if single_err is not None:
            return single_err

        options_total = sum(o.price for o in options)
        unit_price = item.price + options_total

        if merging_enabled:
            for line in order.lines:
                same = (
                    line.item_id == item.id
                    and sorted((o.group_id, o.option_id) for o in line.options)
                    == sorted((o.group_id, o.option_id) for o in options)
                    and normalize_text(line.special_instructions or "")
                    == normalize_text(spec.get("special_instructions") or "")
                )
                if same:
                    new_qty = line.quantity + qty
                    if new_qty > self.repo.rules.max_quantity_per_line:
                        return {"ok": False, "error": ERR_MAX_QTY, "detail": "merged quantity exceeds maximum"}
                    line.quantity = new_qty
                    line.line_total = line.unit_price * new_qty
                    return {"ok": True, "line_id": line.line_id, "merged": True}

        if len(order.lines) >= self.repo.rules.max_lines_per_order:
            return {"ok": False, "error": ERR_MAX_LINES, "detail": "max lines per order reached"}

        line = OrderLine(
            line_id=self._next_line_id(order),
            item_id=item.id,
            item_name_en=item.name_en,
            item_name_ar_eg=item.name_ar_eg,
            category=item.category,
            unit_price=unit_price,
            quantity=qty,
            options=options,
            line_total=unit_price * qty,
            special_instructions=spec.get("special_instructions") or None,
        )
        order.lines.append(line)
        return {"ok": True, "line_id": line.line_id, "merged": False}

    def modify_order(
        self,
        order_id: str | None = None,
        add: list[dict[str, Any]] | None = None,
        set_quantities: list[dict[str, Any]] | None = None,
        remove: list[dict[str, Any]] | None = None,
        set_options: list[dict[str, Any]] | None = None,
        special_instructions: str | None = None,
        weekday: str | None = None,
    ) -> dict[str, Any]:
        order = self.get(order_id or "")
        if order is None:
            return {"ok": False, "error": ERR_NO_ORDER, "detail": "there is no current order to modify"}
        if order.status == "confirmed":
            # A confirmed order has been sent to the kitchen, so editing it
            # would reprice a total the customer was already told. Place a new
            # order instead.
            return {
                "ok": False,
                "error": ERR_ORDER_LOCKED,
                "detail": (
                    f"order {order.order_id} is already confirmed and can no longer be "
                    "changed; create a new order instead"
                ),
                "order_status": order.status,
            }
        if order.status in ("cancelled", "completed"):
            return {"ok": False, "error": ERR_BAD_ARGUMENT, "detail": f"order is {order.status}"}

        changes: list[str] = []
        applied_discounts: list[dict[str, Any]] = []

        for spec in add or []:
            outcome = self._add_line_to(order, spec, merging_enabled=True)
            if not outcome.get("ok"):
                return {
                    "ok": False,
                    "error": outcome.get("error"),
                    "detail": outcome.get("detail", ""),
                    "reference": outcome.get("reference"),
                    "applied_before_failure": changes,
                }
            changes.append(
                f"added {spec.get('quantity', 1)}x {spec.get('item')} as {outcome['line_id']}"
                + (" (merged)" if outcome.get("merged") else "")
            )

        for spec in set_quantities or []:
            target = self._line_target(spec)
            if not target:
                return {
                    "ok": False,
                    "error": ERR_BAD_ARGUMENT,
                    "detail": "each set_quantities entry needs a 'line' reference",
                }
            qty_raw = spec.get("quantity", spec.get("qty"))
            try:
                qty = int(qty_raw)
            except (TypeError, ValueError):
                return {"ok": False, "error": ERR_BAD_ARGUMENT, "detail": f"quantity {qty_raw!r} is not an integer"}
            line = self._find_line(order, target)
            if line is None:
                return {
                    "ok": False,
                    "error": ERR_UNKNOWN_LINE,
                    "reference": target,
                    "current_lines": [self._line_summary(l) for l in order.lines],
                }
            if qty <= 0:
                order.lines = [l for l in order.lines if l.line_id != line.line_id]
                changes.append(f"removed {line.line_id} because quantity was set to 0")
            else:
                if qty > self.repo.rules.max_quantity_per_line:
                    return {"ok": False, "error": ERR_MAX_QTY, "detail": "quantity exceeds maximum"}
                line.quantity = qty
                line.line_total = line.unit_price * qty
                changes.append(f"set {line.line_id} quantity to {qty}")

        for spec in remove or []:
            target = self._line_target(spec)
            if not target:
                return {
                    "ok": False,
                    "error": ERR_BAD_ARGUMENT,
                    "detail": "each remove entry needs a 'line' reference",
                }
            qty_raw = spec.get("quantity", 1)
            try:
                remove_qty = int(qty_raw)
            except (TypeError, ValueError):
                return {"ok": False, "error": ERR_BAD_ARGUMENT, "detail": f"quantity {qty_raw!r} is not an integer"}
            if target in {"all", "everything", "الكل"}:
                removed = [l.line_id for l in order.lines]
                order.lines = []
                changes.append(f"removed all lines: {', '.join(removed) or 'none'}")
                continue
            line = self._find_line(order, target)
            if line is None:
                return {
                    "ok": False,
                    "error": ERR_UNKNOWN_LINE,
                    "reference": target,
                    "current_lines": [self._line_summary(l) for l in order.lines],
                }
            if remove_qty >= line.quantity:
                order.lines = [l for l in order.lines if l.line_id != line.line_id]
                changes.append(f"removed {line.line_id} ({line.item_name_en})")
            else:
                line.quantity -= remove_qty
                line.line_total = line.unit_price * line.quantity
                changes.append(f"removed {remove_qty}x from {line.line_id} (now {line.quantity})")

        for spec in set_options or []:
            target = self._line_target(spec)
            if not target:
                return {
                    "ok": False,
                    "error": ERR_BAD_ARGUMENT,
                    "detail": "each set_options entry needs a 'line' reference",
                }
            option_refs = spec.get("options") or []
            line = self._find_line(order, target)
            if line is None:
                return {
                    "ok": False,
                    "error": ERR_UNKNOWN_LINE,
                    "reference": target,
                    "current_lines": [self._line_summary(l) for l in order.lines],
                }
            resolved, err = self._resolve_options(line.item_id, option_refs)
            if err is not None:
                return err
            resolved = resolved or []
            single_err = self._validate_single_select(line.item_id, resolved)
            if single_err is not None:
                return single_err
            line.options = resolved
            line.unit_price = self.repo.items_by_id[line.item_id].price + sum(o.price for o in resolved)
            line.line_total = line.unit_price * line.quantity
            changes.append(f"updated options on {line.line_id}: {[o.option_id for o in resolved]}")

        if special_instructions is not None:
            order.special_instructions = special_instructions or None
            changes.append("updated order note")

        if not order.lines:
            return {
                "ok": False,
                "error": ERR_BAD_ARGUMENT,
                "detail": "a modification cannot leave the order empty; use cancel_order instead",
            }

        self._refresh_order(order, weekday)
        applied_discounts = [d.model_dump(mode="json") for d in order.discounts]
        self._log(order, "order_modified", "; ".join(changes) if changes else "no change")
        order.status = "modified" if order.status == "draft" else order.status

        return {
            "ok": True,
            "order": order.to_public_dict(),
            "changes": changes,
            "discounts_reapplied": applied_discounts,
        }

    def cancel_order(self, order_id: str | None = None, reason: str | None = None) -> dict[str, Any]:
        order = self.get(order_id or "")
        if order is None:
            return {"ok": False, "error": ERR_NO_ORDER, "detail": "there is no current order to cancel"}
        order.status = "cancelled"
        self._log(order, "order_cancelled", reason or "no reason given")
        self._current_order_id = None
        return {"ok": True, "order": order.to_public_dict(), "reason": reason}

    def confirm_order(self, order_id: str | None = None) -> dict[str, Any]:
        order = self.get(order_id or "")
        if order is None:
            return {"ok": False, "error": ERR_NO_ORDER, "detail": "there is no current order to confirm"}
        if not order.lines:
            return {"ok": False, "error": ERR_BAD_ARGUMENT, "detail": "cannot confirm an empty order"}
        self._refresh_order(order)
        order.status = "confirmed"
        self._log(order, "order_confirmed", "")
        return {"ok": True, "order": order.to_public_dict()}

    # -- line lookup ------------------------------------------------------
    def _line_summary(self, line: OrderLine) -> dict[str, Any]:
        return {
            "line_id": line.line_id,
            "item_id": line.item_id,
            "item_name_en": line.item_name_en,
            "item_name_ar_eg": line.item_name_ar_eg,
            "quantity": line.quantity,
            "line_total": line.line_total,
        }

    @staticmethod
    def _line_target(spec: dict[str, Any]) -> str:
        # `line` is the documented key, but models reasonably reach for the
        # other names the tool result exposes, so accept those too rather than
        # failing with an empty reference.
        return str(
            spec.get("line")
            or spec.get("line_id")
            or spec.get("item_id")
            or spec.get("item")
            or ""
        ).strip()

    def _find_line(self, order: Order, reference: str) -> OrderLine | None:
        ref = normalize_text(reference)
        if not ref:
            return None
        for line in order.lines:
            if normalize_text(line.line_id) == ref:
                return line
        # allow "L1" style with spaces
        compact = ref.replace(" ", "")
        for line in order.lines:
            if line.line_id.lower() == compact.lower():
                return line
        return None

    def snapshot(self) -> dict[str, Any]:
        order = self.current_order()
        return {
            "session_id": self.session_id,
            "current_order_id": self._current_order_id,
            "orders_created": len(self._orders),
            "order": order.to_public_dict() if order else None,
        }
