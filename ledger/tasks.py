import difflib
import re
from datetime import date, datetime
from typing import List, Optional, Tuple
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from ledger.db import Task, Event, Project
from ledger.dates import parse_deadline
from ledger.util import (
    utcnow,
    format_local_date,
    format_deadline_reason,
    format_open_since,
)


def light_stem(word: str) -> str:
    w = word.lower()
    for suffix in ("ing", "ed", "es", "s", "er", "ly"):
        if w.endswith(suffix) and len(w) > len(suffix) + 2:
            w = w[:-len(suffix)]
            break
    return w


def tokenize_and_stem(text: str) -> list[str]:
    words = re.findall(r'[a-zA-Z0-9]+', text.lower())
    return [light_stem(w) for w in words]


def compute_similarity(text1: str, text2: str) -> float:
    tokens1 = set(tokenize_and_stem(text1))
    tokens2 = set(tokenize_and_stem(text2))
    if not tokens1 or not tokens2:
        jaccard = 0.0
    else:
        jaccard = len(tokens1 & tokens2) / len(tokens1 | tokens2)
    seq = difflib.SequenceMatcher(None, text1.lower(), text2.lower()).ratio()
    return max(jaccard, seq)


def task_rank_key(task: Task, today: date):
    """Sorting key for ranking tasks:
    1. Overdue first (ordered by deadline asc)
    2. Nearest deadline (deadline asc)
    3. Tasks without deadline (after tasks with one)
    4. Priority (high > normal > low)
    5. Oldest first (created_at asc)
    """
    priority_order = {"high": 0, "normal": 1, "low": 2}
    prio = priority_order.get(task.priority, 1)

    if task.deadline is not None:
        if task.deadline < today:
            # Overdue
            return (0, task.deadline, prio, task.created_at, task.id)
        else:
            # Upcoming with deadline
            return (1, task.deadline, prio, task.created_at, task.id)
    else:
        # Without deadline
        return (2, date.max, prio, task.created_at, task.id)


def rank_tasks(tasks: List[Task], today: date) -> List[Task]:
    return sorted(tasks, key=lambda t: task_rank_key(t, today))


def get_task_reason(task: Task, today: date, tz_name: str = "UTC") -> str:
    """Return reason string for a ranked task."""
    if task.deadline is not None:
        return format_deadline_reason(task.deadline, today)
    elif task.priority == "high":
        return "high priority"
    else:
        return format_open_since(task.created_at, tz_name)


def get_open_tasks(session: Session, owner_id: int, project_id: Optional[int] = None) -> List[Task]:
    stmt = select(Task).where(Task.owner_id == owner_id, Task.status == "open")
    if project_id is not None:
        stmt = stmt.where(Task.project_id == project_id)
    return list(session.scalars(stmt).all())


def get_open_tasks_shortlist(session: Session, owner_id: int, message: str) -> List[Task]:
    """Return at most 25 open tasks for router prompt.
    All if <= 25; otherwise 20 most similar + 5 most recent.
    """
    all_open = get_open_tasks(session, owner_id)
    if len(all_open) <= 25:
        return all_open

    # Rank by similarity to message
    scored = [(compute_similarity(t.title, message), t) for t in all_open]
    scored.sort(key=lambda x: x[0], reverse=True)
    top_20 = [t for _, t in scored[:20]]
    top_20_ids = {t.id for t in top_20}

    # 5 most recent not in top 20
    remaining = [t for t in all_open if t.id not in top_20_ids]
    remaining.sort(key=lambda t: t.created_at, reverse=True)
    top_5_recent = remaining[:5]

    return top_20 + top_5_recent


def get_duplicate_candidates(session: Session, owner_id: int, project_id: int, new_title: str) -> List[Task]:
    """Return top 40 candidate tasks (status != 'dropped') ranked by similarity."""
    stmt = select(Task).where(
        Task.owner_id == owner_id,
        Task.project_id == project_id,
        Task.status != "dropped",
    )
    tasks = list(session.scalars(stmt).all())
    if not tasks:
        return []

    scored = [(compute_similarity(t.title, new_title), t) for t in tasks]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in scored[:40]]


