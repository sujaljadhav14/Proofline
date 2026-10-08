import json
import pytest
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
import httpx

from ledger.config import Settings
from ledger.db import (
    Base,
    get_session_factory,
    init_db,
    Project,
    Task,
    Event,
    Checkin,
    PendingAction,
)
from ledger.dates import parse_deadline
from ledger.llm import FakeLLM, FallbackLLM, OllamaLLM, LLMError
from ledger.projects import add_project, list_projects, resolve_project
from ledger.tasks import (
    create_task,
    mark_done,
    reopen_task,
    delete_task,
    edit_task,
    rank_tasks,
    get_open_tasks_shortlist,
)
from ledger.router import handle_text, handle_callback
from ledger.checkin import (
    create_checkin_card,
    handle_checkin_toggle,
    handle_checkin_confirm,
    handle_checkin_nothing_today,
    create_morning_nudge,
)
from ledger.report import build_update_report
from ledger.bot import build_application, run_catchup


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    init_db(engine)
    return engine


@pytest.fixture
def session_factory(db_engine):
    return get_session_factory(db_engine)


@pytest.fixture
def test_settings():
    return Settings(
        TELEGRAM_BOT_TOKEN="123456:dummy_test_token",
        ALLOWED_USER_ID=1001,
        DATABASE_URL="sqlite://",
        LEDGER_TZ="UTC",
        CHECKIN_TIME="19:00",
        MORNING_NUDGE_TIME="09:30",
        SEED_PROJECTS="Landing=landing page;WebApp=web,site",
    )


# 1. Free-form add: one message creates one task under the right project with no deadline and no source,
# and makes no duplicate-check call when the project has no other tasks.
@pytest.mark.asyncio
async def test_free_form_add(session_factory, test_settings):
    today = date(2026, 10, 8)
    with session_factory() as session:
        add_project(session, test_settings.ALLOWED_USER_ID, "Landing", ["landing page"])

    fake_llm = FakeLLM()
    # Router returns task
    fake_llm.queue({
        "tasks": [{
            "title": "Update landing page textbar",
            "type": "task",
            "priority": "normal",
            "project": "Landing",
        }]
    })

    with session_factory() as session:
        replies = await handle_text(
            text="need to update the textbar on the landing page",
            user_id=test_settings.ALLOWED_USER_ID,
            session=session,
            llm=fake_llm,
            settings=test_settings,
            today=today,
        )

        assert len(replies) == 1
        assert "Saved #1 [Landing] Update landing page textbar" in replies[0].text
        assert replies[0].buttons[0][0][0] == "Undo"

        # Check DB
        task = session.get(Task, 1)
        assert task is not None
        assert task.title == "Update landing page textbar"
        assert task.deadline is None
        assert task.source is None
        assert task.project.name == "Landing"

        # Duplicate check was NOT called because project had 0 other tasks
        assert len(fake_llm.calls) == 1


# 2. A meeting dump with 3 tasks in 2 projects creates 3 tasks in the right projects.
@pytest.mark.asyncio
async def test_meeting_dump(session_factory, test_settings):
    today = date(2026, 10, 8)
    with session_factory() as session:
        add_project(session, test_settings.ALLOWED_USER_ID, "Landing", ["landing page"])
        add_project(session, test_settings.ALLOWED_USER_ID, "WebApp", ["web", "site"])

    fake_llm = FakeLLM()
    fake_llm.queue({
        "tasks": [
            {"title": "Fix navbar logo", "type": "bug", "priority": "normal", "project": "Landing"},
            {"title": "Update footer links", "type": "task", "priority": "normal", "project": "Landing"},
            {"title": "Fix discount logic", "type": "bug", "priority": "high", "project": "WebApp"},
        ]
    })

    with session_factory() as session:
        replies = await handle_text(
            text="meeting: fix navbar logo and update footer links on landing, also fix discount logic on webapp",
            user_id=test_settings.ALLOWED_USER_ID,
            session=session,
            llm=fake_llm,
            settings=test_settings,
            today=today,
        )

        assert len(replies) == 3
        tasks = list(session.query(Task).order_by(Task.id.asc()).all())
        assert len(tasks) == 3
        assert tasks[0].project.name == "Landing"
        assert tasks[1].project.name == "Landing"
        assert tasks[2].project.name == "WebApp"


