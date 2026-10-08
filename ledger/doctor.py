import asyncio
import os
import sys
import httpx
from sqlalchemy import text

from ledger.config import get_settings
from ledger.db import get_engine, init_db
from ledger.llm import GroqLLM, OllamaLLM, AnthropicLLM


TINY_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string"},
    },
    "required": ["status"],
}


def mask_secret(secret: str) -> str:
    if not secret:
        return "(empty)"
    if len(secret) <= 6:
        return "***"
    return secret[:3] + "..." + secret[-3:]


async def check_env_vars(settings) -> bool:
    all_ok = True
    print("\n--- 1. Environment Variables ---")

    if not settings.TELEGRAM_BOT_TOKEN:
        print("[FAIL] TELEGRAM_BOT_TOKEN is empty.")
        print("       Fix: Get a token from @BotFather and set TELEGRAM_BOT_TOKEN in .env")
        all_ok = False
    else:
        print(f"[ OK ] TELEGRAM_BOT_TOKEN is set ({mask_secret(settings.TELEGRAM_BOT_TOKEN)})")

    if settings.ALLOWED_USER_ID == 0:
        print("[FAIL] ALLOWED_USER_ID is 0.")
        print("       Fix: Start the bot, send /myid, then put your id in ALLOWED_USER_ID in .env")
        all_ok = False
    else:
        print(f"[ OK ] ALLOWED_USER_ID is set ({settings.ALLOWED_USER_ID})")

    if not settings.LLM_PROVIDERS:
        print("[FAIL] LLM_PROVIDERS is empty.")
        print("       Fix: Set LLM_PROVIDERS in .env e.g. groq,ollama")
        all_ok = False
    else:
        print(f"[ OK ] LLM_PROVIDERS: {', '.join(settings.LLM_PROVIDERS)}")

    return all_ok


async def check_telegram_token(settings) -> bool:
    print("\n--- 2. Telegram Bot Token ---")
    if not settings.TELEGRAM_BOT_TOKEN:
        print("[SKIP] TELEGRAM_BOT_TOKEN is not set.")
        return False

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/getMe"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200 and resp.json().get("ok"):
                bot_user = resp.json()["result"]["username"]
                print(f"[ OK ] Telegram bot token is valid (@{bot_user})")
                return True
            else:
                print(f"[FAIL] Telegram API returned error: {resp.text}")
                print("       Fix: Check your TELEGRAM_BOT_TOKEN in .env")
                return False
    except Exception as e:
        print(f"[FAIL] Could not connect to Telegram API: {e}")
        print("       Fix: Check your internet connection or proxy settings.")
        return False


async def check_database(settings) -> bool:
    print("\n--- 3. Database ---")
    try:
        engine = get_engine(settings.DATABASE_URL)
        init_db(engine)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print(f"[ OK ] Database connected and tables created successfully ({settings.DATABASE_URL})")
        return True
    except Exception as e:
        print(f"[FAIL] Database check failed: {e}")
        print("       Fix: Check DATABASE_URL in .env and file system permissions.")
        return False


async def check_groq(settings) -> bool:
    print("\n--- Groq LLM Check ---")
    if not settings.GROQ_API_KEY:
        print("[FAIL] GROQ_API_KEY is empty.")
        print("       Fix: Set GROQ_API_KEY in .env")
        return False

    # Test forced tool calls
    groq_tools = GroqLLM(
        api_key=settings.GROQ_API_KEY,
        model=settings.GROQ_MODEL,
        json_mode=False,
    )
    tools_worked = False
    try:
        res = await groq_tools.structured(
            system="Respond with valid JSON.",
            user="Say status ok.",
            name="respond",
            schema=TINY_SCHEMA,
        )
        if res.get("status"):
            print(f"[ OK ] Groq ({settings.GROQ_MODEL}) forced tool calls work! (GROQ_JSON_MODE=false is good)")
            tools_worked = True
            return True
    except Exception as e:
        print(f"[INFO] Groq forced tool call failed: {e}")

    # Test JSON mode
    groq_json = GroqLLM(
        api_key=settings.GROQ_API_KEY,
        model=settings.GROQ_MODEL,
        json_mode=True,
    )
    try:
        res = await groq_json.structured(
            system="Respond with valid JSON.",
            user="Say status ok.",
            name="respond",
            schema=TINY_SCHEMA,
        )
        if res.get("status"):
            print(f"[ OK ] Groq JSON mode works.")
            if not settings.GROQ_JSON_MODE:
                print("       Recommendation: Set GROQ_JSON_MODE=true in .env because forced tool calls failed.")
            return True
    except Exception as e:
        print(f"[FAIL] Groq JSON mode failed: {e}")
        print("       Fix: Check your GROQ_API_KEY and model in .env.")
        return False

    return False


