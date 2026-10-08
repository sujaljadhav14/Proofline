import logging
import re
from datetime import date, datetime, timedelta, time
from typing import List, Optional
from zoneinfo import ZoneInfo
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from ledger.config import Settings
from ledger.db import Task, Project, PendingAction, Event
from ledger.reply import Reply
from ledger.llm import LLM, LLMError
from ledger.prompts import (
    ROUTER_SYSTEM_PROMPT,
    ROUTER_SCHEMA,
    DUPLICATE_SYSTEM_PROMPT,
    DUPLICATE_SCHEMA,
    build_router_user_prompt,
    build_duplicate_user_prompt,
)
from ledger.projects import (
    list_projects,
    resolve_project,
    add_project,
    parse_project_def,
)
from ledger.tasks import (
    get_open_tasks,
    get_open_tasks_shortlist,
    get_duplicate_candidates,
    create_task,
    mark_done,
    reopen_task,
    delete_task,
    edit_task,
    rank_tasks,
)
from ledger.dates import parse_deadline
from ledger.views import (
    format_save_reply,
    format_done_reply,
    format_done_confirm_card,
    format_duplicate_card,
    format_choose_project_card,
    format_pending_query,
    format_first_query,
    format_finished_query,
    format_due_query,
)
from ledger.report import build_update_report
from ledger.checkin import (
    create_checkin_card,
    handle_checkin_toggle,
    handle_checkin_confirm,
    handle_checkin_nothing_today,
    mark_latest_checkin_replied,
)
from ledger.util import local_today, utcnow

logger = logging.getLogger(__name__)


