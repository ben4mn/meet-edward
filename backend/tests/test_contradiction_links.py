"""Where a contradiction question sends Ellis (UX pass 2026-09-25).

The question linked to ``/settings/memories?flag=<id>``, a page that
ignores the flag; the flag lives in the Dreaming panel. A question that
proposes a rewrite (a surplus triage never rewrites Ellis's memories itself)
says so in its link, so Today does not offer "keep one" for it.
"""

from __future__ import annotations

from types import SimpleNamespace


def test_the_flag_link_opens_the_dreaming_panel():
    from services.contradiction_triage_service import flag_url

    assert flag_url("f-1") == "/settings/dreaming?flag=f-1"
    assert flag_url("f-1", ask="rewrite") == "/settings/dreaming?flag=f-1&ask=rewrite"


def test_a_rewrite_question_is_marked_as_one():
    from services.contradiction_triage_service import rewrite_question

    a = SimpleNamespace(content="Edward's dentist is Dr. Novak")
    b = SimpleNamespace(content="Ellis's dentist is Dr. Park")
    q = rewrite_question({"rewrite_target": "A", "rewrite": "Ellis's dentist is Dr. Novak"}, a, b)
    assert q["ask"] == "rewrite"
    assert q["question"].startswith("I have a memory that says")


async def test_the_note_and_the_push_link_to_the_flag(monkeypatch):
    from services import contradiction_triage_service as cts
    from services import notes_service

    notes, pushes = [], []

    async def fake_note(**kw):
        notes.append(kw)
        return "n1"

    async def fake_push(**kw):
        pushes.append(kw)

    monkeypatch.setattr(notes_service, "leave_note", fake_note)
    monkeypatch.setattr(cts.push_service, "send_push_notification", fake_push)
    flag = SimpleNamespace(id="f-7")
    await cts._note_disambiguation({"question": "Lee or Park?"}, flag)
    await cts._note_disambiguation({"question": "Should it say x?", "ask": "rewrite"}, flag)
    await cts._push_disambiguation({"question": "Lee or Park?"}, flag)
    assert [n["url"] for n in notes] == ["/settings/dreaming?flag=f-7", "/settings/dreaming?flag=f-7&ask=rewrite"]
    assert all(n["needs_you"] and n["title"] == "Which is right?" for n in notes)
    assert pushes[0]["url"] == "/settings/dreaming?flag=f-7"
