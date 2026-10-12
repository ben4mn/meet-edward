"""services/pings/fallback.py — what Edward posts when the reply turn could
not produce an answer (pings-media-spec Appendix A10)."""

import pytest

from services.pings import fallback

pytestmark = pytest.mark.unit


class TestTemplates:
    @pytest.mark.parametrize("reason", sorted(fallback.TEMPLATES))
    def test_every_template_renders_without_braces(self, reason):
        text = fallback.fallback_text(reason, detail="Overloaded", minutes=10)
        assert text and "{" not in text and "}" not in text

    def test_the_known_reasons(self):
        assert set(fallback.TEMPLATES) >= {
            "spend_cap", "spend_cap_friend", "provider", "timeout", "error", "empty", "refused",
            "incomplete", "scope_unavailable", "rate", "skills_off", "garbled"}

    def test_garbled_says_nothing_was_posted_and_promises_nothing_of_owner(self):
        """A reply that was only a tool call written as text (bench §6.1)."""
        text = fallback.fallback_text("garbled")
        assert "garbled" in text and "ask me again" in text.lower()
        assert "Ellis" not in text and "<" not in text

    def test_timeout_names_the_minutes(self):
        assert "10 minutes" in fallback.fallback_text("timeout", minutes=10)

    def test_spend_cap_is_area4s_sentence(self):
        sentence = "I've reached today's spending limit, so I'm resting until midnight."
        assert fallback.fallback_text("spend_cap", detail=sentence) == sentence

    def test_spend_cap_without_a_sentence_says_something(self):
        assert "limit" in fallback.fallback_text("spend_cap")

    def test_friend_cap_lets_owner_know(self):
        assert "let Ellis know" in fallback.fallback_text("spend_cap_friend")

    def test_unfinished_follows_a_progress_line_honestly(self):
        """Review 2026-10-11: after "On it", a crash or the bound must say so —
        and the words stay true when the earlier send was a whole answer."""
        text = fallback.fallback_text("unfinished", detail="RuntimeError")
        assert "RuntimeError" in text and "cut off" in text and "ask me again" in text.lower()
        assert "()" not in fallback.fallback_text("unfinished")

    def test_unfinished_timeout_names_the_minutes(self):
        text = fallback.fallback_text("unfinished_timeout", minutes=10)
        assert "10 minutes" in text and "ask me again" in text.lower()

    def test_provider_names_the_error_class(self):
        text = fallback.fallback_text("provider", detail="APIConnectionError")
        assert "APIConnectionError" in text and "Ask me again" in text


class TestDetailIsAClassName:
    def test_an_error_message_never_reaches_the_chat(self):
        text = fallback.fallback_text("error", detail="ValueError: password=hunter2 in row (1, 'secret')")
        assert "hunter2" not in text and "secret" not in text
        assert "ValueError" in text

    def test_a_bare_class_is_kept(self):
        assert "TimeoutError" in fallback.fallback_text("provider", detail="TimeoutError")

    def test_no_detail_reads_cleanly(self):
        text = fallback.fallback_text("error")
        assert "()" not in text and "( )" not in text

    def test_an_exception_can_be_passed(self):
        text = fallback.fallback_text("error", detail=KeyError("owner's address"))
        assert "KeyError" in text and "address" not in text


class TestUnknownReason:
    def test_falls_back_to_the_error_template(self):
        assert fallback.fallback_text("no_such_reason", detail="Boom") == fallback.fallback_text("error", detail="Boom")
