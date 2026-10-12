"""``send_message`` with a subject is an email from Ellis's Gmail (2026-10-11).

Ellis: email goes out when he asks, through ``send_message`` — the one
guarded send, never a second path. A subject (or ``chat_id`` = a mail id
from ``mail_search``, a reply) makes it an email; without either, ``to`` =
an address is still iMessage to an Apple ID, as before. Both channels share
every guard and differ only at the transport: exactly one recipient, a name
that resolves to one person with one address, never Ellis from a turn he is
not part of (his Gmail, ``GMAIL_ACCOUNT``, the addresses he sends from in
Mail.app), the turn may reach people, stand-downs and the recipient rule
outside his turns, the 10-minute dedupe, the dry run, the files rails, the
egress scan (``subject`` included). "Sent" only when Gmail labels it SENT.
Nothing here reaches Google, Messages or Mail: the Gmail service is a fake.
"""

from __future__ import annotations

import email
import email.policy
import re

import pytest

from services.graph import tools as legacy
from services.toolset import messaging as M
from services.toolset import policy as P
from services.toolset.result import ToolFailure
from tests.fixtures.fake_mail import FakeGmail, FakeHttpError, FakeMailStore, hours_ago

pytestmark = pytest.mark.unit

OWNER = P.TurnPolicy(source="chat", owner_asked=True, may_reach_people=True, may_push=True)
WAKE = P.TurnPolicy(source="mind", may_reach_people=True, may_push=True)
OWNERS_PHONE = "+12015550100"
SENT = "imap://11111111-2222-3333-4444-555555555555/Sent%20Messages"


@pytest.fixture
def world(monkeypatch, tmp_path):
    from services import contacts_service, imessage_service, mail_service

    legacy.reset_send_history()
    texts = []
    monkeypatch.setattr(imessage_service, "is_available", lambda: True)
    monkeypatch.setattr(imessage_service, "send_imessage_to_chat", lambda c, t, **k: texts.append((c, t)) or
                        {"success": True})
    monkeypatch.setattr(imessage_service, "send_imessage", lambda h, t: texts.append((h, t)) or {"success": True})
    if hasattr(imessage_service, "send_to_chat"):
        monkeypatch.setattr(imessage_service, "send_to_chat", lambda c, t, **k: texts.append((c, t)) or
                            {"success": True})
    contacts = {
        "parker": [{"name": "Parker Smith", "phones": ["+12015550111"], "emails": ["parker@example.com"]}],
        "matt": [{"name": "Matt A", "phones": [], "emails": ["matt.a@example.com"]},
                 {"name": "Matt B", "phones": [], "emails": ["matt.b@example.com"]}],
        "jo": [{"name": "Jo Park", "phones": [], "emails": ["jo@work.example", "jo@home.example"]}],
        "pat": [{"name": "Pat Lee", "phones": ["+12015550144"], "emails": []}],
    }
    monkeypatch.setattr(contacts_service, "lookup_contact",
                        lambda q: {"success": True, "matches": contacts.get(q.strip().lower(), [])})

    async def owner_handles():
        return [OWNERS_PHONE]

    async def nobody_waiting():
        return []

    async def no_standdown(target):
        return None

    monkeypatch.setattr(legacy, "_owner_handles", owner_handles)
    monkeypatch.setattr(legacy, "_chats_where_owner_is_waiting", nobody_waiting)
    monkeypatch.setattr(legacy, "_active_standdown_for", no_standdown)
    w = type("World", (), {})()
    w.texts = texts
    w.addressed, w.conversations, w.notified, w.windows = set(), set(), [], []

    async def addressed(chat, hours):
        return chat in w.addressed

    async def conversation(chat):
        return chat in w.conversations

    monkeypatch.setattr(P, "addressed_recently", addressed)
    monkeypatch.setattr(P, "in_conversation", conversation)

    async def notified(cid):
        w.notified.append(cid)

    async def window(*a, **k):
        w.windows.append(a)

    import services.conversation_service as cs
    from services.heartbeat import triage_service

    monkeypatch.setattr(cs, "mark_user_notified", notified)
    monkeypatch.setattr(triage_service, "register_listening_window_for_chat", window)
    monkeypatch.setattr(legacy, "get_current_conversation_id", lambda: "conv-1")
    monkeypatch.delenv("GMAIL_ACCOUNT", raising=False)
    w.gmail = FakeGmail("owner@gmail.example").install(monkeypatch)
    w.store = FakeMailStore(tmp_path).point(monkeypatch)
    monkeypatch.setattr(mail_service, "_mail_app_running", lambda: True)
    return w


