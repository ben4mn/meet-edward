"""Images in saved history (tools-spec §5.6; pings-media-spec §3.5).

A ``look`` result or a ping's photo goes to the model as image blocks for
the turn that asked. In the checkpoint they become a line naming the ref,
so later turns don't re-pay for (or evict on) base64. Requirements from
AREA 3: (a) HumanMessage blocks as well as ToolMessage blocks; (b) blocks
replaced in place, so ``attachments[].block_index`` stays valid, and that
entry marked ``dehydrated``; (c) the line carries ``describe_ref`` when
AREA 3's media module knows the ref; (d) the checkpoint copy only.
"""

from __future__ import annotations

import sys
import types

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from services.graph.history_window import message_cost, strip_media_blocks

pytestmark = pytest.mark.unit

IMG = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "A" * 200_000}}
PDF = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "B" * 50_000}}


def _human_with_photo():
    return HumanMessage(
        content=[{"type": "text", "text": "@edward look at this"},
                 {"type": "text", "text": "[Uploaded image: attachment:449321 · IMG_3533.heic · from Parker 7:20 pm]"},
                 IMG],
        additional_kwargs={"attachments": [{"block_index": 2, "file_id": None, "filename": "IMG_3533.heic",
                                            "mime_type": "image/jpeg"}]},
        id="h1")


def test_tool_message_images_become_a_line():
    tm = ToolMessage(content=[{"type": "text", "text": "attachment:12 · image from Rowan"}, IMG],
                     tool_call_id="c1", id="t1")
    (out,) = strip_media_blocks([tm])
    assert out.content[0] == tm.content[0]
    assert out.content[1]["type"] == "text"
    assert 'look("attachment:12")' in out.content[1]["text"]
    assert "A" * 100 not in str(out.content)


def test_human_message_blocks_in_place_and_dehydrated():
    hm = _human_with_photo()
    (out,) = strip_media_blocks([hm])
    assert len(out.content) == 3
    assert out.content[2]["type"] == "text" and "attachment:449321" in out.content[2]["text"]
    assert out.additional_kwargs["attachments"][0]["dehydrated"] is True
    assert out.additional_kwargs["attachments"][0]["block_index"] == 2


def test_the_description_rides_when_media_knows_the_ref(monkeypatch):
    from services.media import hooks

    monkeypatch.setattr(hooks, "describe_ref",
                        lambda ref: "screenshot of a Ticketmaster order" if ref == "attachment:449321" else None)
    (out,) = strip_media_blocks([_human_with_photo()])
    assert '"screenshot of a Ticketmaster order"' in out.content[2]["text"]


def test_without_area_3s_media_module_the_window_strips_on_its_own(monkeypatch):
    """The fallback: no ``services.media`` (AREA 3 rolled back) — the
    window's own rule, the description from nowhere."""
    hooks = types.ModuleType("services.media.hooks")
    hooks.describe_ref = lambda ref: "screenshot of a Ticketmaster order" if ref == "attachment:449321" else None
    pkg = types.ModuleType("services.media")
    pkg.hooks = hooks
    monkeypatch.setitem(sys.modules, "services.media", pkg)
    monkeypatch.setitem(sys.modules, "services.media.hooks", hooks)
    monkeypatch.delitem(sys.modules, "services.media.dehydrate", raising=False)
    (out,) = strip_media_blocks([_human_with_photo()])
    assert '"screenshot of a Ticketmaster order"' in out.content[2]["text"]
    assert 'look("attachment:449321")' in out.content[2]["text"]


def test_the_originals_are_untouched():
    hm = _human_with_photo()
    strip_media_blocks([hm])
    assert hm.content[2] == IMG
    assert "dehydrated" not in hm.additional_kwargs["attachments"][0]


def test_pdfs_too_and_plain_messages_pass():
    tm = ToolMessage(content=[{"type": "text", "text": "a.pdf"}, PDF], tool_call_id="c", id="t")
    plain = [HumanMessage(content="hi", id="h"), AIMessage(content="hello", id="a")]
    out = strip_media_blocks([tm] + plain)
    assert out[0].content[1]["type"] == "text" and "PDF" in out[0].content[1]["text"]
    assert out[1] is plain[0] and out[2] is plain[1]


