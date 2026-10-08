# Work Ledger: build brief for a coding agent

Read this whole file first. Build the phases in section 11 **in order**, and **stop and report after each phase**.
Unless the user says otherwise, build **Phase 1 only**, then stop.
(This is the full brief. A short `AGENTS.md` next to it points here. The brief lives in its own file because Antigravity truncates rule files above 24,000 bytes.)

---

## 1. What you are building

**Work Ledger** is a private, personal work ledger for a single developer, used through a **Telegram bot**.
It is designed to be open sourced, so nothing in the code or docs may be specific to one company, project or person.

**The problem:** developers get requests from many people (managers, clients, testers, meetings). Requests live in notebooks and chats.
Developers forget whether something was already done, redo work, get the same bug reported again, and cannot prove when or how it was fixed.
Existing standup tools record what you did. None of them records **what you were asked to do and whether it is done, with proof.**

**What it does:**
1. **Capture:** the user types requests in plain English. The bot stores them as tasks.
2. **Remember:** before saving, it warns if the task looks like something already open or already done, and shows when it was done and the commit that did it.
3. **Close the loop:** an end-of-day check-in shows open tasks and today's commits (read from local git repos). The user confirms what is done. Done tasks keep their commits as proof.
4. **Report:** `/update` writes a paste-ready daily or weekly update from the ledger.
5. **Checklist everywhere:** tasks are mirrored to a Notion database, so the user can tick them on phone or laptop (Phase 3).

**Who it is for:** one developer in v1 (freelancers, interns, anyone taking requests from several people). Team features come later.
**Positioning:** the data belongs to the individual. It is a personal tool, not a manager's tracking tool.

### Principles (do not break these)
1. **The database is the source of truth. The user is the authority on status.** Nothing is ever closed automatically from a guess.
   Commits and LLM matches are suggestions only.
2. **Free-form input, English only.** No required message format. Only the task text is required. Missing project, deadline or source is fine.
3. **Never lose or invent data.** If the LLM call fails, save nothing and tell the user to retry.
4. **Every save can be undone** with one tap.
5. **Basic level.** Simple, readable code with few dependencies. A student should be able to read and extend it.
6. **Private by default.** Everything runs on the user's machine. Only the chosen LLM provider (and Notion, in Phase 3) ever receives data.
7. **Team-ready, not team-built.** Every project and task has an `owner_id` column set to the single user's Telegram id. Build no multi-user logic.

---

## 2. How to work

- Build in phase order. After each phase: run the tests, then give a short report and stop.
- Keep the code small and boring. Build nothing that is not in this file. Ask the user before adding anything.
- Write tests as you go. **Tests must never call real Telegram, LLM, Notion or GitHub services.** Use fakes (section 10).
- Never commit secrets. Provide `.env.example`. Keep `.env`, `*.db` and `.venv` in `.gitignore`.
- Plain-text bot messages only (no Markdown or HTML parse modes), so odd characters in task text never break sending.
- Do not deploy anything. Do not create accounts or bots. Explain manual steps in the README instead.
- Do not log message text, task titles or commit messages at INFO level. Log ids and errors only.
- No company-specific, person-specific or product-specific names anywhere. Use generic examples (projects "WebApp", "Mobile", "Landing").
- Do not add a LICENSE file. Put `TODO: choose a licence (MIT or AGPL)` at the end of the README.
- Do not copy code from other projects (section 14). Reimplement ideas.
- If something is ambiguous, pick the simplest option, note it in your report, and continue.
- Check current SDK and API docs before coding against Telegram, Groq, Ollama and Notion. Do not guess parameter names.

---

## 3. Setup steps (write these into README.md)

Prerequisites: Python 3.11+, git, a Telegram account, and an LLM option (Groq key, or Ollama with a local model).

1. **Create the bot:** in Telegram open `@BotFather`, send `/newbot`, copy the token.
2. **Get your Telegram user id:** start the bot (step 6), send `/myid` (it answers anyone), put the number in `ALLOWED_USER_ID`, restart.
3. **LLM, option A (Groq, free tier):** create a key in the Groq console and put it in `GROQ_API_KEY`.
4. **LLM, option B (local, private):** install Ollama, then `ollama pull qwen2.5:7b`. Create a variant with a bigger context, because the
   default context can silently cut long prompts. Make a file named `Modelfile`:
   ```
   FROM qwen2.5:7b
   PARAMETER num_ctx 8192
   ```
   then run `ollama create qwen2.5-ledger -f Modelfile` and set `OLLAMA_MODEL=qwen2.5-ledger`.
   Run `ollama ps` while it answers: on a 6 GB GPU it should say 100% GPU. If it splits to CPU, lower `num_ctx` to 4096.