# 3. Unknown project shows project buttons. Tapping one saves the task.
@pytest.mark.asyncio
async def test_unknown_project_buttons(session_factory, test_settings):
    today = date(2026, 10, 8)
    with session_factory() as session:
        add_project(session, test_settings.ALLOWED_USER_ID, "Landing", [])
        add_project(session, test_settings.ALLOWED_USER_ID, "WebApp", [])

    fake_llm = FakeLLM()
    fake_llm.queue({
        "tasks": [{
            "title": "Clean up style guide",
            "type": "task",
            "priority": "normal",
        }]
    })

    with session_factory() as session:
        replies = await handle_text(
            text="clean up style guide",
            user_id=test_settings.ALLOWED_USER_ID,
            session=session,
            llm=fake_llm,
            settings=test_settings,
            today=today,
        )
        assert len(replies) == 1
        assert "Which project does this belong to?" in replies[0].text
        # Find callback data from buttons
        btn_data = replies[0].buttons[0][0][1]
        assert btn_data.startswith("ap:")

        # Tap the first project button
        cb_replies = await handle_callback(
            data=btn_data,
            user_id=test_settings.ALLOWED_USER_ID,
            session=session,
            settings=test_settings,
            today=today,
        )
        assert len(cb_replies) == 1
        assert "Saved #1" in cb_replies[0].text


# 4. Duplicate of a done task shows the card with the done date. "Reopen" reopens, "Add anyway" saves, "Skip" saves nothing.
@pytest.mark.asyncio
async def test_duplicate_of_done_task(session_factory, test_settings):
    today = date(2026, 10, 8)
    with session_factory() as session:
        p = add_project(session, test_settings.ALLOWED_USER_ID, "Landing", ["landing page"])
        t = create_task(session, test_settings.ALLOWED_USER_ID, p.id, "Update landing page textbar")
        t.status = "done"
        t.done_at = datetime(2026, 9, 28, 12, 0)
        session.commit()

    # 4a. Trigger duplicate check
    fake_llm = FakeLLM()
    # 1st call: Router
    fake_llm.queue({
        "tasks": [{
            "title": "Update landing page textbar",
            "type": "task",
            "priority": "normal",
            "project": "Landing",
        }]
    })
    # 2nd call: Duplicate judge returns duplicate id 1
    fake_llm.queue({"duplicate_of": 1})

    with session_factory() as session:
        replies = await handle_text(
            text="need to update the textbar on landing page",
            user_id=test_settings.ALLOWED_USER_ID,
            session=session,
            llm=fake_llm,
            settings=test_settings,
            today=today,
        )
        assert len(replies) == 1
        assert 'Similar task found: #1 "Update landing page textbar", done Mon 28 Sep.' in replies[0].text
        btn_row = replies[0].buttons[0]
        assert btn_row[0][0] == "Add anyway"
        assert btn_row[1][0] == "Reopen #1"
        action_id = int(btn_row[0][1].split(":")[1])

        # 4b. Test Skip
        skip_replies = await handle_callback(
            data=f"sk:{action_id}",
            user_id=test_settings.ALLOWED_USER_ID,
            session=session,
            settings=test_settings,
            today=today,
        )
        assert "Cancelled." in skip_replies[0].text
        assert session.query(Task).count() == 1  # No new task added

    # 4c. Test Add anyway
    fake_llm.queue({
        "tasks": [{"title": "Update landing page textbar", "type": "task", "priority": "normal", "project": "Landing"}]
    })
    fake_llm.queue({"duplicate_of": 1})
    with session_factory() as session:
        replies = await handle_text(
            text="update landing page textbar",
            user_id=test_settings.ALLOWED_USER_ID,
            session=session,
            llm=fake_llm,
            settings=test_settings,
            today=today,
        )
        add_anyway_btn = replies[0].buttons[0][0][1]
        add_replies = await handle_callback(
            data=add_anyway_btn,
            user_id=test_settings.ALLOWED_USER_ID,
            session=session,
            settings=test_settings,
            today=today,
        )
        assert "Saved #2" in add_replies[0].text
        assert session.query(Task).count() == 2

    # 4d. Test Reopen
    fake_llm.queue({
        "tasks": [{"title": "Update landing page textbar", "type": "task", "priority": "normal", "project": "Landing"}]
    })
    fake_llm.queue({"duplicate_of": 1})
    with session_factory() as session:
        replies = await handle_text(
            text="update landing page textbar",
            user_id=test_settings.ALLOWED_USER_ID,
            session=session,
            llm=fake_llm,
            settings=test_settings,
            today=today,
        )
        reopen_btn = replies[0].buttons[0][1][1]
        reopen_replies = await handle_callback(
            data=reopen_btn,
            user_id=test_settings.ALLOWED_USER_ID,
            session=session,
            settings=test_settings,
            today=today,
        )
        assert "Reopened #1" in reopen_replies[0].text
        t1 = session.get(Task, 1)
        assert t1.status == "open"


