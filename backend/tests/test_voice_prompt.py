"""Voice turns get a spoken-style system prompt and the fastest model tier."""

import importlib


def test_voice_prompt_appends_spoken_guidance_once():
    from services.voice.prompt import VOICE_STYLE_PROMPT, voice_system_prompt

    base = "You are Edward."
    out = voice_system_prompt(base)
    assert out.startswith(base)
    assert VOICE_STYLE_PROMPT in out
    assert out.count(VOICE_STYLE_PROMPT) == 1
    assert voice_system_prompt(out).count(VOICE_STYLE_PROMPT) == 1
    assert "aloud" in VOICE_STYLE_PROMPT.lower()
    assert "markdown" in VOICE_STYLE_PROMPT.lower()


def test_voice_model_defaults_to_luna_and_honours_env(monkeypatch):
    import services.voice.prompt as prompt

    monkeypatch.delenv("EDWARD_VOICE_MODEL", raising=False)
    importlib.reload(prompt)
    assert prompt.VOICE_MODEL == "gpt-5.6-luna"

    monkeypatch.setenv("EDWARD_VOICE_MODEL", "gpt-5.6-terra")
    importlib.reload(prompt)
    assert prompt.VOICE_MODEL == "gpt-5.6-terra"
    monkeypatch.delenv("EDWARD_VOICE_MODEL", raising=False)
    importlib.reload(prompt)