5. **Install:**
   ```
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   cp .env.example .env             # then fill it in
   python -m ledger.doctor          # checks every setting, prints OK/FAIL with fixes
   ```
6. **Run:** `python -m ledger`
7. **Test:** `pytest -q`
8. **Add projects:** set `SEED_PROJECTS` in `.env`, or send `/addproject WebApp = web, site` in Telegram.
9. **Connect repos (Phase 2):** `/setrepo WebApp C:\code\webapp` (a local clone path on the machine running the bot).
10. **Notion (Phase 3):** see section 11.

### `.env.example`
```
TELEGRAM_BOT_TOKEN=
ALLOWED_USER_ID=0
DATABASE_URL=sqlite:///ledger.db
LEDGER_TZ=UTC                    # for example Asia/Kolkata
CHECKIN_TIME=19:00
MORNING_NUDGE_TIME=09:30

# Ordered list. The first provider is tried first; the next one is used when it fails or is rate limited.
LLM_PROVIDERS=groq,ollama
GROQ_API_KEY=
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_JSON_MODE=false             # set true if the model rejects forced tool calls (doctor tells you)
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-ledger
ANTHROPIC_API_KEY=               # optional third provider
ANTHROPIC_MODEL=claude-haiku-4-5-20251001

SEED_PROJECTS=WebApp=web,site;Mobile=app;Landing=landing page
GIT_AUTHOR_EMAILS=               # Phase 2. Comma separated. Empty = each repo's own git user.email
NOTION_TOKEN=                    # Phase 3
NOTION_DATABASE_ID=              # Phase 3
```
Use `LEDGER_TZ`, not `TZ`, so the operating system's variable is not overridden.
If the first provider in `LLM_PROVIDERS` is local and the user lists no cloud provider, nothing ever leaves the machine.

---

## 4. Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | |
| Telegram | `python-telegram-bot[job-queue]` >= 21 | Long polling. JobQueue runs the scheduled jobs. No separate scheduler or web server |
| DB | SQLAlchemy 2.x + SQLite | Plain SQLAlchemy only, so Postgres works later via `DATABASE_URL` |
| LLM | `groq` or `openai` SDK for Groq, `httpx` for Ollama, optional `anthropic` | Behind one interface (section 9) |
| Dates | `dateparser` | Python resolves dates, not the model |
| Git | the `git` command through `subprocess` | Phase 2. Read-only. No GitPython |
| Notion | `httpx` directly | Phase 3. API version `2025-09-03` |
| Config | `python-dotenv` | |
| Time zones | `tzdata` | Needed on Windows |
| Tests | `pytest` | In-memory SQLite, fake LLM, temp git repos, fake Notion |

**Deliberate simplifications:** no FastAPI or webhooks, no embeddings or vector database, no migration tool (`create_all` at startup),
no web dashboard, no accounts or login. If a column is needed later, add it to the model now (section 7) because `create_all` will not alter existing tables.

---

## 5. Project layout

```
.
├── AGENTS.md                 # short pointer to this brief (provided by the user)
├── BUILD_BRIEF.md            # this file
├── README.md                 # setup (section 3), usage examples, privacy note (section 13)
├── requirements.txt
├── .env.example
├── .gitignore
├── ledger/
│   ├── __main__.py           # python -m ledger -> bot.main()
│   ├── config.py             # Settings, loads env
│   ├── db.py                 # models, session factory
│   ├── util.py               # time helpers (naive UTC storage, local date helpers)
│   ├── dates.py              # deadline_text -> date (dateparser)
│   ├── llm.py                # LLM interface, adapters, fallback wrapper
│   ├── prompts.py            # system prompts + JSON schemas
│   ├── projects.py           # alias resolution, add, seed from env
│   ├── tasks.py              # create/done/reopen/delete/edit, ranking, shortlist, duplicate candidates
│   ├── views.py              # DB rows -> replies (no LLM)
│   ├── report.py             # /update text builder (no LLM)
│   ├── checkin.py            # check-in card, toggle, confirm, morning nudge
│   ├── router.py             # handle_text(), handle_callback() -> list[Reply]
│   ├── reply.py              # Reply dataclass
│   ├── bot.py                # Telegram glue only
│   ├── doctor.py             # python -m ledger.doctor
│   ├── gitlog.py             # Phase 2: read commits from local repos
│   ├── evidence.py           # Phase 2: match commits to tasks (suggestions only)
│   ├── notion.py             # Phase 3: tiny Notion API client
│   └── sync.py               # Phase 3: two-way sync loop
└── tests/
    ├── test_core.py
    ├── test_git.py           # Phase 2
    ├── test_notion.py        # Phase 3
    ├── eval_llm.py           # manual, real providers, not run by pytest
    └── eval_cases.json
```