def test_an_image_costs_its_tokens_not_its_base64():
    tm = ToolMessage(content=[{"type": "text", "text": "x"}, IMG], tool_call_id="c")
    assert message_cost(tm) < 10_000
    assert message_cost(tm) > 5_000


def test_checkpoint_messages_strip_them():
    from services.graph.streaming import _checkpoint_messages

    tm = ToolMessage(content=[{"type": "text", "text": "attachment:12"}, IMG], tool_call_id="c1", id="t1")
    call = AIMessage(content="", tool_calls=[{"id": "c1", "name": "look", "args": {"target": "attachment:12"}}],
                     id="a1")
    out = _checkpoint_messages([HumanMessage(content="see", id="h"), call, tm, AIMessage(content="ok", id="a2")])
    assert "A" * 100 not in str([m.content for m in out])
    assert tm.content[1] == IMG


def test_a_dehydrated_upload_still_shows_as_a_photo():
    """The PWA rebuilds the chip from file storage (pings-media-spec §3.5)."""
    from routers.conversations import serialize_checkpoint_message

    hm = HumanMessage(
        content=[{"type": "text", "text": "look"}, {"type": "text", "text": "[Uploaded image: cat.jpg | file_id: f1]"},
                 IMG],
        additional_kwargs={"attachments": [{"block_index": 2, "file_id": "f1", "filename": "cat.jpg",
                                            "mime_type": "image/jpeg"}]}, id="h1")
    (saved,) = strip_media_blocks([hm])
    out = serialize_checkpoint_message(saved)
    assert out.attachments and out.attachments[0].file_id == "f1"
    assert "not kept in this history" not in out.content


class TestPwaUploads:
    """A photo or PDF Ellis uploads in the PWA is labelled ``[Uploaded image:
    X | file_id: Y]``; ``file_id:`` is not ``file:``, so the saved line
    named no target and look could not open one — every follow-up lost the
    picture (review 2026-10-11)."""

    def _upload(self, mime="image/png", file_id="7f3c2a10-aaaa-bbbb-cccc-000000000001"):
        from services.graph.streaming import _build_human_message

        return _build_human_message("what's the total?", [
            {"mime_type": mime, "data": "QUJD" * 10, "filename": "receipt.png" if "image" in mime else "bill.pdf",
             "file_id": file_id}])

    def test_the_saved_line_names_a_file_ref(self):
        hm = self._upload()
        (saved,) = strip_media_blocks([hm])
        line = saved.content[-1]["text"]
        assert 'look("file:7f3c2a10-aaaa-bbbb-cccc-000000000001")' in line

    def test_a_pdf_upload_too(self):
        (saved,) = strip_media_blocks([self._upload(mime="application/pdf")])
        assert 'look("file:7f3c2a10-aaaa-bbbb-cccc-000000000001")' in saved.content[-1]["text"]

    def test_an_upload_with_no_ref_is_kept_whole(self):
        """Nothing could open it again, so it is not dehydrated."""
        hm = self._upload(file_id="")
        (saved,) = strip_media_blocks([hm])
        assert saved.content[-1]["type"] == "image"

    async def test_look_opens_the_named_upload(self, tmp_path, monkeypatch):
        import base64

        from services import file_storage_service
        from services.toolset import look as lk

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
        stored = tmp_path / "7f" / "7f3c2a10_receipt.png"
        stored.parent.mkdir(parents=True)
        stored.write_bytes(png)

        async def get_file_path(file_id):
            return stored if file_id == "7f3c2a10-aaaa-bbbb-cccc-000000000001" else None

        monkeypatch.setattr(file_storage_service, "get_file_path", get_file_path)
        monkeypatch.setattr(lk, "_media_view", None)
        (saved,) = strip_media_blocks([self._upload()])
        ref = saved.content[-1]["text"].split('look("', 1)[1].split('")', 1)[0]
        seen = await lk._resolve(ref)
        assert len(seen.images) == 1 and "receipt.png" in seen.label
        with pytest.raises(Exception, match="no uploaded file"):
            await lk._resolve("file:missing")