async def send(policy, **kw):
    with P.policy_scope(policy):
        return await M.send_message_impl(**kw)


def sent_mime(world, i=-1):
    return email.message_from_bytes(world.gmail.sent[i]["raw"], policy=email.policy.default)


# ------------------------------------------------------------- the tool --

def test_the_signature_and_the_words():
    assert "subject" in M.send_message.args
    doc = M.send_message.description
    assert doc.startswith("Text or email someone — the only way to message anyone, signed as you.")
    assert "With subject it is an email from" in doc and "Ellis's Gmail" in doc


def test_what_counts_as_an_email():
    from services.toolset.mail import is_email_send

    assert is_email_send("Dinner", None)
    assert is_email_send(None, "gmail:abc") and is_email_send("", "gmail:thread:t1")
    assert is_email_send(None, "icloud:48211")
    assert not is_email_send("  ", None) and not is_email_send(None, "chat123")
    assert not is_email_send(None, None)


# ---------------------------------------------------------- sending one --

async def test_a_subject_makes_it_an_email(world):
    out = await send(OWNER, to="parker@example.com", subject="Dinner Friday", message="Are we on?")
    assert out == "Sent via Gmail to parker@example.com: “Dinner Friday” (from owner@gmail.example)"
    assert world.texts == [], "not a text"
    mime = sent_mime(world)
    assert mime["To"] == "parker@example.com" and mime["Subject"] == "Dinner Friday"
    from utils.message_signature import ensure_message_signature

    assert mime.get_content().strip() == ensure_message_signature("Are we on?").strip()
    assert world.gmail.sent[-1]["mimetype"] == "message/rfc822"
    assert world.gmail.sent[-1]["body"] == {}, "a new email starts its own thread"
    assert world.notified == ["conv-1"] and world.windows == [], "no iMessage listening window"
    assert legacy.looks_delivered(out)


async def test_an_address_without_a_subject_is_still_imessage(world):
    out = await send(OWNER, to="parker@example.com", message="hi")
    assert out.startswith("Sent via iMessage")
    assert world.gmail.sent == [] and world.texts


async def test_a_name_with_one_address(world):
    out = await send(OWNER, to="Parker", subject="Dinner", message="hi")
    assert "Parker Smith (parker@example.com)" in out
    assert sent_mime(world)["To"] == "parker@example.com"


@pytest.mark.parametrize("name,listed", [("Matt", ["matt.a@example.com", "matt.b@example.com"]),
                                         ("Jo", ["jo@work.example", "jo@home.example"])])
async def test_a_name_with_several_addresses_is_listed(world, name, listed):
    with pytest.raises(ToolFailure) as e:
        await send(OWNER, to=name, subject="Dinner", message="hi")
    for addr in listed:
        assert addr in str(e.value)
    assert world.gmail.sent == []


async def test_a_name_with_no_address(world):
    with pytest.raises(ToolFailure, match="email address"):
        await send(OWNER, to="Pat", subject="Dinner", message="hi")


async def test_a_chat_id_that_is_not_a_mail_id(world):
    with pytest.raises(ToolFailure, match="mail id"):
        await send(OWNER, chat_id="chat123", subject="Dinner", message="hi")


# -------------------------------------------------------------- replies --

async def test_a_gmail_reply_stays_in_its_thread(world):
    world.gmail.add("g1", thread="t1", sender="Parker Smith <parker@example.com>", subject="Dinner Friday?",
                    message_id_header="<abc@mail.example>", references="<root@mail.example>")
    out = await send(OWNER, chat_id="gmail:g1", message="Yes, 7pm.")
    assert out.startswith("Sent via Gmail to Parker Smith <parker@example.com>: “Re: Dinner Friday?”")
    assert world.gmail.sent[-1]["body"] == {"threadId": "t1"}
    mime = sent_mime(world)
    assert mime["To"] == "parker@example.com"
    assert mime["In-Reply-To"] == "<abc@mail.example>"
    assert mime["References"] == "<root@mail.example> <abc@mail.example>"
    assert mime["Subject"] == "Re: Dinner Friday?"


async def test_a_reply_goes_to_reply_to(world):
    world.gmail.add("g1", thread="t1", sender="Shop <noreply@shop.example>", subject="Your order",
                    reply_to="help@shop.example")
    await send(OWNER, chat_id="gmail:g1", message="Where is it?", subject="Order 123")
    mime = sent_mime(world)
    assert mime["To"] == "help@shop.example" and mime["Subject"] == "Order 123"


