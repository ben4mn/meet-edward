"""R/G TDD red for Bucket A (timezone source-of-truth).

API responses must emit UTC ISO strings ending with ``Z`` so the frontend
can localise once and only once. ``format_iso_local`` bakes the server's
TIMEZONE env var into the string and produces e.g. ``2026-04-21T14:00-05:00``;
browsers then re-convert and the clock drifts whenever the browser TZ differs
from the server TZ.

The replacement ``format_iso_utc`` must:
  * Accept naive datetimes (treat as UTC, matching the DB storage convention)
  * Accept tz-aware datetimes (convert to UTC first)
  * Always end in ``Z``, never a ``+00:00`` or ``-05:00`` offset
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from services import timezone_utils


class TestFormatIsoUtc:
    def test_format_iso_utc_emits_z_suffix_for_naive_datetime(self):
        # DB columns are TIMESTAMP WITHOUT TIME ZONE and store UTC naive.
        naive = datetime(2026, 4, 21, 14, 0, 0)
        out = timezone_utils.format_iso_utc(naive)
        assert out.endswith("Z"), f"expected Z suffix, got {out!r}"
        assert "+00:00" not in out
        assert "-05:00" not in out

    def test_format_iso_utc_converts_aware_datetime_to_utc(self):
        aware_chicago = datetime(2026, 4, 21, 9, 0, 0, tzinfo=ZoneInfo("America/Chicago"))
        out = timezone_utils.format_iso_utc(aware_chicago)
        # 09:00 Springfield (CDT) == 14:00 UTC
        assert out == "2026-04-21T14:00:00Z", f"got {out!r}"

    def test_format_iso_utc_is_stable_regardless_of_server_tz_env(self, monkeypatch):
        # Flip the cached user TZ to ensure the UTC formatter ignores it.
        monkeypatch.setattr(timezone_utils, "_tz", ZoneInfo("Asia/Tokyo"))
        naive = datetime(2026, 4, 21, 14, 0, 0)
        out = timezone_utils.format_iso_utc(naive)
        assert out.endswith("Z")
        assert "T14:00:00" in out
