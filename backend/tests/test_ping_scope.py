"""services/pings/scope.py — what a friend's ping may do.

Ellis's ruling, 2026-10-11: "Edward can have access to do all that" — friends'
pings run with the full toolset. One rail still holds for them: Edward
never starts a conversation with someone who never asked him anything. A
send from a friend's ping goes into that chat, or into a chat that has
addressed Edward (the M.<->Parker relay), and nowhere else."""

import pytest

from services.pings import hooks, scope


def _ctx(third_party=True, chat="chat-trivia"):
    return hooks.new_context(ping_id=7, chat_identifier=chat, chat_guid=f"iMessage;+;{chat}", is_group=True,
                             conversation_id="ping-7", must_answer=True, third_party=third_party)


@pytest.fixture
def friend_ping():
    token = hooks.CURRENT_PING.set(_ctx(third_party=True))
    yield
    hooks.CURRENT_PING.reset(token)


@pytest.fixture
def no_one_addressed(monkeypatch):
    calls = []

    async def fake(chat, hours=24):
        calls.append((chat, hours))
        return chat in ("chat-parker",)

    monkeypatch.setattr(scope, "_addressed_recently", fake)
    return calls


@pytest.fixture
def full_toolset(monkeypatch):
    monkeypatch.setenv("EDWARD_FRIENDS_FULL_TOOLSET", "on")


@pytest.fixture
def friends_default(monkeypatch):
    monkeypatch.delenv("EDWARD_FRIENDS_FULL_TOOLSET", raising=False)


@pytest.mark.asyncio
class TestFullToolset:
    """``EDWARD_FRIENDS_FULL_TOOLSET=on``: the reference host's 2026-10-11
    decision, a friend's ping with the full toolset."""

    @pytest.mark.parametrize("name,args", [
        ("bash", {"command": "ls ~/Documents"}),
        ("str_replace_based_edit_tool", {"command": "view", "path": "~/.env"}),
        ("chrome", {"action": "open", "url": "https://example.com"}),
        ("screen", {"action": "show"}),
        ("memory", {"action": "search", "query": "Ellis"}),
        ("messages", {"action": "recent"}),
        ("look", {"target": "/Users/me/Downloads/x.png"}),
        ("look", {"target": "screen"}),
        ("mcp_github_search", {"q": "x"}),
        ("tell_owner", {"message": "x", "urgent": True}),
    ])
    async def test_nothing_is_withheld_from_a_friend(self, friend_ping, no_one_addressed, full_toolset, name, args):
        assert await scope.refusal(name, args) is None