async def test_a_reply_to_a_thread_answers_its_newest_message(world):
    world.gmail.add("g1", thread="t1", sender="Parker <parker@example.com>", subject="Dinner",
                    internal_date=hours_ago(5), message_id_header="<one@x>")
    world.gmail.add("g2", thread="t1", sender="Dana <dana@example.com>", subject="Re: Dinner",
                    internal_date=hours_ago(1), message_id_header="<two@x>")
    await send(OWNER, chat_id="gmail:thread:t1", message="Count me in")
    mime = sent_mime(world)
    assert mime["To"] == "dana@example.com" and mime["In-Reply-To"] == "<two@x>"
    assert mime["Subject"] == "Re: Dinner", "never Re: Re:"


async def test_an_icloud_reply(world):
    rowid = world.store.add("parker@example.com", "Parker Smith", "Dinner Friday?", received=hours_ago(1),
                            message_id="<icloud-msg@example.com>")
    out = await send(OWNER, chat_id=f"icloud:{rowid}", message="Yes!")
    assert "Sent via Gmail" in out
    assert world.gmail.sent[-1]["body"] == {}, "an iCloud message has no Gmail thread"
    mime = sent_mime(world)
    assert mime["To"] == "parker@example.com" and mime["In-Reply-To"] == "<icloud-msg@example.com>"
    assert mime["Subject"] == "Re: Dinner Friday?"


async def test_a_reply_to_a_thread_owner_answered_last_goes_to_the_other_side(world):
    """2026-10-11 review: "reply to Parker's thread" emailed Ellis when Ellis's own
    reply was the newest message in it."""
    world.gmail.add("m1", thread="t1", sender="Parker Smith <parker@example.com>", subject="Dinner Friday?",
                    internal_date=hours_ago(5), message_id_header="<one@x>")
    world.gmail.add("m2", thread="t1", sender="Ellis Reed <owner@gmail.example>", to="Parker Smith <parker@example.com>",
                    subject="Re: Dinner Friday?", internal_date=hours_ago(2), message_id_header="<two@x>",
                    references="<one@x>", labels=("SENT",))
    world.gmail.add("m3", thread="t1", sender="Ellis Reed <owner@gmail.example>", to="parker@example.com",
                    subject="Re: Dinner Friday?", internal_date=hours_ago(1), message_id_header="<draft@x>",
                    labels=("DRAFT",))
    out = await send(OWNER, chat_id="gmail:thread:t1", message="Also, 7pm?")
    assert out.startswith("Sent via Gmail to Parker Smith <parker@example.com>: “Re: Dinner Friday?”")
    mime = sent_mime(world)
    assert mime["To"] == "parker@example.com"
    assert mime["In-Reply-To"] == "<two@x>", "threaded under the newest sent message, never a draft"
    assert mime["References"] == "<one@x> <two@x>"
    assert world.gmail.sent[-1]["body"] == {"threadId": "t1"}


async def test_a_reply_to_owners_own_email_goes_to_whom_he_wrote(world):
    world.gmail.add("m1", thread="t1", sender="Parker Smith <parker@example.com>", subject="Dinner Friday?",
                    internal_date=hours_ago(5))
    world.gmail.add("m2", thread="t1", sender="Ellis Reed <owner@gmail.example>",
                    to="Ellis Reed <owner@gmail.example>, Parker Smith <parker@example.com>", subject="Re: Dinner Friday?",
                    internal_date=hours_ago(1), message_id_header="<two@x>", labels=("SENT",))
    from services.toolset.mail import resolve_email_target

    address, who, ctx = await resolve_email_target("gmail:m2", None)
    assert (address, who) == ("parker@example.com", "Parker Smith <parker@example.com>")
    assert ctx["in_reply_to"] == "<two@x>" and ctx["thread_id"] == "t1"
    address, _who, _ctx = await resolve_email_target("gmail:thread:t1", None)
    assert address == "parker@example.com"
    # Outside Ellis's turns it is an email to Parker — the recipient rule's
    # question, never the "tell_owner" refusal.
    with pytest.raises(ToolFailure, match="Ellis's call"):
        await send(WAKE, chat_id="gmail:m2", message="noted")
    world.addressed.add("parker@example.com")
    assert (await send(WAKE, chat_id="gmail:m2", message="noted")).startswith("Sent via Gmail to Parker Smith")