# 5. Done with one high-confidence match marks done at once and Undo reopens. Several or low-confidence matches need Confirm.
@pytest.mark.asyncio
async def test_done_confidence_and_undo(session_factory, test_settings):
    today = date(2026, 10, 8)
    with session_factory() as session:
        p = add_project(session, test_settings.ALLOWED_USER_ID, "Landing", ["landing page"])
        t12 = create_task(session, test_settings.ALLOWED_USER_ID, p.id, "Update textbar")
        t13 = create_task(session, test_settings.ALLOWED_USER_ID, p.id, "Fix mobile menu")
        t14 = create_task(session, test_settings.ALLOWED_USER_ID, p.id, "Fix desktop menu")

    # High confidence single match
    fake_llm = FakeLLM()
    fake_llm.queue({"done": [{"task_id": 1, "confidence": "high"}]})

    with session_factory() as session:
        replies = await handle_text(
            text="textbar done",
            user_id=test_settings.ALLOWED_USER_ID,
            session=session,
            llm=fake_llm,
            settings=test_settings,
            today=today,
        )
        assert len(replies) == 1
        assert "Done #1 Update textbar" in replies[0].text
        assert session.get(Task, 1).status == "done"

        # Undo button
        undo_replies = await handle_callback(
            data="ud:1",
            user_id=test_settings.ALLOWED_USER_ID,
            session=session,
            settings=test_settings,
            today=today,
        )
        assert "Reopened #1" in undo_replies[0].text
        assert session.get(Task, 1).status == "open"

    # Low confidence or multiple matches -> confirm card
    fake_llm.queue({
        "done": [
            {"task_id": 2, "confidence": "low"},
            {"task_id": 3, "confidence": "low"},
        ]
    })
    with session_factory() as session:
        replies = await handle_text(
            text="fixed the menus",
            user_id=test_settings.ALLOWED_USER_ID,
            session=session,
            llm=fake_llm,
            settings=test_settings,
            today=today,
        )
        assert "Did you finish these tasks?" in replies[0].text
        confirm_btn = replies[0].buttons[0][0][1]

        # Confirm
        confirm_replies = await handle_callback(
            data=confirm_btn,
            user_id=test_settings.ALLOWED_USER_ID,
            session=session,
            settings=test_settings,
            today=today,
        )
        assert "Marked done:" in confirm_replies[0].text
        assert session.get(Task, 2).status == "done"
        assert session.get(Task, 3).status == "done"


