"""``secret_scan.redact_secrets`` — the redaction every shell/editor output
passes through before the model reads it."""

from __future__ import annotations

import random
import time

import pytest

from services import secret_scan


def test_redacts_known_env_value():
    secret = "ghp_" + "A1b2C3d4E5f6G7h8J9k0L1m2N3o4P5q6R7s8"
    text = f"my token is {secret} ok"
    red, kinds = secret_scan.redact_secrets(text, {"GITHUB_TOKEN": secret})
    assert secret not in red
    assert "[redacted:" in red
    assert any(k.startswith("github") or k == "env:GITHUB_TOKEN" for k in kinds)


def test_redacts_patterned_secret_without_known():
    key = "sk-ant-" + "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg0"
    red, kinds = secret_scan.redact_secrets(f"ANTHROPIC_API_KEY={key}", None)
    assert key not in red
    assert "anthropic_key" in kinds


def test_quiet_on_ordinary_text():
    text = "The meeting is at 3pm on 2026-10-11, color #ff8800, commit a1b2c3d."
    red, kinds = secret_scan.redact_secrets(text, None)
    assert red == text
    assert kinds == []


def test_multiple_secrets_all_redacted():
    a = "sk-ant-" + "A1b2C3d4E5f6G7h8J9k0L1m2N3o4P5"
    b = "AKIA" + "ABCDEFGHIJKLMNOP"
    red, kinds = secret_scan.redact_secrets(f"first {a} then {b}", None)
    assert a not in red and b not in red
    assert len(kinds) >= 2


def test_non_string_and_empty_safe():
    assert secret_scan.redact_secrets(None, None) == ("", [])
    red, kinds = secret_scan.redact_secrets(12345, None)
    assert red == "12345" and kinds == []


def test_known_value_short_is_ignored():
    # A value below the min length is not treated as a secret.
    red, _ = secret_scan.redact_secrets("value=abc", {"X_KEY": "abc"})
    assert red == "value=abc"


def test_google_oauth_tokens_are_redacted():
    access = "ya" + "29.a0AfB_byC" + "Xq7Lm2Np9Rs4Tv6Wx8Yz1Ab3Cd5Ef7Gh9Ij0Kl2Mn4Op6Qr8St"
    refresh = "1/" + "/0gLdQ3vX8" + "Zp2Rk7Tn4Wm9Yb6Hc1Jf5Ls3Qd8Ve0Xg2Ni7Uo4"
    text = f"gmail said access={access} and refresh {refresh}."
    red, kinds = secret_scan.redact_secrets(text, None)
    assert access not in red and refresh not in red
    assert red == ("gmail said access=[redacted: google_oauth_token] and refresh "
                   "[redacted: google_refresh_token].")
    assert kinds == ["google_oauth_token", "google_refresh_token"]


def test_ordinary_ids_and_links_are_never_redacted():
    text = ("commit 9fceb02d0ae598e95dc970b74767f19372d61af8, event 123e4567-e89b-12d3-a456-426614174000, "
            "chat chat123456789012345678901 (iMessage;+;chat987654321098765432), attachment:449321, "
            "https://edward.example.com/chat?c=3f2b8c1e-9a4d-4e7b-8c2a-1d5e6f7a8b9c and "
            "http://localhost:3001//api_endpoint_for_the_long_name_here")
    assert secret_scan.redact_secrets(text, None) == (text, [])


def test_a_long_run_of_token_characters_redacts_in_linear_time():
    """Every local tool result passes through here now (``tool_loop``), on
    the event loop. With the generic rule's key-name prefix unbounded, a
    100K-char base64url blob took ~9 s (each ``-``/``_`` restarted a scan to
    the end of the run: quadratic)."""
    import base64
    import random
    import time

    blob = base64.urlsafe_b64encode(random.Random(7).randbytes(75_000)).decode()
    t0 = time.monotonic()
    red, kinds = secret_scan.redact_secrets(f"page body: {blob} end", None)
    assert time.monotonic() - t0 < 1.5
    assert kinds == []


def test_a_long_key_name_still_counts():
    value = "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2"
    red, kinds = secret_scan.redact_secrets(f"EDWARD_HOUSEHOLD_HUB_SPOTIFY_CLIENT_SECRET={value}", None)
    assert value not in red and kinds == ["generic_secret"]


# One crafted run per rule, ~100K chars: a long run of the rule's own
# prefix. A rule whose start can fall inside a run of token characters
# (``\beyJ`` matches after every ``-``) rescans to the run's end from each
# start — quadratic: ``-eyJ`` × 25 000 took ~3 s, × 50 000 ~12 s. Untrusted
# text reaches here whole (an iMessage thread, a fetched page).
CRAFTED_RUNS = ["-eyJ", "-xoxb-", "-ghp_", "-github_pat_", "-sk-", "x-sk-ant-", "-AIza", "-AKIA", "-ya29.",
                "-1//", "Bearer-", "token-", "-----BEGIN A ", '"access_token":"', "vapid_private_key="]


@pytest.mark.parametrize("unit", CRAFTED_RUNS)
def test_a_crafted_run_of_one_rules_prefix_redacts_in_linear_time(unit):
    text = unit * (100_000 // len(unit))
    t0 = time.monotonic()
    secret_scan.redact_secrets(text, None)
    secret_scan.find_secrets(text)
    assert time.monotonic() - t0 < 1.0


def test_many_secrets_in_one_result_redact_in_linear_time():
    """Each new span was checked against every span taken so far (O(k²)):
    16 000 keys in 448K chars took ~10 s through both scans; ~1.5 s now
    (most of it each key's entropy check)."""
    rnd = random.Random(11)
    alnum = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    keys = ["sk" + "-" + "".join(rnd.choice(alnum) for _ in range(24)) for _ in range(16_000)]
    text = " ".join(keys)
    t0 = time.monotonic()
    red, kinds = secret_scan.redact_secrets(text, None)
    found = secret_scan.find_secrets(text)
    assert time.monotonic() - t0 < 4.0
    assert kinds == ["openai_key"] and keys[0] not in red and keys[-1] not in red
    assert red.count("[redacted: openai_key]") == len(keys) and len(found) == len(keys)


def test_overlapping_rules_still_take_the_first_span_only():
    """The interval check that replaced the linear scan keeps the old
    semantics: a later rule's match inside an earlier one is the same
    secret, a match beside it is another."""
    key = "sk" + "-" + "Q7rT2vX9zB4nM6pL8kJ3hG5fD1sA0wE7yU2iO9pR4tY6"
    aws = "AK" + "IA" + "QWERTYUIOPASDFGH"
    text = f"OPENAI_API_KEY={key} and aws={aws}, then {key}"
    red, kinds = secret_scan.redact_secrets(text, None)
    assert red == ("OPENAI_API_KEY=[redacted: openai_key] and aws=[redacted: aws_access_key], "
                   "then [redacted: openai_key]")
    assert [f.kind for f in secret_scan.find_secrets(text)] == ["openai_key", "aws_access_key"]
