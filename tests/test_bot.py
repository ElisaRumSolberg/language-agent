"""Telegram wiring: message splitting, handler registration, daily job (offline)."""

from datetime import time
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram.ext import CommandHandler

from src.bot import build_application, split_message
from src.config import Config


def config(chat_id):
    return Config(telegram_bot_token="123456:TEST-token", telegram_chat_id=chat_id,
                  anthropic_api_key="", anthropic_model="m", anthropic_grader_model="g",
                  daily_lesson_time=time(7, 30), timezone=ZoneInfo("Europe/Oslo"),
                  start_level="A2", db_path=Path(":memory:"))


def commands(app) -> set[str]:
    return {c for h in app.handlers[0] if isinstance(h, CommandHandler) for c in h.commands}


def test_split_message_respects_limit_and_paragraphs():
    text = "\n\n".join(["a" * 30] * 5)
    parts = split_message(text, limit=70)
    assert all(len(p) <= 70 for p in parts)
    assert "\n\n".join(parts) == text


def test_split_message_cuts_overlong_paragraph():
    parts = split_message("x" * 250, limit=100)
    assert [len(p) for p in parts] == [100, 100, 50]


def test_all_commands_registered_and_daily_job_scheduled():
    app = build_application(SimpleNamespace(), config(chat_id=42))
    assert commands(app) == {"start", "lesson", "busy", "review", "skip", "mistakes", "stats"}
    job = app.job_queue.get_jobs_by_name("daily_lesson")[0]
    assert job.chat_id == 42
    trigger = job.job.trigger
    assert "hour='7'" in str(trigger) and "minute='30'" in str(trigger)
    assert str(trigger.timezone) == "Europe/Oslo"


def test_without_chat_id_only_start_answers():
    app = build_application(SimpleNamespace(), config(chat_id=None))
    assert commands(app) == {"start"}
    assert app.job_queue.get_jobs_by_name("daily_lesson") == ()
