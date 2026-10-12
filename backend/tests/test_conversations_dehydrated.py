"""routers/conversations.py — a PWA upload whose image left saved history
(media.dehydrate) still shows as its photo chip, not as text (pings-media-spec
§3.5)."""

import pytest
from langchain_core.messages import HumanMessage

from routers.conversations import serialize_checkpoint_message

pytestmark = pytest.mark.unit


def _dehydrated(file_id):
    return HumanMessage(
        content=[
            {"type": "text", "text": "what is this?"},
            {"type": "text", "text": "[Uploaded image: IMG_1.jpg | file_id: f-123]"},
            {"type": "text", "text": '[image file:f-123 — not kept in this history. look("file:f-123") to see it again]'},
        ],
        additional_kwargs={"attachments": [{"block_index": 2, "file_id": file_id, "filename": "IMG_1.jpg",
                                            "mime_type": "image/jpeg", "dehydrated": True}]},
    )


def test_a_dehydrated_upload_is_a_chip():
    out = serialize_checkpoint_message(_dehydrated("f-123"))
    assert [(a.file_id, a.filename, a.mime_type) for a in out.attachments] == [("f-123", "IMG_1.jpg", "image/jpeg")]
    assert "not kept in this history" not in out.content
    assert out.content.startswith("what is this?")


def test_without_a_file_the_line_stays_as_text():
    out = serialize_checkpoint_message(_dehydrated(None))
    assert out.attachments is None and "not kept in this history" in out.content