def create_task(
    session: Session,
    owner_id: int,
    project_id: int,
    title: str,
    type: str = "task",
    priority: str = "normal",
    source: Optional[str] = None,
    deadline: Optional[date] = None,
    notes: Optional[str] = None,
) -> Task:
    task = Task(
        owner_id=owner_id,
        project_id=project_id,
        title=title[:300],
        type=type,
        priority=priority,
        source=source,
        deadline=deadline,
        notes=notes,
        status="open",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(task)
    session.flush()

    evt = Event(
        task_id=task.id,
        type="created",
        note=f"Task created: {task.title}",
        ts=utcnow(),
    )
    session.add(evt)
    session.commit()
    return task


def mark_done(session: Session, task_id: int) -> Optional[Task]:
    task = session.get(Task, task_id)
    if not task:
        return None
    now = utcnow()
    task.status = "done"
    task.done_at = now
    task.updated_at = now

    evt = Event(
        task_id=task.id,
        type="done",
        note="Marked done",
        ts=now,
    )
    session.add(evt)
    session.commit()
    return task


def reopen_task(session: Session, task_id: int) -> Optional[Task]:
    task = session.get(Task, task_id)
    if not task:
        return None
    now = utcnow()
    task.status = "open"
    task.done_at = None
    task.updated_at = now

    evt = Event(
        task_id=task.id,
        type="reopened",
        note="Reopened",
        ts=now,
    )
    session.add(evt)
    session.commit()
    return task


def delete_task(session: Session, task_id: int) -> bool:
    """Delete task and its cascade events (used for Undo save)."""
    task = session.get(Task, task_id)
    if not task:
        return False
    session.delete(task)
    session.commit()
    return True


def edit_task(
    session: Session,
    task_id: int,
    field: str,
    value: str,
    today: date,
    tz_name: str = "UTC",
) -> Tuple[bool, str]:
    task = session.get(Task, task_id)
    if not task:
        return False, f"Task #{task_id} not found"
    if task.status != "open":
        return False, f"Task #{task_id} is not open"

    valid_fields = {"deadline", "priority", "title", "type", "source", "notes"}
    if field not in valid_fields:
        return False, f"Invalid field: {field}"

    val_clean = value.strip()
    msg = ""

    if field == "deadline":
        if val_clean.lower() == "none":
            task.deadline = None
            msg = f"Updated #{task.id} deadline → none"
        else:
            parsed, warn = parse_deadline(val_clean, today)
            if parsed is not None:
                task.deadline = parsed
                msg = f"Updated #{task.id} deadline → {format_local_date(parsed)}"
            else:
                return False, f"Invalid deadline: {value}"
    elif field == "priority":
        prio = val_clean.lower()
        if prio in ("low", "normal", "high"):
            task.priority = prio
            msg = f"Updated #{task.id} priority → {task.priority}"
        else:
            return False, f"Invalid priority: {value}"
    elif field == "title":
        if not val_clean:
            return False, "Title cannot be empty"
        task.title = val_clean[:300]
        msg = f"Updated #{task.id} title → {task.title}"
    elif field == "type":
        t = val_clean.lower()
        if t in ("bug", "enhancement", "task"):
            task.type = t
            msg = f"Updated #{task.id} type → {task.type}"
        else:
            return False, f"Invalid type: {value}"
    elif field == "source":
        task.source = None if val_clean.lower() == "none" else val_clean
        msg = f"Updated #{task.id} source → {task.source or 'none'}"
    elif field == "notes":
        task.notes = None if val_clean.lower() == "none" else val_clean
        msg = f"Updated #{task.id} notes → {task.notes or 'none'}"

    task.updated_at = utcnow()
    evt = Event(task_id=task.id, type="edited", note=msg, ts=utcnow())
    session.add(evt)
    session.commit()
    return True, msg
