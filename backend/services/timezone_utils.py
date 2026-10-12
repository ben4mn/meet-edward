import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# The zone a fresh install starts in when ``TIMEZONE`` is unset or not a zone
# name. (The frontend replaces it with the browser's zone on first load:
# frontend/lib/useTimezone.tsx SERVER_DEFAULT_TZS.)
FALLBACK_TZ = "America/Chicago"


def default_timezone() -> str:
    """The zone a fresh install's settings row is seeded with: ``TIMEZONE``
    from the environment (``setup.sh`` writes it to ``.env``) when it is a
    real IANA name, else ``FALLBACK_TZ``. Read per call."""
    name = (os.environ.get("TIMEZONE") or "").strip()
    if name:
        try:
            ZoneInfo(name)
            return name
        except Exception:
            pass
    return FALLBACK_TZ


# In-process cache, primed at startup from the settings table (settings_service
# calls set_user_tz). Until then — e.g. during init_db — it is the same
# ``default_timezone()`` a fresh settings row is seeded with; the owner's own
# zone is set in Settings → Locale.
_tz_name: str = default_timezone()
_tz: ZoneInfo | None = None


def set_user_tz(name: str) -> None:
    """Update the in-process timezone. Called by settings_service on save and
    at startup. Invalid IANA names are ignored so a bad PATCH can't bork the
    process."""
    global _tz, _tz_name
    try:
        _tz = ZoneInfo(name)
        _tz_name = name
    except Exception:
        pass


def get_user_tz() -> ZoneInfo:
    global _tz
    if _tz is None:
        try:
            _tz = ZoneInfo(_tz_name)
        except Exception:
            _tz = ZoneInfo(FALLBACK_TZ)
    return _tz


def current_tz_name() -> str:
    return _tz_name


def now_local() -> datetime:
    return datetime.now(get_user_tz())


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def utc_to_local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(get_user_tz())


def local_naive_to_utc(dt: datetime) -> datetime:
    """A datetime written in the *user's* zone, as aware UTC.

    A naive value is read in ``get_user_tz()``. Never use the bare
    ``dt.astimezone()`` for this: on a naive datetime it means the
    *process's* zone, and when the user zone differs from the host's every
    scheduled time lands off by the difference (2026-09-21: Pacific user
    zone on a Central host put every cron advance two hours in the past, so
    recurring rows re-fired every poll).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=get_user_tz())
    return dt.astimezone(timezone.utc)


def format_local(dt: datetime, fmt: str = '%A, %B %d, %Y at %I:%M %p') -> str:
    return utc_to_local(dt).strftime(fmt)


def format_iso_local(dt: datetime) -> str:
    return utc_to_local(dt).isoformat()


def format_iso_utc(dt: datetime) -> str:
    """ISO 8601 UTC string with a ``Z`` suffix for API responses.

    Naive datetimes are treated as UTC (matches the DB storage convention
    where DateTime columns are TIMESTAMP WITHOUT TIME ZONE holding UTC).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
