"""Images and PDFs never stay in saved history (pings-media-spec §3.5; AREA 2
tools-spec §5.6). At checkpoint each image/PDF block becomes a text line
with what it showed and the ref to look at it again, in place, on a copy.

(a) HumanMessage and ToolMessage blocks; (b) in place, so the attachment
metadata's block_index stays valid, marked ``dehydrated``; (c) the text
carries ``media.hooks.describe_ref`` for the ref found in the label block;
(d) the live messages are untouched."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from services.media import dehydrate, hooks
from services.graph.streaming import _build_human_message, _checkpoint_messages

pytestmark = pytest.mark.unit

B64 = "AAAA"


@pytest.fixture(autouse=True)
def look_installed(monkeypatch):
    """AREA 2's look is the way back to a dehydrated image."""
    monkeypatch.setattr(dehydrate, "_look_available", lambda: True)


def _ping_message():
    return _build_human_message("[PING] can you see that?", [
        {"filename": "attachment:449321 · IMG_3533.heic · from Parker 7:20 pm", "mime_type": "image/jpeg", "data": B64},
        {"filename": "attachment:449400 · menu.pdf", "mime_type": "application/pdf", "data": B64},
    ])


class TestHuman:
    def test_images_become_text_with_the_description_and_the_ref(self):
        hooks.note_description("attachment:449321", "screenshot of a Ticketmaster order")
        msg = _ping_message()
        [out] = dehydrate.strip_media_blocks([msg])
        assert [b["type"] for b in out.content] == ["text", "text", "text", "text", "text"]
        assert out.content[2]["text"] == ('[image attachment:449321 — not kept in this history: '
                                          '"screenshot of a Ticketmaster order". '
                                          'look("attachment:449321") to see it again]')
        assert out.content[4]["text"].startswith('[PDF attachment:449400 — not kept in this history. '
                                                 'look("attachment:449400")')

    def test_block_index_stays_valid_and_is_marked(self):
        msg = _ping_message()
        [out] = dehydrate.strip_media_blocks([msg])
        metas = out.additional_kwargs["attachments"]
        assert [m["block_index"] for m in metas] == [2, 4]
        assert all(m["dehydrated"] is True for m in metas)

    def test_the_live_message_is_untouched(self):
        msg = _ping_message()
        dehydrate.strip_media_blocks([msg])
        assert msg.content[2]["type"] == "image" and "dehydrated" not in msg.additional_kwargs["attachments"][0]

    def test_a_pwa_upload_keeps_its_file_ref(self):
        msg = _build_human_message("what is this?", [
            {"filename": "IMG_1.jpg", "file_id": "f-123", "mime_type": "image/jpeg", "data": B64}])
        [out] = dehydrate.strip_media_blocks([msg])
        assert 'look("file:f-123")' in out.content[2]["text"]
        assert out.additional_kwargs["attachments"][0]["file_id"] == "f-123"

    def test_without_a_ref_a_persons_image_is_kept_whole(self):
        """AREA 2's review fix (2026-10-11), kept at the merge: nothing could
        open it again, so something a person sent is kept rather than lost
        to every later turn."""
        msg = HumanMessage(content=[{"type": "text", "text": "hi"},
                                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": B64}}])
        [out] = dehydrate.strip_media_blocks([msg])
        assert out is msg

    def test_without_a_ref_a_tool_image_becomes_a_line(self):
        msg = ToolMessage(tool_call_id="c9", content=[
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": B64}}])
        [out] = dehydrate.strip_media_blocks([msg])
        assert out.content[0] == {"type": "text", "text": "[image — not kept in this history]"}

    def test_one_label_names_every_image_in_its_run(self):
        """A look result names its target once, then its pages (AREA 2's
        history_window rule, kept at the merge)."""
        img = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": B64}}
        msg = ToolMessage(tool_call_id="c2", content=[{"type": "text", "text": "media:12 · 2 pages"}, img, dict(img)])
        [out] = dehydrate.strip_media_blocks([msg])
        assert 'look("media:12")' in out.content[1]["text"] and 'look("media:12")' in out.content[2]["text"]

    def test_image_url_blocks_too(self):
        msg = HumanMessage(content=[{"type": "text", "text": "[Uploaded image: attachment:5 · x.jpg]"},
                                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}])
        [out] = dehydrate.strip_media_blocks([msg])
        assert out.content[1]["type"] == "text" and 'look("attachment:5")' in out.content[1]["text"]

    def test_text_only_messages_are_the_same_objects(self):
        msgs = [HumanMessage(content="hi"), AIMessage(content="hello")]
        assert all(a is b for a, b in zip(dehydrate.strip_media_blocks(msgs), msgs))


class TestBeforeLookExists:
    def test_an_attachment_points_at_todays_tool(self, monkeypatch):
        monkeypatch.setattr(dehydrate, "_look_available", lambda: False)
        [out] = dehydrate.strip_media_blocks([_ping_message()])
        assert "get_imessage_attachment(449321) to see it again" in out.content[2]["text"]

    def test_a_pwa_upload_stays_as_today(self, monkeypatch):
        monkeypatch.setattr(dehydrate, "_look_available", lambda: False)
        msg = _build_human_message("what is this?", [
            {"filename": "IMG_1.jpg", "file_id": "f-123", "mime_type": "image/jpeg", "data": B64}])
        [out] = dehydrate.strip_media_blocks([msg])
        assert out is msg


class TestTool:
    def test_a_look_result_is_dehydrated(self):
        msg = ToolMessage(tool_call_id="c1", content=[
            {"type": "text", "text": "attachment:77 · image · IMG_77.heic · 2.1 MB"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": B64}}])
        [out] = dehydrate.strip_media_blocks([msg])
        assert out.tool_call_id == "c1" and out.content[1]["type"] == "text"
        assert 'look("attachment:77")' in out.content[1]["text"]


class TestCheckpoint:
    def test_checkpoint_messages_keep_no_base64(self):
        msgs = [_ping_message(), AIMessage(content="I can see it — it's a Ticketmaster order.")]
        saved = _checkpoint_messages(msgs)
        assert B64 not in str([m.content for m in saved])
        assert msgs[0].content[2]["type"] == "image"

    def test_media_off_keeps_history_as_it_was(self, monkeypatch):
        monkeypatch.setenv("EDWARD_MEDIA", "off")
        msgs = [_ping_message()]
        assert _checkpoint_messages(msgs)[0].content[2]["type"] == "image"

    def test_media_off_still_strips_a_look_result(self, monkeypatch):
        """``EDWARD_MEDIA=off`` rolls back AREA 3's ping media; a ``look``
        result is AREA 2's, and its base64 never stays in history
        (tools-spec §5.6)."""
        monkeypatch.setenv("EDWARD_MEDIA", "off")
        tm = ToolMessage(tool_call_id="c1", content=[
            {"type": "text", "text": "attachment:77 · image"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": B64}}])
        [out] = dehydrate.strip_media_blocks([tm])
        assert out.content[1]["type"] == "text"

    def test_stripped_exactly_once_through_the_history_window(self, monkeypatch):
        """The merge (2026-10-11): ``_checkpoint_messages`` strips media once,
        through ``history_window.strip_media_blocks``, which calls this module."""
        from services.graph import history_window

        calls = []
        real = dehydrate.strip_media_blocks

        def counting(messages):
            calls.append(len(messages))
            return real(messages)

        monkeypatch.setattr(dehydrate, "strip_media_blocks", counting)
        saved = _checkpoint_messages([_ping_message(), AIMessage(content="ok")])
        assert len(calls) == 1
        assert B64 not in str([m.content for m in saved])
        calls.clear()
        history_window.strip_media_blocks([_ping_message()])
        assert len(calls) == 1
