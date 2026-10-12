"""Token -> sentence feeder for streaming TTS, plus markdown stripping.

Tokens arrive a few characters at a time. The feed hands back complete
sentences as soon as they close, so TTS can start on sentence one while
the model is still writing sentence three.
"""

from services.voice.sentence_feed import SentenceFeed, strip_for_speech


def _push_all(feed, tokens):
    out = []
    for t in tokens:
        out.extend(feed.push(t))
    return out


def test_no_sentence_until_punctuation_closes_it():
    feed = SentenceFeed()
    assert feed.push("It's 2:27") == []
    assert feed.push(" PM") == []


def test_sentence_released_when_next_one_starts():
    feed = SentenceFeed()
    got = _push_all(feed, ["Sure thing.", " Let me", " check that."])
    assert got == ["Sure thing."]
    assert feed.flush() == "Let me check that."


def test_terminator_followed_by_space_releases_immediately():
    # Holding "First sentence. " until the next capital letter would delay
    # speech through a tool call (found by the barge-in router test).
    feed = SentenceFeed()
    assert feed.push("First sentence. ") == ["First sentence."]
    assert feed.push("Dr. ") == []
    assert feed.flush() == "Dr."


def test_abbreviations_do_not_split():
    feed = SentenceFeed()
    got = _push_all(feed, ["Dr. Smith called.", " He's fine."])
    assert got == ["Dr. Smith called."]


def test_question_and_exclamation_close_sentences():
    feed = SentenceFeed()
    got = _push_all(feed, ["Really?", " Wow!", " Okay"])
    assert got == ["Really?", "Wow!"]
    assert feed.flush() == "Okay"


def test_flush_on_empty_is_none():
    assert SentenceFeed().flush() is None


def test_long_run_without_punctuation_is_released_at_threshold():
    feed = SentenceFeed(max_pending_chars=40)
    got = _push_all(feed, ["one two three four five six seven eight nine ten eleven twelve, and more"])
    assert got and len(got[0]) <= 60
    assert got[0].endswith(",") or got[0].endswith("twelve") or " " in got[0]


def test_markdown_is_stripped_before_speech():
    assert strip_for_speech("**Bold** and _it_ and `code`") == "Bold and it and code"
    assert strip_for_speech("# Heading\n- item one\n- item two") == "Heading. item one. item two"
    assert strip_for_speech("see [the doc](https://x.y/z) now") == "see the doc now"
    assert strip_for_speech("a  b\n\n\nc") == "a b. c"


def test_feed_strips_markdown_from_released_sentences():
    feed = SentenceFeed()
    got = _push_all(feed, ["**Done.**", " Next"])
    assert got == ["Done."]
