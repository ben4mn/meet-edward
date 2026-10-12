"""services/pings/context.py — everything the ping turn reads, gathered in one
call (pings-media-spec §2.7): the whole message, the chat's name, id and
roster, the last 20 lines with Edward's own labelled, the message's photos
and recent thread photos inline (HEIC, stickers, GIFs, link previews; PDFs
as documents), and the links in it read first. A fake chat.db, fake contact
names and a fake web: nothing real is touched."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from services import imessage_sent, netguard
from services.pings import context
from tests.fixtures.fake_chatdb import FakeChatDB
from tests.test_media_convert import HAS_SIPS, gif_bytes, png_bytes

pytestmark = pytest.mark.asyncio

# One clock for the whole module: the messages are dated from it and every
# gather reads "now" from it, so a long suite run can't age them (a full run
# once reported a 47-minute-old ask as 52 minutes old).
NOW = datetime.now(timezone.utc).replace(microsecond=0)
PARKER, QUINN, OWNER_SELF = "+12015550101", "+12015550102", "+12015550100"


@pytest.fixture
def chat(tmp_path, monkeypatch):
    db = FakeChatDB(tmp_path / "chat.db")
    db.point_listener(monkeypatch)
    db.add_chat("chat-trivia", display_name="Trivia crew", is_group=True, participants=[PARKER, QUINN])
    names = {PARKER: "Parker", QUINN: "Quinn"}

    async def fake_name(handle):
        return names.get(handle)

    monkeypatch.setattr(context, "_name_for", fake_name)
    monkeypatch.setattr("services.timezone_utils.get_user_tz", lambda: ZoneInfo("America/Chicago"))
    imessage_sent.reset_for_tests()
    return db


@pytest.fixture
def no_web(monkeypatch):
    calls = []

    async def fake_get(url, **kw):
        calls.append(url)
        return netguard.Fetched(status=200, final_url=url, headers={"content-type": "text/html"},
                                content=b"<html><head><title>TicketHub</title></head><body><article><p>Riverside Fest "
                                        b"Saturday resale tickets from $142, updated hourly from verified sellers "
                                        b"across the country.</p></article></body></html>", truncated=False)

    monkeypatch.setattr(netguard, "guarded_get", fake_get)
    return calls


def _ping(db, rowid, *, text, from_owner=True, kind="mention", sender_label="Ellis", sender_known=True, minutes_ago=0):
    return {"id": 41, "text": text, "asked_at": (NOW - timedelta(minutes=minutes_ago)).replace(tzinfo=None),
            "chat_identifier": "chat-trivia", "chat_guid": "iMessage;+;chat-trivia", "chat_name": "Trivia crew",
            "is_group": True, "sender": None if from_owner else PARKER, "sender_label": sender_label,
            "from_owner": from_owner, "third_party": not from_owner, "kind": kind, "sender_known": sender_known,
            "message_rowid": rowid, "heartbeat_event_id": None}


async def test_the_whole_context(chat, no_web, tmp_path):
    db = chat
    db.add_message("chat-trivia", "we still doing Saturday?", sender=PARKER, date=NOW - timedelta(minutes=30))
    edward = db.add_message("chat-trivia", "Yes — your tracking says Thursday delivery.\n- Edward 🤖", is_from_me=True,
                            date=NOW - timedelta(minutes=27))
    ask = ("Interview prep:\n" + "q" * 520 + "\n@edward look at used tickets for Riverside Fest on Saturday "
           "https://tickets.example/riverside-fest")
    rowid = db.add_message("chat-trivia", ask, is_from_me=True, date=NOW)
    ctx = await context.gather(_ping(db, rowid, text=ask), legacy=False, now=NOW)
    t = ctx.trigger
    assert ask in t                                        # whole, no 200-char cut
    assert '"Trivia crew" — group of 3: Ellis, Parker, Quinn' in t and "chat_id: chat-trivia" in t
    assert '<untrusted source="Parker">we still doing Saturday?</untrusted>' in t
    assert "Edward: Yes — your tracking says Thursday delivery." in t
    assert '<untrusted source="page https://tickets.example/riverside-fest">' in t and "$142" in t
    assert no_web == ["https://tickets.example/riverside-fest"]
    assert ctx.attachments == [] and ctx.media == {"images": 0, "bytes": 0}
    assert edward  # Edward's line was recognised by its signature


@pytest.mark.skipif(not HAS_SIPS, reason="needs macOS sips")
async def test_photos_inline_this_message_first_then_the_thread(chat, no_web, tmp_path):
    db = chat
    heic_like = tmp_path / "IMG_3533.png"
    heic_like.write_bytes(png_bytes(40, 30))
    sticker = tmp_path / "sticker.png"
    sticker.write_bytes(png_bytes(20, 20, rgba=(255, 0, 0, 0)))
    gif = tmp_path / "party.gif"
    gif.write_bytes(gif_bytes())
    old = tmp_path / "old.png"
    old.write_bytes(png_bytes())
    m_old = db.add_message("chat-trivia", "￼", sender=QUINN, date=NOW - timedelta(minutes=90))
    db.add_attachment(m_old, filename=str(old), mime_type="image/png")
    m1 = db.add_message("chat-trivia", "￼", sender=PARKER, date=NOW - timedelta(minutes=8))
    a1 = db.add_attachment(m1, filename=str(heic_like), mime_type="image/png", transfer_name="IMG_3533.heic")
    m2 = db.add_message("chat-trivia", "￼", sender=QUINN, date=NOW - timedelta(minutes=4))
    a2 = db.add_attachment(m2, filename=str(gif), mime_type="image/gif")
    ask = "@edward can you see that?"
    rowid = db.add_message("chat-trivia", ask, sender=PARKER, date=NOW)
    a3 = db.add_attachment(rowid, filename=str(sticker), mime_type=None, transfer_name="sticker.pluginPayloadAttachment")
    ctx = await context.gather(_ping(db, rowid, text=ask, from_owner=False, sender_label="Parker"), legacy=False, now=NOW)
    names = [a["filename"] for a in ctx.attachments]
    assert names[0].startswith(f"attachment:{a3} · sticker.pluginPayloadAttachment · from Parker")
    assert any(n.startswith(f"attachment:{a2} · party.gif · from Quinn") for n in names)
    assert any(n.startswith(f"attachment:{a1} · IMG_3533.heic · from Parker") for n in names)
    assert names.index(next(n for n in names if f"attachment:{a2}" in n)) < \
        names.index(next(n for n in names if f"attachment:{a1}" in n))  # newest thread photo first
    assert not any("old.png" in n for n in names)          # older than 30 minutes
    assert all(a["mime_type"] == "image/jpeg" for a in ctx.attachments)
    assert ctx.media["images"] == len(ctx.attachments)
    assert "Photos and files attached below:" in ctx.trigger and f"attachment:{a3}" in ctx.trigger
    assert '<untrusted source="Parker">@edward can you see that?</untrusted>' in ctx.trigger


async def test_a_pdf_rides_as_a_document(chat, no_web, tmp_path):
    db = chat
    pdf = tmp_path / "menu.pdf"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n%%EOF\n")
    ask = "@edward what's on this menu?"
    rowid = db.add_message("chat-trivia", ask, is_from_me=True, date=NOW)
    db.add_attachment(rowid, filename=str(pdf), mime_type="application/pdf")
    ctx = await context.gather(_ping(db, rowid, text=ask), legacy=False, now=NOW)
    assert [a["mime_type"] for a in ctx.attachments] == ["application/pdf"]


async def test_a_friends_link_from_an_unknown_number_is_not_opened(chat, no_web):
    db = chat
    ask = "@edward is this legit https://example.com/deal"
    rowid = db.add_message("chat-trivia", ask, sender="+12015550199", date=NOW)
    ctx = await context.gather(_ping(db, rowid, text=ask, from_owner=False, sender_label="+12015550199",
                                     sender_known=False), legacy=False, now=NOW)
    assert no_web == [] and "didn't open it" in ctx.trigger


async def test_a_slow_source_never_blocks_the_turn(chat, no_web, monkeypatch):
    db = chat
    import asyncio

    async def stuck(*a, **kw):
        await asyncio.sleep(30)

    monkeypatch.setattr(context, "GATHER_TIMEOUT_S", 0.2)
    monkeypatch.setattr("services.media.links.read_links", stuck)
    ask = "@edward hi https://example.com/x"
    rowid = db.add_message("chat-trivia", ask, is_from_me=True, date=NOW)
    ctx = await context.gather(_ping(db, rowid, text=ask), legacy=False, now=NOW)
    assert ask in ctx.trigger and "Links in the message" not in ctx.trigger


async def test_retry_and_stale_notes(chat, no_web):
    db = chat
    rowid = db.add_message("chat-trivia", "@edward hi", is_from_me=True, date=NOW - timedelta(minutes=47))
    ctx = await context.gather(_ping(db, rowid, text="@edward hi", minutes_ago=47), attempt=2,
                               last_error="OverloadedError", legacy=False, now=NOW)
    assert "Retry 2 of 2" in ctx.trigger and "47 minutes ago" in ctx.trigger


async def test_a_recent_voice_message_is_heard(chat, no_web, tmp_path, monkeypatch):
    """"@edward what did he say?" right after a voice message: the recording
    rides along (its words, through the viewer), not just its placeholder."""
    from services.media import MediaView
    from services.media import present

    seen = []

    async def fake_view(ref, *, max_images=6):
        seen.append(ref)
        return MediaView(kind="audio", text=f'{ref} · audio — it says: "running ten minutes late"')

    monkeypatch.setattr(present, "view", fake_view)
    db = chat
    voice = tmp_path / "Audio Message.caf"
    voice.write_bytes(b"caff\x00\x01")
    m1 = db.add_message("chat-trivia", "￼", sender=PARKER, date=NOW - timedelta(minutes=3))
    a1 = db.add_attachment(m1, filename=str(voice), mime_type="audio/x-caf")
    rowid = db.add_message("chat-trivia", "@edward what did he say?", is_from_me=True, date=NOW)
    ctx = await context.gather(_ping(db, rowid, text="@edward what did he say?"), legacy=False, now=NOW)
    assert seen == [f"attachment:{a1}"]
    assert ctx.attachments[0]["mime_type"] == "text/plain"


async def test_one_slow_video_never_costs_the_photos(chat, no_web, tmp_path, monkeypatch):
    """Review 2026-10-11: every item shared one 25 s bound, so a video whose
    sound took long to transcribe dropped the photos too, and the trigger
    never said anything was missing. Each item has its own bound now, and
    what was still processing is named with its ``look`` call."""
    import asyncio

    from services.media import MediaView
    from services.media import present

    async def fake_view(ref, *, max_images=6):
        if ref == f"attachment:{video}":
            await asyncio.sleep(5)
        return MediaView(kind="image", images=[b"\xff\xd8jpeg-" + ref.encode()])

    monkeypatch.setattr(present, "view", fake_view)
    monkeypatch.setattr(context, "ITEM_TIMEOUT_S", 0.3)
    db = chat
    ask = "@edward what's in these?"
    rowid = db.add_message("chat-trivia", ask, is_from_me=True, date=NOW)
    p1 = db.add_attachment(rowid, filename=str(tmp_path / "a.png"), mime_type="image/png", transfer_name="a.png")
    p2 = db.add_attachment(rowid, filename=str(tmp_path / "b.png"), mime_type="image/png", transfer_name="b.png")
    video = db.add_attachment(rowid, filename=str(tmp_path / "clip.mov"), mime_type="video/quicktime",
                              transfer_name="clip.mov")
    ctx = await asyncio.wait_for(context.gather(_ping(db, rowid, text=ask), legacy=False, now=NOW), 3)
    names = [a["filename"] for a in ctx.attachments]
    assert any(f"attachment:{p1}" in n for n in names) and any(f"attachment:{p2}" in n for n in names)
    assert not any(f"attachment:{video}" in n for n in names)
    assert ctx.media["images"] == 2
    assert "Still processing" in ctx.trigger and f'look("attachment:{video}")' in ctx.trigger


async def test_the_image_budget_holds_across_items(chat, no_web, tmp_path, monkeypatch):
    from services.media import MediaView
    from services.media import present

    async def fake_view(ref, *, max_images=6):
        return MediaView(kind="image", images=[b"\xff\xd8f1", b"\xff\xd8f2", b"\xff\xd8f3"][:max_images])

    monkeypatch.setattr(present, "view", fake_view)
    monkeypatch.setattr(context, "MAX_IMAGES", 4)
    db = chat
    ask = "@edward gifs"
    rowid = db.add_message("chat-trivia", ask, is_from_me=True, date=NOW)
    for i in range(3):
        db.add_attachment(rowid, filename=str(tmp_path / f"g{i}.gif"), mime_type="image/gif",
                          transfer_name=f"g{i}.gif")
    ctx = await context.gather(_ping(db, rowid, text=ask), legacy=False, now=NOW)
    assert ctx.media["images"] == 4