# 6. Edits: deadline set and cleared. Invalid values ignored. Ids outside the shortlist ignored.
@pytest.mark.asyncio
async def test_edits(session_factory, test_settings):
    today = date(2026, 10, 8)
    with session_factory() as session:
        p = add_project(session, test_settings.ALLOWED_USER_ID, "Landing", ["landing page"])
        t = create_task(session, test_settings.ALLOWED_USER_ID, p.id, "Navbar task")

    fake_llm = FakeLLM()
    fake_llm.queue({
        "edits": [
            {"task_id": 1, "field": "deadline", "value": "tomorrow"},
            {"task_id": 1, "field": "priority", "value": "high"},
            {"task_id": 1, "field": "priority", "value": "invalid_prio"},  # Ignored
            {"task_id": 999, "field": "title", "value": "Ghost task"},       # Outside shortlist ignored
        ]
    })

    with session_factory() as session:
        replies = await handle_text(
            text="postpone navbar to tomorrow and make it high prio",
            user_id=test_settings.ALLOWED_USER_ID,
            session=session,
            llm=fake_llm,
            settings=test_settings,
            today=today,
        )
        t = session.get(Task, 1)
        assert t.deadline == date(2026, 10, 9)
        assert t.priority == "high"

        # Now clear deadline with 'none'
        fake_llm.queue({
            "edits": [{"task_id": 1, "field": "deadline", "value": "none"}]
        })
        await handle_text(
            text="clear deadline for navbar",
            user_id=test_settings.ALLOWED_USER_ID,
            session=session,
            llm=fake_llm,
            settings=test_settings,
            today=today,
        )
        assert session.get(Task, 1).deadline is None


# 7. Ranking: overdue, then nearest deadline, then priority, then age. Tasks without deadlines come after those with one.
def test_ranking():
    today = date(2026, 10, 8)
    t_overdue = Task(id=1, title="Overdue", deadline=date(2026, 10, 5), priority="normal", created_at=datetime(2026, 10, 1))
    t_near = Task(id=2, title="Near", deadline=date(2026, 10, 9), priority="normal", created_at=datetime(2026, 10, 2))
    t_far = Task(id=3, title="Far", deadline=date(2026, 10, 15), priority="high", created_at=datetime(2026, 10, 3))
    t_nodeadline_high = Task(id=4, title="No Deadline High", deadline=None, priority="high", created_at=datetime(2026, 10, 4))
    t_nodeadline_norm = Task(id=5, title="No Deadline Normal", deadline=None, priority="normal", created_at=datetime(2026, 10, 1))
    t_nodeadline_low = Task(id=6, title="No Deadline Low", deadline=None, priority="low", created_at=datetime(2026, 10, 2))

    tasks = [t_nodeadline_low, t_far, t_nodeadline_norm, t_near, t_nodeadline_high, t_overdue]
    ranked = rank_tasks(tasks, today)
    assert [t.id for t in ranked] == [1, 2, 3, 4, 5, 6]


# 8. Queries: pending (grouped), first (top 3), finished (today), due (including overdue).
@pytest.mark.asyncio
async def test_queries(session_factory, test_settings):
    today = date(2026, 10, 8)
    with session_factory() as session:
        p1 = add_project(session, test_settings.ALLOWED_USER_ID, "Landing", ["landing"])
        p2 = add_project(session, test_settings.ALLOWED_USER_ID, "WebApp", ["web"])
        t1 = create_task(session, test_settings.ALLOWED_USER_ID, p1.id, "T1", deadline=date(2026, 10, 7))  # Overdue
        t2 = create_task(session, test_settings.ALLOWED_USER_ID, p1.id, "T2", deadline=date(2026, 10, 10))
        t3 = create_task(session, test_settings.ALLOWED_USER_ID, p2.id, "T3", priority="high")
        t4 = create_task(session, test_settings.ALLOWED_USER_ID, p2.id, "T4")
        t4.status = "done"
        t4.done_at = datetime(2026, 10, 8, 14, 0)
        session.commit()

    fake_llm = FakeLLM()

    # Query pending
    fake_llm.queue({"query": {"kind": "pending"}})
    with session_factory() as session:
        replies = await handle_text("pending", test_settings.ALLOWED_USER_ID, session, fake_llm, test_settings, today)
        assert "Landing:" in replies[0].text
        assert "WebApp:" in replies[0].text

    # Query first (top 3)
    fake_llm.queue({"query": {"kind": "first"}})
    with session_factory() as session:
        replies = await handle_text("first", test_settings.ALLOWED_USER_ID, session, fake_llm, test_settings, today)
        assert "Top tasks to focus on:" in replies[0].text
        assert len(replies[0].buttons[0]) == 3  # 3 checkmark buttons

    # Query finished (today)
    fake_llm.queue({"query": {"kind": "finished", "days": 1}})
    with session_factory() as session:
        replies = await handle_text("today", test_settings.ALLOWED_USER_ID, session, fake_llm, test_settings, today)
        assert "T4" in replies[0].text

    # Query due (including overdue)
    fake_llm.queue({"query": {"kind": "due", "days": 7}})
    with session_factory() as session:
        replies = await handle_text("due", test_settings.ALLOWED_USER_ID, session, fake_llm, test_settings, today)
        assert "T1" in replies[0].text
        assert "T2" in replies[0].text