async def test_an_icloud_reply_to_owners_sent_mail_goes_to_whom_he_wrote(world):
    rowid = world.store.add("owner@icloud.example", "Ellis Reed", "Dinner Friday?", received=hours_ago(1),
                            mailbox=SENT, to="Parker Smith <parker@example.com>", message_id="<sent@icloud>")
    from services.toolset.mail import resolve_email_target

    address, who, ctx = await resolve_email_target(f"icloud:{rowid}", None)
    assert (address, who) == ("parker@example.com", "Parker Smith <parker@example.com>")
    assert ctx["in_reply_to"] == "<sent@icloud>"


# ------------------------------------------------------------ never Ellis --

async def test_never_email_owner_from_a_turn_he_is_not_part_of(world, monkeypatch):
    monkeypatch.setenv("GMAIL_ACCOUNT", "owner.work@gmail.example")
    world.store.add("owner@icloud.example", "Ellis Reed", "re: plans", mailbox=SENT, write_file=False)
    from services import mail_service

    mail_service.reset_for_tests()
    for to in ("owner.work@gmail.example", "OWNER@gmail.example", "owner@icloud.example", "Ellis"):
        with pytest.raises(ToolFailure, match="tell_owner"):
            await send(WAKE, to=to, subject="Biscuit's pill", message="late")
    assert world.gmail.sent == []
    out = await send(OWNER, to="owner.work@gmail.example", subject="Note to self", message="the address")
    assert out.startswith("Sent via Gmail")


async def test_a_reply_to_owners_own_email_is_refused_from_a_wake(world):
    world.gmail.add("g1", thread="t1", sender="Ellis Reed <owner@gmail.example>", subject="Plans")
    world.addressed.add("owner@gmail.example")
    with pytest.raises(ToolFailure, match="tell_owner"):
        await send(WAKE, chat_id="gmail:g1", message="noted")
    assert world.gmail.sent == []


# ----------------------------------------------- the rules outside Ellis --

async def test_the_recipient_rule_holds_outside_owners_turns(world):
    with pytest.raises(ToolFailure, match="Ellis's call"):
        await send(WAKE, to="stranger@example.com", subject="Hello", message="hi")
    assert world.gmail.sent == []
    world.addressed.add("parker@example.com")
    out = await send(WAKE, to="parker@example.com", subject="Hello", message="hi")
    assert out.startswith("Sent via Gmail")


async def test_a_quiet_turn_emails_no_one(world):
    with pytest.raises(ToolFailure, match="can't message people"):
        await send(P.TurnPolicy(source="pulse"), to="parker@example.com", subject="Hello", message="hi")


async def test_a_stand_down_binds_email_too(world, monkeypatch):
    from datetime import datetime, timezone

    world.addressed.add("parker@example.com")

    class Row:
        requested_by = "Parker"
        requested_at = datetime.now(timezone.utc)

    async def standdown(target):
        return Row() if target == "parker@example.com" else None

    monkeypatch.setattr(legacy, "_active_standdown_for", standdown)
    with pytest.raises(ToolFailure, match="stand-down"):
        await send(WAKE, to="parker@example.com", subject="Hello", message="hi")


async def test_a_friends_ping_cannot_email_a_stranger(world, monkeypatch):
    from services.pings import hooks, scope

    async def no_one(chat, hours=24):
        return False

    monkeypatch.setattr(scope, "_addressed_recently", no_one)
    ctx = hooks.new_context(ping_id=7, chat_identifier="chat-trivia", chat_guid="iMessage;+;chat-trivia", is_group=True,
                            conversation_id="ping-7", must_answer=True, third_party=True)
    token = hooks.CURRENT_PING.set(ctx)
    try:
        assert await scope.refusal("send_message", {"to": "stranger@example.com", "subject": "Hi",
                                                    "message": "x"})
        assert await scope.refusal("send_message", {"chat_id": "gmail:g1", "message": "x"})
        assert await scope.refusal("messages", {"action": "mail_search", "query": "x"}) is None
    finally:
        hooks.CURRENT_PING.reset(token)


# ----------------------------------------------- dedupe, dry run, delivery --

async def test_the_same_email_twice_is_sent_once(world):
    from services import turn_ledger

    rec = turn_ledger.begin_turn(conversation_id="c", source="chat", model="m", provider="p", message="x")
    try:
        await send(OWNER, to="parker@example.com", subject="Dinner", message="Are we on?")
        out = await send(OWNER, to="parker@example.com", subject="Dinner", message="Are we on?")
    finally:
        turn_ledger.end_turn(rec, "completed")
    assert out.startswith("Already sent to parker@example.com at") and out.endswith("not sending again.")
    assert len(world.gmail.sent) == 1


