"""``messages(mail_search | mail_read)`` — Ellis's email, in every turn (2026-10-11).

Few restrictions on Edward, by design: mail reads have no gate — every
surface and every turn may search and read iCloud mail (Mail on the Mac,
its Envelope Index and ``.emlx`` files) and Gmail (the API).
What the reads must be is honest: a query matches the sender (address or
name) or the subject, in every mailbox unless one is named, read and unread
alike unless asked; Gmail's search operators work there and are cut down
for iCloud; a stale index or a stopped Mail.app is said, never passed off
as an empty inbox; Gmail not connected or signed out is said in Ellis's
words. ``mail_read`` prints the headers, a reply hint, the attachments and
the body as text (HTML converted, capped). Nothing here reads Ellis's mail or
reaches Google.
"""

from __future__ import annotations

import re

import pytest

from services.toolset import messaging as M
from services.toolset.result import ToolFailure
from tests.fixtures.fake_mail import FakeGmail, FakeMailStore, hours_ago

pytestmark = pytest.mark.unit

ARCHIVE = "imap://11111111-2222-3333-4444-555555555555/Archive"


@pytest.fixture
def store(tmp_path, monkeypatch):
    from services import mail_service

    s = FakeMailStore(tmp_path).point(monkeypatch)
    s.running = True
    monkeypatch.setattr(mail_service, "_mail_app_running", lambda: s.running)
    FakeGmail().install(monkeypatch, connected=False)
    return s


async def search(**kw):
    return await M.messages_impl(action="mail_search", **kw)


async def read(mail_id):
    return await M.messages_impl(action="mail_read", id=mail_id)


def ids(text):
    return re.findall(r"id=(\S+)", text)


# ------------------------------------------------------------- the tool --

def test_the_actions_and_the_signature():
    assert M.MESSAGES_ACTIONS == ("recent", "thread", "participants", "who", "stand_down", "mail_search",
                                  "mail_read")
    schema = M.messages.args
    for arg in ("id", "account", "mailbox", "unread_only"):
        assert arg in schema
    assert schema["limit"].get("default") is None
    doc = M.messages.description
    assert "mail_search" in doc and "mail_read" in doc and "Gmail search syntax" in doc


async def test_thread_still_reads_40_lines_by_default(monkeypatch):
    from services.graph import tools as legacy

    seen = {}

    class Fake:
        async def ainvoke(self, args):
            seen.update(args)
            return "thread text"

    monkeypatch.setattr(legacy, "read_imessage_thread", Fake())
    assert await M.messages_impl(action="thread", chat_id="chat1") == "thread text"
    assert seen["limit"] == 40


# ---------------------------------------------------------------- iCloud --

async def test_a_query_matches_the_sender_or_the_subject(store):
    a = store.add("parker@example.com", "Parker Smith", "Dinner Friday?", received=hours_ago(1))
    b = store.add("deals@pots.example", "Pottery Barn", "Parker pots on sale", received=hours_ago(2))
    store.add("bob@example.com", "Bob", "Lunch", received=hours_ago(3))
    out = await search(query="parker", account="icloud")
    assert ids(out) == [f"icloud:{a}", f"icloud:{b}"], "newest first"
    assert "Parker Smith <parker@example.com>" in out and "Dinner Friday?" in out
    assert ids(await search(query="smith", account="icloud")) == [f"icloud:{a}"], "the sender's name counts"


async def test_every_word_must_match_somewhere(store):
    a = store.add("parker@example.com", "Parker Smith", "Dinner Friday?", received=hours_ago(1))
    store.add("parker@example.com", "Parker Smith", "Lunch", received=hours_ago(2))
    assert ids(await search(query="parker dinner", account="icloud")) == [f"icloud:{a}"]


