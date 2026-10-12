"""The hub's household comes from configuration, not from code.

* The pets and their care are planted from the JSON file named by
  ``EDWARD_HUB_SEED`` (``backend/hub_seed.example.json`` is a worked
  example); unset, the board starts empty.
* The people at the hub are ``EDWARD_HOUSEHOLD`` (owner first), served by
  ``GET /api/hub/who`` as ``people``.
* "Local" for the care day is the owner's timezone from settings.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pytest

from services import hub_pet_service, hub_service, hub_who_service

EXAMPLE = hub_service._BACKEND_DIR / "hub_seed.example.json"


def _board(pets, items, now):
    return hub_pet_service.build_board(
        [SimpleNamespace(photo_url=None, **p) for p in pets],
        [SimpleNamespace(active=True, **i) for i in items],
        [],
        now=now,
    )


# --------------------------------------------------------------------------
# the seed file
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestSeedFile:
    def test_unset_means_an_empty_board(self, monkeypatch):
        monkeypatch.delenv("EDWARD_HUB_SEED", raising=False)
        assert hub_service.seed_path() is None
        assert hub_service.load_seed() is None

    def test_a_relative_path_is_read_from_the_backend_directory(self, monkeypatch):
        monkeypatch.setenv("EDWARD_HUB_SEED", "hub_seed.example.json")
        assert hub_service.seed_path() == EXAMPLE
        assert hub_service.load_seed() is not None

    def test_a_missing_or_broken_file_plants_nothing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EDWARD_HUB_SEED", str(tmp_path / "nope.json"))
        assert hub_service.load_seed() is None
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        monkeypatch.setenv("EDWARD_HUB_SEED", str(bad))
        assert hub_service.load_seed() is None

    async def test_seed_if_empty_does_nothing_without_a_seed(self, monkeypatch):
        monkeypatch.delenv("EDWARD_HUB_SEED", raising=False)
        assert await hub_service.seed_if_empty() is False

    def test_the_example_keeps_the_hub_rules(self):
        pets, items = hub_service.load_seed(EXAMPLE)
        by_id = {i["id"]: i for i in items}
        assert [p["id"] for p in pets] == ["biscuit", "pepper"]
        # Every pet's bowls are windowed: food never goes overdue.
        bowls = [i for i in items if i["kind"] == "feeding"]
        assert bowls and all(i["schedule_type"] == "window" and i["at_time"] is None for i in bowls)
        # A twice-daily timed protocol.
        timed = sorted((i["window"], i["at_time"]) for i in items
                       if i["pet_id"] == "biscuit" and i["name"] == "Carprofen")
        assert timed == [("am", time(6, 0)), ("pm", time(18, 0))]
        # One unconfirmed item, badged.
        assert [i["id"] for i in items if i["needs_review"]] == ["biscuit-joint-chew-am"]
        # A supplement stirred into a bowl rides on that bowl's tap.
        assert by_id["pepper-probiotic-pm"]["bundle_with_id"] == "pepper-food-pm"
        assert hub_pet_service.items_to_log(
            "pepper-food-pm", [SimpleNamespace(active=True, **i) for i in items]
        ) == ["pepper-food-pm", "pepper-probiotic-pm"]

    def test_only_the_timed_doses_alert(self):
        pets, items = hub_service.load_seed(EXAMPLE)
        tz = hub_pet_service.household_tz()
        board = _board(pets, items, datetime(2026, 10, 12, 7, 0, tzinfo=tz))  # an hour late
        assert sorted(a["item"] for a in board["alerts"]) == ["Carprofen", "Joint chew"]
        assert all(not p["feedings"][w]["overdue"] for p in board["pets"] for w in ("am", "pm"))

    @pytest.mark.parametrize("doc,needle", [
        ({}, "pets"),
        ({"pets": [{"name": "No id"}]}, "id and a name"),
        ({"pets": [{"id": "a", "name": "A", "food": ["noon"]}]}, "food"),
        ({"pets": [{"id": "a", "name": "A", "care": [{"name": "X", "schedule_type": "timed", "window": "am"}]}]},
         "at_time"),
        ({"pets": [{"id": "a", "name": "A", "care": [{"name": "X", "window": "am", "at_time": "6am",
                                                      "schedule_type": "timed"}]}]}, "HH:MM"),
        ({"pets": [{"id": "a", "name": "A", "care": [{"name": "X", "window": "dusk"}]}]}, "window"),
        ({"pets": [{"id": "a", "name": "A", "care": [{"name": "X", "window": "am", "bundle_with": "a-food-noon"}]}]},
         "bundle_with"),
        ({"pets": [{"id": "a", "name": "A"}, {"id": "a", "name": "B"}]}, "duplicate"),
    ])
    def test_a_bad_seed_says_what_is_wrong(self, doc, needle):
        with pytest.raises(hub_service.SeedError, match=needle):
            hub_service.parse_seed(doc)

    def test_a_pet_with_no_bowls(self):
        pets, items = hub_service.parse_seed({"pets": [{"id": "fish", "name": "Fish", "food": []}]})
        assert [p["id"] for p in pets] == ["fish"] and items == []

    def test_the_example_is_valid_json(self):
        assert json.loads(EXAMPLE.read_text())["pets"]


# --------------------------------------------------------------------------
# the household
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestHousehold:
    def test_the_people_are_the_configured_household(self, monkeypatch):
        monkeypatch.setenv("EDWARD_HOUSEHOLD", "Sam, Alex")
        assert hub_who_service.hub_people() == ("Sam", "Alex")
        assert hub_who_service.HUB_PEOPLE == ("Sam", "Alex")
        assert hub_who_service.normalize_person(" alex ") == "Alex"
        assert hub_who_service.normalize_person("owner") == "Sam"
        assert hub_who_service.normalize_person("Rowan") is None

    def test_unset_it_is_just_the_owner(self, monkeypatch):
        monkeypatch.delenv("EDWARD_HOUSEHOLD", raising=False)
        monkeypatch.setenv("EDWARD_OWNER_NAME", "Sam")
        assert hub_who_service.hub_people() == ("Sam",)

    def test_the_who_payload_serves_the_people(self, monkeypatch):
        monkeypatch.setenv("EDWARD_HOUSEHOLD", "Sam,Alex")
        out = hub_service._who_payload(None, datetime(2026, 10, 12, tzinfo=timezone.utc))
        assert out["people"] == ["Sam", "Alex"] and out["person"] is None


# --------------------------------------------------------------------------
# the household's clock
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestHouseholdZone:
    def test_local_is_the_settings_timezone(self, monkeypatch):
        from services import timezone_utils

        before = timezone_utils.current_tz_name()
        try:
            timezone_utils.set_user_tz("Europe/London")
            # A naive "now" is read in the owner's zone: 02:30 London is
            # still the previous care day.
            assert hub_pet_service.care_day_for(datetime(2026, 7, 2, 2, 30)) == date(2026, 7, 1)
            six = datetime.combine(date(2026, 7, 1), time(6), tzinfo=hub_pet_service.HOUSEHOLD_TZ)
            assert six.astimezone(timezone.utc).hour == 5
        finally:
            timezone_utils.set_user_tz(before)


# --------------------------------------------------------------------------
# planting it (database)
# --------------------------------------------------------------------------

@pytest.fixture
async def no_pets(clean_db):
    """clean_db does not truncate the pet board; start each test with none."""
    from sqlalchemy import delete

    from services.database import CareEventModel, CareItemModel, PetModel, async_session

    async def wipe():
        async with async_session() as session:
            for model in (CareEventModel, CareItemModel, PetModel):
                await session.execute(delete(model))
            await session.commit()

    await wipe()
    yield
    await wipe()


@pytest.mark.integration
async def test_the_seed_is_planted_once_into_an_empty_board(no_pets, monkeypatch):
    monkeypatch.delenv("EDWARD_HUB_SEED", raising=False)
    board = await hub_service.get_board()
    assert board["pets"] == [], "no seed configured: an empty board"

    monkeypatch.setenv("EDWARD_HUB_SEED", str(EXAMPLE))
    assert await hub_service.seed_if_empty() is True
    assert await hub_service.seed_if_empty() is False, "planted once"
    board = await hub_service.get_board()
    assert [p["name"] for p in board["pets"]] == ["Biscuit", "Pepper"]
    biscuit = board["pets"][0]
    assert any(m["needs_review"] for m in biscuit["meds"])

    out = await hub_service.log_care("pepper-food-pm", given_by="Sam")
    assert out["logged"] == ["pepper-food-pm", "pepper-probiotic-pm"]
