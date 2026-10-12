"""services/media/present.py — the ping trigger's media: at most 6 images and
one PDF of at most 8 MB inline, each labelled with its ``attachment:<rowid>``
ref (pings-media-spec §3.3), and everything else as a short text note."""

import base64

import pytest

from services.media import MediaView, hooks, present
from services.graph.streaming import _build_human_message

pytestmark = pytest.mark.asyncio

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


@pytest.fixture
def views(monkeypatch):
    """Fake media.view: ref -> MediaView, recording the max_images asked."""
    table, calls = {}, []

    async def fake_view(ref, *, max_images=6):
        calls.append((ref, max_images))
        return table.get(ref) or MediaView(kind="file", error="that attachment isn't on this Mac yet")

    monkeypatch.setattr(present, "view", fake_view)
    return table, calls


def _att(rowid, mime="image/heic", name=None, label=None):
    d = {"rowid": rowid, "mime_type": mime, "name": name or f"IMG_{rowid}.heic", "path": f"/x/{rowid}"}
    if label:
        d["label"] = label
    return d


class TestImages:
    async def test_at_most_six_images(self, views):
        table, calls = views
        for i in range(1, 10):
            table[f"attachment:{i}"] = MediaView(kind="image", images=[JPEG], text=f"attachment:{i} · image")
        out = await present.turn_attachments([_att(i) for i in range(1, 10)])
        images = [a for a in out if a["mime_type"] == "image/jpeg"]
        assert len(images) == 6
        assert [c[0] for c in calls] == [f"attachment:{i}" for i in range(1, 7)]  # 7..9 never converted
        listed = [a for a in out if a["mime_type"] == "text/plain"]
        assert len(listed) == 3 and 'look("attachment:9")' in base64.b64decode(listed[-1]["data"]).decode()

    async def test_labels_carry_the_ref(self, views):
        table, _ = views
        table["attachment:449321"] = MediaView(kind="image", images=[JPEG], text="")
        out = await present.turn_attachments([_att(449321, label="attachment:449321 · IMG_3533.heic · from Parker 7:20 pm")])
        assert out == [{"filename": "attachment:449321 · IMG_3533.heic · from Parker 7:20 pm", "mime_type": "image/jpeg",
                        "data": base64.b64encode(JPEG).decode("ascii")}]

    async def test_a_default_label(self, views):
        table, _ = views
        table["attachment:5"] = MediaView(kind="image", images=[JPEG], text="")
        out = await present.turn_attachments([_att(5, name="sticker.png")])
        assert out[0]["filename"] == "attachment:5 · sticker.png"

    async def test_animation_frames_share_the_budget(self, views):
        table, calls = views
        table["attachment:1"] = MediaView(kind="image", images=[JPEG] * 4, text="")
        table["attachment:2"] = MediaView(kind="image", images=[JPEG] * 4, text="")
        out = await present.turn_attachments([_att(1, "image/gif"), _att(2, "image/gif")], max_images=6)
        assert sum(1 for a in out if a["mime_type"] == "image/jpeg") == 6
        assert calls == [("attachment:1", 6), ("attachment:2", 2)]
        assert out[1]["filename"].endswith("· frame 2 of 4")

    async def test_an_unlabelled_preview_past_the_budget_is_listed(self, views):
        table, _ = views
        table["attachment:1"] = MediaView(kind="image", images=[JPEG], text="")
        table["attachment:2"] = MediaView(kind="image", images=[JPEG], text="attachment:2 · image")
        out = await present.turn_attachments([_att(1), _att(2, mime=None, name="x.pluginPayloadAttachment")],
                                             max_images=1)
        assert [a["mime_type"] for a in out] == ["image/jpeg", "text/plain"]
        assert 'look("attachment:2")' in base64.b64decode(out[1]["data"]).decode()

    async def test_the_same_attachment_once(self, views):
        table, calls = views
        table["attachment:1"] = MediaView(kind="image", images=[JPEG], text="")
        out = await present.turn_attachments([_att(1), _att(1)])
        assert len(out) == 1 and len(calls) == 1


class TestPdf:
    async def test_one_pdf_inline(self, views):
        table, _ = views
        table["attachment:1"] = MediaView(kind="pdf", pdf=b"%PDF-1", text="")
        table["attachment:2"] = MediaView(kind="pdf", pdf=b"%PDF-2", text="")
        out = await present.turn_attachments([_att(1, "application/pdf", "a.pdf"), _att(2, "application/pdf", "b.pdf")])
        pdfs = [a for a in out if a["mime_type"] == "application/pdf"]
        assert len(pdfs) == 1 and base64.b64decode(pdfs[0]["data"]) == b"%PDF-1"
        note = base64.b64decode(out[1]["data"]).decode()
        assert 'look("attachment:2")' in note

    async def test_a_pdf_over_8_mb_is_listed(self, views, monkeypatch):
        monkeypatch.setattr(present, "TRIGGER_PDF_MAX_BYTES", 10)
        table, _ = views
        table["attachment:1"] = MediaView(kind="pdf", pdf=b"%PDF-" + b"x" * 20, text="")
        out = await present.turn_attachments([_att(1, "application/pdf", "big.pdf")])
        assert out[0]["mime_type"] == "text/plain" and "too big" in base64.b64decode(out[0]["data"]).decode()


class TestNotes:
    async def test_a_transcript_rides_as_text(self, views):
        table, _ = views
        table["attachment:3"] = MediaView(kind="audio", text='attachment:3 · audio — "see you at 7"')
        out = await present.turn_attachments([_att(3, "audio/x-caf", "Audio Message.caf")])
        assert out[0]["mime_type"] == "text/plain"
        assert "see you at 7" in base64.b64decode(out[0]["data"]).decode()

    async def test_an_error_is_said(self, views):
        out = await present.turn_attachments([_att(4)])
        assert "isn't on this Mac yet" in base64.b64decode(out[0]["data"]).decode()

    async def test_video_frames_and_its_words(self, views):
        table, _ = views
        table["attachment:5"] = MediaView(kind="video", images=[JPEG, JPEG], text="a 12 s video — it says: hi")
        out = await present.turn_attachments([_att(5, "video/quicktime", "clip.mov")])
        assert [a["mime_type"] for a in out] == ["image/jpeg", "image/jpeg", "text/plain"]


class TestIntoTheTurn:
    async def test_the_human_message_gets_image_blocks_after_their_labels(self, views):
        table, _ = views
        table["attachment:7"] = MediaView(kind="image", images=[JPEG], text="")
        atts = await present.turn_attachments([_att(7, label="attachment:7 · IMG_7.heic · from Parker 7:20 pm")])
        msg = _build_human_message("[PING] …", atts)
        assert msg.content[1] == {"type": "text", "text": "[Uploaded image: attachment:7 · IMG_7.heic · from Parker 7:20 pm]"}
        assert msg.content[2]["type"] == "image"

    async def test_through_the_hook(self, views):
        table, _ = views
        table["attachment:8"] = MediaView(kind="image", images=[JPEG], text="")
        out = await hooks.turn_attachments([_att(8)])
        assert out and out[0]["mime_type"] == "image/jpeg"

    async def test_counts_for_the_turn(self, views):
        table, _ = views
        table["attachment:1"] = MediaView(kind="image", images=[JPEG], text="")
        table["attachment:2"] = MediaView(kind="pdf", pdf=b"%PDF-1234", text="")
        out = await present.turn_attachments([_att(1), _att(2, "application/pdf", "a.pdf")])
        assert present.media_counts(out) == {"images": 1, "bytes": len(JPEG) + len(b"%PDF-1234")}