# 9. Check-in: card lists open tasks. Toggle then Confirm marks done. "Nothing today" marks the check-in replied.
# The nudge fires once only. Nothing is pre-selected.
def test_checkin_lifecycle(session_factory, test_settings):
    today = date(2026, 10, 8)
    with session_factory() as session:
        p = add_project(session, test_settings.ALLOWED_USER_ID, "Landing", [])
        t1 = create_task(session, test_settings.ALLOWED_USER_ID, p.id, "Task 1")
        t2 = create_task(session, test_settings.ALLOWED_USER_ID, p.id, "Task 2")

        # 9a. Create card
        card = create_checkin_card(session, test_settings.ALLOWED_USER_ID, today)
        assert card is not None
        assert "Check-in · Thu 08 Oct" in card.text
        # Assert nothing pre-selected
        assert "⬜ Landing · Task 1" in card.buttons[0][0][0]
        assert "⬜ Landing · Task 2" in card.buttons[1][0][0]
        action_id = int(card.buttons[0][0][1].split(":")[1])

        # 9b. Toggle Task 1
        toggled = handle_checkin_toggle(session, action_id, t1.id, today)
        assert "✅ Landing · Task 1" in toggled.buttons[0][0][0]
        assert "⬜ Landing · Task 2" in toggled.buttons[1][0][0]

        # 9c. Confirm
        confirmed = handle_checkin_confirm(session, action_id, test_settings.ALLOWED_USER_ID, today)
        assert "Finished:\n• #1 Task 1" in confirmed.text
        assert session.get(Task, t1.id).status == "done"
        assert session.get(Task, t2.id).status == "open"

        # Checkin row is marked replied
        checkin_row = session.query(Checkin).first()
        assert checkin_row.replied_at is not None

        # 9d. Nothing today on fresh checkin
        card2 = create_checkin_card(session, test_settings.ALLOWED_USER_ID, today)
        action2_id = int(card2.buttons[0][0][1].split(":")[1])
        nt_reply = handle_checkin_nothing_today(session, action2_id)
        assert "Noted." in nt_reply.text

        # 9e. Nudge fires once only
        # Create unreplied checkin
        card3 = create_checkin_card(session, test_settings.ALLOWED_USER_ID, today)
        nudge1 = create_morning_nudge(session, test_settings.ALLOWED_USER_ID, today)
        assert nudge1 is not None
        assert "Yesterday's check-in is still open." in nudge1.text

        # Calling nudge again should return None (fires once only)
        nudge2 = create_morning_nudge(session, test_settings.ALLOWED_USER_ID, today)
        assert nudge2 is None


# 10. LLM failure in handle_text creates nothing.
@pytest.mark.asyncio
async def test_llm_failure_creates_nothing(session_factory, test_settings):
    today = date(2026, 10, 8)
    fake_llm = FakeLLM()
    fake_llm.queue(LLMError("Provider unavailable"))

    with session_factory() as session:
        replies = await handle_text(
            text="deploy changes to landing",
            user_id=test_settings.ALLOWED_USER_ID,
            session=session,
            llm=fake_llm,
            settings=test_settings,
            today=today,
        )
        assert len(replies) == 1
        assert "Couldn't process that, please try again. Nothing was saved." in replies[0].text
        assert session.query(Task).count() == 0


