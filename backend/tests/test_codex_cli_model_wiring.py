"""
Codex CLI invocation must pass the active model to ``codex exec`` so the
evolution loop uses the same model as the main chat (settings.model).

Context: from 2026-05-18 → 2026-05-26 every evolution cycle failed with
``invalid_request_error: 'gpt-5.2-codex' is not supported when using
Codex with a ChatGPT account``. The CLI was being invoked with no
``-m`` flag, so it fell back to its built-in default which ChatGPT
accounts no longer accept. These tests pin the wiring so the cmd
always carries an explicit, settings-driven model.
"""

import pytest


def test_build_codex_cmd_appends_model_flag_when_provided():
    from services.codex_cli_service import _build_codex_cmd

    cmd = _build_codex_cmd(task="hello", model="gpt-5.5")

    assert cmd[0] == "codex"
    assert "exec" in cmd
    assert "-m" in cmd, f"-m flag missing from cmd: {cmd}"
    assert cmd[cmd.index("-m") + 1] == "gpt-5.5"


def test_build_codex_cmd_omits_model_flag_when_none():
    from services.codex_cli_service import _build_codex_cmd

    cmd = _build_codex_cmd(task="hello", model=None)

    assert "-m" not in cmd
    assert "--model" not in cmd


@pytest.mark.asyncio
async def test_get_default_codex_model_reads_settings_model(monkeypatch):
    """Without an explicit model, the resolver must return whatever the
    user has set as the main chat model in the settings row."""
    from services import codex_cli_service

    class _FakeSettings:
        model = "gpt-5.5"

    class _FakeResult:
        def scalar_one_or_none(self):
            return _FakeSettings()

    class _FakeSession:
        async def execute(self, *args, **kwargs):
            return _FakeResult()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def _fake_async_session():
        return _FakeSession()

    monkeypatch.setattr(
        codex_cli_service, "async_session", _fake_async_session, raising=False
    )

    resolved = await codex_cli_service._get_default_codex_model()
    assert resolved == "gpt-5.5"