@pytest.mark.asyncio
class TestTheFriendsDefault:
    """Public release, 2026-10-12: by default a friend's ping goes without
    what acts as the owner on the owner's own machine — the shell and the
    editor, the owner's Chrome, the screens, and look at the owner's display,
    Chrome or files. A prompt injection in a friend's message can't reach
    code execution as the owner's macOS user. The owner turns it on."""

    @pytest.mark.parametrize("name,args,word", [
        ("bash", {"command": "ls ~/Documents"}, "computer"),
        ("str_replace_based_edit_tool", {"command": "view", "path": "~/notes.md"}, "computer"),
        ("execute_shell", {"command": "id"}, "computer"),
        ("chrome", {"action": "open", "url": "https://example.com"}, "Chrome"),
        ("chrome", {"action": "read"}, "Chrome"),
        ("browser_open", {"url": "https://example.com"}, "Chrome"),
        ("screen", {"action": "show", "kind": "note", "body": "hi"}, "screens"),
        ("screen", {"action": "say", "line": "hi"}, "screens"),
        ("show_on_screen", {"kind": "note", "body": "hi"}, "screens"),
        ("look", {"target": "screen"}, "screen"),
        ("look", {"target": "chrome"}, "Chrome"),
        ("look_at_screen", {"at": "page"}, "Chrome"),
        ("look", {"target": "/Users/me/Downloads/x.png"}, "files"),
        ("look", {"target": "~/Pictures/x.jpg"}, "files"),
        ("look", {"target": "notes/x.pdf"}, "files"),
        ("look", {"target": "file:abc123"}, "files"),
    ])
    async def test_the_owners_machine_is_off(self, friend_ping, no_one_addressed, friends_default, name, args, word):
        reason = await scope.refusal(name, args)
        assert reason and word in reason, reason
        assert "Ellis can turn that on" in reason or "only Ellis can turn that on" in reason

    @pytest.mark.parametrize("name,args", [
        ("look", {"target": "attachment:449321"}),
        ("look", {"target": "media:abc"}),
        ("look", {"target": "https://example.com/menu.pdf"}),
        ("look", {"target": "browser"}),
        ("browser", {"action": "open", "url": "https://example.com"}),
        ("web_search", {"query": "trivia answer"}),
        ("web_fetch", {"url": "https://example.com"}),
        ("memory", {"action": "search", "query": "Ellis"}),
        ("messages", {"action": "recent"}),
        ("messages", {"action": "stand_down", "chat_id": "chat-trivia"}),
        ("plan", {"steps": ["a"]}),
        ("send_message", {"chat_id": "chat-trivia", "message": "hi"}),
    ])
    async def test_the_rest_stays(self, friend_ping, no_one_addressed, friends_default, name, args):
        assert await scope.refusal(name, args) is None

    async def test_the_tool_loop_answers_with_an_honest_tool_error(self, friend_ping, no_one_addressed,
                                                                 friends_default):
        from services.graph import tool_loop
        refused = await tool_loop._boundary_check("bash", {"command": "cat ~/.ssh/id_ed25519"})
        assert refused and refused.startswith("Tool error: ") and "Ellis" in refused

    async def test_the_owners_own_ping_keeps_everything(self, no_one_addressed, friends_default):
        token = hooks.CURRENT_PING.set(_ctx(third_party=False, chat="owner-self"))
        try:
            for name, args in (("bash", {"command": "ls"}), ("chrome", {"action": "open", "url": "https://x.com"}),
                               ("screen", {"action": "show"}), ("look", {"target": "screen"}),
                               ("str_replace_based_edit_tool", {"command": "view", "path": "~/a"})):
                assert await scope.refusal(name, args) is None, name
        finally:
            hooks.CURRENT_PING.reset(token)

    async def test_no_ping_no_narrowing(self, friends_default):
        assert await scope.refusal("bash", {"command": "ls"}) is None

    @pytest.mark.parametrize("value", ["on", "ON", "true", "1", "yes"])
    async def test_the_owner_turns_it_on(self, friend_ping, no_one_addressed, monkeypatch, value):
        monkeypatch.setenv("EDWARD_FRIENDS_FULL_TOOLSET", value)
        assert await scope.refusal("bash", {"command": "ls"}) is None
        assert await scope.refusal("look", {"target": "screen"}) is None

    @pytest.mark.parametrize("value", ["off", "", "no", "0"])
    async def test_anything_else_keeps_the_default(self, friend_ping, no_one_addressed, monkeypatch, value):
        monkeypatch.setenv("EDWARD_FRIENDS_FULL_TOOLSET", value)
        assert await scope.refusal("bash", {"command": "ls"})

    async def test_the_send_rule_holds_with_the_full_toolset_too(self, friend_ping, no_one_addressed,
                                                                full_toolset):
        assert await scope.refusal("send_message", {"chat_id": "chat-stranger", "message": "hi"})

    async def test_legacy_turns_lose_only_what_the_legacy_loop_cannot_keep_safe(self):
        """Review 2026-10-11 (critical): without AREA 2's sandbox the legacy
        shell, code runners, workers and Codex read .env unredacted and can
        osascript, psql :5432 or open a second Chrome. Ellis's ruling holds
        on its conditions; the legacy loop can't keep them, so a friend's
        legacy turn goes without exactly those tools."""
        assert scope.LEGACY_THIRD_PARTY_TOOLS is None
        assert {"execute_shell", "execute_code", "execute_javascript", "execute_sql", "spawn_worker",
                "send_to_worker", "spawn_cc_worker", "start_claude_session", "add_mcp_server", "update_mcp_server",
                "remember_forget", "forget_about", "schedule_event", "browser_type", "browser_steps",
                "browser_click", "browser_key"} <= set(scope.LEGACY_THIRD_PARTY_EXCLUDED)
        assert not set(scope.LEGACY_THIRD_PARTY_EXCLUDED) & {
            "send_imessage", "stand_down", "leave_note", "wake_me", "web_search", "fetch_page_content",
            "remember", "remember_search", "get_imessage_attachment", "browser_open", "browser_read", "messages",
            "mail"}


