from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo
from typing import List, Dict
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from ledger.db import Project, Task
from ledger.reply import Reply
from ledger.tasks import rank_tasks
from ledger.util import format_local_date, to_local, format_deadline_reason


def build_update_report(
    session: Session,
    owner_id: int,
    days: int,
    today: date,
    tz_name: str = "UTC",
) -> Reply:
    """Build paste-ready update for today (days=1) or week (days=7).
    No LLM, DB only.
    """
    tz = ZoneInfo(tz_name)
    # Local start time for the period (e.g. today at 00:00:00 minus (days-1) days)
    start_date = today - timedelta(days=days - 1)
    local_start_dt = datetime.combine(start_date, time.min, tzinfo=tz)
    # Convert to naive UTC
    start_utc = local_start_dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    stmt_projs = select(Project).where(Project.owner_id == owner_id).order_by(Project.name.asc())
    projects = list(session.scalars(stmt_projs).all())

    # Title
    date_header = format_local_date(today)
    period_title = f"Update · {date_header}"

    total_done_count = 0
    project_blocks: List[str] = []

    for p in projects:
        # Done tasks in period
        stmt_done = (
            select(Task)
            .where(
                Task.owner_id == owner_id,
                Task.project_id == p.id,
                Task.status == "done",
                Task.done_at >= start_utc,
            )
            .order_by(Task.done_at.asc())
        )
        done_tasks = list(session.scalars(stmt_done).all())
        total_done_count += len(done_tasks)

        # Top 5 ranked open tasks
        stmt_open = (
            select(Task)
            .where(
                Task.owner_id == owner_id,
                Task.project_id == p.id,
                Task.status == "open",
            )
        )
        open_tasks = list(session.scalars(stmt_open).all())
        next_tasks = rank_tasks(open_tasks, today)[:5]

        # Skip project if nothing to show
        if not done_tasks and not next_tasks:
            continue

        block_lines = [p.name]
        if done_tasks:
            block_lines.append("  Done")
            for t in done_tasks:
                block_lines.append(f"  - {t.title}")
        
        if next_tasks:
            block_lines.append("  Next")
            for t in next_tasks:
                if t.deadline:
                    deadline_str = f" (due {format_local_date(t.deadline)})"
                else:
                    deadline_str = ""
                block_lines.append(f"  - {t.title}{deadline_str}")

        project_blocks.append("\n".join(block_lines))

    lines = [period_title, ""]
    if total_done_count == 0:
        period_str = "today" if days == 1 else "this period"
        lines.append(f"Nothing was done {period_str}.")
        lines.append("")

    if project_blocks:
        lines.append("\n\n".join(project_blocks))
    elif total_done_count == 0:
        # No project blocks and no done
        lines.append("No active projects or tasks.")

    text = "\n".join(lines).strip()
    return Reply(text=text, buttons=[], edit=False)
