"""Deterministic read access to the fictional restaurant dataset.

The repository is the single source of business truth. Business tools must read
from here and nowhere else, which is what prevents the language model from
inventing prices, availability, or delivery facts.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .schema import (
    BusinessHours,
    CustomizationGroup,
    CustomizationOption,
    DeliveryZone,
    MenuItem,
    OrderRules,
    Promotion,
    PromotionBook,
    RestaurantData,
)

_ARABIC_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
_ARABIC_TATWEEL = re.compile(r"\u0640")
_LATIN_PUNCT = re.compile(r"[^0-9a-z\u0600-\u06FF\s]+")
_WS = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """Fold a string into a comparison key.

    Lowercases, strips Arabic diacritics and tatweel, removes tatweel, collapses
    whitespace, and drops punctuation while keeping Arabic and Latin letters and
    digits. Arabic letter-form variants (alef, ya, ta-marbuta) are folded
    separately by :func:`fold_arabic`.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = _ARABIC_DIACRITICS.sub("", text)
    text = _ARABIC_TATWEEL.sub("", text)
    text = text.lower()
    text = _LATIN_PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip()


_ARABIC_FOLD = str.maketrans(
    {
        "\u0623": "\u0627",  # alef with hamza above
        "\u0625": "\u0627",  # alef with hamza below
        "\u0622": "\u0627",  # alef madda
        "\u0671": "\u0627",  # alef wasla
        "\u0649": "\u064a",  # alef maksura -> ya
        "\u0629": "\u0647",  # ta marbuta -> ha
        "\u064a": "\u064a",
    }
)


def fold_arabic(value: str) -> str:
    return value.translate(_ARABIC_FOLD)


class ItemNotFound(LookupError):
    def __init__(self, reference: str, suggestions: list[str]):
        super().__init__(reference)
        self.reference = reference
        self.suggestions = suggestions