async def test_every_mailbox_unless_one_is_named(store):
    inbox = store.add("parker@example.com", "Parker", "Dinner", received=hours_ago(1))
    archived = store.add("parker@example.com", "Parker", "Old plans", received=hours_ago(2), mailbox=ARCHIVE)
    assert ids(await search(query="parker", account="icloud")) == [f"icloud:{inbox}", f"icloud:{archived}"]
    out = await search(query="parker", account="icloud", mailbox="inbox")
    assert ids(out) == [f"icloud:{inbox}"] and "iCloud (inbox)" in out
    assert ids(await search(query="parker", account="icloud", mailbox="archive")) == [f"icloud:{archived}"]
    assert "iCloud/Archive" in await search(query="old plans", account="icloud")


async def test_an_unknown_mailbox_names_the_ones_there_are(store):
    store.add("parker@example.com", "Parker", "Dinner", received=hours_ago(1))
    with pytest.raises(ToolFailure, match="INBOX"):
        await search(query="parker", account="icloud", mailbox="Receipts")


async def test_read_mail_counts_unless_unread_only(store):
    unread = store.add("parker@example.com", "Parker", "Dinner", received=hours_ago(1))
    seen = store.add("parker@example.com", "Parker", "Lunch", received=hours_ago(2), read=1)
    out = await search(query="parker", account="icloud")
    assert ids(out) == [f"icloud:{unread}", f"icloud:{seen}"]
    lines = [line for line in out.splitlines() if line.startswith("- ")]
    assert "· unread ·" in lines[0] and "· unread ·" not in lines[1]
    assert ids(await search(query="parker", account="icloud", unread_only=True)) == [f"icloud:{unread}"]


async def test_deleted_mail_is_not_found(store):
    store.add("parker@example.com", "Parker", "Dinner", received=hours_ago(1), deleted=1)
    out = await search(query="parker", account="icloud")
    assert out.startswith("No email matching")


async def test_gmail_operators_are_cut_down_for_icloud(store):
    unread = store.add("parker@example.com", "Parker", "Dinner", received=hours_ago(1))
    store.add("parker@example.com", "Parker", "Lunch", received=hours_ago(2), read=1)
    store.add("parker@example.com", "Parker", "Old", received=hours_ago(3), mailbox=ARCHIVE)
    out = await search(query="from:parker is:unread in:inbox has:attachment", account="icloud")
    assert ids(out) == [f"icloud:{unread}"]


def test_icloud_terms():
    from services.toolset.mail import _icloud_terms

    assert _icloud_terms('from:parker subject:"dinner friday" is:unread') == ("parker dinner friday", True, None)
    assert _icloud_terms("in:inbox newer_than:2d receipts") == ("receipts", False, "inbox")
    assert _icloud_terms("") == ("", False, None)


async def test_no_match_is_a_success(store):
    out = await search(query="zebra", account="icloud")
    assert out.startswith('No email matching "zebra"')


async def test_mail_app_not_running_is_said(store):
    store.add("parker@example.com", "Parker", "Dinner", received=hours_ago(1))
    store.running = False
    out = await search(query="parker", account="icloud")
    assert "(iCloud: Mail.app isn't running on this Mac, so new iCloud mail isn't arriving — the newest it has " \
           "is from" in out


async def test_a_stale_index_is_said(store):
    store.add("parker@example.com", "Parker", "Dinner", received=hours_ago(40))
    out = await search(query="parker", account="icloud")
    assert "(iCloud: the newest mail in Mail's index is from" in out and "it may be behind.)" in out


async def test_an_unreadable_index(store, monkeypatch, tmp_path):
    from services import email_management_service

    monkeypatch.setattr(email_management_service, "MAIL_DB_PATH", str(tmp_path / "gone" / "Envelope Index"))
    with pytest.raises(ToolFailure, match="Mail's index"):
        await search(query="parker", account="icloud")


async def test_an_unknown_account_is_refused(store):
    with pytest.raises(ToolFailure, match="icloud, gmail or both"):
        await search(query="parker", account="yahoo")


# ----------------------------------------------------------------- Gmail --