async def test_a_dry_run_records_and_sends_nothing(world):
    policy = P.TurnPolicy(source="mind", may_reach_people=True, reply_to=("parker@example.com",),
                          dry_run_outward=True)
    out = await send(policy, to="parker@example.com", subject="Dinner", message="Are we on?")
    assert out.startswith("[dry run] Would have emailed parker@example.com: “Dinner”")
    assert world.gmail.sent == []


async def test_sent_only_when_gmail_labels_it_sent(world):
    world.gmail.send_labels = None  # Gmail's reply left the labels out: one minimal get
    out = await send(OWNER, to="parker@example.com", subject="One", message="a")
    assert out.startswith("Sent via Gmail")
    assert ("get", "sent-1", "minimal") in world.gmail.calls
    world.gmail.labels_after_get = []
    with pytest.raises(ToolFailure, match="Gmail couldn't send it"):
        await send(OWNER, to="parker@example.com", subject="Two", message="b")


async def test_a_gmail_error_is_an_honest_failure(world):
    world.gmail.send_error = FakeHttpError(403, "Forbidden")
    with pytest.raises(ToolFailure, match="Gmail couldn't send it: 403 Forbidden"):
        await send(OWNER, to="parker@example.com", subject="Dinner", message="hi")


async def test_gmail_not_connected(world, monkeypatch):
    FakeGmail().install(monkeypatch, connected=False)
    with pytest.raises(ToolFailure, match=re.escape("I can only send email from Ellis's Gmail, and it isn't "
                                                    "connected — Ellis connects it in Settings → Gmail.")):
        await send(OWNER, to="parker@example.com", subject="Dinner", message="hi")


# ------------------------------------------------------------------ files --

async def test_attachments_become_mime_parts(world, tmp_path):
    pdf = tmp_path / "menu.pdf"
    pdf.write_bytes(b"%PDF-1.4 menu")
    out = await send(OWNER, to="parker@example.com", subject="Menu", message="attached", files=[str(pdf)])
    assert out.endswith("(from owner@gmail.example, 1 file)")
    parts = [p for p in sent_mime(world).iter_attachments()]
    assert [(p.get_filename(), p.get_content_type()) for p in parts] == [("menu.pdf", "application/pdf")]
    assert parts[0].get_content() == b"%PDF-1.4 menu"


async def test_more_than_twenty_megabytes_is_refused(world, tmp_path):
    big = tmp_path / "video.mov"
    with open(big, "wb") as f:
        f.seek(21 * 1024 * 1024)
        f.write(b"\0")
    with pytest.raises(ToolFailure, match="20 MB"):
        await send(OWNER, to="parker@example.com", subject="Video", message="here", files=[str(big)])
    assert world.gmail.sent == []


async def test_autonomous_turns_attach_only_what_edward_made(world, tmp_path, monkeypatch):
    home = tmp_path / "edward-home"
    home.mkdir()
    owners = tmp_path / "taxes.pdf"
    owners.write_bytes(b"%PDF")
    monkeypatch.setenv("EDWARD_HOME", str(home))
    world.addressed.add("parker@example.com")
    with pytest.raises(ToolFailure, match="edward-home"):
        await send(WAKE, to="parker@example.com", subject="Taxes", message="here", files=[str(owners)])


# ------------------------------------------------- what the others read --

def test_the_egress_scan_reads_the_subject():
    from services import secret_scan

    assert secret_scan.egress_args("send_message", {"to": "a@b.c", "subject": "s", "message": "m"}) == (
        "message", "files", "subject")


def test_the_work_rail_says_email():
    from services.graph.streaming import _summarize_toolset_args

    trunc = lambda s, n: s[:n]  # noqa: E731
    assert _summarize_toolset_args("send_message", {"to": "parker@example.com", "subject": "S", "message": "m"},
                                   trunc) == "email: parker@example.com"
    assert _summarize_toolset_args("send_message", {"chat_id": "gmail:g1", "message": "m"}, trunc) == "email: a reply"
    assert _summarize_toolset_args("send_message", {"to": "Parker", "message": "m"}, trunc) == "to: Parker"