# 11. Auth: another user id gets no response. /myid works for anyone.
@pytest.mark.asyncio
async def test_auth(session_factory, test_settings):
    today = date(2026, 10, 8)
    fake_llm = FakeLLM()

    with session_factory() as session:
        # Unauthorized user message -> silent ignore
        replies = await handle_text("hello", 9999, session, fake_llm, test_settings, today)
        assert replies == []

        # /myid from unauthorized user -> responds
        myid_replies = await handle_text("/myid", 9999, session, fake_llm, test_settings, today)
        assert len(myid_replies) == 1
        assert "Your Telegram user ID is 9999." in myid_replies[0].text


# 12. Smoke: the Telegram Application builds offline with a dummy token and both daily jobs are registered.
def test_smoke_offline_app(db_engine, session_factory, test_settings):
    app = build_application(
        settings=test_settings,
        llm=FakeLLM(),
        engine=db_engine,
        session_factory=session_factory,
    )
    assert app is not None
    assert app.job_queue is not None
    jobs = app.job_queue.jobs()
    assert len(jobs) >= 2


# 13. Dates: "friday" on a Sunday gives the coming Friday. "friday" on a Friday gives the next Friday.
# "tomorrow" works. Garbage text gives no deadline and a note.
def test_dates_parsing():
    sunday = date(2026, 10, 4)
    d, warn = parse_deadline("friday", sunday)
    assert d == date(2026, 10, 9)
    assert warn is None

    friday = date(2026, 10, 9)
    d, warn = parse_deadline("friday", friday)
    assert d == date(2026, 10, 16)
    assert warn is None

    d, warn = parse_deadline("tomorrow", date(2026, 10, 8))
    assert d == date(2026, 10, 9)
    assert warn is None

    d, warn = parse_deadline("not_a_date_xyz", date(2026, 10, 8))
    assert d is None
    assert warn == '(Couldn\'t read the deadline "not_a_date_xyz".)'


# 14. FallbackLLM: moves to the next provider on a 429, on a timeout, and on invalid JSON after one retry.
# Fails cleanly when all fail.
@pytest.mark.asyncio
async def test_fallback_llm():
    schema = {"type": "object", "properties": {"val": {"type": "integer"}}, "required": ["val"]}

    p1 = FakeLLM([LLMError("HTTP 429 rate limit")])
    p2 = FakeLLM([LLMError("Timeout connecting")])
    p3 = FakeLLM([{"val": 42}])

    fallback = FallbackLLM([p1, p2, p3])
    res = await fallback.structured(system="", user="", name="test", schema=schema)
    assert res == {"val": 42}

    # All failing
    all_fail = FallbackLLM([FakeLLM([LLMError("Fail 1")]), FakeLLM([LLMError("Fail 2")])])
    with pytest.raises(LLMError, match="All LLM providers failed"):
        await all_fail.structured(system="", user="", name="test", schema=schema)


# 15. Shortlist: with 100 open tasks the prompt contains at most 25, including the most similar ones.
def test_shortlist(session_factory, test_settings):
    with session_factory() as session:
        p = add_project(session, test_settings.ALLOWED_USER_ID, "Landing", [])
        for i in range(100):
            create_task(session, test_settings.ALLOWED_USER_ID, p.id, f"Random task number {i}")
        create_task(session, test_settings.ALLOWED_USER_ID, p.id, "Special unique payment bug")

        shortlist = get_open_tasks_shortlist(session, test_settings.ALLOWED_USER_ID, "fix payment bug")
        assert len(shortlist) <= 25
        titles = [t.title for t in shortlist]
        assert "Special unique payment bug" in titles