async def test_both_accounts_merge_newest_first(store, monkeypatch):
    a = store.add("parker@example.com", "Parker", "Dinner", received=hours_ago(3))
    g = FakeGmail("owner@gmail.example").install(monkeypatch)
    g.add("g1", thread="t1", sender="Parker <parker@example.com>", subject="Re: Dinner", internal_date=hours_ago(1))
    g.add("g2", thread="t2", sender="Parker <parker@example.com>", subject="Photos", internal_date=hours_ago(5),
          labels=("INBOX",))
    out = await search(query="parker")
    assert ids(out) == ["gmail:g1", f"icloud:{a}", "gmail:g2"]
    assert out.splitlines()[0] == ('Email matching "parker" — iCloud (all mailboxes) + Gmail (owner@gmail.example), '
                                   'newest first:')
    assert "(thread gmail:thread:t1)" in out
    lines = [line for line in out.splitlines() if line.startswith("- ")]
    assert "· unread ·" in lines[0] and "· unread ·" not in lines[2]


async def test_gmails_query_carries_the_filters(store, monkeypatch):
    g = FakeGmail().install(monkeypatch)
    await search(query="from:parker", account="gmail", unread_only=True, mailbox="inbox")
    await search(query="receipts", account="gmail", mailbox="Travel")
    await search(query="receipts", account="gmail", mailbox="Travel Plans")
    qs = [c[1] for c in g.calls if c[0] == "list"]
    assert qs == ["from:parker is:unread in:inbox", "receipts label:Travel", 'receipts label:"Travel Plans"']


@pytest.mark.parametrize("mailbox", ["Travel Plans", "sent"])
async def test_a_mailbox_only_gmail_has_still_lists_the_gmail_hits(store, monkeypatch, mailbox):
    """2026-10-11 review: with account=both, a mailbox Mail.app doesn't have
    (a Gmail label, "sent") threw the Gmail results away."""
    store.add("parker@example.com", "Parker", "Dinner", received=hours_ago(1))
    g = FakeGmail("owner@gmail.example").install(monkeypatch)
    g.add("g1", thread="t1", sender="Parker <parker@example.com>", subject="Trip", labels=("Label_7",))
    out = await search(query="parker", mailbox=mailbox)
    assert ids(out) == ["gmail:g1"]
    assert out.splitlines()[0].startswith('Email matching "parker" — Gmail (owner@gmail.example)')
    assert f"(iCloud: no mailbox named {mailbox!r} in Mail — there are: INBOX.)" in out
    with pytest.raises(ToolFailure, match="no mailbox named"):
        await search(query="parker", mailbox=mailbox, account="icloud")


async def test_gmail_not_connected(store):
    store.add("parker@example.com", "Parker", "Dinner", received=hours_ago(1))
    with pytest.raises(ToolFailure, match=re.escape("Gmail isn't connected — Ellis connects it in Settings → Gmail.")):
        await search(query="parker", account="gmail")
    out = await search(query="parker")
    assert "(Gmail isn't connected — Ellis connects it in Settings → Gmail.)" in out
    assert out.splitlines()[0].startswith('Email matching "parker" — iCloud (all mailboxes), newest first')


async def test_gmail_signed_out(store, monkeypatch):
    from services import gmail_service

    FakeGmail().install(monkeypatch)

    async def lapsed(*_a, **_k):
        return [{"error": "Gmail not configured"}]

    monkeypatch.setattr(gmail_service, "search_gmail", lapsed)
    monkeypatch.setattr(gmail_service, "_last_refresh_error", "RefreshError: invalid_grant")
    with pytest.raises(ToolFailure, match="Gmail's sign-in has lapsed — Ellis reconnects it in Settings → Gmail."):
        await search(query="parker", account="gmail")


async def test_neither_side_available(store, monkeypatch, tmp_path):
    from services import email_management_service

    monkeypatch.setattr(email_management_service, "MAIL_DB_PATH", str(tmp_path / "gone" / "Envelope Index"))
    with pytest.raises(ToolFailure) as e:
        await search(query="parker")
    assert "Mail's index" in str(e.value) and "Gmail isn't connected" in str(e.value)


