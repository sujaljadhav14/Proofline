import re
from datetime import date, datetime, timedelta
import dateparser

WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def parse_deadline(text: str | None, today: date) -> tuple[date | None, str | None]:
    """Parse a deadline string using dateparser relative to today.
    
    Returns (parsed_date, warning_note).
    If text is empty, returns (None, None).
    If unparseable or in the past, returns (None, warning_note).
    """
    if not text or not text.strip():
        return None, None

    raw = text.strip()
    norm = re.sub(r'[^a-z]', '', raw.lower())

    relative_base = datetime(today.year, today.month, today.day, 0, 0, 0)
    settings = {
        "PREFER_DATES_FROM": "future",
        "RELATIVE_BASE": relative_base,
        "RETURN_AS_TIMEZONE_AWARE": False,
    }

    # Handle bare weekdays explicitly to guarantee strictly next occurrence after today
    if norm in WEEKDAYS:
        target_weekday = WEEKDAYS[norm]
        days_ahead = (target_weekday - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        parsed = today + timedelta(days=days_ahead)
        return parsed, None

    dt = dateparser.parse(raw, settings=settings)
    if dt is None:
        return None, f'(Couldn\'t read the deadline "{raw}".)'

    parsed_date = dt.date()
    if parsed_date < today:
        return None, f'(Couldn\'t read the deadline "{raw}".)'

    return parsed_date, None
