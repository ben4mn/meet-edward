"""The skills rows in Settings say what is true (2026-10-11, the integrations
audit's contacts track).

- WhatsApp (MCP) read "connected" whenever the MCP server listed its tools,
  while the Go bridge behind it had been unpaired since April (port 8080
  closed, its message store untouched): "connected" now needs the bridge
  listening *and* its store changed within 48 h.
- Both Twilio rows read "connected" from three env vars; the account is
  asked (``GET /Accounts/<sid>.json``, 3 s, cached 10 min, one request in
  flight), and a rejected or suspended account says so — never the SID.
- Apple Services declares what it is without probing Reminders or Notes
  (an apple-mcp probe raises a TCC dialog on the Mac).
- A row whose toggle gates nothing under the v2 toolset says so.
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import sqlite3
import time
from pathlib import Path

import httpx
import pytest

from services import mcp_client, skills_service as sk, twilio_service

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- WhatsApp --

@pytest.fixture
def whatsapp(tmp_path, monkeypatch):
    """The MCP server's directory and, beside it, the bridge's store: its
    message store (``messages.db``) and its session store (``whatsapp.db``,
    whatsmeow's ``whatsmeow_device`` holds a row per linked device)."""
    server_dir = tmp_path / "whatsapp-mcp" / "whatsapp-mcp-server"
    server_dir.mkdir(parents=True)
    store = tmp_path / "whatsapp-mcp" / "whatsapp-bridge" / "store" / "messages.db"
    store.parent.mkdir(parents=True)
    store.write_bytes(b"x")
    monkeypatch.setattr(mcp_client, "MCP_WHATSAPP_ENABLED", True)
    monkeypatch.setattr(mcp_client, "MCP_WHATSAPP_SERVER_DIR", str(server_dir))
    monkeypatch.setattr(mcp_client, "_whatsapp_initialized", True)
    monkeypatch.setattr(mcp_client, "_whatsapp_mcp_tools", ["send_message", "list_chats"])
    return store


def _session_store(messages_db: Path, devices: int) -> Path:
    path = messages_db.with_name("whatsapp.db")
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE whatsmeow_device (jid TEXT PRIMARY KEY, registration_id BIGINT)")
    for n in range(devices):
        conn.execute("INSERT INTO whatsmeow_device VALUES (?, ?)", (f"1555000{n}:1@s.whatsapp.net", n))
    conn.commit()
    conn.close()
    return path


def _age(path: Path, hours: float) -> None:
    then = time.time() - hours * 3600
    os.utime(path, (then, then))


@pytest.fixture
def listening(monkeypatch):
    """A local listener standing in for the bridge on an ephemeral port."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    monkeypatch.setattr(mcp_client, "WHATSAPP_BRIDGE_ADDR", srv.getsockname())
    yield srv
    srv.close()


def _closed_port(monkeypatch):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    addr = s.getsockname()
    s.close()
    monkeypatch.setattr(mcp_client, "WHATSAPP_BRIDGE_ADDR", addr)


class TestWhatsApp:
    """Paired is the bridge listening and a linked device in its session
    store. A quiet account is not an unpaired one: two days without
    WhatsApp traffic used to flip the row to "not paired" (review,
    2026-10-11); the message store's age only stands in when the session
    store can't be read, and then it says "quiet", never "not paired"."""

    def test_paired_means_listening_and_a_linked_device(self, whatsapp, listening):
        _session_store(whatsapp, devices=1)
        _age(whatsapp, 72)
        st = mcp_client.get_whatsapp_status()
        assert st["status"] == "connected", st
        assert "paired" in st["status_message"]

    def test_no_linked_device_is_not_paired(self, whatsapp, listening):
        _session_store(whatsapp, devices=0)
        st = mcp_client.get_whatsapp_status()
        assert st["status"] == "error"
        assert st["status_message"] == ("not paired — the bridge has no linked device; scan the QR in "
                                        "/tmp/whatsapp-bridge.log or turn this off")

    def test_a_closed_port_is_a_bridge_that_isnt_running(self, whatsapp, monkeypatch):
        _session_store(whatsapp, devices=1)
        _closed_port(monkeypatch)
        st = mcp_client.get_whatsapp_status()
        assert st["status"] == "error"
        assert st["status_message"].startswith("the bridge isn't running (nothing listening on 127.0.0.1:")
        assert "not paired" not in st["status_message"]

    def test_without_a_session_store_a_fresh_message_store_reads_as_paired(self, whatsapp, listening):
        assert mcp_client.get_whatsapp_status()["status"] == "connected"

    def test_without_a_session_store_a_quiet_one_is_said_as_quiet(self, whatsapp, listening):
        _age(whatsapp, 49)
        st = mcp_client.get_whatsapp_status()
        assert st["status"] == "error"
        assert "quiet for 48 h (paired?)" in st["status_message"]
        assert not st["status_message"].startswith("not paired")

    def test_an_unreadable_session_store_falls_back_to_the_message_store(self, whatsapp, listening):
        whatsapp.with_name("whatsapp.db").write_bytes(b"not a database")
        assert mcp_client.get_whatsapp_status()["status"] == "connected"

    def test_no_store_at_all_cannot_say(self, whatsapp, listening):
        whatsapp.unlink()
        st = mcp_client.get_whatsapp_status()
        assert st["status"] == "error" and "store" in st["status_message"]

    async def test_the_probe_runs_off_the_event_loop(self, monkeypatch):
        import threading

        seen: list = []

        def status():
            seen.append(threading.current_thread() is threading.main_thread())
            return {"status": "connected", "status_message": "ok", "metadata": None}

        monkeypatch.setattr(mcp_client, "get_whatsapp_status", status)
        assert (await sk._get_whatsapp_mcp_status())["status"] == "connected"
        assert seen == [False], "the TCP probe and the store reads block: they run in a thread"


# ------------------------------------------------------------------ Twilio --

SID = "AC" + "0123456789abcdef" * 2  # a placeholder, built so secret scanners skip it


@pytest.fixture
def twilio(monkeypatch):
    monkeypatch.setattr(twilio_service, "TWILIO_ACCOUNT_SID", SID)
    monkeypatch.setattr(twilio_service, "TWILIO_AUTH_TOKEN", "token-xyz")
    monkeypatch.setattr(twilio_service, "TWILIO_PHONE_NUMBER", "+12015550100")
    monkeypatch.setattr(twilio_service, "_account_cache", None)
    monkeypatch.setattr(twilio_service, "_account_inflight", None)
    calls: list = []

    def answer(response):
        async def fetch():
            calls.append(1)
            await asyncio.sleep(0)
            if isinstance(response, BaseException):
                raise response
            return response

        monkeypatch.setattr(twilio_service, "_fetch_account", fetch)

    answer.calls = calls
    return answer


def _json(code: int, body: dict | None = None) -> httpx.Response:
    return httpx.Response(code, json=body or {}, request=httpx.Request("GET", "https://api.twilio.com/x"))


class TestTwilio:
    async def test_an_active_account_is_connected(self, twilio):
        twilio(_json(200, {"status": "active", "sid": SID}))
        assert (await twilio_service.account_status())["status"] == "connected"
        st = await sk._get_twilio_status()
        assert st["status"] == "connected" and SID not in str(st)

    @pytest.mark.parametrize("state", ["suspended", "closed"])
    async def test_an_inactive_account_says_so(self, twilio, state):
        twilio(_json(200, {"status": state, "sid": SID}))
        st = await sk._get_twilio_status()
        assert st == {"status": "error", "status_message": f"Twilio account inactive ({state})", "metadata": None}
        st = await sk._get_twilio_whatsapp_status()
        assert st["status"] == "error" and state in st["status_message"]

    async def test_rejected_credentials_read_as_an_inactive_account(self, twilio):
        twilio(_json(401, {"message": f"Authenticate {SID}"}))
        st = await sk._get_twilio_status()
        assert st["status_message"] == "Twilio rejected the credentials — the account looks inactive"
        assert SID not in str(st)

    async def test_a_timeout_cannot_reach_twilio(self, twilio):
        twilio(httpx.ConnectTimeout(f"timed out reaching https://api.twilio.com/2010-04-01/Accounts/{SID}.json"))
        st = await twilio_service.account_status()
        assert st == {"status": "error", "status_message": "couldn't reach Twilio"}

    async def test_the_answer_is_cached_for_ten_minutes(self, twilio, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(twilio_service, "_now", lambda: clock[0])
        twilio(_json(200, {"status": "active"}))
        await twilio_service.account_status()
        clock[0] += 9 * 60
        await twilio_service.account_status()
        assert len(twilio.calls) == 1
        clock[0] += 2 * 60
        await twilio_service.account_status()
        assert len(twilio.calls) == 2

    async def test_one_request_in_flight(self, twilio):
        twilio(_json(200, {"status": "active"}))
        results = await asyncio.gather(*(twilio_service.account_status() for _ in range(5)))
        assert len(twilio.calls) == 1
        assert all(r["status"] == "connected" for r in results)

    async def test_the_sid_never_reaches_the_log(self, twilio, capsys):
        twilio(httpx.ConnectError(f"boom https://api.twilio.com/2010-04-01/Accounts/{SID}.json"))
        await twilio_service.account_status()
        out = capsys.readouterr()
        assert SID not in out.out + out.err

    async def test_unconfigured_never_asks(self, twilio, monkeypatch):
        monkeypatch.setattr(twilio_service, "TWILIO_AUTH_TOKEN", None)
        twilio(_json(200, {"status": "active"}))
        st = await sk._get_twilio_status()
        assert st["status"] == "error" and "TWILIO_AUTH_TOKEN" in st["status_message"]
        assert twilio.calls == []


# ---------------------------------------------------------- Apple Services --

def test_apple_services_declares_itself_without_a_subprocess(monkeypatch):
    calls: list = []

    def refuse(*a, **k):
        calls.append(a)
        raise AssertionError("no subprocess for a status")

    import subprocess

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(mcp_client, "MCP_APPLE_ENABLED", True)
    monkeypatch.setattr(mcp_client, "_apple_initialized", True)
    monkeypatch.setattr(mcp_client, "_apple_mcp_tools", ["calendar", "contacts", "maps"])
    st = sk._get_apple_services_status()
    assert st["status"] == "connected"
    assert st["status_message"] == ("apple-mcp running (3 tools); Calendar is read from Calendar's own database; "
                                    "Reminders and Notes don't go through apple-mcp")
    assert calls == []


# ------------------------------------------------- awaited, and ungated rows --

@pytest.fixture
def no_db(monkeypatch):
    class Row:
        enabled = True

    async def get_row(_skill_id):
        return Row()

    async def update(*a, **k):
        return Row()

    monkeypatch.setattr(sk, "_get_or_create_skill_db", get_row)
    monkeypatch.setattr(sk, "_update_skill_db", update)


async def test_an_async_status_is_awaited(no_db, monkeypatch):
    async def status():
        return {"status": "connected", "status_message": "awaited"}

    monkeypatch.setattr(sk, "SKILL_DEFINITIONS", {"brave_search": {"name": "x", "description": "y",
                                                                   "get_status": status}})
    [row] = await sk.get_all_skills()
    assert row.status.value == "connected" and row.status_message == "awaited"


async def test_a_row_that_gates_nothing_on_v2_says_so(no_db, monkeypatch):
    monkeypatch.delenv("EDWARD_TOOLSET", raising=False)
    ok = lambda: {"status": "connected", "status_message": "Ready"}  # noqa: E731
    monkeypatch.setattr(sk, "SKILL_DEFINITIONS", {
        "contacts_lookup": {"name": "Contacts", "description": "", "get_status": ok},
        "imessage_applescript": {"name": "iMessage", "description": "", "get_status": ok},
        "code_interpreter": {"name": "Code", "description": "", "get_status": sk._get_code_interpreter_status},
    })
    rows = {r.id: r for r in await sk.get_all_skills()}
    assert rows["contacts_lookup"].status_message == "Ready — doesn't change Edward's tools (v2 toolset)"
    assert rows["imessage_applescript"].status_message == "Ready"
    assert rows["code_interpreter"].status_message.startswith("Retired")
    assert "v2 toolset" not in rows["code_interpreter"].status_message

    monkeypatch.setenv("EDWARD_TOOLSET", "legacy")
    rows = {r.id: r for r in await sk.get_all_skills()}
    assert rows["contacts_lookup"].status_message == "Ready"


async def test_an_ungated_row_in_error_keeps_its_error_whole(no_db, monkeypatch):
    """The note follows an error as its own sentence, never glued to the
    error's own advice ("…or turn this off — doesn't change…")."""
    monkeypatch.delenv("EDWARD_TOOLSET", raising=False)
    err = lambda: {"status": "error", "status_message": "not paired — scan the QR or turn this off"}  # noqa: E731
    monkeypatch.setattr(sk, "SKILL_DEFINITIONS", {"whatsapp_mcp": {"name": "WhatsApp", "description": "",
                                                                   "get_status": err}})
    [row] = await sk.get_all_skills()
    assert row.status_message == ("not paired — scan the QR or turn this off. "
                                  "This switch doesn't change Edward's tools (v2 toolset).")


def test_no_ungated_row_is_read_anywhere():
    """Every id in UNGATED_ON_V2 is one no code asks about: not a toolset
    skill (``REGISTRY_SKILLS``) and never passed to ``is_skill_enabled`` /
    ``skill_enabled`` outside the legacy registry and the legacy send
    router."""
    from services.toolset.skills import REGISTRY_SKILLS

    asked = set(REGISTRY_SKILLS.values())
    pattern = re.compile(r"skill_enabled\(\s*[\"']([a-z_]+)[\"']")
    legacy = {BACKEND / "services" / "tool_registry.py", BACKEND / "services" / "graph" / "tools.py",
              BACKEND / "services" / "skills_service.py"}
    for root in (BACKEND / "services", BACKEND / "routers"):
        for path in root.rglob("*.py"):
            if path not in legacy:
                asked |= set(pattern.findall(path.read_text(encoding="utf-8", errors="ignore")))
    assert sk.UNGATED_ON_V2, "the set exists"
    assert not (set(sk.UNGATED_ON_V2) & asked), set(sk.UNGATED_ON_V2) & asked
    assert set(sk.UNGATED_ON_V2) <= set(sk.SKILL_DEFINITIONS)
    assert not {"code_interpreter", "javascript_interpreter", "sql_interpreter"} & set(sk.UNGATED_ON_V2)