**Architecture rule:** all logic lives in `router.py`, `tasks.py`, `views.py`, `report.py`, `checkin.py`, `evidence.py`, `sync.py`.
They return plain `Reply` objects and know nothing about Telegram. `bot.py` only converts `Reply` to Telegram messages.

```python
@dataclass
class Reply:
    text: str
    buttons: list[list[tuple[str, str]]]   # rows of (label, callback_data)
    edit: bool = True                      # for button taps: edit the card in place; False = send a new message
```

---

## 6. Behaviour

### 6.1 Free-form capture
Every text message goes through **one** LLM call (the "router", section 9) that returns structured JSON with any of:
`tasks` (new), `done`, `edits`, `query`, `question`, `reply`. One message may combine them
("textbar done, also add footer link for the landing page").

| User types | Result |
|---|---|
| `need to update the textbar on the landing page` | Task under Landing, title "Update landing page textbar", no deadline, no source |
| `tester says discount not applying for 3+ seats in webapp` | WebApp, type bug, source tester |
| `boss wants an export button in the webapp by Friday` | WebApp, enhancement, source "boss", deadline = next upcoming Friday |
| `meeting: date filter broken on webapp, push notification copy change on mobile` | Two tasks in two projects |
| `done the textbar thing` | Matches the open task and marks it done |
| `what's pending for mobile` | Pending list for that project |

