"""Approvals out of band (plan Phase 6.1, 2026-09-24).

With ``EDWARD_TAINT_APPROVALS=on`` a gated send is stored in
``pending_approvals`` and Ellis is asked through a push with Approve / Deny
buttons — never through chat, which injected text could forge. Approve
runs the stored call exactly once (past the gate, never past the secret
scan); deny runs nothing; a pending approval expires after 24 h.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from services import approvals_service as aps
from tests.fixtures.tool_loop import FakeTool

SK = "sk" + "-"
SECRET = SK + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2W3x4"
NOW = datetime(2026, 9, 24, 12, 0, 0)


# ------------------------------------------------------------------ pure --

class TestPure:
    pytestmark = pytest.mark.unit

    def test_summaries_name_the_tool_the_recipient_and_the_words(self):
        s = aps.summarize_call("send_imessage", {"contact": "Parker", "message": "On my way,\n10 min."})
        assert s == 'send_imessage to Parker: "On my way, 10 min."'
        assert aps.summarize_call("send_message", {"recipient": "+12015550199", "message": "hi"}) == \
            'send_message to +12015550199: "hi"'
        assert aps.summarize_call("create_hosted_page", {"html": "<p>" + "x" * 500 + "</p>", "slug": "trip"}) == \
            "create_hosted_page trip: 507 characters of HTML"
        assert aps.summarize_call("browser_type", {"text": "hello there", "ref": 3}, where="https://x.com/compose") == \
            'browser_type into https://x.com/compose: "hello there"'
        assert aps.summarize_call("mail", {"operation": "send", "to": "a@b.c", "subject": "Dinner", "body": "…"}) == \
            'mail to a@b.c: "Dinner"'
        long = aps.summarize_call("send_imessage", {"contact": "Parker", "message": "word " * 200})
        assert len(long) <= aps.SUMMARY_MAX_CHARS

    def test_waiting_message_tells_the_model_not_to_retry(self):
        assert aps.waiting_message(12, 'send_imessage to Parker: "hi"') == (
            "Waiting for Ellis's approval (#12): send_imessage to Parker: \"hi\". "
            "Don't retry; you'll see the outcome on your next wake.")

    def test_expiry_is_a_day(self):
        assert not aps.is_expired(NOW - timedelta(hours=23, minutes=59), NOW)
        assert aps.is_expired(NOW - timedelta(hours=24, minutes=1), NOW)

    def test_today_needs_carry_approve_and_deny(self):
        rows = [{"id": 7, "tool_name": "send_imessage", "summary": 'send_imessage to Parker: "hi"',
                 "conversation_id": "edward-mind", "taint_reason": "source:mind", "status": "pending",
                 "created_at": NOW - timedelta(minutes=5)},
                {"id": 8, "tool_name": "send_sms", "summary": "x", "conversation_id": None,
                 "taint_reason": "history", "status": "approved", "created_at": NOW}]
        needs = aps.approval_needs(rows, now=NOW)
        assert len(needs) == 1
        n = needs[0]
        assert n["id"] == "approval:7" and n["kind"] == "approval" and n["href"] == "/chat?c=edward-mind"
        assert n["title"] == "Approve Edward's send?" and n["detail"] == 'send_imessage to Parker: "hi"'
        by_id = {a["id"]: a for a in n["actions"]}
        assert by_id["approve"] == {"id": "approve", "label": "Approve", "method": "POST",
                                    "url": "/api/approvals/7", "body": {"decision": "approve"}, "primary": True}
        assert by_id["deny"]["body"] == {"decision": "deny"} and by_id["deny"]["method"] == "POST"
        assert by_id["open"]["url"] == "/chat?c=edward-mind"

    def test_today_needs_carry_the_send_itself_untruncated(self):
        """Today shows what Edward wants to send, whole (review follow-up
        2026-09-26): the summary is cut at 300 characters and names the
        tool, which is not what Ellis is deciding on."""
        long = "Saturday's still on. " * 30
        rows = [{"id": 7, "tool_name": "send_imessage", "args": {"contact": "Parker", "message": long},
                 "summary": aps.summarize_call("send_imessage", {"contact": "Parker", "message": long}),
                 "conversation_id": "edward-mind", "status": "pending", "created_at": NOW - timedelta(minutes=5)}]
        [n] = aps.approval_needs(rows, now=NOW)
        assert n["send"] == {"channel": "imessage", "recipient": "Parker", "text": long}
        assert n["detail"] == rows[0]["summary"], "the one-line summary stays for older clients"

    @pytest.mark.parametrize("tool,args,send", [
        ("send_imessage", {"chat_id": "chat123", "message": "hi"},
         {"channel": "imessage", "recipient": "chat123", "text": "hi"}),
        ("send_message", {"recipient": "+12015550199", "message": "hi", "channel": "whatsapp"},
         {"channel": "whatsapp", "recipient": "+12015550199", "text": "hi"}),
        ("send_message", {"recipient": "Sam", "message": "hi"}, {"channel": "message", "recipient": "Sam", "text": "hi"}),
        ("send_sms", {"phone_number": "+12015550199", "message": "hi"},
         {"channel": "sms", "recipient": "+12015550199", "text": "hi"}),
        ("send_whatsapp", {"phone_number": "+1201", "message": "hi"}, {"channel": "whatsapp", "recipient": "+1201", "text": "hi"}),
        ("whatsapp_send_message", {"recipient": "1203@g.us", "message": "hi"},
         {"channel": "whatsapp", "recipient": "1203@g.us", "text": "hi"}),
        ("mail", {"operation": "send", "to": "a@b.c", "subject": "Dinner", "body": "At 7?"},
         {"channel": "email", "recipient": "a@b.c", "text": "At 7?", "subject": "Dinner"}),
        ("create_hosted_page", {"slug": "trip", "html": "<p>x</p>"}, {"channel": "page", "recipient": "trip", "text": None}),
        ("browser_type", {"text": "hello", "ref": 3}, {"channel": "browser", "recipient": None, "text": "hello"}),
    ])
    def test_the_send_is_structured_per_tool(self, tool, args, send):
        assert aps.structured_call(tool, args) == send

    @pytest.mark.parametrize("result,delivered", [
        ("iMessage sent to Parker", True),
        ("SMS sent to +12015550199", True),
        ("Already sent to Parker 3 minutes ago — not sent again.", False),
        ("Waiting for Ellis's approval (#3): …", False),
        ("Tool error: blocked — the message contains what looks like a secret (openai_key)", False),
        ("Failed to send iMessage: AppleScript error", False),
        ("'Parker' matches multiple contacts: …", False),
        ("Sent to Parker.", False),
    ])
    def test_delivered_reads_the_send_tools_own_reply(self, result, delivered):
        assert aps.delivered("send_imessage", result) is delivered

    def test_delivered_for_other_tools_and_denials(self):
        assert aps.delivered("create_hosted_page", "Page published: https://pages.example.com/trip") is True
        assert aps.delivered("create_hosted_page", "Tool error: slug taken") is False
        assert aps.delivered("send_imessage", None) is False

    def test_pack_lines_say_what_waits_and_what_owner_decided(self):
        pending = [{"id": 7, "summary": 'send_imessage to Parker: "hi"', "created_at": NOW - timedelta(hours=2),
                    "status": "pending"}]
        decided = [{"id": 5, "summary": 'send_sms to +1: "yo"', "status": "approved", "result_text": "Sent.",
                    "decided_at": NOW - timedelta(hours=1)},
                   {"id": 6, "summary": 'send_message to Ann: "x"', "status": "denied", "result_text": None,
                    "decided_at": NOW - timedelta(minutes=30)},
                   {"id": 4, "summary": 'send_message to Bo: "y"', "status": "expired", "result_text": None,
                    "decided_at": NOW - timedelta(minutes=10)}]
        lines = aps.pack_lines(pending, decided, now=NOW)
        text = "\n".join(lines)
        assert lines[0] == "Sends waiting for Ellis's approval (1):"
        assert '#7 send_imessage to Parker: "hi" — asked 2h ago' in text
        assert "Ellis's answers on sends you held for approval:" in text
        assert '#5 approved: send_sms to +1: "yo" → Sent.' in text
        assert '#6 denied: send_message to Ann: "x" — not sent' in text
        assert '#4 expired unanswered: send_message to Bo: "y" — not sent' in text
        assert aps.pack_lines([], [], now=NOW) == []

    @pytest.mark.parametrize("url,own", [
        ("https://edward.example.com/today", True), ("http://localhost:3000/today", True),
        ("http://my-mac.local:3000/chat", True), ("https://www.youtube.com/watch?v=x", False),
        ("https://www.edward.example.com/today", True), ("http://other-mac.local:3000/chat", False),
        (None, False), ("", False),
    ])
    def test_own_app_urls(self, url, own, monkeypatch):
        """The app's own hosts come from ``host_config``: its public URL
        and this Mac's Bonjour name (plus localhost)."""
        from services import host_config

        monkeypatch.setenv("EDWARD_PUBLIC_BASE_URL", "https://edward.example.com")
        monkeypatch.delenv("EDWARD_PUBLIC_SCREEN_URL", raising=False)
        monkeypatch.setattr(host_config, "local_hostname", lambda: "my-mac.local")
        assert aps.is_own_app_url(url) is own


