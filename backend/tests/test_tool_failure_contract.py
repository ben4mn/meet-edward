"""The uniform failure contract (tools-spec §3, §5.3.5).

Before the toolset, only results that began ``Tool error:`` counted as
failures — against ~115 other failure shapes ("Failed to send iMessage: …",
"Memory with ID x not found", "Push notification failed …"). The audit
found ``fetch_page_content`` failing 18% of the time while the ledger said
0%. Now every failure is ``Tool error: <the real error>`` with
``is_error``, whichever way the tool failed: by raising ``ToolFailure``,
by raising anything else, or by returning an error-shaped string.
"""

from __future__ import annotations

import pytest

from services.toolset import result as r

pytestmark = pytest.mark.unit


class TestNormalize:
    def test_a_tool_failure_is_a_tool_error_with_the_real_message(self):
        out = r.normalize(r.ToolFailure("no contact matches 'Parker'"))
        assert out.is_error
        assert out.content == "Tool error: no contact matches 'Parker'"

    def test_a_tool_failure_already_prefixed_is_not_doubled(self):
        out = r.normalize(r.ToolFailure("Tool error: stand-down active"))
        assert out.content == "Tool error: stand-down active"

    def test_any_exception_carries_its_type_and_message(self):
        out = r.normalize(ValueError("boom: the disk is full"))
        assert out.is_error
        assert out.content == "Tool error: ValueError: boom: the disk is full"

    def test_an_exception_with_no_message_still_says_what_it_was(self):
        out = r.normalize(TimeoutError())
        assert out.content == "Tool error: TimeoutError"

    @pytest.mark.parametrize("text", ["Tool error: x", "Unknown tool: frob", "BLOCKED: no", "  Tool error: y"])
    def test_error_shaped_strings_are_errors(self, text):
        out = r.normalize(text)
        assert out.is_error
        assert out.content == text

    def test_an_ordinary_string_is_a_success(self):
        out = r.normalize("Sent via iMessage to Parker")
        assert not out.is_error and out.content == "Sent via iMessage to Parker"

    def test_a_tool_result_flagged_as_error_gets_the_prefix(self):
        out = r.normalize(r.ToolResult("Couldn't reach the page", is_error=True))
        assert out.is_error
        assert out.content == "Tool error: Couldn't reach the page"

    def test_content_blocks_are_kept(self):
        blocks = [{"type": "text", "text": "a photo"},
                  {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}}]
        out = r.normalize(blocks)
        assert not out.is_error
        assert out.content == blocks
        assert r.text_of(out.content) == "a photo\n[image]"

    def test_blocks_flagged_as_an_error_get_the_prefix_on_their_text(self):
        out = r.normalize(r.ToolResult([{"type": "text", "text": "no frames"}], is_error=True))
        assert out.is_error
        assert out.content[0]["text"] == "Tool error: no frames"

    def test_none_and_dicts(self):
        assert r.normalize(None).content == "(no output)"
        assert r.normalize({"a": 1}).content == '{"a": 1}'


class TestTheLedgerCountsEveryFailure:
    def test_classify_tool_result_sees_normalized_failures(self):
        from services.turn_ledger import classify_tool_result

        for raw in (r.ToolFailure("not found"), KeyError("id"), r.ToolResult("nope", is_error=True)):
            assert classify_tool_result(r.normalize(raw).text) is not None
        assert classify_tool_result(r.normalize("ok (memory_id: 1)").text) is None