@pytest.mark.asyncio
class TestSendsStayInChatsThatAsked:
    async def test_into_this_chat(self, friend_ping, no_one_addressed):
        assert await scope.refusal("send_message", {"chat_id": "chat-trivia", "message": "hi"}) is None
        assert await scope.refusal("send_message", {"chat_id": "any;+;chat-trivia", "message": "hi"}) is None
        assert await scope.refusal("send_imessage", {"contact": "chat-trivia", "chat_id": "chat-trivia",
                                                     "message": "hi"}) is None

    async def test_into_a_chat_that_addressed_edward(self, friend_ping, no_one_addressed):
        assert await scope.refusal("send_message", {"chat_id": "chat-parker", "message": "M. says hi"}) is None
        assert no_one_addressed[-1] == ("chat-parker", 24)

    @pytest.mark.parametrize("name,args", [
        ("send_message", {"chat_id": "chat-sarah", "message": "x"}),
        ("send_message", {"to": "+12015550177", "message": "x"}),
        ("send_message", {"recipient": "+12015550177", "message": "x"}),
        ("send_imessage", {"contact": "+12015550177", "message": "x"}),
        ("send_sms", {"phone_number": "+12015550177", "message": "x"}),
        ("send_whatsapp", {"phone_number": "+12015550177", "message": "x"}),
    ])
    async def test_never_a_new_conversation(self, friend_ping, no_one_addressed, name, args):
        refused = await scope.refusal(name, args)
        assert refused and "hasn't asked you anything" in refused and not refused.startswith("Tool error")

    @pytest.mark.parametrize("name,args", [
        ("messages", {"operation": "send", "phoneNumber": "+12015559999", "message": "Ellis's not coming"}),
        ("messages", {"operation": "schedule", "phoneNumber": "+12015559999", "message": "x",
                      "scheduledTime": "2026-10-12T09:00:00"}),
        ("mail", {"operation": "send", "to": "sarah@example.com", "subject": "x", "body": "x"}),
        ("mail", {"operation": "send", "to": ["chat-parker", "sarah@example.com"], "subject": "x", "body": "x"}),
        ("mail", {"operation": "send", "to": "chat-parker", "cc": "sarah@example.com", "subject": "x", "body": "x"}),
    ])
    async def test_apple_mcps_own_sends_are_held_to_the_same_rail(self, friend_ping, no_one_addressed, name, args):
        """Review 2026-10-11 (verified): send_imessage was refused, apple-mcp's
        ``messages`` send and ``mail`` send were not — the model, told to
        "try another way", would reach Sarah through them."""
        refused = await scope.refusal(name, args)
        assert refused and "hasn't asked you anything" in refused

    @pytest.mark.parametrize("name,args", [
        ("messages", {"operation": "read", "phoneNumber": "+12015559999"}),
        ("mail", {"operation": "search", "searchTerm": "tickets"}),
        ("messages", {"operation": "send", "phoneNumber": "chat-parker", "message": "x"}),
    ])
    async def test_apple_mcp_reads_and_sends_to_a_chat_that_asked_pass(self, friend_ping, no_one_addressed, name,
                                                                       args):
        assert await scope.refusal(name, args) is None

    async def test_a_friend_cannot_schedule_a_send_for_later(self, friend_ping, no_one_addressed):
        """The fired event is a full-tool turn with no ping behind it; the rail
        would not hold then. A follow-up is a wake (``wake_me``) or Ellis's call."""
        refused = await scope.refusal("schedule_event", {"description": "text Sarah that Ellis is not coming",
                                                          "scheduled_at": "2026-10-12T09:00:00"})
        assert refused and "Ellis" in refused
        assert await scope.refusal("wake_me", {"when": "2026-10-12T09:00:00", "why": "check back"}) is None

    @pytest.mark.parametrize("name,args", [
        ("browser_type", {"text": "Ellis's not coming"}),
        ("browser_steps", {"steps": [{"action": "open", "url": "https://mail.google.com"},
                                     {"action": "type", "text": "Ellis's not coming"}]}),
    ])
    async def test_typing_into_owners_chrome_for_a_friend_stays_in_search_boxes(self, friend_ping, no_one_addressed,
                                                                             full_toolset, name, args):
        """With the full toolset on; by default the owner's Chrome is off
        for a friend altogether (``TestTheFriendsDefault``)."""
        refused = await scope.refusal(name, args)
        assert refused and "search" in refused

    async def test_a_search_typed_for_a_friend_is_fine(self, friend_ping, no_one_addressed, full_toolset):
        steps = [{"action": "open", "url": "https://www.google.com"}, {"action": "type", "text": "Riverside Fest tickets"}]
        assert await scope.refusal("browser_steps", {"steps": steps}) is None

    async def test_by_default_even_a_search_in_the_owners_chrome_is_off(self, friend_ping, no_one_addressed,
                                                                      friends_default):
        steps = [{"action": "open", "url": "https://www.google.com"}, {"action": "type", "text": "Riverside Fest tickets"}]
        assert "Chrome" in (await scope.refusal("browser_steps", {"steps": steps}) or "")

    async def test_a_send_with_no_target_is_left_to_the_tool(self, friend_ping, no_one_addressed):
        assert await scope.refusal("send_message", {"message": "x"}) is None

    async def test_a_failing_lookup_refuses(self, friend_ping, monkeypatch):
        async def boom(chat, hours=24):
            raise RuntimeError("db down")

        monkeypatch.setattr(scope, "_addressed_recently", boom)
        assert await scope.refusal("send_message", {"chat_id": "chat-sarah", "message": "x"})