Task fields: `project`, `title` (short, imperative), `type` (bug / enhancement / task), `source` (a role or a name, or empty),
`deadline_text` (the user's own words for the date, or empty), `priority` (low / normal / high, default normal), `notes`.

Rules:
- Project names are matched **in Python** against names and aliases, ignoring case, spaces and punctuation (`Landing Page` = `landingpage` = `landing`).
- If the project cannot be resolved: with exactly one project, use it; otherwise ask with one button per project (callback `ap`).
- **Dates are resolved in Python** (`dates.py`), not by the model. The model copies the user's words ("friday", "tomorrow", "15 oct", "next week").
  Use `dateparser` with `PREFER_DATES_FROM=future`, `RELATIVE_BASE` = today in the user's timezone. A bare weekday means the next occurrence
  strictly after today. If the result cannot be parsed or is in the past, save the task without a deadline and add "(Couldn't read the deadline "xyz".)".
- Ask a clarifying question only when something important is truly ambiguous (for example one deadline that could belong to either of two tasks:
  leave it unset on both and ask in a follow-up message). The user answers in plain text and the `edits` mechanism fixes it.
- **Save instantly, with Undo.** Reply with one line and an `[Undo]` button, for example:
  `Saved #12 [Landing] Update landing page textbar · due Fri 10 Oct · from boss`.
- Unrecognised message: reply with a short hint and an example. Never crash.

### 6.2 "Did I already do this?" check (before saving a task)
1. Take the project's tasks with status other than `dropped`. Rank them by text similarity to the new title
   (token Jaccard on lightly stemmed words, combined with `difflib.SequenceMatcher`; take the max). Keep the top 40.
2. If there are none, skip the check (no extra LLM call).
3. Otherwise make one LLM call (the "duplicate judge", section 9) that returns `duplicate_of` (an id) or nothing. Only accept ids from the candidate list.
4. If a duplicate is found, do **not** save yet. Store a pending action and show:
   - done duplicate: `Similar task found: #8 "Update landing page textbar", done Sun 28 Sep.` and, **when proof exists (Phase 2)**, a second line
     `Proof: a1b2c3d "fix textbar copy"`. Buttons `[Add anyway] [Reopen #8] [Skip]`.
   - open duplicate: `Already open: #8 "...".` Buttons `[Add anyway] [Skip]`.
5. If the duplicate check itself fails, save the task anyway and add "(Couldn't run the duplicate check this time.)".

### 6.3 Marking done
- The user says it in text, or taps a ✅ button in a list.
- Text path: the LLM returns task ids **only from the open-task list it was given**, each with confidence `high` or `low`.
  - Exactly one id with `high` confidence: mark done immediately and show `[Undo]` (reopens it).
  - Anything else: show a confirm card listing the tasks with `[Confirm] [Cancel]`.
- Statuses: `open`, `in_progress`, `done`, `dropped`. (The UI uses only `open` and `done` in v1.)
- Every change writes an `events` row (created, edited, done, reopened) and updates `tasks.updated_at`.

### 6.4 Edits
The router can return `edits`: `{task_id, field, value}` with field in `deadline | priority | title | type | source | notes`.
For `deadline`, `value` is the user's words (resolved in Python) or `none`. `none` also clears source and notes.
Validate in Python, ignore invalid edits, and reply `Updated #12 deadline → Mon 06 Oct`. Only open tasks can be edited this way.

### 6.5 Questions (answered from the database, never from LLM guesses)
The router returns `query.kind` in `pending | first | finished | due` plus optional `project` and `days`. Python builds the answer.

- **pending:** open tasks grouped by project, in ranked order, each line `#id title · reason`. Max 15 per group with "+N more".
  ✅ buttons for the first 8 tasks (callback `dn`, reply with `edit=False` so the list stays).
- **first:** top 3 ranked tasks with a one-line reason each, plus ✅ buttons.
- **finished:** tasks done in the last `days` (default 1 = today), grouped by local date.
- **due:** open tasks with a deadline within `days` (default 7), including overdue.
- Unknown project name: say so and list the known projects.

**Ranking** (exactly this order): overdue first, then nearest deadline (tasks without a deadline after those with one),
then priority (high > normal > low), then oldest first.
**Reason text:** `overdue by 2d (was Fri 03 Oct)`, `due Fri 10 Oct (in 6d)` / `(today)` / `(tomorrow)`, `high priority`, or `open since 28 Sep`.
Dates are shown like `Fri 10 Oct` in the user's timezone.

### 6.6 End-of-day check-in and morning nudge
- A daily job at `CHECKIN_TIME` (local) sends a check-in card to `ALLOWED_USER_ID`. If there are no open tasks, send nothing.
- Card text: `Check-in · Thu 08 Oct` + `What did you finish? Tap the tasks, then Confirm. Or just type it, e.g. "did the textbar".`
- One toggle button per open task, up to 12 in ranked order: `⬜ Landing · Update landing page te…` (becomes `✅` when selected).
  If more than 12 are open, add "(+N more open tasks not shown. Type it if you finished one of those.)".
- Last row: `[Confirm (n)] [Nothing today]`. Confirm with nothing selected re-shows the card with a hint.
- Confirm marks the selected tasks done and replies with the list. **Nothing else is closed.** Nothing is pre-selected, ever.
- Typing a reply instead of tapping goes through the normal router (6.3).
- Selected state lives in a stored pending action, so the card survives a restart.
- Track check-ins in `checkins`. Any done action or "Nothing today" marks the latest unanswered check-in as replied.
- A daily job at `MORNING_NUDGE_TIME`: if the latest check-in was never replied to and never nudged, resend the card prefixed
  `Yesterday's check-in is still open.` Nudge **once** per check-in.
- **Phase 2 adds** a "Today's commits" block above the buttons and a 💡 marker on tasks that have a suggested commit (section 11).
  In Phase 1 leave a clean function where this block plugs in. Do not build it yet.

### 6.7 `/update` (generated update, no LLM)
`/update` builds a paste-ready plain-text update for **today**. `/update week` covers the last 7 days. Built by Python from the database only,
so it works offline and can never invent anything:

```
Update · Thu 08 Oct

Landing
  Done
  - Update landing page textbar
  Commits                      <- Phase 2 only, only for projects with repos
  - a1b2c3d fix textbar copy
  Next
  - Add footer link (due Fri 10 Oct)

WebApp
  Done
  - ...
```
Rules: group by project, skip projects with nothing to show, "Next" lists the top 5 ranked open tasks per project, done items sorted by time.
If nothing was done in the period, say so plainly. The reply has no buttons, so the text is easy to copy.

### 6.8 Commands
| Command | Does |
|---|---|
| `/start`, `/help` | Short usage with 3 examples |
| `/myid` | Replies with the sender's Telegram user id. **Works for anyone** (needed for setup) |
| `/projects` | Lists projects, aliases and connected repos |
| `/addproject Name = alias1, alias2` | Adds a project (aliases optional) |
| `/pending [project]` | Same as the pending query |
| `/first [project]` | Same as the first query |
| `/today` | Tasks done today |
| `/checkin` | Sends the check-in card now |
| `/update [week]` | Section 6.7 |
| `/setrepo Project path` | Phase 2: connect a local git repo to a project |
| `/sync` | Phase 3: run a Notion sync now |

Everything except `/myid` is **ignored silently for any user other than `ALLOWED_USER_ID`**. If `ALLOWED_USER_ID` is 0, only `/myid` and `/start`
respond (telling the user to run `/myid` and set the variable). Non-text messages get "Text only for now."

### 6.9 Button cards
- Callback data is limited to 64 bytes, so use short codes with ids. State needed across taps lives in `pending_actions`, **not in memory**.
- Taps edit the original card in place, except ✅ taps on lists, which send a new message so the list stays.
- A tap on an expired or already used card replies "That card has expired. Just send the message again."

| Code | Meaning |
|---|---|
| `un:<task>` | Undo a save (delete the task and its events) |
| `ud:<task>` | Undo a done (reopen) |
| `dn:<task>` | Mark done from a list |
| `ap:<action>:<project>` | Choose the project for a pending add |
| `aa:<action>` | Add anyway (skip the duplicate check) |
| `ro:<action>:<task>` | Reopen the existing duplicate instead of adding |
| `sk:<action>` | Skip / cancel a card |
| `dc:<action>` | Confirm a done list |
| `ct:<action>:<task>` | Toggle a task on the check-in card |
| `cok:<action>` / `cno:<action>` | Check-in Confirm / Nothing today |

### 6.10 Errors
- LLM call fails or returns invalid output (after the fallback chain): reply "Couldn't process that, please try again. Nothing was saved." Log the error.
- Telegram "message is not modified" errors on edits are ignored.
- A failing background job must never crash the bot. Log it.

---

## 7. Data model (SQLAlchemy)

Store datetimes as **naive UTC**. Convert to the user's timezone only for display and for "today" boundaries.
Declare **all** columns below now, even those used only in later phases.

```
projects(id, owner_id int, name, aliases JSON list, repo_paths JSON list default [], created_at)   # unique (owner_id, name)
tasks(id, owner_id int, project_id FK, title<=300, type 'bug'|'enhancement'|'task', source nullable,
      status, priority, deadline date nullable, notes nullable,
      created_at, updated_at, done_at nullable,
      notion_page_id nullable, notion_synced_at nullable)
events(id, task_id FK, type, note, ts)                       # audit trail
pending_actions(id, kind 'add'|'done'|'checkin', payload JSON, created_at)
checkins(id, sent_at, replied_at nullable, nudged_at nullable)
```
Added by later phases (new tables are fine, since `create_all` creates missing tables):
```
commits(id, project_id FK, repo_path, sha, subject, files JSON list, author_email, committed_at)   # unique (repo_path, sha)   Phase 2
task_commits(id, task_id FK, commit_id FK, confidence 'high'|'low', confirmed bool)                 # Phase 2
meta(key primary key, value)                                                                         # Phase 3, sync cursor
```
Prune `pending_actions` older than 14 days at startup.

---

## 8. Scheduling
Use the JobQueue from python-telegram-bot: `run_daily` for the check-in and the nudge, with `datetime.time` objects carrying
`tzinfo=ZoneInfo(LEDGER_TZ)`. Schedule only when `ALLOWED_USER_ID` is set. A private chat id equals the user id.
If `app.job_queue` is `None`, raise a clear error telling the user to install `python-telegram-bot[job-queue]`.

**Catch-up (laptops sleep):** at startup, if today's `CHECKIN_TIME` has already passed by less than 4 hours and no check-in was sent today, send it once now.

---

## 9. LLM layer

One interface, one method. It forces the model to answer with JSON that matches a schema:

```python
class LLM(Protocol):
    async def structured(self, *, system: str, user: str, name: str, schema: dict) -> dict: ...
```

**Providers** (selected by `LLM_PROVIDERS`, an ordered list):
- **GroqLLM:** Groq's OpenAI-compatible API (check the docs for the base URL, or use the `groq` SDK). Default model `llama-3.3-70b-versatile`.
  Use a forced tool call: `tools=[{type: function, function: {name, parameters: schema}}]` with `tool_choice` naming that function.
  Groq's docs do not confirm forced `tool_choice`, so if the API rejects it, use JSON mode instead
  (`response_format={"type": "json_object"}`, schema pasted into the system prompt) when `GROQ_JSON_MODE=true`. `doctor` tests both and tells the user which to set.
  Free-tier limits are tight (the docs list about 8,000 tokens per minute for some models; verify in the user's console), so **keep each call under about 2,000 tokens**.
- **OllamaLLM:** native API, not the OpenAI-compatible one (that one does not support `tool_choice`).
  `POST {OLLAMA_URL}/api/chat` with `"stream": false`, `"format": <the JSON schema>`, `"options": {"temperature": 0}`, and the schema also pasted into the system prompt.
  Parse `message.content` as JSON. The context size comes from the model variant the user created (section 3, step 4).
- **AnthropicLLM** (optional): `tools` + `tool_choice={"type": "tool", "name": name}`; return the `tool_use` input.

**Validation:** after every response, validate against the schema (`jsonschema`). On invalid output, retry once with the error text appended. Then fail.
**FallbackLLM:** tries providers in order. It moves to the next provider on rate limit (429), timeout, connection error, or invalid output after the retry.
If all fail, raise `LLMError`. Log which provider answered (never the text).

### 9.1 Making it work with small models (required)
1. **Shortlist the open tasks.** Send the model at most 25 open tasks: all of them if there are 25 or fewer, otherwise the 20 most similar to the message
   (same similarity function as 6.2) plus the 5 most recent. Format `id | project | title`. No deadlines in the list.
2. **Dates stay out of the model** (6.1).
3. **Short prompts.** The router call should stay well under 2,000 tokens.
4. **Python validates everything:** ids must be in the shortlist, enums are checked, bad edits are dropped.

### 9.2 Router prompt
System prompt (keep it close to this):

> You are the parser for a personal work-tracking bot used by a software developer. The user types short, messy messages in English about tasks on their projects.
> Tasks come from managers, clients, testers and meetings, or are the user's own notes.
> Answer with exactly one call to `respond`. Fill only the parts that apply:
> - **tasks:** NEW tasks to add. One entry per distinct task; split meeting dumps. Never invent a project, deadline or source the message does not state or clearly imply. Titles are short, specific, imperative.
> - **deadline_text:** copy the user's own words for the date ("friday", "tomorrow", "15 oct"). Do not convert it. Omit if none.
> - **done:** existing OPEN tasks the user says they finished. Use ONLY ids from the open task list. confidence is "high" only if exactly one listed task clearly matches; otherwise "low".
> - **edits:** changes to existing open tasks (deadline, priority, title, type, source, notes).
> - **query:** when the user asks what is pending, what to do first, what they finished, or what is due. Set project only if they name one; set days only if they give a period ("this week" = 7).
> - **question:** ONE short clarifying question, only if something important is genuinely ambiguous. Otherwise omit.
> - **reply:** a short friendly reply only if the message is none of the above.
> A message can combine these. For project, use the name exactly as listed when the message names it or an alias.

User prompt, built in Python:
```
Projects:
- Landing (aliases: landing page)
- WebApp (aliases: web, site)

Open tasks (id | project | title):
12 | Landing | Update landing page textbar

Message:
<the user's text>
```

JSON schema (all optional at the top level; avoid nullable types, write "omit if none" in descriptions):
```
tasks[]:  title (req), type enum bug|enhancement|task (req), priority enum low|normal|high (req),
          project, source, deadline_text, notes
done[]:   task_id int (req), confidence enum high|low (req)
edits[]:  task_id int, field enum deadline|priority|title|type|source|notes, value string   (all req)
query:    kind enum pending|first|finished|due (req), project string, days int
question: string
reply:    string
```
Apply in this order: edits, done, add tasks, query, question, reply (so a query reflects changes made in the same message).

### 9.3 Duplicate judge
System prompt: *Compare a NEW task with EXISTING tasks from the same project. Answer with one call to `respond`. Set `duplicate_of` to the id of an existing task ONLY if the new task is clearly the same piece of work (same bug or same change), not merely the same area. If unsure, omit it.*
Schema: `{duplicate_of: integer (optional)}`. The user prompt lists `id | status | title` (add the done date for done tasks).

---

## 10. Tests

### `tests/test_core.py` (pytest, never touches the network)
Use in-memory SQLite (`sqlite://` with `StaticPool`, `check_same_thread=False`) and a **FakeLLM** that returns queued responses in order and can raise.
Pass a fixed `today` into the logic so tests are deterministic. Required tests:

1. Free-form add: one message creates one task under the right project with no deadline and no source, and makes **no duplicate-check call** when the project has no other tasks.
2. A meeting dump with 3 tasks in 2 projects creates 3 tasks in the right projects.
3. Unknown project shows project buttons. Tapping one saves the task.
4. Duplicate of a done task shows the card with the done date. "Reopen" reopens, "Add anyway" saves, "Skip" saves nothing.
5. Done with one high-confidence match marks done at once and Undo reopens. Several or low-confidence matches need Confirm.
6. Edits: deadline set and cleared. Invalid values ignored. Ids outside the shortlist ignored.
7. Ranking: overdue, then nearest deadline, then priority, then age. Tasks without deadlines come after those with one.
8. Queries: pending (grouped), first (top 3), finished (today), due (including overdue).
9. Check-in: card lists open tasks. Toggle then Confirm marks done. "Nothing today" marks the check-in replied. The nudge fires once only. Nothing is pre-selected.
10. LLM failure in `handle_text` creates nothing.
11. Auth: another user id gets no response. `/myid` works for anyone.
12. Smoke: the Telegram `Application` builds offline with a dummy token and both daily jobs are registered.
13. Dates: "friday" on a Sunday gives the coming Friday. "friday" on a Friday gives the next Friday. "tomorrow" works. Garbage text gives no deadline and a note.
14. FallbackLLM: moves to the next provider on a 429, on a timeout, and on invalid JSON after one retry. Fails cleanly when all fail.
15. Shortlist: with 100 open tasks the prompt contains at most 25, including the most similar ones.
16. Ollama adapter (use `httpx.MockTransport`): the request goes to `/api/chat` with `format` set to the schema and `temperature` 0.
17. `/update`: groups by project, skips empty projects, shows "Next" with at most 5 per project, and says so plainly when nothing was done.
18. Catch-up: a startup shortly after the check-in time sends it once. A startup the next morning does not.

### `tests/eval_llm.py` + `tests/eval_cases.json` (manual, real providers)
About 20 realistic English cases, each with a fixture (projects, open tasks), a message, and the expected result. Run:
`python -m tests.eval_llm --provider groq` (or `ollama`). It prints pass/fail per case and a score per category.
Cover: project resolution, source detection, multi-task splitting, relative date words, single done-match, ambiguous done-match (must be low confidence),
edits, each query kind, messy or typo-filled phrasing, and chit-chat that must create no task. Do not tune the cases to make a model pass.
The point is to let the user compare Groq and local Qwen and choose the default order. Report the scores.

---

## 11. Build phases

### Phase 1: core (build this now)
Everything in sections 6 to 10 except the parts marked Phase 2 and Phase 3, plus `ledger/doctor.py`:
`python -m ledger.doctor` prints OK/FAIL with a fix hint for: required env vars, the Telegram token (`getMe`), each provider in `LLM_PROVIDERS`
(a tiny structured call; for Groq also whether forced tool calls work; for Ollama whether the model exists and whether it reports a context size), and the database.
It never prints secrets.

**Done when** all tests pass, `python -m ledger.doctor` is green, `python -m ledger` runs with a real `.env`, and this works in Telegram:
1. Seed three projects.
2. Send `need to update the textbar on the landing page`: saved without questions, with Undo.
3. Send the same thing again: the duplicate card appears.
4. Send `textbar done`: marked done, Undo works.
5. Send `what's pending?` and `what should I do first?`: answers match the data.
6. `/checkin`: tap a task, Confirm: marked done.
7. `/update`: a clean paste-ready update appears.

Then run the eval for each configured provider and **report the scores**. Stop and report: what works, what you simplified, and what is untested against live services (be explicit).

### Phase 2: local git evidence (only after the user approves Phase 1)
Read commits from repos on the machine running the bot. **Read-only. No network. No GitHub API, tokens or webhooks.**
- `/setrepo Project path` stores a local path in `projects.repo_paths` (validate that it is a git repo; allow several per project).
- `gitlog.py`: run `git -C <path> log --since=<local midnight> --author=<email> --pretty=... --name-only` through `subprocess` (arguments as a list, never `shell=True`).
  Authors come from `GIT_AUTHOR_EMAILS`, or the repo's own `git config user.email` when empty. Skip merge commits. Store commits in `commits` (unique on repo and sha).
  Ignore lock files and minified or generated files in the file list. Never read file contents or diffs.
- `evidence.py`: a commit message tag like `#12` or `[T-12]` gives a `high` suggestion. For the rest, make **one batched LLM call per check-in** with all of today's
  commits (subject + file paths) and the shortlist of open tasks for that project, returning `{commit_sha, task_id, confidence}` pairs. Only ids from the shortlist are accepted.
  If the call fails, show the commits without suggestions.
- Check-in card: add a "Today's commits" block (short sha + subject, grouped by project). Tasks with a suggested commit get a 💡 and the commit's subject under the label.
  **Nothing is pre-selected.** When the user confirms a task that has suggested commits, store them in `task_commits` with `confirmed = true`.
- Commits that match nothing are listed as "Unmatched commits". They are information only in v1.
- The duplicate card (6.2) and `/update` (6.7) show proof and commits as described there.
- Tests (`test_git.py`): create temp repos with `git init` and commits made by a chosen author, then check parsing, author filtering, lookback, ignored files, tag matching,
  the batched call with FakeLLM, confirm-attaches-proof, and that **no task is ever closed by a commit alone**.
- Privacy: commit subjects and file paths go to the LLM provider. Only projects with `repo_paths` are read. State this in the README.

### Phase 3: Notion checklist (only after the user approves Phase 2)
The bot's database stays the source of truth. Notion is a mirror the user can tick on phone or laptop. A tick counts as the user saying "done".
- **Setup (README):** create an internal integration in Notion and copy its token into `NOTION_TOKEN`. Create an empty page, share it with the integration.
  Run `python -m ledger.notion_setup <page_id>`, which creates the database and prints `NOTION_DATABASE_ID`. The user then creates a table view
  filtered to "Done is unchecked" and grouped by Project (the API may not create views; document it).
- **API:** send `Notion-Version: 2025-09-03`. Pages are created under a `data_source_id` and queried through data sources, not `database_id`.
  Discover the data source id once with `GET /v1/databases/{id}`. Read the current docs before coding. Use `httpx`. Respect the rate limit
  (about 3 requests per second): small delays, retry on 429 using `Retry-After`.
- **Properties:** Name (title), Done (checkbox), Project (select), Type (select), Source (text), Deadline (date), Priority (select), Ledger ID (number).
- **Sync loop (`sync.py`, every 60 seconds through the JobQueue, plus `/sync`):**
  1. *Push:* create a page for every task with no `notion_page_id`. Update pages for tasks where `updated_at > notion_synced_at`
     (title, project, deadline, priority, Done). If a save is undone (the task is deleted), archive its page.
  2. *Pull:* query pages with `last_edited_time` on or after the stored cursor minus 2 minutes (cursor in `meta`). For each page matching a task by `notion_page_id`:
     if Done is ticked and the task is open, mark it done and write an `events` row "ticked in Notion", then send the user a one-line Telegram message;
     if Done is unticked and the task is done, reopen it. Comparing the Done state keeps the loop idempotent, so there are no echo loops.
     Edits to other fields in Notion are ignored in v1. Rows created by hand in Notion are ignored.
  3. If Notion says a page is archived or gone, set the task to `dropped` and stop syncing it.
  4. Notion errors are logged and retried next cycle. **They never block or break a Telegram reply.**
  5. After a restart the cursor makes the sync catch up on its own.
- Tests (`test_notion.py`, `httpx.MockTransport` as fake Notion): push creates a page, a tick pulls through and marks done, an untick reopens, an archived page drops the task,
  a 429 is retried, a Notion outage does not break `handle_text`, and the second sync with no changes does nothing.

### Phase 4: not now (do not build)
Team mode and multiple users, a hosted version, GitHub API or webhooks, LLM-polished updates, voice notes, a web dashboard, WhatsApp, embeddings, billing.

---

## 12. Running it
The bot runs on the user's own computer (`python -m ledger`). It is offline when the computer sleeps. Do not rely on Telegram holding messages for long
(check its docs for how long updates are kept); rely on the catch-up in section 8 and the Notion cursor in Phase 3. Local git and a local Ollama model both require this setup.

## 13. Privacy (put this in the README)
Task text goes to the first working provider in `LLM_PROVIDERS`. In Phase 2, commit subjects and file paths of connected repos go there too.
Never type customer data, passwords or keys into the bot. For full privacy use only `ollama` in `LLM_PROVIDERS`.
Check a cloud provider's data policy before sending real work data to it. Free tiers of some providers use prompts to improve their products, so read the terms.
Secrets live only in `.env`.

## 14. Prior art (ideas only, do not copy code; no licence was visible on the first two)
- `leverty93/notion-tasks-bot`: Telegram + Notion + LLM free-text parsing, status buttons, an ordered fallback list of models for free-tier limits, catch-up after restarts.
- `mymvsic228/notion-tasks-bot`: polls Notion every 60 seconds, buttons change status back in Notion, a connection self-test script.
- `muba00/gitstandup` (MIT): reads local git repos filtered by author email and a lookback window, skips generated files.
- Hermes Agent (MIT): a general agent runtime with Telegram, memory and cron. Not used here.
What none of them does, and this project must: check new requests against done history with proof, and run an end-of-day confirm-and-attach-proof check-in.
