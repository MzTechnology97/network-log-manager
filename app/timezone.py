from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import ENV


def display_zone():
    name = ENV.get("NETLOG_TIMEZONE") or ENV.get("TZ") or "Europe/Rome"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def utc_naive_to_local(value):
    """Convert a naive MariaDB UTC DATETIME to the configured display timezone."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(display_zone())


def local_naive_to_utc(value):
    """Convert a naive operator-entered local datetime to naive UTC for DB queries."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=display_zone())
    return value.astimezone(timezone.utc).replace(tzinfo=None)