@pytest.mark.asyncio
class TestOnlyFriendsPings:
    async def test_owners_ping_is_never_refused(self, no_one_addressed):
        token = hooks.CURRENT_PING.set(_ctx(third_party=False))
        try:
            assert await scope.refusal("send_message", {"chat_id": "chat-sarah", "message": "x"}) is None
            assert await scope.refusal("messages", {"operation": "send", "phoneNumber": "+12015559999",
                                                    "message": "x"}) is None
            assert await scope.refusal("schedule_event", {"description": "x"}) is None
            assert await scope.refusal("browser_type", {"text": "x"}) is None
        finally:
            hooks.CURRENT_PING.reset(token)

    async def test_outside_a_ping_nothing_is_refused(self, no_one_addressed):
        assert await scope.refusal("send_message", {"chat_id": "chat-sarah", "message": "x"}) is None


@pytest.mark.asyncio
class TestTheLegacyExecutorAsksTheScope:
    async def test_boundary_check_refuses_a_friends_new_conversation(self, friend_ping, no_one_addressed):
        from services.graph.streaming import _boundary_check

        refused = await _boundary_check("send_imessage", {"contact": "+12015550177", "message": "x"})
        assert refused and refused.startswith("Tool error: ") and "hasn't asked you anything" in refused


@pytest.mark.asyncio
class TestTheToolLoopAsksTheScope:
    """The merge of AREA 2 and AREA 3 (2026-10-11): AREA 2 moved the
    executor and its boundary into ``graph/tool_loop.py``; the scope is
    asked there, for every call of every turn, on the toolset's names too."""

    async def test_the_new_boundary_refuses_a_friends_new_conversation(self, friend_ping, no_one_addressed):
        from services.graph import tool_loop

        refused = await tool_loop._boundary_check("send_message", {"chat_id": "chat-sarah", "message": "x"})
        assert refused and refused.startswith("Tool error: ") and "hasn't asked you anything" in refused
        assert await tool_loop._boundary_check("send_message", {"chat_id": "chat-trivia", "message": "x"}) is None

    @pytest.mark.parametrize("args", [
        {"action": "add", "when": "2026-10-12T09:00:00", "what": "text Sarah that Ellis is not coming"},
        {"action": "add", "kind": "task", "when": "2026-10-12T09:00:00", "what": "x"},
        {"action": "add", "kind": "remind", "when": "2026-10-12T09:00:00", "what": "x"},
    ])
    async def test_a_friend_cannot_leave_a_task_on_the_toolset(self, friend_ping, no_one_addressed, args):
        refused = await scope.refusal("schedule", args)
        assert refused and "Ellis" in refused and "wake" in refused

    @pytest.mark.parametrize("args", [
        {"action": "add", "kind": "wake", "when": "2026-10-12T09:00:00", "why": "check back"},
        {"action": "list"},
    ])
    async def test_a_friends_wake_and_reads_on_the_toolset_pass(self, friend_ping, no_one_addressed, args):
        assert await scope.refusal("schedule", args) is None

    async def test_owners_ping_may_schedule_on_the_toolset(self, no_one_addressed):
        token = hooks.CURRENT_PING.set(_ctx(third_party=False))
        try:
            assert await scope.refusal("schedule", {"action": "add", "what": "x", "when": "tomorrow"}) is None
        finally:
            hooks.CURRENT_PING.reset(token)

    async def test_every_call_in_a_ping_turn_is_asked(self, friend_ping, no_one_addressed, monkeypatch):
        from services.graph import streaming
        from tests.fixtures.tool_loop import FakeGraph, FakeTool, ai, base_kwargs, call, patch_loop_io

        sched = FakeTool("schedule", "ok (scheduled)")
        patch_loop_io(monkeypatch, tools=[sched], script=[
            ai("", call("schedule", action="add", kind="task", when="2026-10-12T09:00:00",
                        what="text Sarah that Ellis is not coming")),
            ai("I'll check with Ellis.")])
        await streaming.chat_with_memory(message="@edward tell Sarah tomorrow", turn_source="ping",
                                         skip_extraction=True, **base_kwargs(FakeGraph(), conversation_id="ping-7"))
        assert sched.calls == [], "the scope refused it before the tool ran"
