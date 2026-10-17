from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

# Sample-dataset default. Override with EVALUATION_TODAY=YYYY-MM-DD for the test set.
_DEFAULT_TODAY = date(2026, 10, 16)


def evaluation_today() -> date:
    raw = os.environ.get("EVALUATION_TODAY", "").strip()
    if raw:
        return date.fromisoformat(raw[:10])
    return _DEFAULT_TODAY


EVALUATION_TODAY = evaluation_today()


def parse_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        raise ValueError("Empty date value")
    return date.fromisoformat(text[:10])


def in_date_window(
    value: date | datetime | str | None,
    start: date | datetime | str,
    end: date | datetime | str,
) -> bool:
    """Inclusive calendar-date membership using the UTC date prefix of timestamps."""
    if value is None:
        return False
    current = parse_date(value)
    return parse_date(start) <= current <= parse_date(end)


def parse_datetime(value: date | datetime | str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "T" not in text:
        return datetime.fromisoformat(text[:10]).replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def add_days(value: date | datetime | str, days: int) -> datetime:
    return parse_datetime(value) + timedelta(days=days)


def add_hours(value: date | datetime | str, hours: float) -> datetime:
    return parse_datetime(value) + timedelta(hours=hours)


def lookback_start(as_of: date | datetime | str, *, days: int | None = None, years: int | None = None) -> date:
    current = parse_date(as_of)
    if years is not None:
        return date(current.year - years, current.month, current.day)
    if days is None:
        raise ValueError("days or years is required")
    return current - timedelta(days=days)
