import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

# Load .env file automatically
load_dotenv()


@dataclass
class Settings:
    TELEGRAM_BOT_TOKEN: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    ALLOWED_USER_ID: int = field(default_factory=lambda: int(os.getenv("ALLOWED_USER_ID", "0") or "0"))
    DATABASE_URL: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///ledger.db"))
    LEDGER_TZ: str = field(default_factory=lambda: os.getenv("LEDGER_TZ", "UTC"))
    CHECKIN_TIME: str = field(default_factory=lambda: os.getenv("CHECKIN_TIME", "19:00"))
    MORNING_NUDGE_TIME: str = field(default_factory=lambda: os.getenv("MORNING_NUDGE_TIME", "09:30"))
    LLM_PROVIDERS: list[str] = field(
        default_factory=lambda: [
            p.strip()
            for p in os.getenv("LLM_PROVIDERS", "groq,ollama").split(",")
            if p.strip()
        ]
    )
    GROQ_API_KEY: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""))
    GROQ_MODEL: str = field(default_factory=lambda: os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"))
    GROQ_JSON_MODE: bool = field(
        default_factory=lambda: os.getenv("GROQ_JSON_MODE", "false").strip().lower() in ("true", "1", "yes")
    )
    OLLAMA_URL: str = field(default_factory=lambda: os.getenv("OLLAMA_URL", "http://localhost:11434"))
    OLLAMA_MODEL: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen2.5-ledger"))
    ANTHROPIC_API_KEY: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    ANTHROPIC_MODEL: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"))
    SEED_PROJECTS: str = field(
        default_factory=lambda: os.getenv(
            "SEED_PROJECTS", "WebApp=web,site;Mobile=app;Landing=landing page"
        )
    )
    GIT_AUTHOR_EMAILS: str = field(default_factory=lambda: os.getenv("GIT_AUTHOR_EMAILS", ""))
    NOTION_TOKEN: str = field(default_factory=lambda: os.getenv("NOTION_TOKEN", ""))
    NOTION_DATABASE_ID: str = field(default_factory=lambda: os.getenv("NOTION_DATABASE_ID", ""))


def get_settings() -> Settings:
    return Settings()
