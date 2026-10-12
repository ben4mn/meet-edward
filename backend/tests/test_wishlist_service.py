"""Edward's wishlist — features he wants Ellis to build.

Replaces the self-evolution machinery (2026-09-12): Edward cannot change
his own code any more. When he notices something he wishes he could do he
logs it here; Ellis reads the list and builds what he agrees with.

Pure decisions (title normalization, duplicate detection, status
validation) plus one DB round trip through the service.
"""

from __future__ import annotations

import pytest

from services import wishlist_service as ws


# --- pure -------------------------------------------------------------------

def test_normalize_title_lowercases_collapses_whitespace_and_strips_punctuation():
    assert ws.normalize_title("  Read  PDFs,  please!! ") == "read pdfs please"
    assert ws.normalize_title("Read PDFs") == ws.normalize_title("read pdfs.")
    assert ws.normalize_title("") == ""


def test_find_existing_matches_open_items_by_normalized_title_only():
    items = [
        {"id": "a", "title": "Read PDFs", "status": "done"},
        {"id": "b", "title": "read pdfs!", "status": "open"},
        {"id": "c", "title": "Hear the doorbell", "status": "planned"},
        {"id": "d", "title": "Something declined", "status": "declined"},
    ]
    assert ws.find_existing(items, "READ  PDFs")["id"] == "b"
    assert ws.find_existing(items, "hear the doorbell")["id"] == "c"
    assert ws.find_existing(items, "something declined") is None
    assert ws.find_existing(items, "nothing like this") is None
    assert ws.find_existing([], "x") is None


def test_statuses_and_normalize_status():
    assert ws.STATUSES == ("open", "planned", "building", "done", "declined")
    assert ws.normalize_status(" Planned ") == "planned"
    with pytest.raises(ValueError) as exc:
        ws.normalize_status("wishful")
    assert "open" in str(exc.value) and "declined" in str(exc.value)
    with pytest.raises(ValueError):
        ws.normalize_status(None)


def test_priority_is_clamped_to_one_two_three():
    assert ws.normalize_priority(None) == 2
    assert ws.normalize_priority(1) == 1
    assert ws.normalize_priority(0) == 1
    assert ws.normalize_priority(9) == 3
    assert ws.normalize_priority("3") == 3
    assert ws.normalize_priority("high") == 2


# --- DB round trip ------------------------------------------------------------

@pytest.mark.integration
async def test_add_merge_list_update_delete_round_trip(clean_db, capsys):
    first = await ws.add_wish(title="Read PDFs", why="Ellis sends me PDFs I cannot open.",
                              conversation_id="conv-1")
    assert first["merged"] is False
    assert first["status"] == "open" and first["priority"] == 2
    assert first["source"] == "edward" and first["times_wanted"] == 1
    assert first["conversation_id"] == "conv-1"
    assert "[wish]" in capsys.readouterr().out

    # The same wish again: merged, counted, the new reason kept.
    again = await ws.add_wish(title="read PDFs!", why="The vet sends invoices as PDF.")
    assert again["merged"] is True and again["id"] == first["id"]
    assert again["times_wanted"] == 2
    assert "vet sends invoices" in (again["detail"] or "")
    assert again["why"] == first["why"]

    # A different wish, from Ellis, high priority.
    second = await ws.add_wish(title="Hear the doorbell", why="Ring camera events.",
                               priority=1, source="owner")
    assert second["merged"] is False and second["source"] == "owner"

    # A closed wish does not absorb a new one with the same title.
    closed = await ws.add_wish(title="Old idea", why="x")
    await ws.update_wish(closed["id"], status="done")
    reopened = await ws.add_wish(title="Old idea", why="y")
    assert reopened["id"] != closed["id"] and reopened["merged"] is False

    # Listing: open ones first, newest-updated first; closed at the end.
    listed = await ws.list_wishes()
    ids = [w["id"] for w in listed]
    assert ids[-1] == closed["id"]
    assert set(ids[:3]) == {first["id"], second["id"], reopened["id"]}
    assert ids[0] == reopened["id"]           # most recently touched
    assert [w["id"] for w in await ws.list_wishes(status="done")] == [closed["id"]]

    # Update: status and fields; a bad status is refused.
    updated = await ws.update_wish(second["id"], status="building", priority=2, detail="Started.")
    assert updated["status"] == "building" and updated["priority"] == 2 and updated["detail"] == "Started."
    with pytest.raises(ValueError):
        await ws.update_wish(second["id"], status="maybe")
    assert await ws.update_wish("no-such-id", status="done") is None

    # Today reads the last 24h.
    recent = await ws.wishes_since(hours=24)
    assert {w["id"] for w in recent} >= {first["id"], second["id"]}

    # Delete.
    assert await ws.delete_wish(second["id"]) is True
    assert await ws.delete_wish(second["id"]) is False
    assert second["id"] not in [w["id"] for w in await ws.list_wishes()]
