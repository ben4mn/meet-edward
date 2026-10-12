"""Who is at the hub: the household, not a user.

Plan Phase 7, 2026-09-24. The pet-station hub already themes itself per
person (the first person's palette, the second's, …) from a small toggle,
but only the Pi knew who was standing there. Edward's pack and pulse spoke
about "Ellis" whoever tapped the screen. Now the hub posts the selection,
the server remembers it with a timestamp, and it clears after 30 minutes
with no activity — so "At the hub: Rowan (since 10:02)" is a fact, not a
guess from a sticky toggle left over from yesterday.

The household is ``EDWARD_HOUSEHOLD`` (owner first). conftest leaves it
unset, so every test here runs with a two-person household (``household``
below).
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from services import hub_who_service as who

CHICAGO = ZoneInfo("America/Chicago")
NOW = datetime(2026, 9, 24, 15, 2, tzinfo=timezone.utc)  # 10:02 in Springfield


@pytest.fixture(autouse=True)
def household(monkeypatch):
    """A two-person household at the hub: the owner first, then Rowan."""
    monkeypatch.setenv("EDWARD_HOUSEHOLD", "Ellis,Rowan")


def _state(person="Rowan", since_min_ago=0, seen_min_ago=0):
    return {
        "person": person,
        "since": (NOW - timedelta(minutes=since_min_ago)).isoformat(),
        "last_seen": (NOW - timedelta(minutes=seen_min_ago)).isoformat(),
    }


# --------------------------------------------------------------------------
# pure
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestNormalizePerson:
    def test_the_household_matches_the_hub_toggle(self):
        assert who.HUB_PEOPLE == ("Ellis", "Rowan")

    def test_no_household_configured_is_the_owner_alone(self, monkeypatch):
        monkeypatch.delenv("EDWARD_HOUSEHOLD", raising=False)
        assert who.HUB_PEOPLE == ("Ellis",)
        assert who.normalize_person("owner") == "Ellis"
        assert who.normalize_person("Rowan") is None

    @pytest.mark.parametrize("raw,expected", [
        ("Ellis", "Ellis"), ("owner", "Ellis"), (" Rowan ", "Rowan"), ("ROWAN", "Rowan"),
    ])
    def test_known_people_normalize_to_their_name(self, raw, expected):
        assert who.normalize_person(raw) == expected

    @pytest.mark.parametrize("raw", ["", "Nobody", "Edward", None, 3, "Ellis; drop table"])
    def test_anyone_else_is_refused(self, raw):
        assert who.normalize_person(raw) is None


@pytest.mark.unit
class TestCurrentHubPerson:
    def test_nobody_has_been_at_the_hub(self):
        assert who.current_hub_person(None, NOW) is None
        assert who.current_hub_person({}, NOW) is None

    def test_a_recent_selection_is_who_is_there(self):
        assert who.current_hub_person(_state(seen_min_ago=10), NOW) == "Rowan"

    def test_thirty_minutes_idle_is_still_there(self):
        assert who.current_hub_person(_state(seen_min_ago=30), NOW) == "Rowan"

    def test_more_than_thirty_minutes_idle_clears_it(self):
        assert who.current_hub_person(_state(seen_min_ago=31), NOW) is None

    def test_an_unknown_stored_person_is_nobody(self):
        assert who.current_hub_person(_state(person="Stranger"), NOW) is None

    def test_a_broken_timestamp_is_nobody(self):
        assert who.current_hub_person({"person": "Ellis", "since": "x", "last_seen": "yesterday"}, NOW) is None

    def test_a_naive_timestamp_is_read_as_utc(self):
        naive = (NOW - timedelta(minutes=5)).replace(tzinfo=None).isoformat()
        assert who.current_hub_person({"person": "Ellis", "since": naive, "last_seen": naive}, NOW) == "Ellis"


@pytest.mark.unit
class TestRecordSelection:
    def test_a_new_person_starts_a_new_stay(self):
        state = who.record_selection(_state(person="Ellis", since_min_ago=20, seen_min_ago=5), "Rowan", NOW)
        assert state == {"person": "Rowan", "since": NOW.isoformat(), "last_seen": NOW.isoformat()}

    def test_the_same_person_keeps_their_since(self):
        prior = _state(person="Rowan", since_min_ago=20, seen_min_ago=5)
        state = who.record_selection(prior, "Rowan", NOW)
        assert state["since"] == prior["since"]
        assert state["last_seen"] == NOW.isoformat()

    def test_the_same_person_after_going_idle_starts_over(self):
        state = who.record_selection(_state(person="Rowan", since_min_ago=90, seen_min_ago=45), "Rowan", NOW)
        assert state["since"] == NOW.isoformat()

    def test_first_selection_ever(self):
        assert who.record_selection(None, "Ellis", NOW)["person"] == "Ellis"


@pytest.mark.unit
class TestPresenceLine:
    def test_the_line_names_the_person_and_since_when_in_local_time(self):
        line = who.presence_line(_state(since_min_ago=0, seen_min_ago=0), NOW, CHICAGO)
        assert line == "At the hub: Rowan (since 10:02)"

    def test_since_is_the_start_of_the_stay_not_the_last_tap(self):
        line = who.presence_line(_state(since_min_ago=25, seen_min_ago=1), NOW, CHICAGO)
        assert line == "At the hub: Rowan (since 09:37)"

    def test_no_line_when_nobody_is_there(self):
        assert who.presence_line(None, NOW, CHICAGO) is None
        assert who.presence_line(_state(seen_min_ago=45), NOW, CHICAGO) is None


# --------------------------------------------------------------------------
# persistence and the API
# --------------------------------------------------------------------------

@pytest.fixture
async def no_hub_who(clean_db):
    """clean_db does not truncate display_config; start each test with no stay."""
    from sqlalchemy import delete

    from services.database import DisplayConfigModel, async_session
    from services import hub_service

    async with async_session() as session:
        await session.execute(delete(DisplayConfigModel).where(DisplayConfigModel.id == hub_service.HUB_WHO_CONFIG_ID))
        await session.commit()
    yield


@pytest.mark.integration
async def test_set_and_read_back_who_is_at_the_hub(no_hub_who):
    from services import hub_service

    state = await hub_service.set_hub_person("rowan", now=NOW)
    assert state["person"] == "Rowan"
    got = await hub_service.get_hub_person(now=NOW + timedelta(minutes=5))
    assert got["person"] == "Rowan"
    assert got["since"] == NOW.isoformat()
    assert got["people"] == ["Ellis", "Rowan"]


@pytest.mark.integration
async def test_an_unknown_person_is_refused(no_hub_who):
    from services import hub_service

    with pytest.raises(ValueError):
        await hub_service.set_hub_person("Nobody", now=NOW)
    assert (await hub_service.get_hub_person(now=NOW))["person"] is None


@pytest.mark.integration
async def test_presence_line_reads_the_stored_stay(no_hub_who, monkeypatch):
    from services import hub_service

    monkeypatch.setattr(hub_service, "_presence_tz", lambda: CHICAGO)
    assert await hub_service.hub_presence_line(now=NOW) is None
    await hub_service.set_hub_person("Rowan", now=NOW)
    assert await hub_service.hub_presence_line(now=NOW + timedelta(minutes=3)) == "At the hub: Rowan (since 10:02)"
    assert await hub_service.hub_presence_line(now=NOW + timedelta(minutes=31)) is None


@pytest.mark.integration
async def test_activity_at_the_hub_keeps_the_stay_alive(no_hub_who):
    from services import hub_service

    await hub_service.set_hub_person("Rowan", now=NOW)
    await hub_service.touch_hub_person("Rowan", now=NOW + timedelta(minutes=25))
    got = await hub_service.get_hub_person(now=NOW + timedelta(minutes=50))
    assert got["person"] == "Rowan" and got["since"] == NOW.isoformat()


@pytest.mark.integration
async def test_activity_by_someone_else_or_no_one_known(no_hub_who):
    from services import hub_service

    await hub_service.set_hub_person("Rowan", now=NOW)
    await hub_service.touch_hub_person("Edward", now=NOW + timedelta(minutes=1))  # not a person at the hub
    assert (await hub_service.get_hub_person(now=NOW + timedelta(minutes=2)))["person"] == "Rowan"
    await hub_service.touch_hub_person("owner", now=NOW + timedelta(minutes=3))
    got = await hub_service.get_hub_person(now=NOW + timedelta(minutes=4))
    assert got["person"] == "Ellis" and got["since"] == (NOW + timedelta(minutes=3)).isoformat()


@pytest.mark.integration
async def test_who_round_trip_via_api(no_hub_who):
    from httpx import ASGITransport, AsyncClient
    from main import app
    from services.auth_service import create_access_token

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={"edward_token": create_access_token()}) as client:
        r = await client.get("/api/hub/who")
        assert r.status_code == 200 and r.json()["person"] is None
        r = await client.post("/api/hub/who", json={"person": "Rowan"})
        assert r.status_code == 200 and r.json()["person"] == "Rowan"
        r = await client.get("/api/hub/who")
        assert r.json()["person"] == "Rowan" and r.json()["since"]
        r = await client.post("/api/hub/who", json={"person": "Mallory"})
        assert r.status_code == 422
        assert (await client.get("/api/hub/who")).json()["person"] == "Rowan"


@pytest.fixture
async def seeded_board(no_hub_who, monkeypatch):
    """The example household's pets on an otherwise empty board (clean_db
    does not truncate the pet board)."""
    from sqlalchemy import delete

    from services.database import CareEventModel, CareItemModel, PetModel, async_session

    async def wipe():
        async with async_session() as session:
            for model in (CareEventModel, CareItemModel, PetModel):
                await session.execute(delete(model))
            await session.commit()

    await wipe()
    monkeypatch.setenv("EDWARD_HUB_SEED", "hub_seed.example.json")
    yield
    await wipe()


@pytest.mark.integration
async def test_logging_a_dose_at_the_hub_says_who_is_there(seeded_board):
    from httpx import ASGITransport, AsyncClient
    from main import app
    from services import hub_service
    from services.auth_service import create_access_token

    assert await hub_service.seed_if_empty() is True
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={"edward_token": create_access_token()}) as client:
        r = await client.post("/api/hub/care/biscuit-food-am/log", json={"given_by": "Rowan"})
        assert r.status_code == 200
        assert (await client.get("/api/hub/who")).json()["person"] == "Rowan"
        r = await client.post("/api/hub/lists/Shopping/items", json={"text": "cat litter", "created_by": "Ellis"})
        assert r.status_code == 200
        assert (await client.get("/api/hub/who")).json()["person"] == "Ellis"
