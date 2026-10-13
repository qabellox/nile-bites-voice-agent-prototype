"""Restaurant dataset integrity and lookup behaviour.

These tests are a guard rail for the whole study: every answer the agent gives
must be traceable to this file. A silent dataset regression would corrupt every
trial that depends on menu facts, so the counts and the known-unavailable items
are pinned explicitly.
"""

from __future__ import annotations

import pytest

from nbva.db.schema import RestaurantData

# Items the dataset marks unavailable. Listed here so a change to the fixture is
# a deliberate, visible edit rather than a silent drift.
UNAVAILABLE = {"NB-GRIL-005", "NB-SIDE-006", "NB-DSRT-005"}

DAYS = (
    "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday", "sunday",
)


# -- dataset shape --------------------------------------------------------


def test_dataset_declares_itself_as_fictional(restaurant_data):
    meta = restaurant_data.meta
    assert meta.IS_FICTIONAL is True
    assert meta.currency == "EGP"
    assert meta.DISCLAIMER.strip()
    assert any("unavailable on purpose" in n for n in meta.notes)


def test_menu_has_forty_items(repo):
    assert len(repo.menu) == 40
    assert len(repo.items_by_id) == 40


def test_item_ids_are_unique_and_well_formed(repo):
    ids = [i.id for i in repo.menu]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("NB-") for i in ids)


def test_every_item_has_bilingual_names_and_a_positive_price(repo):
    for item in repo.menu:
        assert item.name_en and item.name_ar_eg, f"{item.id} missing a name"
        assert item.price > 0, f"{item.id} has a non-positive price"


def test_there_are_eight_categories(repo):
    assert len(repo.categories()) == 8
    for cid, entry in repo.category_names().items():
        assert entry.get("name_en") and entry.get("name_ar_eg"), f"{cid} missing a bilingual label"


def test_every_item_belongs_to_a_declared_category(repo, restaurant_data):
    declared = {c.id for c in restaurant_data.categories}
    for item in repo.menu:
        assert item.category in declared, f"{item.id} references unknown category {item.category}"


def test_delivery_zones_and_promotions_are_loaded(repo):
    assert len(repo.zones()) == 6
    assert len(repo.promotions.promotions) == 5


def test_the_dataset_defines_hours_for_all_seven_days(restaurant_data):
    schedule = restaurant_data.business_hours.schedule
    for day in DAYS:
        assert day in schedule, f"no hours declared for {day}"


def test_the_dataset_is_marked_not_being_a_research_run(config):
    assert config.get("experiment.is_research_run") is False


# -- availability ---------------------------------------------------------


def test_the_known_unavailable_items_are_unavailable(repo):
    for item_id in UNAVAILABLE:
        assert repo.is_available(item_id) is False, f"{item_id} should be unavailable"


def test_unavailable_items_carry_a_human_reason(repo):
    for item_id in UNAVAILABLE:
        assert repo.unavailable_reason(item_id).strip()


def test_unavailable_items_offer_alternatives(repo):
    for item_id in UNAVAILABLE:
        item = repo.get_item(item_id)
        assert repo.unavailable_alternatives(item), f"{item_id} has no alternatives"


def test_a_regular_item_is_available(repo):
    assert repo.is_available("NB-BURG-001") is True


def test_availability_agrees_with_the_item_flag(repo):
    for item in repo.menu:
        assert repo.is_available(item.id) is item.available


# -- lookups --------------------------------------------------------------


def test_items_resolve_by_id_and_by_arabic_name(repo):
    item = repo.get_item("NB-BURG-001")
    assert repo.get_item(item.name_en).id == item.id
    assert repo.get_item(item.name_ar_eg).id == item.id


def test_an_unknown_reference_raises_instead_of_guessing(repo):
    with pytest.raises(Exception):
        repo.get_item("does-not-exist-at-all")


def test_try_get_item_returns_none_for_an_unknown_reference(repo):
    assert repo.try_get_item("does-not-exist-at-all") is None


def test_ambiguous_arabic_search_preserves_every_candidate(repo):
    # "برجر" legitimately matches more than one burger; collapsing that to one
    # row would hide a real choice the participant has to make.
    results = repo.search_menu("برجر", max_results=20)
    assert len(results) > 1
    assert len({r.id for r in results}) == len(results)


def test_search_can_be_restricted_to_available_items_only(repo):
    results = repo.search_menu("", only_available=True, max_results=60)
    assert results
    assert all(repo.is_available(r.id) for r in results)


def test_suggestions_stay_inside_the_same_category(repo):
    burger = repo.get_item("NB-BURG-001")
    for suggestion in repo.suggest_items(burger.id):
        assert repo.get_item(suggestion).category == burger.category


def test_find_option_locates_a_customization_option(repo):
    item = repo.get_item("NB-BURG-001")
    found = repo.find_option("extra cheese", repo.groups_for_item(item))
    assert found is not None
    group, option = found
    assert group.id == "burger_build"
    assert option.id == "extra_cheese"


def test_the_no_cheese_option_exists_and_is_free(repo):
    item = repo.get_item("NB-BURG-001")
    _, option = repo.find_option("no cheese", repo.groups_for_item(item))
    assert option.id == "no_cheese"
    assert option.price == 0


def test_find_option_returns_none_for_an_unknown_option(repo):
    item = repo.get_item("NB-BURG-001")
    assert repo.find_option("caviar", repo.groups_for_item(item)) is None


def test_zones_resolve_by_id_and_by_either_name(repo):
    zone = repo.zones()[0]
    assert repo.zone(zone.id) is zone
    assert repo.zone(zone.name_en) is not None
    assert repo.zone(zone.name_ar_eg) is not None


def test_an_unknown_zone_returns_none(repo):
    assert repo.zone("atlantis") is None


def test_promotions_resolve_by_id(repo):
    for promo in repo.promotions.promotions:
        assert repo.promotion(promo.id) is promo


def test_promotion_days_are_lowercase_weekday_names(repo):
    for promo in repo.promotions.promotions:
        for day in promo.days:
            assert day in DAYS, f"{promo.id} names an unknown day {day!r}"


# -- hours ----------------------------------------------------------------


def test_hours_can_be_read_for_any_day(repo):
    for day in DAYS:
        hours = repo.hours_for_day(day)
        assert hours is not None
        assert "open" in hours or "closed" in hours


def test_hours_for_an_unknown_day_are_none(repo):
    assert repo.hours_for_day("caturday") is None


# -- digests used to brief the model --------------------------------------


def test_the_menu_digest_is_bounded_but_not_empty(repo):
    digest = repo.compact_menu_digest(max_items=5)
    assert digest.strip()
    assert digest.count("NB-") <= 5


def test_the_zone_digest_lists_every_zone(repo):
    digest = repo.compact_zone_digest()
    for zone in repo.zones():
        assert zone.id in digest


# -- the schema itself ----------------------------------------------------


def test_the_dataset_file_validates_against_the_schema(restaurant_data):
    # Round-tripping proves the on-disk file and the in-memory model agree.
    again = RestaurantData.model_validate(restaurant_data.model_dump(mode="json"))
    assert again.meta.data_version == restaurant_data.meta.data_version
    assert len(again.menu) == len(restaurant_data.menu)