# ------------------------------------------------------------- mail_read --

async def test_reading_an_icloud_email_converts_html_and_caps_it(store):
    html = "<html><body><p>Hi Ellis,</p><p>" + ("word " * 2000) + "</p><a href='https://menu.example/x'>menu</a>" \
           "<p>https://menu.example/x https://tickets.example/y</p></body></html>"
    rowid = store.add("parker@example.com", "Parker Smith", "Dinner Friday?", received=hours_ago(1), body=None,
                      html=html, attachments=[("menu.pdf", b"x" * 2048)])
    out = await read(f"icloud:{rowid}")
    head, _, body = out.partition("\n---\n")
    assert "From: Parker Smith <parker@example.com>" in head
    assert "To: owner@icloud.example" in head and "Subject: Dinner Friday?" in head
    assert re.search(r"Date: \w{3} \d{1,2} \d{1,2}:\d{2} [ap]m", head)
    assert f'Reply: send_message(chat_id="icloud:{rowid}"' in head
    assert "menu.pdf (2 KB)" in head
    assert body.startswith("Hi Ellis,") and "<p>" not in body
    assert re.search(r"\[cut: \d+ more characters\]", body)
    assert "https://tickets.example/y" in body


async def test_a_body_mail_has_not_downloaded(store):
    missing = store.add("parker@example.com", "Parker", "Dinner", received=hours_ago(1), write_file=False)
    out = await read(f"icloud:{missing}")
    assert "Mail.app hasn't downloaded this message's body yet" in out
    assert "From: Parker <parker@example.com>" in out or "parker@example.com" in out


async def test_a_partial_emlx_still_reads_its_text(store):
    rowid = store.add("parker@example.com", "Parker", "Dinner", received=hours_ago(1), body="See you at 7.",
                      partial=True)
    out = await read(f"icloud:{rowid}")
    assert "See you at 7." in out


async def test_a_partial_emlx_without_text(store):
    rowid = store.add("parker@example.com", "Parker", "Photo", received=hours_ago(1), body=None, partial=True,
                      attachments=[("IMG_1.heic", b"x" * 10)])
    out = await read(f"icloud:{rowid}")
    assert "Mail.app hasn't downloaded this message's body yet" in out


async def test_an_icloud_id_that_isnt_there(store):
    with pytest.raises(ToolFailure, match="icloud:999"):
        await read("icloud:999")


async def test_reading_a_gmail_message(store, monkeypatch):
    g = FakeGmail().install(monkeypatch)
    g.add("g1", thread="t1", sender="Parker <parker@example.com>", subject="Dinner", body="Are we on?",
          attachments=[("menu.pdf", 4096)])
    out = await read("gmail:g1")
    assert "From: Parker <parker@example.com>" in out and "Subject: Dinner" in out
    assert "Are we on?" in out and "menu.pdf (4 KB)" in out
    assert "gmail:thread:t1" in out and 'send_message(chat_id="gmail:g1"' in out


async def test_a_gmail_html_body_reads_as_text(store, monkeypatch):
    g = FakeGmail().install(monkeypatch)
    g.add("g1", body=None, html="<div>Your <b>order</b> shipped&nbsp;today</div>")
    out = await read("gmail:g1")
    assert "Your order shipped today" in out


async def test_reading_a_gmail_thread(store, monkeypatch):
    g = FakeGmail().install(monkeypatch)
    g.add("g1", thread="t1", subject="Dinner", body="first " * 1500, internal_date=hours_ago(5))
    g.add("g2", thread="t1", subject="Re: Dinner", body="second " * 1500, internal_date=hours_ago(4))
    g.add("g3", thread="t1", subject="Re: Dinner", body="third " * 1500, internal_date=hours_ago(3))
    g.add("g4", thread="t1", subject="Re: Dinner", body="fourth " * 1500, internal_date=hours_ago(2))
    g.add("g5", thread="t1", subject="Re: Dinner", body="newest reply", internal_date=hours_ago(1))
    out = await read("gmail:thread:t1")
    assert out.index("id=gmail:g2") < out.index("id=gmail:g5"), "oldest first"
    assert "newest reply" in out and "fourth" in out, "the newest are kept"
    assert len(out) < 16_000 + 3_000
    assert "first first" not in out, "the oldest body is cut to fit"


