from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Union


def utcnow() -> datetime:
    """Return current naive UTC datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(dt: datetime, tz_name: str) -> datetime:
    """Convert naive UTC datetime to timezone-aware datetime in tz_name."""
    utc_dt = dt.replace(tzinfo=timezone.utc)
    return utc_dt.astimezone(ZoneInfo(tz_name))


def local_today(tz_name: str) -> date:
    """Return today's date in tz_name."""
    return datetime.now(ZoneInfo(tz_name)).date()


def format_local_date(d: Union[date, datetime], tz_name: str | None = None) -> str:
    """Format a date or naive UTC datetime as 'Fri 10 Oct'."""
    if isinstance(d, datetime):
        if tz_name:
            d = to_local(d, tz_name).date()
        else:
            d = d.date()
    return d.strftime("%a %d %b")


def format_open_since(created_at: Union[datetime, date], tz_name: str = "UTC") -> str:
    """Format created date as 'open since 28 Sep'."""
    if isinstance(created_at, datetime):
        dt = to_local(created_at, tz_name) if tz_name else created_at
        d = dt.date()
    else:
        d = created_at
    # %d with leading zero or without? Let's check brief: "open since 28 Sep"
    # %d is 2-digit day. If day < 10, e.g. 08 Sep vs 8 Sep. strftime("%d %b").lstrip("0") or "%d %b"
    # In section 6.5: "open since 28 Sep"
    day_str = str(d.day)
    month_str = d.strftime("%b")
    return f"open since {day_str} {month_str}"


def format_deadline_reason(deadline: date, today: date) -> str:
    """Return reason string for ranking and list display."""
    if deadline < today:
        diff = (today - deadline).days
        return f"overdue by {diff}d (was {format_local_date(deadline)})"
    elif deadline == today:
        return f"due {format_local_date(deadline)} (today)"
    elif deadline == today + timedelta(days=1):
        return f"due {format_local_date(deadline)} (tomorrow)"
    else:
        diff = (deadline - today).days
        return f"due {format_local_date(deadline)} (in {diff}d)"
