"""Application configuration loaded from environment variables (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    """Typed, immutable view of all settings the app needs."""

    telegram_bot_token: str
    telegram_chat_id: int | None
    anthropic_api_key: str
    anthropic_model: str
    anthropic_grader_model: str
    daily_lesson_time: time
    timezone: ZoneInfo
    start_level: str
    db_path: Path


def _parse_time(value: str) -> time:
    """Parse 'HH:MM' into a datetime.time."""
    hour, minute = value.strip().split(":")
    return time(hour=int(hour), minute=int(minute))


def load_config() -> Config:
    """Read .env (if present) and environment variables into a Config.

    Missing secrets are allowed here (empty strings) so that offline parts
    such as the dry-run and tests work without Telegram/Anthropic keys.
    Components that need a secret validate it when they start.
    """
    load_dotenv(PROJECT_ROOT / ".env")

    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    db_path = Path(os.getenv("DB_PATH", "data/agent.db"))
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    return Config(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=int(chat_id) if chat_id else None,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        anthropic_grader_model=os.getenv(
            "ANTHROPIC_GRADER_MODEL", "claude-haiku-4-5-20251001"
        ),
        daily_lesson_time=_parse_time(os.getenv("DAILY_LESSON_TIME", "07:30")),
        timezone=ZoneInfo(os.getenv("TIMEZONE", "Europe/Oslo")),
        start_level=os.getenv("START_LEVEL", "A2"),
        db_path=db_path,
    )
