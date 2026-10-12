"""The mind's side of AREA 3's pings and media (pings-spec §4.1; proactivity
spec §3.2.3; 2026-10-11).

Pings — an ``@Edward``, a reply to his bubble, the owner's self-chat — are
answered by AREA 3's reply path, never by a wake. The mind still has to
*see* them, and must never mistake its own words for the owner's:

* the pack lists recent pings and how each ended (``pings.hooks.pack_lines``),
  right after "Recent messages";
* a message line that is a ping says so: `` (ping — answered)``;
* ``speaker()`` trusts the reader's structural ``by_edward`` before the
  signature guess;
* an escalation carries the message's full text (``media.present.event_text``
  when AREA 3's media module is there, else ``raw_data.full_text``), not the
  200-character summary;
* rule 6 of the mind prompt says pings are not its to answer, and that the
  owner reads their own texts within minutes.

Every hook is a lazy import: without AREA 3 the pack is what it was.
"""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timezone

import pytest

from tests.area1_fakes import fake_module, hide_pings, install_pings_hooks
from tests.test_mind_service import _pack

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 7, 0, 30, tzinfo=timezone.utc)


def test_speaker_trusts_by_edward():
    from services.mind_service import speaker

    assert speaker({"is_from_me": True, "text": "on it, unsigned", "by_edward": True}) == "Edward"
    assert speaker({"is_from_me": True, "text": "omw", "by_edward": False}) == "Ellis"
    assert speaker({"is_from_me": True, "text": "omw"}) == "Ellis"
    assert speaker({"is_from_me": False, "sender": "Parker", "text": "hi", "by_edward": True}) == "Parker", \
        "only a from-me line can be Edward's"


def test_a_ping_line_says_so():
    from services.mind_service import render_pack

    text = render_pack(_pack(messages=[
        {"sender": "Parker", "chat_identifier": "chat-fest", "chat_name": "Festival crew", "is_from_me": False,
         "text": "@edward look at used skis", "timestamp": "2026-10-07T00:28:00", "ping_status": "answered"},
        {"sender": "Parker", "chat_identifier": "chat-fest", "is_from_me": False, "text": "nice",
         "timestamp": "2026-10-07T00:29:00"},
    ]))
    assert "@edward look at used skis (ping — answered)" in text
    assert "nice (ping" not in text


def test_the_pack_lists_pings_after_recent_messages():
    from services.mind_service import render_pack

    lines = ['7:28 pm Ellis in Festival crew "@edward look at used tickets…" → answered 7:29 pm (61 s)']
    text = render_pack(_pack(ping_lines=lines))
    recent = text.index("Recent messages")
    pings = text.index("Pings")
    assert recent < pings
    assert "answered by your reply path" in text[pings:pings + 200]
    assert lines[0] in text
    assert "Pings" not in render_pack(_pack())


async def test_ping_lines_come_from_the_hooks(monkeypatch):
    import services.mind_service as ms

    hooks = install_pings_hooks(monkeypatch, lines=["a line"])
    assert await ms._ping_lines_source(NOW) == ["a line"]
    assert hooks.test_calls == [("pack_lines", 6)]


async def test_no_pings_module_no_lines(monkeypatch):
    import services.mind_service as ms

    hide_pings(monkeypatch)
    assert await ms._ping_lines_source(NOW) == []


def _row(**kw):
    base = {"summary": "short summary", "raw_data": None}
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_event_text_prefers_media_then_full_text_then_summary(monkeypatch):
    import services.mind_service as ms

    fake_module(monkeypatch, "services.media.present", None)
    full = "x" * 900 + " the end"
    assert ms.event_text(_row(raw_data=json.dumps({"full_text": full}))) == full
    assert ms.event_text(_row()) == "short summary"
    assert ms.event_text(_row(raw_data="not json")) == "short summary"
    assert len(ms.event_text(_row(raw_data=json.dumps({"full_text": "y" * 9000})))) == ms.EVENT_TEXT_MAX

    present = types.ModuleType("services.media.present")
    present.event_text = lambda row: "described: a photo of skis"
    media = types.ModuleType("services.media")
    media.__path__ = []
    fake_module(monkeypatch, "services.media", media)
    fake_module(monkeypatch, "services.media.present", present)
    assert ms.event_text(_row(raw_data=json.dumps({"full_text": full}))) == "described: a photo of skis"

    def broken(row):
        raise RuntimeError("media down")

    present.event_text = broken
    assert ms.event_text(_row(raw_data=json.dumps({"full_text": full}))) == full


def test_escalations_render_their_full_text():
    from services.mind_service import render_pack

    long = "Can you bring the cooler, the folding chairs and " + "the long table " * 30 + "THE-END"
    text = render_pack(_pack(reason="mention", escalations=[
        {"id": "ev-1", "sender": "Parker", "chat": "Family", "text": long, "at": "2026-10-07T00:20:00"}]))
    assert "THE-END" in text


def test_rule_six_is_the_pings_rule():
    from services.mind_service import MIND_SYSTEM_PROMPT

    six = next(line for line in MIND_SYSTEM_PROMPT.splitlines() if line.startswith("6. "))
    assert "Pings are not yours here." in six
    assert "answered by your reply path, never in a wake" in six
    assert "Since Ellis reads their own texts within minutes, never tell them what a message they have read says." in six


def test_rule_six_never_contradicts_rule_three_in_a_mention_wake():
    """Review 2026-10-11: with pings off (``EDWARD_PINGS=off``, the
    rollback) the mention wake is the only path that answers an @Edward;
    rule 3 says it must reply, so rule 6 exempts the mention it was woken
    for — the conflict the judgement check's ``mention_answerable`` guards."""
    from services.mind_service import MIND_SYSTEM_PROMPT

    lines = MIND_SYSTEM_PROMPT.splitlines()
    six = next(line for line in lines if line.startswith("6. "))
    three = next(line for line in lines if line.startswith("3. "))
    assert "[WAKE: mention]" in three and "must reply" in three
    assert "unless this wake was woken for that mention (`[WAKE: mention]`" in six


def test_the_act_line_names_tell_owner_and_keeps_the_never_text_owner_rule():
    from services.mind_service import MIND_SYSTEM_PROMPT

    act = next(line for line in MIND_SYSTEM_PROMPT.splitlines() if line.startswith("- **Act**"))
    assert "`tell_owner`" in act
    never = act.split("never by", 1)[1]
    for tool in ("send_message", "send_imessage", "send_sms", "send_whatsapp"):
        assert f"`{tool}`" in never, tool
    assert "except to answer them in their own chat when they have just messaged you there" in never
    assert "This Mac's screen (if one is set up) is yours" in MIND_SYSTEM_PROMPT