# --------------------------------------------------------------- the DB --

@pytest.fixture
def pushes(monkeypatch):
    sent = []

    async def fake_push(**kw):
        sent.append(kw)
        return {"sent": 1, "failed": 0, "total": 1}

    import services.push_service as ps
    monkeypatch.setattr(ps, "send_push_notification", fake_push)
    return sent


@pytest.fixture
def tool(monkeypatch):
    t = FakeTool("send_imessage", "Sent to Parker.")

    async def find(name):
        return t if name == t.name else None

    monkeypatch.setattr(aps, "_find_tool", find)
    return t


@pytest.fixture
def notes(monkeypatch):
    got = []

    async def fake_note(conversation_id, text):
        got.append((conversation_id, text))

    monkeypatch.setattr(aps, "append_thread_note", fake_note)
    return got


async def _settle():
    await asyncio.gather(*list(aps._note_tasks), return_exceptions=True)


async def _request(**over):
    kw = dict(tool_name="send_imessage", args={"contact": "Parker", "message": "On my way"},
              conversation_id="edward-mind", turn_id="t1", taint_reason="source:mind", turn_source="mind")
    kw.update(over)
    return await aps.request(**kw)


async def _ledger(kind="approval"):
    from sqlalchemy import select
    from services.database import LedgerEventModel, async_session

    async with async_session() as s:
        return [(r.source, r.outcome) for r in (await s.execute(
            select(LedgerEventModel).where(LedgerEventModel.kind == kind).order_by(LedgerEventModel.id))).scalars()]