class RestaurantRepository:
    """Read-only, deterministic accessor over the restaurant dataset."""

    def __init__(self, data: RestaurantData):
        self.data = data
        self._items_by_id: dict[str, MenuItem] = {i.id: i for i in data.menu}
        self._groups_by_id: dict[str, CustomizationGroup] = {
            g.id: g for g in data.customization_groups
        }
        self._zones_by_id: dict[str, DeliveryZone] = {
            z.id: z for z in data.delivery.zones
        }
        self._alias_index: dict[str, str] = {}
        self._build_alias_index()

    # -- construction ---------------------------------------------------
    @classmethod
    def from_path(cls, path: str | Path) -> "RestaurantRepository":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"restaurant dataset not found: {p}")
        raw = json.loads(p.read_text(encoding="utf-8"))
        return cls(RestaurantData.model_validate(raw))

    def _build_alias_index(self) -> None:
        for item in self.data.menu:
            keys = [item.id, item.id.lower(), item.name_en, item.name_ar_eg, *item.aliases]
            for key in keys:
                norm = normalize_text(key)
                if norm:
                    self._alias_index.setdefault(norm, item.id)

    # -- menu -----------------------------------------------------------
    @property
    def menu(self) -> list[MenuItem]:
        return list(self.data.menu)

    @property
    def items_by_id(self) -> dict[str, MenuItem]:
        return dict(self._items_by_id)

    def get_item(self, reference: str) -> MenuItem:
        """Resolve an item by id, name, or alias. Raises ItemNotFound."""
        ref = (reference or "").strip()
        if not ref:
            raise ItemNotFound(reference, [])
        if ref in self._items_by_id:
            return self._items_by_id[ref]
        if ref.lower() in self._items_by_id:
            return self._items_by_id[ref.lower()]
        norm = normalize_text(ref)
        if norm in self._alias_index:
            return self._items_by_id[self._alias_index[norm]]
        raise ItemNotFound(reference, self.suggest_items(ref))

    def try_get_item(self, reference: str) -> MenuItem | None:
        try:
            return self.get_item(reference)
        except ItemNotFound:
            return None

    def suggest_items(self, reference: str, limit: int = 5) -> list[str]:
        """Deterministic suggestions: shared-token overlap, then stable order."""
        norm = normalize_text(reference)
        if not norm:
            return []
        ref_tokens = set(norm.split())
        scored: list[tuple[int, str]] = []
        for item in self.data.menu:
            keys = [item.name_en, item.name_ar_eg, *item.aliases]
            best = 0
            for key in keys:
                key_tokens = set(normalize_text(key).split())
                if not key_tokens:
                    continue
                overlap = len(ref_tokens & key_tokens)
                if overlap:
                    best = max(best, overlap)
            if best:
                scored.append((best, item.id))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [item_id for _, item_id in scored[:limit]]

    def search_menu(
        self,
        query: str = "",
        category: str | None = None,
        only_available: bool = False,
        max_results: int = 10,
    ) -> list[MenuItem]:
        """Deterministic menu search.

        Matching is token-overlap on the folded query, with an exact alias hit
        always ranked first. Ties break on the dataset's declared order so that
        repeated calls return identical lists.
        """
        if category:
            cat = normalize_text(category)
            pool = [
                i
                for i in self.data.menu
                if i.category == cat or normalize_text(i.category) == cat
            ]
        else:
            pool = list(self.data.menu)

        if only_available:
            pool = [i for i in pool if i.available]

        norm = normalize_text(query)
        if not norm:
            return pool[:max_results]

        exact: list[MenuItem] = []
        partial: list[tuple[int, int, MenuItem]] = []
        ref_tokens = set(norm.split())
        for idx, item in enumerate(pool):
            keys = [item.id, item.name_en, item.name_ar_eg, *item.aliases]
            folded = {normalize_text(k) for k in keys}
            if norm in folded:
                exact.append(item)
                continue
            best = 0
            for key in folded:
                key_tokens = set(key.split())
                if key_tokens:
                    best = max(best, len(ref_tokens & key_tokens))
            if best:
                partial.append((best, -idx, item))

        partial.sort(key=lambda t: (-t[0], t[1]))
        out = exact + [item for _, _, item in partial]
        return out[:max_results]

    def categories(self) -> list[str]:
        return [c.id for c in self.data.categories]

    def category_names(self) -> dict[str, dict[str, str]]:
        return {c.id: {"name_en": c.name_en, "name_ar_eg": c.name_ar_eg} for c in self.data.categories}

    # -- customization ---------------------------------------------------
    def groups_for_item(self, item: MenuItem) -> list[CustomizationGroup]:
        out: list[CustomizationGroup] = []
        for gid in item.customization_groups:
            g = self._groups_by_id.get(gid)
            if g:
                out.append(g)
        for g in self.data.customization_groups:
            if g.id in item.customization_groups:
                continue
            if item.category in g.applies_to_categories:
                out.append(g)
        return out

    def group(self, group_id: str) -> CustomizationGroup | None:
        return self._groups_by_id.get(group_id)

    def find_option(
        self, option_ref: str, groups: Iterable[CustomizationGroup]
    ) -> tuple[CustomizationGroup, CustomizationOption] | None:
        """Resolve a customization option by option id, English name, or Arabic name."""
        norm = normalize_text(option_ref)
        if not norm:
            return None
        for group in groups:
            for option in group.options:
                keys = {
                    normalize_text(option.id),
                    normalize_text(option.name_en),
                    normalize_text(option.name_ar_eg),
                }
                if norm in keys:
                    return group, option
        return None

    # -- availability / hours --------------------------------------------
    def is_available(self, item_id: str) -> bool:
        item = self._items_by_id.get(item_id)
        return bool(item and item.available)

    def unavailable_reason(self, item_id: str) -> str:
        return self.data.unavailable_reasons.get(item_id, "Not available.")

    def unavailable_alternatives(self, item: MenuItem, limit: int = 3) -> list[str]:
        pool = [
            i
            for i in self.data.menu
            if i.available and i.category == item.category and i.id != item.id
        ]
        return [i.id for i in pool[:limit]]

    @property
    def business_hours(self) -> BusinessHours:
        return self.data.business_hours

    def hours_for_day(self, day: str) -> dict[str, Any] | None:
        key = day.strip().lower()
        day_map = {
            "sunday": "sunday", "monday": "monday", "tuesday": "tuesday",
            "wednesday": "wednesday", "thursday": "thursday", "friday": "friday",
            "saturday": "saturday", "sun": "sunday", "mon": "monday", "tue": "tuesday",
            "wed": "wednesday", "thu": "thursday", "fri": "friday", "sat": "saturday",
        }
        canon = day_map.get(key)
        if canon is None:
            return None
        h = self.data.business_hours.schedule.get(canon)
        if h is None:
            return None
        return {"day": canon, "open": h.open, "close": h.close, "last_order": h.last_order}

    # -- delivery --------------------------------------------------------
    @property
    def delivery(self) -> Any:
        return self.data.delivery

    def zone(self, reference: str) -> DeliveryZone | None:
        ref = normalize_text(reference)
        if not ref:
            return None
        for zone in self.data.delivery.zones:
            keys = {normalize_text(zone.id), normalize_text(zone.name_en), normalize_text(zone.name_ar_eg)}
            keys |= {normalize_text(a) for a in zone.aliases}
            if ref in keys:
                return zone
        return None

    def zones(self) -> list[DeliveryZone]:
        return list(self.data.delivery.zones)

    # -- promotions -------------------------------------------------------
    @property
    def promotions(self) -> PromotionBook:
        return self.data.promotions

    def active_promotions(self) -> list[Promotion]:
        return [p for p in self.data.promotions.promotions if p.active]

    def promotion(self, promotion_id: str) -> Promotion | None:
        for p in self.data.promotions.promotions:
            if p.id.lower() == promotion_id.strip().lower():
                return p
        return None

    # -- rules ------------------------------------------------------------
    @property
    def rules(self) -> OrderRules:
        return self.data.order_rules

    # -- convenience for prompts -------------------------------------------
    def compact_menu_digest(self, max_items: int = 60) -> str:
        """Deterministic one-line-per-item digest for LLM grounding prompts."""
        lines = []
        for item in self.data.menu[:max_items]:
            flag = "" if item.available else " [UNAVAILABLE]"
            lines.append(f"{item.id} | {item.name_en} | {item.price} EGP{flag}")
        return "\n".join(lines)

    def compact_zone_digest(self) -> str:
        return "\n".join(
            f"{z.id} | {z.name_en} | fee {z.fee} EGP | min {z.min_order} EGP | {z.eta_min_minutes}-{z.eta_max_minutes} min"
            for z in self.data.delivery.zones
        )


@lru_cache(maxsize=8)
def load_repository(path: str) -> RestaurantRepository:
    """Cached repository loader. Keyed by absolute path string."""
    return RestaurantRepository.from_path(path)