def test_the_journal_says_emailed_and_counts_it_as_reached():
    from services import thought_journal as tj
    from services.toolset.names import legacy_view

    args = {"to": "parker@example.com", "subject": "Dinner", "message": "Are we on?"}
    result = "Sent via Gmail to parker@example.com: “Dinner” (from owner@gmail.example)"
    assert legacy_view("send_message", args) == ("mail", {"to": "parker@example.com", "subject": "Dinner"})
    assert legacy_view("send_message", {"chat_id": "gmail:g1", "message": "x"}) == (
        "mail", {"to": "a reply", "subject": None})
    line = tj.describe_action({"name": "send_message", "args": args, "result": result, "ok": True})
    assert line == "Emailed parker@example.com: “Dinner”"
    assert tj.action_ok("send_message", result) == (True, False)
    assert tj.reached_someone([{"name": "send_message", "args": args, "ok": True}])
    assert tj.action_tier("send_message", args) == "outward"


# --------------------------------------------- sent, but not confirmed --

async def test_send_gmail_says_when_a_send_is_unconfirmed(monkeypatch):
    """2026-10-11 review: Gmail took the send (it has an id) but the
    labels couldn't be read back — the email went out."""
    from services import gmail_service

    g = FakeGmail().install(monkeypatch)
    g.send_labels = None
    g.confirm_error = ConnectionError("network blip")
    out = await gmail_service.send_gmail("parker@example.com", "Dinner", "Are we on?")
    assert out["delivered"] is False and out["unconfirmed"] is True and out["id"] == "sent-1"
    assert "sent but unconfirmed (id sent-1)" in out["error"]
    assert len(g.sent) == 1


async def test_an_unconfirmed_email_is_never_sent_twice(world):
    from services import turn_ledger

    world.gmail.send_labels = None
    world.gmail.confirm_error = ConnectionError("network blip")
    rec = turn_ledger.begin_turn(conversation_id="c", source="chat", model="m", provider="p", message="x")
    try:
        with pytest.raises(ToolFailure) as e:
            await send(OWNER, to="parker@example.com", subject="Dinner", message="Are we on?")
        assert "Gmail took it (id sent-1) but didn't confirm SENT — don't resend; check Sent" in str(e.value)
        out = await send(OWNER, to="parker@example.com", subject="Dinner", message="Are we on?")
    finally:
        turn_ledger.end_turn(rec, "completed")
    assert out.startswith("Already sent to parker@example.com at")
    assert len(world.gmail.sent) == 1


# ----------------------------------- Ellis's waiting self-chat is not email --

async def test_owners_waiting_self_chat_never_lets_a_wake_email_him(world, monkeypatch):
    """2026-10-11 review: the exception that lets a reply go into Ellis's
    self-chat where his ping waits (or the chat this turn was asked in) is
    for a text there — never an email to that address."""
    async def owner_handles():
        return [OWNERS_PHONE, "owner@icloud.example"]

    async def waiting():
        return ["owner@icloud.example"]

    monkeypatch.setattr(legacy, "_owner_handles", owner_handles)
    monkeypatch.setattr(legacy, "_chats_where_owner_is_waiting", waiting)
    with pytest.raises(ToolFailure, match="tell_owner"):
        await send(WAKE, to="owner@icloud.example", subject="Biscuit's pill", message="logged")
    asked_there = P.TurnPolicy(source="mind", may_reach_people=True, reply_to=("owner@icloud.example",))
    with pytest.raises(ToolFailure, match="tell_owner"):
        await send(asked_there, to="owner@icloud.example", subject="Biscuit's pill", message="logged")
    assert world.gmail.sent == []
    out = await send(WAKE, chat_id="owner@icloud.example", message="logged")
    assert out.startswith("Sent via iMessage"), "a text into his waiting self-chat still goes"


async def test_the_executor_knows_owners_email_addresses_too(world, monkeypatch):
    """The executor's never-text-Ellis check (legacy sends) counts Ellis's Gmail
    and Mail.app addresses, not only his iMessage handles."""
    from types import SimpleNamespace

    from services import turn_ledger

    monkeypatch.setattr(turn_ledger, "current_turn", lambda: SimpleNamespace(source="mind", id="t1"))
    out = await legacy.text_to_owner_refusal("send_imessage", {"contact": "OWNER@gmail.example", "message": "x"})
    assert out == legacy.TEXT_TO_OWNER_REFUSAL
    assert await legacy.text_to_owner_refusal("send_imessage", {"contact": "parker@example.com", "message": "x"}) is None
