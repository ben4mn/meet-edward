"""A chat turn's thinking and tool use survive a reload (UX pass 2026-09-25).

Ellis insists the work stays visible (CLAUDE.md, "Chat work rail"), but the
reasoning summary, the tool calls and the memory count lived only in the
SSE stream: reload the conversation and the answer stood alone. The
streaming loop now keeps a compact trace on the turn's final AIMessage
(``additional_kwargs["trace"]``) and ``GET /api/conversations/{id}``
returns it as ``message.trace``:

    {"reasoning": str | None,            # the reasoning summary, ≤ 4K chars
     "tools": [{"name", "args_summary",  # ≤ 120 chars, never a message body
                "status": "ok" | "error", "duration_ms", "result_preview"}],  # ≤ 200
     "memory_hits": int | None}

No secret survives into it: every string goes through the egress secret
scanner, and a send's body or a typed field is never summarised.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from services.graph import turn_trace as tt


def _reasoning(*texts):
    return {"reasoning_items": [{"type": "reasoning", "summary": [{"type": "summary_text", "text": t}
                                                                 for t in texts]}]}


# --- the builder (pure) ----------------------------------------------------------

def test_a_turn_with_two_tools_is_traced():
    b = tt.TraceBuilder()
    b.note_response(AIMessage(content="", additional_kwargs=_reasoning("Looking for quiet ones.")))
    b.note_tool("web_search", {"query": "quiet dishwasher"}, "1. Bosch 800\n2. Miele G7000", 412)
    b.note_tool("remember_search", {"query": "kitchen"}, "Tool error: database unavailable", 9)
    trace = b.build(memory_hits=3)
    assert trace["reasoning"] == "Looking for quiet ones."
    assert trace["memory_hits"] == 3
    web, mem = trace["tools"]
    assert web == {"name": "web_search", "args_summary": '"quiet dishwasher"', "status": "ok",
                   "duration_ms": 412, "result_preview": "1. Bosch 800 2. Miele G7000"}
    assert mem["status"] == "error" and mem["result_preview"].startswith("Tool error:")


def test_everything_is_capped():
    b = tt.TraceBuilder()
    b.note_response(AIMessage(content="", additional_kwargs=_reasoning("r" * 9000)))
    for i in range(tt.MAX_TOOLS + 7):
        b.note_tool("web_search", {"query": "q" * 500}, "x" * 5000, i)
    trace = b.build(memory_hits=None)
    assert len(trace["reasoning"]) <= tt.REASONING_MAX_CHARS
    assert len(trace["tools"]) == tt.MAX_TOOLS
    assert trace["tools_omitted"] == 7
    for t in trace["tools"]:
        assert len(t["args_summary"]) <= tt.ARGS_SUMMARY_MAX_CHARS
        assert len(t["result_preview"]) <= tt.RESULT_PREVIEW_MAX_CHARS


def test_reasoning_prefers_the_summaries_and_falls_back_to_the_streamed_deltas():
    b = tt.TraceBuilder()
    b.note_reasoning_delta("Thinking about ")
    b.note_reasoning_delta("the flight.")
    assert b.build(memory_hits=None)["reasoning"] == "Thinking about the flight."
    b.note_response(AIMessage(content="", additional_kwargs=_reasoning("Thinking about the flight.")))
    b.note_response(AIMessage(content="", additional_kwargs=_reasoning("Thinking about the flight.", "Then Parker.")))
    assert b.build(memory_hits=None)["reasoning"] == "Thinking about the flight.\n\nThen Parker."


def test_no_secret_reaches_the_trace():
    key = "sk-proj-" + "Ab3dE9fGh1JkLmN0pQrS7tUvWx2Yz"
    b = tt.TraceBuilder()
    b.note_tool("fetch_page_content", {"url": f"https://x.test/?k={key}"}, f"echo {key}", 5)
    b.note_tool("execute_shell", {"command": "cat ~/.env"}, "TWILIO_AUTH_TOKEN=9f8e7d6c5b4a39281706f5e4d3c2b1a0", 5)
    for t in b.build(memory_hits=None)["tools"]:
        assert key not in str(t) and "9f8e7d6c5b4a" not in str(t), t


def test_a_send_body_or_a_typed_field_is_never_summarised():
    b = tt.TraceBuilder(summarize=lambda name, args: f'"{args.get("text") or args.get("message")}"')
    b.note_tool("browser_type", {"ref": 4, "text": "hunter2-my-password"}, "typed", 3)
    b.note_tool("whatsapp_send_message", {"recipient": "Parker", "message": "the gate code is 4411"}, "sent", 3)
    b.note_tool("send_imessage", {"to": "Parker", "message": "see you at 5"}, "Sent to Parker.", 3)
    tools = b.build(memory_hits=None)["tools"]
    assert "hunter2" not in str(tools) and "4411" not in str(tools) and "see you at 5" not in str(tools)
    assert tools[0]["args_summary"] is None  # a typed field: nothing at all
    # A send says who it went to, never what it said.
    assert tools[1]["args_summary"] == "to: Parker" and tools[2]["args_summary"] == "to: Parker"


def test_closing_an_intention_is_summarised_by_its_outcome_never_its_id():
    """close_intention (2026-09-27): the rail reads "Closed an intention" with
    what came of it beside it. The default summary took the first string
    argument — the memory id, a UUID chip nobody can read."""
    from services.graph.streaming import _summarize_tool_args

    args = {"memory_id": "5b1f0c2e-8d7a-4c1b-9e3f-2a6d8c4b7e10", "outcome": "Briefed Theo on Friday"}
    assert _summarize_tool_args("close_intention", args) == '"Briefed Theo on Friday"'
    b = tt.TraceBuilder(summarize=_summarize_tool_args)
    b.note_tool("close_intention", args, "ok (memory_id: 5b1f… closed)", 4)
    assert b.build(memory_hits=None)["tools"][0]["args_summary"] == '"Briefed Theo on Friday"'


def test_a_bare_turn_carries_no_trace():
    assert tt.TraceBuilder().build(memory_hits=0) is None
    assert tt.TraceBuilder().build(memory_hits=None) is None
    assert tt.TraceBuilder().build(memory_hits=2) == {"reasoning": None, "tools": [], "memory_hits": 2}


# --- the loop keeps it on the answer ------------------------------------------------

async def test_the_streaming_turn_keeps_its_trace_on_the_final_message(monkeypatch):
    from tests.fixtures.tool_loop import FakeGraph, FakeTool, ai, base_kwargs, call, patch_loop_io, run_stream

    patch_loop_io(monkeypatch, tools=[FakeTool("web_search", "1. Bosch 800"),
                                      FakeTool("remember_search", "Tool error: boom")],
                  script=[ai("", call("web_search", query="quiet dishwasher"), call("remember_search", query="kitchen"),
                             additional_kwargs=_reasoning("Checking what's quiet.")),
                          ai("The Bosch 800 is the quiet one.")])
    graph = FakeGraph()
    await run_stream(message="find me a quiet dishwasher", **base_kwargs(graph))
    final = [m for m in graph.messages if isinstance(m, AIMessage)][-1]
    assert final.content == "The Bosch 800 is the quiet one."
    trace = final.additional_kwargs.get("trace")
    assert trace is not None, final.additional_kwargs
    assert trace["reasoning"] == "Checking what's quiet."
    assert [(t["name"], t["status"]) for t in trace["tools"]] == [("web_search", "ok"), ("remember_search", "error")]
    assert all(isinstance(t["duration_ms"], int) for t in trace["tools"])
    assert trace["memory_hits"] == 0


# --- and the conversation API returns it --------------------------------------------

def test_serialization_returns_the_trace_and_old_messages_still_serialize():
    from routers.conversations import serialize_checkpoint_message

    trace = {"reasoning": "hm", "tools": [{"name": "web_search", "args_summary": '"x"', "status": "ok",
                                            "duration_ms": 5, "result_preview": "r"}], "memory_hits": 1}
    with_trace = serialize_checkpoint_message(AIMessage(content="answer", additional_kwargs={"trace": trace}))
    assert with_trace.trace == trace
    assert with_trace.model_dump()["trace"] == trace
    old = serialize_checkpoint_message(AIMessage(content="answer"))
    assert old.content == "answer" and old.trace is None
    human = serialize_checkpoint_message(HumanMessage(content="q", additional_kwargs={"trace": trace}))
    assert human.trace is None  # only Edward's turns carry one
    assert serialize_checkpoint_message(ToolMessage(content="x", tool_call_id="1")) is None
    odd = serialize_checkpoint_message(AIMessage(content="answer", additional_kwargs={"trace": "not a dict"}))
    assert odd.trace is None