def is_authorized(user_id: int, settings: Settings, command: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """Check user authorization.
    Returns (authorized, rejection_message).
    """
    if command == "/myid":
        return True, None

    if settings.ALLOWED_USER_ID == 0:
        if command in ("/start", "/help"):
            return True, None
        return False, "Bot is not configured yet. Send /myid to get your Telegram user ID and set ALLOWED_USER_ID in .env."

    if user_id != settings.ALLOWED_USER_ID:
        return False, None  # Ignored silently

    return True, None


async def handle_callback(
    data: str,
    user_id: int,
    session: Session,
    settings: Settings,
    today: Optional[date] = None,
) -> List[Reply]:
    authorized, _ = is_authorized(user_id, settings)
    if not authorized:
        return []

    cur_today = today or local_today(settings.LEDGER_TZ)
    parts = data.split(":")
    code = parts[0]

    # un:<task_id> -> Undo save
    if code == "un" and len(parts) >= 2:
        task_id = int(parts[1])
        deleted = delete_task(session, task_id)
        if deleted:
            return [Reply(text=f"Undone. Task #{task_id} deleted.", buttons=[], edit=True)]
        return [Reply(text=f"Task #{task_id} was already removed.", buttons=[], edit=True)]

    # ud:<task_id> -> Undo done (reopen)
    if code == "ud" and len(parts) >= 2:
        task_id = int(parts[1])
        reopened = reopen_task(session, task_id)
        if reopened:
            return [Reply(text=f"Reopened #{task_id} {reopened.title}.", buttons=[], edit=True)]
        return [Reply(text="Task not found.", buttons=[], edit=True)]

    # dn:<task_id> -> Mark done from list (edit=False so list stays)
    if code == "dn" and len(parts) >= 2:
        task_id = int(parts[1])
        done_t = mark_done(session, task_id)
        if done_t:
            mark_latest_checkin_replied(session)
            return [format_done_reply(done_t)]
        return [Reply(text="Task not found.", buttons=[], edit=False)]

    # sk:<action_id> -> Skip / Cancel
    if code == "sk" and len(parts) >= 2:
        action_id = int(parts[1])
        action = session.get(PendingAction, action_id)
        if action:
            session.delete(action)
            session.commit()
        return [Reply(text="Cancelled.", buttons=[], edit=True)]

    # aa:<action_id> -> Add anyway
    if code == "aa" and len(parts) >= 2:
        action_id = int(parts[1])
        action = session.get(PendingAction, action_id)
        if not action or action.kind != "add":
            return [Reply(text="That card has expired. Just send the message again.", buttons=[], edit=True)]
        payload = action.payload
        t_data = payload["task_data"]
        deadline_val = None
        if t_data.get("deadline"):
            deadline_val = date.fromisoformat(t_data["deadline"])
        saved = create_task(
            session=session,
            owner_id=user_id,
            project_id=t_data["project_id"],
            title=t_data["title"],
            type=t_data.get("type", "task"),
            priority=t_data.get("priority", "normal"),
            source=t_data.get("source"),
            deadline=deadline_val,
            notes=t_data.get("notes"),
        )
        session.delete(action)
        session.commit()
        reply = format_save_reply(saved)
        reply.edit = True
        return [reply]

    # ro:<action_id>:<task_id> -> Reopen existing duplicate instead of adding
    if code == "ro" and len(parts) >= 3:
        action_id = int(parts[1])
        task_id = int(parts[2])
        action = session.get(PendingAction, action_id)
        if action:
            session.delete(action)
        reopened = reopen_task(session, task_id)
        if reopened:
            return [Reply(text=f"Reopened #{task_id} {reopened.title}.", buttons=[], edit=True)]
        return [Reply(text="Task not found.", buttons=[], edit=True)]

    # ap:<action_id>:<project_id> -> Choose project for pending add
    if code == "ap" and len(parts) >= 3:
        action_id = int(parts[1])
        project_id = int(parts[2])
        action = session.get(PendingAction, action_id)
        if not action or action.kind != "add":
            return [Reply(text="That card has expired. Just send the message again.", buttons=[], edit=True)]
        t_data = action.payload["task_data"]
        deadline_val, warn = parse_deadline(t_data.get("deadline_text"), cur_today)
        saved = create_task(
            session=session,
            owner_id=user_id,
            project_id=project_id,
            title=t_data["title"],
            type=t_data.get("type", "task"),
            priority=t_data.get("priority", "normal"),
            source=t_data.get("source"),
            deadline=deadline_val,
            notes=t_data.get("notes"),
        )
        session.delete(action)
        session.commit()
        reply = format_save_reply(saved, warn)
        reply.edit = True
        return [reply]

    # dc:<action_id> -> Confirm a done list
    if code == "dc" and len(parts) >= 2:
        action_id = int(parts[1])
        action = session.get(PendingAction, action_id)
        if not action or action.kind != "done":
            return [Reply(text="That card has expired. Just send the message again.", buttons=[], edit=True)]
        task_ids = action.payload.get("task_ids", [])
        done_list = []
        for tid in task_ids:
            t = mark_done(session, tid)
            if t:
                done_list.append(t)
        mark_latest_checkin_replied(session)
        session.delete(action)
        session.commit()
        text = "Marked done:\n" + "\n".join(f"• #{t.id} {t.title}" for t in done_list)
        return [Reply(text=text, buttons=[], edit=True)]

    # ct:<action_id>:<task_id> -> Toggle task on checkin
    if code == "ct" and len(parts) >= 3:
        action_id = int(parts[1])
        task_id = int(parts[2])
        return [handle_checkin_toggle(session, action_id, task_id, cur_today, settings.LEDGER_TZ)]

    # cok:<action_id> -> Confirm checkin
    if code == "cok" and len(parts) >= 2:
        action_id = int(parts[1])
        return [handle_checkin_confirm(session, action_id, user_id, cur_today, settings.LEDGER_TZ)]

    # cno:<action_id> -> Nothing today checkin
    if code == "cno" and len(parts) >= 2:
        action_id = int(parts[1])
        return [handle_checkin_nothing_today(session, action_id)]

    return [Reply(text="That card has expired. Just send the message again.", buttons=[], edit=True)]


async def handle_text(
    text: str,
    user_id: int,
    session: Session,
    llm: LLM,
    settings: Settings,
    today: Optional[date] = None,
) -> List[Reply]:
    clean_text = text.strip()
    if not clean_text:
        return []

    cur_today = today or local_today(settings.LEDGER_TZ)

    # 1. Check for command
    first_token = clean_text.split()[0].lower()
    is_cmd = first_token.startswith("/")
    cmd_name = first_token if is_cmd else None

    authorized, reject_msg = is_authorized(user_id, settings, cmd_name)
    if not authorized:
        if reject_msg:
            return [Reply(text=reject_msg, buttons=[], edit=False)]
        return []

    if is_cmd:
        return await handle_command(clean_text, user_id, session, settings, cur_today)

    # 2. Free-form message -> router LLM
    open_tasks_shortlist = get_open_tasks_shortlist(session, user_id, clean_text)
    projects = list_projects(session, user_id)

    # Build prompts
    proj_lines = []
    for p in projects:
        aliases_str = f" (aliases: {', '.join(p.aliases)})" if p.aliases else ""
        proj_lines.append(f"- {p.name}{aliases_str}")
    projects_text = "\n".join(proj_lines)

    task_lines = [f"{t.id} | {t.project.name} | {t.title}" for t in open_tasks_shortlist]
    open_tasks_text = "\n".join(task_lines)

    user_prompt = build_router_user_prompt(projects_text, open_tasks_text, clean_text)

    try:
        parsed = await llm.structured(
            system=ROUTER_SYSTEM_PROMPT,
            user=user_prompt,
            name="respond",
            schema=ROUTER_SCHEMA,
        )
    except Exception as e:
        logger.error("LLM call failed in handle_text: %s", e)
        return [Reply(text="Couldn't process that, please try again. Nothing was saved.", buttons=[], edit=False)]

    replies: List[Reply] = []
    shortlist_ids = {t.id for t in open_tasks_shortlist}

    # Order of application: edits, done, add tasks, query, question, reply

    # 1. Edits
    for edit in parsed.get("edits", []):
        tid = edit.get("task_id")
        field = edit.get("field")
        val = edit.get("value", "")
        if tid in shortlist_ids:
            success, msg = edit_task(session, tid, field, val, cur_today, settings.LEDGER_TZ)
            if success:
                replies.append(Reply(text=msg, buttons=[], edit=False))

    # 2. Done
    done_items = [d for d in parsed.get("done", []) if d.get("task_id") in shortlist_ids]
    if len(done_items) == 1 and done_items[0].get("confidence") == "high":
        tid = done_items[0]["task_id"]
        t = mark_done(session, tid)
        if t:
            mark_latest_checkin_replied(session)
            replies.append(format_done_reply(t))
    elif done_items:
        t_ids = [d["task_id"] for d in done_items]
        tasks_to_confirm = [session.get(Task, tid) for tid in t_ids]
        tasks_to_confirm = [t for t in tasks_to_confirm if t and t.status == "open"]
        if tasks_to_confirm:
            action = PendingAction(
                kind="done",
                payload={"task_ids": [t.id for t in tasks_to_confirm]},
                created_at=utcnow(),
            )
            session.add(action)
            session.commit()
            replies.append(format_done_confirm_card(action.id, tasks_to_confirm))

    # 3. Tasks (add)
    for t_spec in parsed.get("tasks", []):
        proj_name = t_spec.get("project")
        target_proj = None
        if proj_name:
            target_proj = resolve_project(proj_name, projects)

        if target_proj is None:
            if len(projects) == 1:
                target_proj = projects[0]
            elif len(projects) > 1:
                action = PendingAction(
                    kind="add",
                    payload={"task_data": t_spec},
                    created_at=utcnow(),
                )
                session.add(action)
                session.commit()
                replies.append(format_choose_project_card(action.id, projects, t_spec["title"]))
                continue
            else:
                target_proj = None

        if target_proj is not None:
            deadline_val, deadline_warn = parse_deadline(t_spec.get("deadline_text"), cur_today)

            # Duplicate check
            candidates = get_duplicate_candidates(session, user_id, target_proj.id, t_spec["title"])
            dup_id = None
            dup_failed = False

            if candidates:
                cand_lines = []
                for c in candidates:
                    line = f"{c.id} | {c.status} | {c.title}"
                    if c.status == "done" and c.done_at:
                        line += f" (done {c.done_at.strftime('%a %d %b')})"
                    cand_lines.append(line)
                cand_text = "\n".join(cand_lines)
                dup_prompt = build_duplicate_user_prompt(t_spec["title"], cand_text)

                try:
                    dup_res = await llm.structured(
                        system=DUPLICATE_SYSTEM_PROMPT,
                        user=dup_prompt,
                        name="respond",
                        schema=DUPLICATE_SCHEMA,
                    )
                    cand_ids = {c.id for c in candidates}
                    sug = dup_res.get("duplicate_of")
                    if sug in cand_ids:
                        dup_id = sug
                except Exception as e:
                    logger.warning("Duplicate check failed: %s", e)
                    dup_failed = True

            if dup_id is not None:
                dup_task = session.get(Task, dup_id)
                t_payload = dict(t_spec)
                t_payload["project_id"] = target_proj.id
                t_payload["deadline"] = deadline_val.isoformat() if deadline_val else None
                action = PendingAction(
                    kind="add",
                    payload={"task_data": t_payload},
                    created_at=utcnow(),
                )
                session.add(action)
                session.commit()
                replies.append(format_duplicate_card(action.id, t_payload, dup_task, settings.LEDGER_TZ))
            else:
                saved = create_task(
                    session=session,
                    owner_id=user_id,
                    project_id=target_proj.id,
                    title=t_spec["title"],
                    type=t_spec.get("type", "task"),
                    priority=t_spec.get("priority", "normal"),
                    source=t_spec.get("source"),
                    deadline=deadline_val,
                    notes=t_spec.get("notes"),
                )
                warn = deadline_warn
                if dup_failed:
                    fail_note = "(Couldn't run the duplicate check this time.)"
                    warn = f"{warn} {fail_note}".strip() if warn else fail_note
                replies.append(format_save_reply(saved, warn))

    # 4. Query
    query = parsed.get("query")
    if query:
        q_reply = execute_query(session, user_id, query, projects, cur_today, settings.LEDGER_TZ)
        if q_reply:
            replies.append(q_reply)

    # 5. Question
    question = parsed.get("question")
    if question:
        replies.append(Reply(text=question, buttons=[], edit=False))

    # 6. Reply
    direct_reply = parsed.get("reply")
    if direct_reply and not replies:
        replies.append(Reply(text=direct_reply, buttons=[], edit=False))

    if not replies:
        replies.append(
            Reply(
                text="I didn't catch an action in that message. Try 'need to update textbar on Landing' or 'what is pending?'.",
                buttons=[],
                edit=False,
            )
        )

    return replies


def execute_query(
    session: Session,
    owner_id: int,
    query: dict,
    projects: List[Project],
    today: date,
    tz_name: str = "UTC",
) -> Optional[Reply]:
    kind = query.get("kind")
    proj_name = query.get("project")
    days = query.get("days")

    target_proj = None
    if proj_name:
        target_proj = resolve_project(proj_name, projects)
        if not target_proj:
            known = ", ".join(p.name for p in projects)
            return Reply(
                text=f"Unknown project '{proj_name}'. Known projects: {known if known else '(none)'}",
                buttons=[],
                edit=False,
            )

    if kind == "pending":
        if target_proj:
            t_list = get_open_tasks(session, owner_id, target_proj.id)
            return format_pending_query([target_proj], {target_proj.id: t_list}, today, tz_name)
        else:
            by_proj = {}
            for p in projects:
                by_proj[p.id] = get_open_tasks(session, owner_id, p.id)
            return format_pending_query(projects, by_proj, today, tz_name)

    elif kind == "first":
        if target_proj:
            t_list = get_open_tasks(session, owner_id, target_proj.id)
        else:
            t_list = get_open_tasks(session, owner_id)
        return format_first_query(t_list, today, tz_name)

    elif kind == "finished":
        d_count = days or 1
        tz = ZoneInfo(tz_name)
        start_date = today - timedelta(days=d_count - 1)
        start_utc = datetime.combine(start_date, time.min, tzinfo=tz).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

        stmt = select(Task).where(
            Task.owner_id == owner_id,
            Task.status == "done",
            Task.done_at >= start_utc,
        )
        if target_proj:
            stmt = stmt.where(Task.project_id == target_proj.id)
        stmt = stmt.order_by(Task.done_at.desc())
        tasks = list(session.scalars(stmt).all())
        return format_finished_query(tasks, d_count, today, tz_name)

    elif kind == "due":
        d_count = days or 7
        due_limit = today + timedelta(days=d_count)
        stmt = select(Task).where(
            Task.owner_id == owner_id,
            Task.status == "open",
            Task.deadline.is_not(None),
            Task.deadline <= due_limit,
        )
        if target_proj:
            stmt = stmt.where(Task.project_id == target_proj.id)
        tasks = list(session.scalars(stmt).all())
        return format_due_query(tasks, d_count, today, tz_name)

    return None


async def handle_command(
    text: str,
    user_id: int,
    session: Session,
    settings: Settings,
    today: date,
) -> List[Reply]:
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/myid":
        return [Reply(text=f"Your Telegram user ID is {user_id}.", buttons=[], edit=False)]

    if cmd in ("/start", "/help"):
        help_text = (
            "Work Ledger tracks your tasks from messages and check-ins.\n\n"
            "Examples:\n"
            "• need to update the textbar on the landing page\n"
            "• tester says discount not applying for 3+ seats in webapp\n"
            "• textbar done\n\n"
            "Commands:\n"
            "/projects - List projects and aliases\n"
            "/addproject Name = alias1, alias2\n"
            "/pending [project] - List open tasks\n"
            "/first [project] - Top 3 tasks to do\n"
            "/today - Tasks completed today\n"
            "/checkin - Run daily check-in now\n"
            "/update [week] - Generate copy-paste update"
        )
        return [Reply(text=help_text, buttons=[], edit=False)]

    if cmd == "/projects":
        projs = list_projects(session, user_id)
        if not projs:
            return [Reply(text="No projects yet. Add one with /addproject Name = alias1, alias2", buttons=[], edit=False)]
        lines = ["Projects:"]
        for p in projs:
            aliases_str = f" (aliases: {', '.join(p.aliases)})" if p.aliases else ""
            lines.append(f"• {p.name}{aliases_str}")
        return [Reply(text="\n".join(lines), buttons=[], edit=False)]

    if cmd == "/addproject":
        if not arg:
            return [Reply(text="Usage: /addproject Name = alias1, alias2", buttons=[], edit=False)]
        name, aliases = parse_project_def(arg)
        proj = add_project(session, user_id, name, aliases)
        alias_msg = f" (aliases: {', '.join(proj.aliases)})" if proj.aliases else ""
        return [Reply(text=f"Added project {proj.name}{alias_msg}.", buttons=[], edit=False)]

    if cmd == "/pending":
        projects = list_projects(session, user_id)
        query = {"kind": "pending"}
        if arg:
            query["project"] = arg
        rep = execute_query(session, user_id, query, projects, today, settings.LEDGER_TZ)
        return [rep] if rep else []

    if cmd == "/first":
        projects = list_projects(session, user_id)
        query = {"kind": "first"}
        if arg:
            query["project"] = arg
        rep = execute_query(session, user_id, query, projects, today, settings.LEDGER_TZ)
        return [rep] if rep else []

    if cmd == "/today":
        projects = list_projects(session, user_id)
        rep = execute_query(session, user_id, {"kind": "finished", "days": 1}, projects, today, settings.LEDGER_TZ)
        return [rep] if rep else []

    if cmd == "/checkin":
        card = create_checkin_card(session, user_id, today, settings.LEDGER_TZ)
        if card:
            return [card]
        return [Reply(text="No open tasks to check in.", buttons=[], edit=False)]

    if cmd == "/update":
        days = 7 if arg.lower() == "week" else 1
        return [build_update_report(session, user_id, days, today, settings.LEDGER_TZ)]

    return [Reply(text=f"Unknown command {cmd}. Type /help for usage.", buttons=[], edit=False)]