async def check_ollama(settings) -> bool:
    print("\n--- Ollama LLM Check ---")
    base_url = settings.OLLAMA_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/api/tags")
            if resp.status_code != 200:
                print(f"[FAIL] Ollama tags check returned HTTP {resp.status_code}")
                return False
            data = resp.json()
            models = [m.get("name") for m in data.get("models", [])]
    except Exception as e:
        print(f"[FAIL] Could not connect to Ollama at {base_url}: {e}")
        print("       Fix: Ensure Ollama is running (`ollama serve`).")
        return False

    model_name = settings.OLLAMA_MODEL
    # Match model name or tag
    matching = [m for m in models if m == model_name or m.startswith(f"{model_name}:")]
    if not matching:
        print(f"[FAIL] Model '{model_name}' not found in Ollama.")
        print(f"       Installed models: {', '.join(models) if models else '(none)'}")
        print(f"       Fix: Run `ollama pull qwen2.5:7b` or create variant `{model_name}` (see BUILD_BRIEF.md section 3).")
        return False
    else:
        print(f"[ OK ] Found model '{model_name}' in Ollama.")

    # Check context size in show info
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            show_resp = await client.post(f"{base_url}/api/show", json={"name": model_name})
            if show_resp.status_code == 200:
                params = show_resp.json().get("parameters", "")
                if "num_ctx" in params:
                    print(f"[ OK ] Model parameters report context: {params.strip()}")
                else:
                    print("[INFO] num_ctx not explicitly set in Modelfile. (Consider setting num_ctx 8192).")
    except Exception:
        pass

    # Tiny structured call
    ollama_llm = OllamaLLM(url=settings.OLLAMA_URL, model=settings.OLLAMA_MODEL)
    try:
        res = await ollama_llm.structured(
            system="Respond with valid JSON.",
            user="Say status ok.",
            name="respond",
            schema=TINY_SCHEMA,
        )
        if res.get("status"):
            print(f"[ OK ] Ollama structured call successful: {res}")
            return True
        else:
            print(f"[FAIL] Ollama response missing required keys: {res}")
            return False
    except Exception as e:
        print(f"[FAIL] Ollama structured call failed: {e}")
        return False


async def check_anthropic(settings) -> bool:
    print("\n--- Anthropic LLM Check ---")
    if not settings.ANTHROPIC_API_KEY:
        print("[FAIL] ANTHROPIC_API_KEY is empty.")
        print("       Fix: Set ANTHROPIC_API_KEY in .env")
        return False
    anthropic_llm = AnthropicLLM(api_key=settings.ANTHROPIC_API_KEY, model=settings.ANTHROPIC_MODEL)
    try:
        res = await anthropic_llm.structured(
            system="Respond with valid JSON.",
            user="Say status ok.",
            name="respond",
            schema=TINY_SCHEMA,
        )
        if res.get("status"):
            print(f"[ OK ] Anthropic structured call successful: {res}")
            return True
        return False
    except Exception as e:
        print(f"[FAIL] Anthropic check failed: {e}")
        return False


async def run_doctor() -> None:
    print("========================================")
    print("       Work Ledger System Doctor        ")
    print("========================================")

    settings = get_settings()
    results = {}

    results["env"] = await check_env_vars(settings)
    results["db"] = await check_database(settings)
    results["telegram"] = await check_telegram_token(settings)

    for p in settings.LLM_PROVIDERS:
        name = p.lower().strip()
        if name == "groq":
            results["groq"] = await check_groq(settings)
        elif name == "ollama":
            results["ollama"] = await check_ollama(settings)
        elif name == "anthropic":
            results["anthropic"] = await check_anthropic(settings)

    print("\n========================================")
    print("                Summary                 ")
    print("========================================")
    all_passed = True
    for k, v in results.items():
        status = "OK" if v else "FAIL"
        if not v:
            all_passed = False
        print(f"  {k:12}: {status}")

    if all_passed:
        print("\nAll checks passed! Your Work Ledger is ready to run.")
        sys.exit(0)
    else:
        print("\nSome checks failed. See the fix suggestions above.")
        # Non-zero exit code indicates issues found
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_doctor())
