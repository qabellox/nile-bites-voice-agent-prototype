"""Typed schema for the fictional 'Nile Bites' restaurant dataset.

Everything the business tools can return is defined here, so that tool output is
structurally guaranteed rather than free-form. Prices are kept as integers in
EGP so that all total arithmetic is exact and reproducible.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Currency = Literal["EGP"]
OrderMode = Literal["delivery", "pickup"]
OrderStatus = Literal["draft", "confirmed", "modified", "cancelled", "completed"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class DatasetMeta(Strict):
    dataset_name: str
    dataset_id: str
    schema_version: str
    data_version: str
    IS_FICTIONAL: bool
    DISCLAIMER: str
    currency: Currency
    language_of_record: str
    authored_for: str
    notes: list[str] = Field(default_factory=list)


class Category(Strict):
    id: str
    name_en: str
    name_ar_eg: str
    sort_order: int = 0


class CustomizationOption(Strict):
    id: str
    name_en: str
    name_ar_eg: str
    price: int = 0


class CustomizationGroup(Strict):
    id: str
    name_en: str
    name_ar_eg: str
    selection: Literal["single", "multi"]
    max_selections: int = 1
    applies_to_categories: list[str] = Field(default_factory=list)
    required: bool = False
    options: list[CustomizationOption]


class MenuItem(Strict):
    id: str
    category: str
    name_en: str
    name_ar_eg: str
    price: int
    available: bool = True
    prep_time_min: int = 0
    ingredients: list[str] = Field(default_factory=list)
    allergens: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    customization_groups: list[str] = Field(default_factory=list)


class DeliveryZone(Strict):
    id: str
    name_en: str
    name_ar_eg: str
    fee: int
    min_order: int
    eta_min_minutes: int
    eta_max_minutes: int
    aliases: list[str] = Field(default_factory=list)


class DeliveryPolicy(Strict):
    policy: str
    order_modes: list[OrderMode]
    min_order_general: int
    free_delivery_threshold: int
    free_delivery_note: str
    pickup_address: str
    pickup_ready_minutes: int
    max_cash_on_delivery: int
    out_of_zone_behavior: str
    zones: list[DeliveryZone]


class DayHours(Strict):
    open: str
    close: str
    last_order: str


class BusinessHours(Strict):
    timezone: str
    note: str
    schedule: dict[str, DayHours]
    closed_dates: list[str] = Field(default_factory=list)
    closed_dates_note: str = ""


class Promotion(Strict):
    id: str
    name_en: str
    name_ar_eg: str
    type: Literal[
        "percent_off_item",
        "percent_off_order",
        "amount_off_order",
        "free_delivery",
    ]
    value: int
    eligible_item_ids: list[str] = Field(default_factory=list)
    eligible_zone_ids: list[str] = Field(default_factory=list)
    min_subtotal: int = 0
    min_qty_of_item: int = 0
    days: list[str] = Field(default_factory=list)
    active: bool = True
    requires_mention: str | None = None


class PromotionBook(Strict):
    note: str
    stackable: bool
    promotions: list[Promotion]


class OrderRules(Strict):
    max_lines_per_order: int
    max_quantity_per_line: int
    max_orders_per_session: int
    line_merging: str
    delivery_requires_zone: bool
    delivery_min_order_checked_against: str
    closed_hours_blocks_new_orders: bool


class RestaurantData(Strict):
    meta: DatasetMeta
    categories: list[Category]
    customization_groups: list[CustomizationGroup]
    menu: list[MenuItem]
    unavailable_reasons: dict[str, str] = Field(default_factory=dict)
    delivery: DeliveryPolicy
    business_hours: BusinessHours
    promotions: PromotionBook
    order_rules: OrderRules


# --------------------------------------------------------------------------
# Order state (Phase 4)
# --------------------------------------------------------------------------


class SelectedOption(Strict):
    group_id: str
    option_id: str
    name_en: str
    name_ar_eg: str
    price: int


class OrderLine(Strict):
    line_id: str
    item_id: str
    item_name_en: str
    item_name_ar_eg: str
    category: str
    unit_price: int
    quantity: int
    options: list[SelectedOption] = Field(default_factory=list)
    line_total: int
    special_instructions: str | None = None

    @property
    def options_total(self) -> int:
        return sum(o.price for o in self.options)


class AppliedDiscount(Strict):
    promotion_id: str
    name_en: str
    amount: int
    reason: str


class OrderHistoryEvent(Strict):
    event: str
    detail: str = ""
    sequence: int = 0


class Order(Strict):
    order_id: str
    session_id: str
    status: OrderStatus = "draft"
    lines: list[OrderLine] = Field(default_factory=list)
    discounts: list[AppliedDiscount] = Field(default_factory=list)
    order_mode: OrderMode = "delivery"
    delivery_zone_id: str | None = None
    special_instructions: str | None = None
    # The weekday this order is priced under. Promotions and the free-delivery
    # waiver depend on it, so it is pinned when the order is created and reused
    # for every later recalculation. Without this, re-totalling an order on a
    # different calendar day silently changes the number the customer was
    # already quoted.
    priced_weekday: str | None = None
    # Evidence the customer supplied for a promotion that requires the agent to
    # ask first. Stored on the order so the promotion decision is reproducible
    # and does not depend on dialogue the log no longer holds.
    student_id: str | None = None
    subtotal: int = 0
    discount_total: int = 0
    delivery_fee: int = 0
    total: int = 0
    created_at: str = ""
    updated_at: str = ""
    history: list[OrderHistoryEvent] = Field(default_factory=list)

    def rebuild_totals(self, delivery_fee: int) -> None:
        """Recompute every derived money field from lines + discounts.

        Deterministic by construction: subtotal is the sum of line totals, and
        the total is subtotal - discount_total + delivery_fee.
        """
        self.subtotal = sum(line.line_total for line in self.lines)
        self.discount_total = sum(d.amount for d in self.discounts)
        self.delivery_fee = delivery_fee
        self.total = self.subtotal - self.discount_total + delivery_fee

    def to_public_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
