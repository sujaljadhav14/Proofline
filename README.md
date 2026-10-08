# Proofline

**Proofline** is a private, personal work ledger operated through a Telegram bot. 

It solves a universal developer problem: requests arrive scattered across chats, meetings, and tickets. Proofline captures them in plain English, warns before saving if work was already done, runs an end-of-day interactive check-in, and builds clean updates—with proof.

---

## Features

- 💬 **Free-form Capture:** Type requests in plain English; Proofline extracts task, project, priority, deadline, and source.
- 🔍 **Duplicate Prevention:** Checks against completed and open history before saving; lets you reopen or skip with one tap.
- 📋 **Interactive Daily Check-in:** A daily card lets you toggle finished tasks and confirm in seconds.
- ⏰ **Sleep Catch-up:** Automatically catches up if your laptop was asleep during check-in time.
- 📊 **Zero-LLM Updates:** `/update` builds instant, paste-ready daily or weekly summaries directly from your database.
- 🔒 **Private by Default:** Everything runs locally on your machine with SQLite and optional local Ollama.

---

## Core Principles

1. **User is the Authority on Status:** The database is the source of truth. Nothing is ever auto-closed from an LLM guess.
2. **Never Lose or Invent Data:** Python resolves dates and validates all entity IDs.
3. **Instant Undo:** Every created task or marked-done action comes with an instant undo button.
4. **Offline First:** Daily summaries and status queries run strictly against your local database without requiring external API calls.

---

## Quick Start

For detailed step-by-step instructions, see **[SETUP.md](file:///d:/Project/random%20project%20dumps/work-ledger-spec/SETUP.md)**.

```bash
# 1. Clone & activate virtual environment
git clone https://github.com/sujaljadhav14/Proofline.git
cd Proofline
python -m venv .venv
.venv\Scripts\activate   # Linux/macOS: source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your TELEGRAM_BOT_TOKEN and GROQ_API_KEY (or Ollama)

# 4. Run system doctor & test suite
python -m ledger.doctor
pytest -q

# 5. Start bot
python -m ledger
```

---

## Telegram Usage

Talk to the bot naturally:
- `need to update the textbar on the landing page`
- `tester says discount not applying for 3+ seats in webapp`
- `boss wants export button in webapp by Friday`
- `textbar done`
- `whats pending?`
- `what should I do first?`

### Available Commands
- `/start` or `/help` — Overview and examples.
- `/myid` — Replies with your Telegram ID.
- `/projects` — List projects and aliases.
- `/addproject Name = alias1, alias2` — Add a project.
- `/pending [project]` — List active open tasks.
- `/first [project]` — Top 3 tasks to focus on.
- `/today` — Tasks completed today.
- `/checkin` — Trigger the check-in card now.
- `/update [week]` — Generate plain-text update.

---

## Roadmap

- [x] **Phase 1: Core Work Ledger** — Natural language capture, duplicate warnings, end-of-day check-in, offline update generator.
- [ ] **Phase 2: Local Git Evidence** — Inspect local repositories, suggest matching commits for finished tasks, and attach commit SHAs as proof.
- [ ] **Phase 3: Notion Sync** — Two-way sync to a Notion checklist database for seamless ticking across devices.

---

## Privacy

- All tasks and records reside in your local SQLite database (`ledger.db`).
- Task messages are sent only to your configured provider in `LLM_PROVIDERS`.
- For 100% offline privacy, set `LLM_PROVIDERS=ollama`.
- API keys and tokens reside only in `.env` (git-ignored).

---

## License

MIT License