@pytest.mark.integration
class TestRequest:
    async def test_request_stores_a_row_and_pushes_approve_deny(self, clean_db, pushes):
        text = await _request()
        rows = await aps.list_approvals(status="pending")
        assert len(rows) == 1
        a = rows[0]
        assert text == aps.waiting_message(a["id"], a["summary"])
        assert a["args"] == {"contact": "Parker", "message": "On my way"}
        assert a["summary"] == 'send_imessage to Parker: "On my way"'
        assert len(pushes) == 1
        p = pushes[0]
        assert p["source"] == "approval" and p["approval_id"] == a["id"]
        assert p["url"] == "/chat?c=edward-mind" and p["conversation_id"] == "edward-mind"
        assert "On my way" in p["body"] and p["tag"] == f"approval-{a['id']}"
        assert await _ledger() == [("send_imessage", "pending")]

    async def test_an_identical_pending_request_is_not_asked_twice(self, clean_db, pushes):
        first = await _request()
        second = await _request()
        assert first == second
        assert len(await aps.list_approvals()) == 1 and len(pushes) == 1

    async def test_the_queue_is_bounded(self, clean_db, pushes, monkeypatch):
        monkeypatch.setattr(aps, "MAX_PENDING", 2)
        await _request(args={"contact": "A", "message": "1"})
        await _request(args={"contact": "B", "message": "2"})
        third = await _request(args={"contact": "C", "message": "3"})
        assert third.startswith("Tool error: blocked — 2 sends are already waiting for Ellis's approval")
        assert len(await aps.list_approvals()) == 2

    async def test_a_failed_push_still_leaves_the_approval(self, clean_db, monkeypatch):
        async def boom(**kw):
            raise RuntimeError("no vapid")

        import services.push_service as ps
        monkeypatch.setattr(ps, "send_push_notification", boom)
        text = await _request()
        assert text.startswith("Waiting for Ellis's approval (#")
        assert len(await aps.list_approvals(status="pending")) == 1


