from datetime import date, datetime
from typing import List, Dict, Optional
from ledger.db import Task, Project
from ledger.reply import Reply
from ledger.tasks import rank_tasks, get_task_reason
from ledger.util import format_local_date, to_local


def format_save_reply(task: Task, warning_note: Optional[str] = None) -> Reply:
    """Format the confirmation line when a task is saved.
    Example: Saved #12 [Landing] Update landing page textbar · due Fri 10 Oct · from boss
    """
    parts = [f"Saved #{task.id}"]
    proj_name = task.project.name if task.project else "General"
    parts.append(f"[{proj_name}] {task.title}")

    details = []
    if task.deadline:
        details.append(f"due {format_local_date(task.deadline)}")
    if task.source:
        details.append(f"from {task.source}")
    if task.priority and task.priority != "normal":
        details.append(f"{task.priority} priority")

    text = " · ".join([parts[0] + " " + parts[1]] + details)
    if warning_note:
        text += f" {warning_note}"

    buttons = [[("Undo", f"un:{task.id}")]]
    return Reply(text=text, buttons=buttons, edit=False)


def format_done_reply(task: Task) -> Reply:
    """Format single task marked done."""
    text = f"Done #{task.id} {task.title}"
    buttons = [[("Undo", f"ud:{task.id}")]]
    return Reply(text=text, buttons=buttons, edit=False)


def format_done_confirm_card(action_id: int, tasks: List[Task]) -> Reply:
    """Card when multiple tasks or low confidence match for done."""
    lines = ["Did you finish these tasks?"]
    for t in tasks:
        pname = t.project.name if t.project else ""
        lines.append(f"• #{t.id} [{pname}] {t.title}")
    buttons = [
        [("Confirm", f"dc:{action_id}"), ("Cancel", f"sk:{action_id}")]
    ]
    return Reply(text="\n".join(lines), buttons=buttons, edit=False)


def format_duplicate_card(action_id: int, new_task_payload: dict, duplicate_task: Task, tz_name: str = "UTC") -> Reply:
    """Format duplicate warning card before saving."""
    if duplicate_task.status == "done":
        done_str = format_local_date(duplicate_task.done_at, tz_name) if duplicate_task.done_at else "previously"
        text = f'Similar task found: #{duplicate_task.id} "{duplicate_task.title}", done {done_str}.'
        buttons = [
            [("Add anyway", f"aa:{action_id}"), (f"Reopen #{duplicate_task.id}", f"ro:{action_id}:{duplicate_task.id}")],
            [("Skip", f"sk:{action_id}")],
        ]
    else:
        text = f'Already open: #{duplicate_task.id} "{duplicate_task.title}".'
        buttons = [
            [("Add anyway", f"aa:{action_id}"), ("Skip", f"sk:{action_id}")],
        ]
    return Reply(text=text, buttons=buttons, edit=False)


def format_choose_project_card(action_id: int, projects: List[Project], task_title: str) -> Reply:
    """Card when project cannot be resolved and there are multiple projects."""
    text = f'Which project does this belong to?\n"{task_title}"'
    buttons = []
    # 2 buttons per row
    row = []
    for p in projects:
        row.append((p.name, f"ap:{action_id}:{p.id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([("Cancel", f"sk:{action_id}")])
    return Reply(text=text, buttons=buttons, edit=False)


def format_pending_query(
    projects: List[Project],
    tasks_by_project: Dict[int, List[Task]],
    today: date,
    tz_name: str = "UTC",
) -> Reply:
    """Format pending query: grouped by project, ranked, max 15 per group with +N more.
    Checkmark buttons for first 8 tasks.
    """
    lines = []
    all_ranked_tasks: List[Task] = []

    for p in projects:
        t_list = tasks_by_project.get(p.id, [])
        if not t_list:
            continue
        ranked = rank_tasks(t_list, today)
        all_ranked_tasks.extend(ranked)

        lines.append(f"{p.name}:")
        shown = ranked[:15]
        for t in shown:
            reason = get_task_reason(t, today, tz_name)
            lines.append(f"  #{t.id} {t.title} · {reason}")
        if len(ranked) > 15:
            lines.append(f"  (+{len(ranked) - 15} more)")
        lines.append("")

    if not lines:
        return Reply(text="No pending tasks.", buttons=[], edit=False)

    text = "\n".join(lines).strip()

    # Buttons for the first 8 tasks across all projects
    buttons = []
    btn_row = []
    for t in all_ranked_tasks[:8]:
        btn_row.append((f"✅ #{t.id}", f"dn:{t.id}"))
        if len(btn_row) == 4:
            buttons.append(btn_row)
            btn_row = []
    if btn_row:
        buttons.append(btn_row)

    return Reply(text=text, buttons=buttons, edit=False)


def format_first_query(tasks: List[Task], today: date, tz_name: str = "UTC") -> Reply:
    """Top 3 ranked tasks with a one-line reason each, plus checkmark buttons."""
    if not tasks:
        return Reply(text="No open tasks pending.", buttons=[], edit=False)

    top3 = rank_tasks(tasks, today)[:3]
    lines = ["Top tasks to focus on:"]
    buttons_row = []

    for t in top3:
        reason = get_task_reason(t, today, tz_name)
        pname = t.project.name if t.project else ""
        lines.append(f"• #{t.id} [{pname}] {t.title} · {reason}")
        buttons_row.append((f"✅ #{t.id}", f"dn:{t.id}"))

    return Reply(text="\n".join(lines), buttons=[buttons_row], edit=False)


def format_finished_query(tasks: List[Task], days: int, today: date, tz_name: str = "UTC") -> Reply:
    """Tasks done in the last N days, grouped by local date."""
    if not tasks:
        period_str = "today" if days == 1 else f"the last {days} days"
        return Reply(text=f"No tasks finished {period_str}.", buttons=[], edit=False)

    # Group by local date
    by_date: Dict[date, List[Task]] = {}
    for t in tasks:
        if t.done_at:
            d = to_local(t.done_at, tz_name).date()
        else:
            d = today
        by_date.setdefault(d, []).append(t)

    lines = []
    for d in sorted(by_date.keys(), reverse=True):
        lines.append(format_local_date(d) + ":")
        for t in by_date[d]:
            pname = t.project.name if t.project else ""
            lines.append(f"  - #{t.id} [{pname}] {t.title}")
        lines.append("")

    return Reply(text="\n".join(lines).strip(), buttons=[], edit=False)


def format_due_query(tasks: List[Task], days: int, today: date, tz_name: str = "UTC") -> Reply:
    """Open tasks with deadline within days, including overdue."""
    if not tasks:
        return Reply(text=f"No tasks due within {days} days.", buttons=[], edit=False)

    ranked = rank_tasks(tasks, today)
    lines = [f"Tasks due within {days} days (including overdue):"]
    for t in ranked:
        reason = get_task_reason(t, today, tz_name)
        pname = t.project.name if t.project else ""
        lines.append(f"• #{t.id} [{pname}] {t.title} · {reason}")

    return Reply(text="\n".join(lines), buttons=[], edit=False)
