# Proofline: Setup Guide

**Proofline** is a private, personal work ledger operated through a Telegram bot. It captures unstructured work requests in plain English, detects duplicates before saving, tracks daily tasks, and produces clean updates with proof.

---

## 1. Prerequisites

- **Python 3.11+** installed
- **Git**
- A **Telegram account**
- An LLM option:
  - **Cloud:** [Groq API Key](https://console.groq.com/) (Fast, free tier)
  - **Local:** [Ollama](https://ollama.com/) with `qwen2.5:7b` (100% private, runs entirely offline)

---

## 2. Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/sujaljadhav14/Proofline.git
   cd Proofline
   ```

2. **Create and activate a virtual environment:**
   - **Windows:**
     ```powershell
     python -m venv .venv
     .venv\Scripts\activate
     ```
   - **Linux / macOS:**
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     ```
   *(Alternatively, if you use `uv`: `uv venv` then `uv pip install -r requirements.txt`)*

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

---

## 3. Configuration (.env)

Copy the sample environment file:
```bash
cp .env.example .env     # Windows: copy .env.example .env
```

Open `.env` in your editor and configure the fields:

### Step 3.1: Telegram Setup
1. In Telegram, open **`@BotFather`**.
2. Send `/newbot`, choose a display name, and pick a unique username ending in `bot` (e.g. `my_work_ledger_bot`).
3. Copy the HTTP API token provided by BotFather and paste it into `TELEGRAM_BOT_TOKEN`:
   ```ini
   TELEGRAM_BOT_TOKEN=1234567890:ABCdefGhIJKlmNoPQRsTUVwxyZ
   ```
4. To get your personal Telegram numeric ID, open **`@userinfobot`** in Telegram, press Start, copy the `Id` value, and put it in `ALLOWED_USER_ID`:
   ```ini
   ALLOWED_USER_ID=123456789
   ```

### Step 3.2: LLM Configuration

#### Option A: Groq (Recommended for quick cloud setup)
1. Generate an API key at [console.groq.com](https://console.groq.com/).
2. Set:
   ```ini
   LLM_PROVIDERS=groq,ollama
   GROQ_API_KEY=your_groq_api_key_here
   GROQ_MODEL=qwen/qwen3.8-27b
   GROQ_JSON_MODE=true
   ```

#### Option B: Ollama (100% private, local)
1. Install [Ollama](https://ollama.com/) and run:
   ```bash
   ollama pull qwen2.5:7b
   ```
2. Set:
   ```ini
   LLM_PROVIDERS=ollama
   OLLAMA_URL=http://localhost:11434
   OLLAMA_MODEL=qwen2.5:7b
   ```

### Step 3.3: Seed Projects
Configure your initial project names and aliases separated by semicolons:
```ini
SEED_PROJECTS=WebApp=web,site;Mobile=app;Landing=landing page
```

---

## 4. Health Check & Validation

Run the built-in diagnostic doctor to verify all settings, API connectivity, and database tables:
```bash
python -m ledger.doctor
```
If all checks display `[ OK ]`, run the offline test suite:
```bash
pytest -q
```

---

## 5. Running Proofline

Start the bot:
```bash
python -m ledger
```

Open your bot in Telegram and start typing:
- `need to update the textbar on the landing page`
- `tester says discount not applying for 3+ seats in webapp`
- `textbar done`
- `whats pending?`
- `/checkin`
- `/update`