@pytest.mark.integration
class TestDecide:
    async def test_approve_runs_the_call_exactly_once(self, clean_db, pushes, tool, notes):
        await _request()
        aid = (await aps.list_approvals())[0]["id"]
        out = await aps.decide(aid, "approve")
        assert out["status"] == "approved" and out["result_text"] == "Sent to Parker."
        assert tool.calls == [{"contact": "Parker", "message": "On my way"}]
        again = await aps.decide(aid, "approve")
        assert again["status"] == "approved" and again.get("already_decided") is True
        assert len(tool.calls) == 1, "a second approve never re-runs the send"
        await _settle()
        assert len(notes) == 1 and notes[0][0] == "edward-mind"
        assert notes[0][1].startswith(f"[Approval #{aid}] Ellis approved")
        assert "Sent to Parker." in notes[0][1]
        assert await _ledger() == [("send_imessage", "pending"), ("send_imessage", "approved")]

    async def test_approve_says_whether_the_send_was_delivered(self, clean_db, pushes, tool, notes):
        tool._result = "iMessage sent to Parker"
        await _request()
        aid = (await aps.list_approvals())[0]["id"]
        out = await aps.decide(aid, "approve")
        assert out["delivered"] is True
        again = await aps.decide(aid, "approve")
        assert again["already_decided"] is True and again["delivered"] is True

    async def test_an_approved_send_the_tool_refused_is_not_delivered(self, clean_db, pushes, tool, notes):
        tool._result = "Already sent to Parker 3 minutes ago — not sent again."
        await _request()
        aid = (await aps.list_approvals())[0]["id"]
        assert (await aps.decide(aid, "approve"))["delivered"] is False
        await _request(args={"contact": "Sam", "message": "x"})
        sid = [a for a in await aps.list_approvals() if a["status"] == "pending"][0]["id"]
        assert (await aps.decide(sid, "deny"))["delivered"] is False

    async def test_concurrent_approves_run_once(self, clean_db, pushes, tool, notes):
        await _request()
        aid = (await aps.list_approvals())[0]["id"]
        results = await asyncio.gather(aps.decide(aid, "approve"), aps.decide(aid, "approve"),
                                       return_exceptions=True)
        assert len(tool.calls) == 1, results

    async def test_deny_runs_nothing(self, clean_db, pushes, tool, notes):
        await _request()
        aid = (await aps.list_approvals())[0]["id"]
        out = await aps.decide(aid, "deny")
        assert out["status"] == "denied" and tool.calls == []
        with pytest.raises(aps.ApprovalConflict):
            await aps.decide(aid, "approve")
        assert tool.calls == []
        await _settle()
        assert notes and "denied" in notes[0][1]

    async def test_unknown_and_bad_decisions(self, clean_db):
        with pytest.raises(aps.ApprovalNotFound):
            await aps.decide(999, "approve")
        with pytest.raises(ValueError):
            await aps.decide(1, "maybe")

    async def test_a_day_old_request_expires_and_cannot_be_approved(self, clean_db, pushes, tool, notes):
        from sqlalchemy import update
        from services.database import PendingApprovalModel, async_session

        await _request()
        aid = (await aps.list_approvals())[0]["id"]
        async with async_session() as s:
            await s.execute(update(PendingApprovalModel).where(PendingApprovalModel.id == aid)
                            .values(created_at=datetime.utcnow() - timedelta(hours=25)))
            await s.commit()
        assert await aps.list_approvals(status="pending") == []
        assert (await aps.get_approval(aid))["status"] == "expired"
        with pytest.raises(aps.ApprovalConflict):
            await aps.decide(aid, "approve")
        assert tool.calls == []
        assert ("send_imessage", "expired") in await _ledger()

    async def test_approve_still_runs_the_secret_scan(self, clean_db, pushes, tool, notes):
        from services.database import PendingApprovalModel, async_session

        async with async_session() as s:
            row = PendingApprovalModel(tool_name="send_imessage",
                                       args_json=json.dumps({"contact": "Parker", "message": f"key {SECRET}"}),
                                       summary="x", conversation_id="edward-mind", status="pending")
            s.add(row)
            await s.commit()
            await s.refresh(row)
        out = await aps.decide(row.id, "approve")
        assert tool.calls == []
        assert out["result_text"].startswith("Tool error: blocked")
        assert ("send_imessage", "blocked") in await _ledger("egress")

    async def test_decisions_are_refused_while_edwards_hands_are_on_the_app(self, clean_db, pushes, tool, notes,
                                                                            monkeypatch):
        await _request()
        aid = (await aps.list_approvals())[0]["id"]
        monkeypatch.setattr(aps, "hands_on_own_app", lambda: True)
        with pytest.raises(aps.ApprovalConflict):
            await aps.decide(aid, "approve")
        assert tool.calls == [] and (await aps.get_approval(aid))["status"] == "pending"

    async def test_needs_and_pack_lines_read_the_table(self, clean_db, pushes, tool, notes):
        await _request()
        await _request(args={"contact": "Ann", "message": "later"})
        rows = await aps.list_approvals()
        await aps.decide(rows[-1]["id"], "approve")
        needs = await aps.today_needs()
        assert [n["kind"] for n in needs] == ["approval"]
        # The mind's last_wake_at is timezone-aware; the table is naive UTC.
        from datetime import timezone

        lines = await aps.load_pack_lines(since=datetime.now(timezone.utc) - timedelta(hours=1))
        assert lines[0] == "Sends waiting for Ellis's approval (1):"
        assert any("approved" in line for line in lines)