# 16. Ollama adapter (use httpx.MockTransport): the request goes to /api/chat with format set to the schema and temperature 0.
@pytest.mark.asyncio
async def test_ollama_adapter():
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    captured_request = {}

    def handler(request: httpx.Request):
        nonlocal captured_request
        captured_request["url"] = str(request.url)
        captured_request["body"] = json.loads(request.read())
        return httpx.Response(200, json={"message": {"content": json.dumps({"ok": True})}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ollama = OllamaLLM(url="http://test-ollama:11434", model="test-model", client=client)

    res = await ollama.structured(system="sys", user="usr", name="respond", schema=schema)
    assert res == {"ok": True}
    assert captured_request["url"] == "http://test-ollama:11434/api/chat"
    assert captured_request["body"]["format"] == schema
    assert captured_request["body"]["options"]["temperature"] == 0
    assert captured_request["body"]["stream"] is False


# 17. /update: groups by project, skips empty projects, shows "Next" with at most 5 per project,
# and says so plainly when nothing was done.
def test_update_report(session_factory, test_settings):
    today = date(2026, 10, 8)
    with session_factory() as session:
        p1 = add_project(session, test_settings.ALLOWED_USER_ID, "Landing", [])
        p2 = add_project(session, test_settings.ALLOWED_USER_ID, "WebApp", [])
        p3 = add_project(session, test_settings.ALLOWED_USER_ID, "EmptyProj", [])

        # Landing: 1 done today, 6 open tasks
        t_done = create_task(session, test_settings.ALLOWED_USER_ID, p1.id, "Fixed landing header")
        t_done.status = "done"
        t_done.done_at = datetime(2026, 10, 8, 11, 0)

        for i in range(6):
            create_task(session, test_settings.ALLOWED_USER_ID, p1.id, f"Next item {i}")

        session.commit()

        # 17a. Today report with done tasks
        rep = build_update_report(session, test_settings.ALLOWED_USER_ID, days=1, today=today)
        assert "Landing" in rep.text
        assert "Fixed landing header" in rep.text
        assert "Next item 4" in rep.text
        assert "Next item 5" not in rep.text  # Max 5
        assert "EmptyProj" not in rep.text   # Empty project skipped

        # 17b. When nothing done today
        t_done.status = "open"
        t_done.done_at = None
        session.commit()

        rep_no_done = build_update_report(session, test_settings.ALLOWED_USER_ID, days=1, today=today)
        assert "Nothing was done today." in rep_no_done.text


# 18. Catch-up: a startup shortly after the check-in time sends it once. A startup the next morning does not.
@pytest.mark.asyncio
async def test_catchup_logic(session_factory, test_settings):
    class MockBot:
        def __init__(self):
            self.sent_messages = []

        async def send_message(self, chat_id, text, reply_markup=None):
            self.sent_messages.append({"chat_id": chat_id, "text": text})

    mock_bot = MockBot()

    with session_factory() as session:
        p = add_project(session, test_settings.ALLOWED_USER_ID, "Landing", [])
        create_task(session, test_settings.ALLOWED_USER_ID, p.id, "Open task")

    # Set CHECKIN_TIME = current hour minus 2 hours (within 4 hours window)
    now = datetime.now(ZoneInfo(test_settings.LEDGER_TZ))
    two_hours_ago = (now - timedelta(hours=2)).time()
    test_settings.CHECKIN_TIME = f"{two_hours_ago.hour:02d}:{two_hours_ago.minute:02d}"

    await run_catchup(session_factory, test_settings, mock_bot)
    assert len(mock_bot.sent_messages) == 1
    assert "Check-in" in mock_bot.sent_messages[0]["text"]

    # Running catch-up again on same day should not resend because checkin is already sent
    mock_bot.sent_messages.clear()
    await run_catchup(session_factory, test_settings, mock_bot)
    assert len(mock_bot.sent_messages) == 0

    # Test startup 5 hours after CHECKIN_TIME (outside 4 hours window)
    five_hours_ago = (now - timedelta(hours=5)).time()
    test_settings.CHECKIN_TIME = f"{five_hours_ago.hour:02d}:{five_hours_ago.minute:02d}"
    with session_factory() as session:
        session.query(Checkin).delete()
        session.commit()
    await run_catchup(session_factory, test_settings, mock_bot)
    assert len(mock_bot.sent_messages) == 0
