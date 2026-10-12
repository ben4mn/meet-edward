"""Gmail's OAuth client can come from Google's downloaded client JSON
(2026-10-11): ``GMAIL_CLIENT_SECRETS_FILE`` points at the file the console's
"Download JSON" saves, so the client secret never has to be pasted into
``.env`` by hand. The env vars still win when set."""

from __future__ import annotations

import json
import os

from services import gmail_service


def _client_file(tmp_path, section="web", cid="edward-test.apps.googleusercontent.com", secret="test-secret"):
    p = tmp_path / "client.json"
    p.write_text(json.dumps({section: {"client_id": cid, "client_secret": secret,
                                       "redirect_uris": ["https://edward.example.com/api/gmail/oauth/callback"]}}))
    return p


def test_the_downloaded_client_json_supplies_id_and_secret(tmp_path):
    p = _client_file(tmp_path)
    assert gmail_service.client_credentials({"GMAIL_CLIENT_SECRETS_FILE": str(p)}) == (
        "edward-test.apps.googleusercontent.com", "test-secret")


def test_an_installed_app_file_works_too(tmp_path):
    p = _client_file(tmp_path, section="installed", cid="desk.apps.googleusercontent.com", secret="s2")
    assert gmail_service.client_credentials({"GMAIL_CLIENT_SECRETS_FILE": str(p)}) == (
        "desk.apps.googleusercontent.com", "s2")


def test_the_env_vars_win_over_the_file(tmp_path):
    p = _client_file(tmp_path)
    env = {"GMAIL_CLIENT_ID": "env-id", "GMAIL_CLIENT_SECRET": "env-secret", "GMAIL_CLIENT_SECRETS_FILE": str(p)}
    assert gmail_service.client_credentials(env) == ("env-id", "env-secret")


def test_a_tilde_path_is_expanded(tmp_path, monkeypatch):
    _client_file(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert gmail_service.client_credentials({"GMAIL_CLIENT_SECRETS_FILE": "~/client.json"})[0] == (
        "edward-test.apps.googleusercontent.com")


def test_a_missing_or_broken_file_is_not_configured(tmp_path):
    assert gmail_service.client_credentials({"GMAIL_CLIENT_SECRETS_FILE": str(tmp_path / "nope.json")}) == ("", "")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert gmail_service.client_credentials({"GMAIL_CLIENT_SECRETS_FILE": str(bad)}) == ("", "")


def test_no_path_reads_no_file():
    assert gmail_service.client_credentials({}) == ("", "")
    assert gmail_service.client_credentials({"GMAIL_CLIENT_SECRETS_FILE": ""}) == ("", "")


def test_tests_never_read_owners_client_file():
    # conftest pins it empty before anything loads the production .env.
    assert os.environ.get("GMAIL_CLIENT_SECRETS_FILE") == ""