# -------------------------------------------------------------- router --

async def _client():
    from httpx import ASGITransport, AsyncClient
    from main import app
    from services.auth_service import create_access_token

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                       cookies={"edward_token": create_access_token()})


@pytest.mark.integration
class TestRouter:
    async def test_list_approve_and_idempotent_repeat(self, clean_db, pushes, tool, notes):
        await _request()
        async with await _client() as c:
            r = await c.get("/api/approvals?status=pending")
            assert r.status_code == 200, r.text
            items = r.json()["approvals"]
            assert len(items) == 1 and items[0]["tool_name"] == "send_imessage"
            aid = items[0]["id"]
            r = await c.post(f"/api/approvals/{aid}", json={"decision": "approve"})
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "approved" and r.json()["result_text"] == "Sent to Parker."
            assert r.json()["delivered"] is False, "not the send tool's own delivered reply"
            r = await c.post(f"/api/approvals/{aid}", json={"decision": "approve"})
            assert r.status_code == 200 and r.json()["already_decided"] is True
            r = await c.post(f"/api/approvals/{aid}", json={"decision": "deny"})
            assert r.status_code == 409
            r = await c.get(f"/api/approvals/{aid}")
            assert r.status_code == 200 and r.json()["status"] == "approved"
        assert len(tool.calls) == 1

    async def test_errors(self, clean_db):
        async with await _client() as c:
            assert (await c.post("/api/approvals/42", json={"decision": "approve"})).status_code == 404
            assert (await c.post("/api/approvals/42", json={"decision": "sure"})).status_code == 400
            assert (await c.get("/api/approvals/42")).status_code == 404
            assert (await c.get("/api/approvals?status=bogus")).status_code == 400

    async def test_requires_auth(self, clean_db, monkeypatch):
        """Only Ellis's session decides: the SW posts with his cookie; Edward's
        own tools have none."""
        from httpx import ASGITransport, AsyncClient
        from main import app
        from services import auth_service

        monkeypatch.setattr(auth_service, "_password_configured_cache", True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/approvals/1", json={"decision": "approve"})
            assert r.status_code == 401


# ------------------------------------------------------------- payload --

class TestPayload:
    pytestmark = pytest.mark.unit

    def test_an_approval_push_carries_approve_deny_open(self):
        from services.push_service import build_payload

        p = build_payload(title="Approve Edward's send?", body="B", url="/chat?c=edward-mind", tag="approval-7",
                          icon=None, delivery_id=None, ledger_id="l1", source="approval", approval_id=7,
                          conversation_id="edward-mind")
        assert p["kind"] == "approval"
        assert [a["action"] for a in p["actions"]] == ["approve", "deny", "open"]
        assert [a["title"] for a in p["actions"]] == ["Approve", "Deny", "Open"]
        assert p["approval_id"] == 7 and p["conversation_id"] == "edward-mind"
        assert p["url"] == "/chat?c=edward-mind"

    def test_every_other_push_is_unchanged(self):
        from services.push_service import build_payload

        p = build_payload(title="T", body="B", url="/today", tag=None, icon=None, delivery_id="d",
                          ledger_id="l", source="mind")
        assert [a["action"] for a in p["actions"]] == ["open", "done", "snooze"]
        assert "approval_id" not in p

    async def test_send_push_notification_threads_the_approval_through(self, monkeypatch):
        import services.push_service as ps
        from services import push_ledger

        sent = []

        async def subs():
            return [type("S", (), {"id": "s1", "device_id": None, "kind": None})()]

        async def noop(*a, **k):
            return None

        async def record(**kw):
            return kw.get("row_id")

        monkeypatch.setattr(ps, "is_configured", lambda: True)
        monkeypatch.setattr(ps, "get_active_subscriptions", subs)
        monkeypatch.setattr(ps, "_send_to_subscription", lambda sub, payload: sent.append(payload) or True)
        monkeypatch.setattr(ps, "_mark_subscription_used", noop)
        monkeypatch.setattr(push_ledger, "record", record)
        await ps.send_push_notification(title="Approve Edward's send?", body="b", url="/chat?c=c1",
                                        source="approval", approval_id=9)
        assert sent[0]["approval_id"] == 9 and sent[0]["conversation_id"] == "c1"
        assert [a["action"] for a in sent[0]["actions"]] == ["approve", "deny", "open"]
