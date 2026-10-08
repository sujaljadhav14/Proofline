from datetime import date, datetime
from typing import Optional, List
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from ledger.db import Checkin, PendingAction, Task, Project
from ledger.reply import Reply
from ledger.tasks import get_open_tasks, rank_tasks, mark_done
from ledger.util import utcnow, format_local_date


def format_commits_block(session: Session, owner_id: int, today: date) -> str:
    """Phase 1 clean function placeholder where Phase 2 commits block will plug in."""
    return ""


def mark_latest_checkin_replied(session: Session) -> Optional[Checkin]:
    """Mark the latest unanswered check-in as replied."""
    stmt = select(Checkin).where(Checkin.replied_at.is_(None)).order_by(Checkin.sent_at.desc())
    checkin = session.scalar(stmt)
    if checkin:
        checkin.replied_at = utcnow()
        session.commit()
    return checkin


def render_checkin_card(
    session: Session,
    action_id: int,
    owner_id: int,
    today: date,
    tz_name: str = "UTC",
    prefix: str = "",
) -> Reply:
    action = session.get(PendingAction, action_id)
    if not action:
        return Reply(text="That card has expired. Just send the message again.", buttons=[], edit=True)

    selected_ids = set(action.payload.get("selected_ids", []))
    task_ids = action.payload.get("task_ids", [])
    total_open_count = action.payload.get("total_open_count", len(task_ids))

    tasks = [session.get(Task, tid) for tid in task_ids]
    tasks = [t for t in tasks if t and t.status == "open"]

    date_str = format_local_date(today)
    lines = []
    if prefix:
        lines.append(prefix.strip())
    lines.append(f"Check-in · {date_str}")
    lines.append('What did you finish? Tap the tasks, then Confirm. Or just type it, e.g. "did the textbar".')

    commits_block = format_commits_block(session, owner_id, today)
    if commits_block:
        lines.append("")
        lines.append(commits_block)

    if total_open_count > 12:
        not_shown = total_open_count - 12
        lines.append("")
        lines.append(f"(+{not_shown} more open tasks not shown. Type it if you finished one of those.)")

    text = "\n\n".join(lines) if prefix else f"{lines[0]}\n\n{lines[1]}"
    if len(lines) > 2:
        text = "\n\n".join(lines)

    buttons = []
    for t in tasks:
        pname = t.project.name if t.project else ""
        icon = "✅ " if t.id in selected_ids else "⬜ "
        # Truncate title cleanly for Telegram button
        full_label = f"{icon}{pname} · {t.title}"
        if len(full_label) > 36:
            full_label = full_label[:35] + "…"
        buttons.append([(full_label, f"ct:{action_id}:{t.id}")])

    n = len(selected_ids)
    bottom_row = [
        (f"Confirm ({n})", f"cok:{action_id}"),
        ("Nothing today", f"cno:{action_id}"),
    ]
    buttons.append(bottom_row)

    return Reply(text=text, buttons=buttons, edit=True)


def create_checkin_card(
    session: Session,
    owner_id: int,
    today: date,
    tz_name: str = "UTC",
    prefix: str = "",
) -> Optional[Reply]:
    open_tasks = get_open_tasks(session, owner_id)
    if not open_tasks:
        return None

    # Record check-in in checkins table
    checkin = Checkin(sent_at=utcnow())
    session.add(checkin)
    session.flush()

    ranked = rank_tasks(open_tasks, today)
    tasks_to_show = ranked[:12]

    action = PendingAction(
        kind="checkin",
        payload={
            "selected_ids": [],
            "task_ids": [t.id for t in tasks_to_show],
            "total_open_count": len(ranked),
            "owner_id": owner_id,
        },
        created_at=utcnow(),
    )
    session.add(action)
    session.commit()

    reply = render_checkin_card(
        session=session,
        action_id=action.id,
        owner_id=owner_id,
        today=today,
        tz_name=tz_name,
        prefix=prefix,
    )
    # The initial send is a new message, not an edit
    reply.edit = False
    return reply


def handle_checkin_toggle(
    session: Session,
    action_id: int,
    task_id: int,
    today: date,
    tz_name: str = "UTC",
) -> Reply:
    action = session.get(PendingAction, action_id)
    if not action:
        return Reply(text="That card has expired. Just send the message again.", buttons=[], edit=True)

    payload = dict(action.payload)
    selected = set(payload.get("selected_ids", []))
    if task_id in selected:
        selected.remove(task_id)
    else:
        selected.add(task_id)
    payload["selected_ids"] = list(selected)
    action.payload = payload
    session.commit()

    owner_id = payload.get("owner_id", 0)
    return render_checkin_card(
        session=session,
        action_id=action.id,
        owner_id=owner_id,
        today=today,
        tz_name=tz_name,
    )


def handle_checkin_confirm(
    session: Session,
    action_id: int,
    owner_id: int,
    today: date,
    tz_name: str = "UTC",
) -> Reply:
    action = session.get(PendingAction, action_id)
    if not action:
        return Reply(text="That card has expired. Just send the message again.", buttons=[], edit=True)

    selected = action.payload.get("selected_ids", [])
    if not selected:
        return render_checkin_card(
            session=session,
            action_id=action.id,
            owner_id=owner_id,
            today=today,
            tz_name=tz_name,
            prefix="Tap the tasks you finished first, then tap Confirm.",
        )

    done_tasks = []
    for tid in selected:
        t = mark_done(session, tid)
        if t:
            done_tasks.append(t)

    mark_latest_checkin_replied(session)

    # Clean up pending action
    session.delete(action)
    session.commit()

    lines = ["Finished:"]
    for t in done_tasks:
        lines.append(f"• #{t.id} {t.title}")
    return Reply(text="\n".join(lines), buttons=[], edit=True)


def handle_checkin_nothing_today(session: Session, action_id: int) -> Reply:
    action = session.get(PendingAction, action_id)
    if action:
        session.delete(action)
    mark_latest_checkin_replied(session)
    session.commit()
    return Reply(text="Noted. See you tomorrow!", buttons=[], edit=True)


def create_morning_nudge(
    session: Session,
    owner_id: int,
    today: date,
    tz_name: str = "UTC",
) -> Optional[Reply]:
    stmt = select(Checkin).order_by(Checkin.sent_at.desc())
    latest = session.scalar(stmt)
    if not latest:
        return None

    # Nudge once only: if replied or already nudged, skip
    if latest.replied_at is not None or latest.nudged_at is not None:
        return None

    open_tasks = get_open_tasks(session, owner_id)
    if not open_tasks:
        return None

    latest.nudged_at = utcnow()

    ranked = rank_tasks(open_tasks, today)
    tasks_to_show = ranked[:12]

    action = PendingAction(
        kind="checkin",
        payload={
            "selected_ids": [],
            "task_ids": [t.id for t in tasks_to_show],
            "total_open_count": len(ranked),
            "owner_id": owner_id,
        },
        created_at=utcnow(),
    )
    session.add(action)
    session.commit()

    reply = render_checkin_card(
        session=session,
        action_id=action.id,
        owner_id=owner_id,
        today=today,
        tz_name=tz_name,
        prefix="Yesterday's check-in is still open.",
    )
    reply.edit = False
    return reply
