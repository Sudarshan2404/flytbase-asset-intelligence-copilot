from __future__ import annotations

from datetime import date, datetime

# Challenge materials treat 16 Oct 2026 as "today".
EVALUATION_TODAY = date(2026, 10, 16)


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
