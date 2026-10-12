"""Unit tests for services/log_setup.py.

Regression origin (2026-09-02): /tmp/edward-backend.log had 42k lines and
not one timestamp. 591 Codex 401s, 213 entity-extraction timeouts and a
347-second triage cycle could not be placed in time without the database.
`logger.info` lines were invisible too — nothing ever configured logging,
so Python's last-resort handler only showed WARNING and above.
"""

import io
import logging

import pytest

from services.log_setup import TimestampingStream, configure_logging, prefix_lines

pytestmark = pytest.mark.unit


class TestPrefixLines:
    def test_stamps_each_line_once(self):
        out, at_start = prefix_lines("a\nb\n", "T ", at_line_start=True)
        assert out == "T a\nT b\n"
        assert at_start is True

    def test_handles_partial_line_across_writes(self):
        out1, at_start = prefix_lines("partial", "T ", at_line_start=True)
        out2, at_start = prefix_lines(" rest\nnext\n", "T ", at_line_start=at_start)
        assert out1 + out2 == "T partial rest\nT next\n"

    def test_empty_write_is_a_noop(self):
        assert prefix_lines("", "T ", at_line_start=False) == ("", False)


class TestTimestampingStream:
    def test_wrapped_stream_gets_a_stamp_per_line(self):
        sink = io.StringIO()
        stream = TimestampingStream(sink, stamp=lambda: "2026-09-02T22:00:00 ")
        stream.write("one\n")
        stream.write("two")
        stream.write(" more\n")
        assert sink.getvalue() == "2026-09-02T22:00:00 one\n2026-09-02T22:00:00 two more\n"


class TestConfigureLogging:
    def test_logger_info_becomes_visible_with_timestamp(self):
        sink = io.StringIO()
        configure_logging(stream=sink, stamp=lambda: "STAMP ")
        logging.getLogger("services.something").info("hello there")
        assert "hello there" in sink.getvalue()
        assert sink.getvalue().startswith("STAMP ")

    def test_print_output_is_timestamped_after_configure(self, capsys):
        sink = io.StringIO()
        configure_logging(stream=sink, stamp=lambda: "STAMP ", wrap_stdio=True)
        try:
            print("[Heartbeat] Triage cycle #1: 3 pending events")
        finally:
            configure_logging.restore_stdio()
        assert "STAMP [Heartbeat] Triage cycle #1: 3 pending events\n" in sink.getvalue()
