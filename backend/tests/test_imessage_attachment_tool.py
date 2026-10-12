"""get_imessage_attachment: Edward can look at a photo someone texted."""

import pytest

pytestmark = pytest.mark.unit


def test_tool_is_gated_with_the_other_imessage_tools():
    from services.tool_registry import SKILL_TOOL_MAPPING
    assert "get_imessage_attachment" in SKILL_TOOL_MAPPING["imessage_applescript"]
    import inspect

    from services import tool_registry

    assert '"get_imessage_attachment"' in inspect.getsource(tool_registry._get_messaging_tools)


async def test_tool_describes_and_stores_the_image(monkeypatch, tmp_path):
    from services.graph import tools
    from services import attachment_vision as av
    from services.heartbeat import listener_imessage as listener

    p = tmp_path / "p.jpeg"; p.write_bytes(b"\xff\xd8\xff")
    monkeypatch.setattr(listener, "find_attachment", lambda rowid: {"rowid": rowid, "mime_type": "image/jpeg", "path": str(p), "name": "p.jpeg"})

    async def fake_desc(path, **kw):
        return "Biscuit asleep on the couch."

    async def fake_store(path, **kw):
        return {"id": "file-1", "url": "/api/files/file-1/download"}

    monkeypatch.setattr(av, "describe_image", fake_desc)
    monkeypatch.setattr(av, "store_attachment_copy", fake_store)
    out = await tools.get_imessage_attachment.ainvoke({"attachment_rowid": 7})
    assert "Biscuit asleep" in out and "file-1" in out
    monkeypatch.setattr(listener, "find_attachment", lambda rowid: None)
    assert "not found" in (await tools.get_imessage_attachment.ainvoke({"attachment_rowid": 8})).lower()
