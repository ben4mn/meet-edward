import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_tz: ZoneInfo | None = None

def get_user_tz() -> ZoneInfo:
    global _tz
    if _tz is None:
        _tz = ZoneInfo(os.environ.get("TIMEZONE", "UTC"))
    return _tz

def now_local() -> datetime:
    return datetime.now(get_user_tz())

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def utc_to_local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(get_user_tz())

def format_local(dt: datetime, fmt: str = '%A, %B %d, %Y at %I:%M %p') -> str:
    return utc_to_local(dt).strftime(fmt)

def format_iso_local(dt: datetime) -> str:
    return utc_to_local(dt).isoformat()