async def test_mail_read_needs_a_mail_id(store):
    with pytest.raises(ToolFailure, match="mail_read needs id"):
        await M.messages_impl(action="mail_read")
    with pytest.raises(ToolFailure, match="icloud:<n>"):
        await read("48211")


async def test_chat_id_is_an_alias_for_id(store, monkeypatch):
    g = FakeGmail().install(monkeypatch)
    g.add("g1", body="hello there")
    out = await M.messages_impl(action="mail_read", chat_id="gmail:g1")
    assert "hello there" in out


async def test_links_are_listed_once_and_at_most_ten(store):
    links = " ".join(f"https://site{i}.example/p" for i in range(14)) + " https://site0.example/p"
    rowid = store.add("parker@example.com", "Parker", "Links", received=hours_ago(1), body=links)
    out = await read(f"icloud:{rowid}")
    listed = out.split("Links:", 1)[1]
    assert listed.count("https://site0.example/p") == 1
    assert len(re.findall(r"^- https://", listed, re.M)) == 10


async def test_a_search_line_from_another_year_says_the_year(store):
    old = store.add("old@example.com", "Old", "Ancient", received=hours_ago(24 * 400))
    out = await search(query="ancient", account="icloud")
    line = next(line for line in out.splitlines() if f"id=icloud:{old}" in line)
    assert re.search(r"- [A-Z][a-z]{2} \d{1,2}, \d{4} \d{1,2}:\d{2} [ap]m · Old <old@example.com>", line), line


# ------------------------------------------------- links behind the words --

TRACKING_HTML = ("<html><body><p>Your package shipped.</p>"
                 "<a href=\"https://track.example/pkg?id=1Z9&amp;src=email\">Track your package</a> "
                 "<a href='https://shop.example/orders/42'>View order</a> "
                 "<a href=\"mailto:help@shop.example\">Email us</a> "
                 "<a href=https://shop.example/unsub>Unsubscribe</a></body></html>")


def _listed(out):
    return re.findall(r"^- (https?://\S+)$", out.split("Links:", 1)[1], re.M) if "Links:" in out else []


async def test_an_html_only_icloud_email_lists_its_links(store):
    """2026-10-11 review: HTML-only mail (receipts, tracking) listed no
    links — html_to_text threw the hrefs away with the tags."""
    rowid = store.add("ship@shop.example", "Shop", "Shipped", received=hours_ago(1), body=None, html=TRACKING_HTML)
    links = _listed(await read(f"icloud:{rowid}"))
    assert links == ["https://track.example/pkg?id=1Z9&src=email", "https://shop.example/orders/42",
                     "https://shop.example/unsub"]


async def test_an_html_only_gmail_email_lists_its_links(store, monkeypatch):
    g = FakeGmail().install(monkeypatch)
    g.add("g1", body=None, html=TRACKING_HTML)
    links = _listed(await read("gmail:g1"))
    assert links == ["https://track.example/pkg?id=1Z9&src=email", "https://shop.example/orders/42",
                     "https://shop.example/unsub"]


def test_html_links():
    from services.mail_service import html_links

    assert html_links(TRACKING_HTML) == ["https://track.example/pkg?id=1Z9&src=email",
                                         "https://shop.example/orders/42", "https://shop.example/unsub"]
    many = "".join(f'<a href="https://s{i}.example/">x</a><a href="https://s{i}.example/">dup</a>' for i in range(30))
    assert len(html_links(many)) == 20 and len(set(html_links(many))) == 20
    assert html_links(None) == [] and html_links("<p>no links</p>") == []
