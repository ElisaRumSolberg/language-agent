"""Telegram handlers and the daily job. Thin: every decision lives in Tutor.

Blocking Tutor calls run in a worker thread (asyncio.to_thread) behind one
lock, so the event loop stays responsive and SQLite is never used concurrently.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from typing import TypeVar

from telegram import Bot, BotCommand, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.config import Config
from src.formatting import (
    exercise_message,
    feedback_message,
    lesson_messages,
    mistakes_message,
    session_summary,
    stats_message,
)
from src.lesson import GeneratedLesson
from src.llm import LLMError
from src.session import Session
from src.tutor import Tutor

log = logging.getLogger(__name__)

T = TypeVar("T")
MAX_MESSAGE = 4000  # Telegram limit is 4096; keep a margin

COMMANDS = [
    BotCommand("lesson", "Bugünün dersi (tam)"),
    BotCommand("busy", "Bugünün dersi (15 dk)"),
    BotCommand("review", "Sadece tekrarlar"),
    BotCommand("skip", "Bu soruyu geç"),
    BotCommand("mistakes", "Açık hatalar"),
    BotCommand("stats", "İlerleme"),
]

WELCOME = (
    "Hei! 🇳🇴 Ben senin Norveççe koçunum.\n\n"
    "Her sabah kısa bir ders gönderirim: dünkü ve geçen haftaki kelimeleri tekrar ederiz, "
    "yeni kelimeler öğreniriz, cevaplarını Türkçe açıklamalarla düzeltirim.\n\n"
    "/lesson — bugünün dersi\n/busy — yoğun gün (15 dk)\n/review — sadece tekrar\n"
    "/mistakes — hatalarım\n/stats — ilerleme\n/skip — soruyu geç"
)
LLM_FAILED = "Şu an bağlantıda bir sorun var, birazdan tekrar dener misin? 🙏"
NO_SESSION = "Şu an aktif bir alıştırma yok. /lesson, /busy veya /review yazabilirsin."


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split on paragraph boundaries so no message exceeds Telegram's limit."""
    parts, current = [], ""
    for para in text.split("\n\n"):
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        while len(para) > limit:
            parts.append(para[:limit])
            para = para[limit:]
        current = para
    if current:
        parts.append(current)
    return parts


class TutorBot:
    """Wires Tutor use cases to Telegram updates."""

    def __init__(self, tutor: Tutor, cfg: Config) -> None:
        self.tutor = tutor
        self.cfg = cfg
        self.lock = asyncio.Lock()

    # ------------------------------------------------------------ helpers

    async def run(self, fn: Callable[..., T], *args) -> T:
        """Run a blocking Tutor call in a thread, one at a time."""
        async with self.lock:
            return await asyncio.to_thread(fn, *args)

    @contextlib.asynccontextmanager
    async def typing(self, bot: Bot, chat_id: int) -> AsyncIterator[None]:
        """Show 'typing…' while slow work (LLM) runs."""
        async def keep_typing() -> None:
            while True:
                with contextlib.suppress(Exception):
                    await bot.send_chat_action(chat_id, ChatAction.TYPING)
                await asyncio.sleep(4)

        task = asyncio.create_task(keep_typing())
        try:
            yield
        finally:
            task.cancel()

    @staticmethod
    async def send(bot: Bot, chat_id: int, text: str) -> None:
        for part in split_message(text):
            await bot.send_message(chat_id, part, parse_mode=ParseMode.HTML)

    async def send_current_exercise(self, bot: Bot, chat_id: int, session: Session) -> None:
        if session.current is not None:
            await self.send(bot, chat_id, exercise_message(
                session.current, session.index + 1, len(session.exercises)))

    async def deliver_lesson(self, bot: Bot, chat_id: int, mode: str) -> None:
        """Generate/reuse today's lesson, send it and the first open exercise."""
        try:
            async with self.typing(bot, chat_id):
                lesson, session = await self.run(self.tutor.start_lesson, mode)
        except LLMError:
            log.exception("lesson generation failed")
            await self.send(bot, chat_id, LLM_FAILED)
            return
        if lesson.mode != mode:
            await self.send(bot, chat_id, "Bugünün dersi zaten hazırlanmış, onunla devam ediyoruz.")
        await self.send_lesson(bot, chat_id, lesson, session)

    async def send_lesson(self, bot: Bot, chat_id: int, lesson: GeneratedLesson,
                          session: Session) -> None:
        if session.index == 0:
            for msg in lesson_messages(lesson.content, lesson.new_items):
                await self.send(bot, chat_id, msg)
            await self.send(bot, chat_id, "✍️ Şimdi alıştırmalar. Her soruya cevabını yaz.")
        else:
            await self.send(bot, chat_id, "Kaldığın yerden devam ediyoruz 👇")
        await self.send_current_exercise(bot, chat_id, session)

    # ------------------------------------------------------------ commands

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.send(context.bot, update.effective_chat.id, WELCOME)

    async def lesson_full(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.deliver_lesson(context.bot, update.effective_chat.id, "full")

    async def lesson_busy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.deliver_lesson(context.bot, update.effective_chat.id, "busy")

    async def review(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        try:
            async with self.typing(context.bot, chat_id):
                session = await self.run(self.tutor.start_review)
        except LLMError:
            log.exception("review generation failed")
            await self.send(context.bot, chat_id, LLM_FAILED)
            return
        if session is None:
            await self.send(context.bot, chat_id, "Bugün zamanı gelen tekrar yok 🎉")
            return
        await self.send(context.bot, chat_id, f"🔄 {len(session.exercises)} tekrar sorusu.")
        await self.send_current_exercise(context.bot, chat_id, session)

    async def skip(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        session = await self.run(self.tutor.skip)
        if session is None:
            await self.send(context.bot, chat_id, NO_SESSION)
        elif session.finished:
            await self.send(context.bot, chat_id, session_summary(session))
        else:
            await self.send_current_exercise(context.bot, chat_id, session)

    async def mistakes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        items = await self.run(self.tutor.open_mistakes)
        await self.send(context.bot, update.effective_chat.id, mistakes_message(items))

    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        s = await self.run(self.tutor.stats)
        await self.send(context.bot, update.effective_chat.id, stats_message(s))

    # ------------------------------------------------------------ answers

    async def answer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        text = (update.message.text or "").strip()
        if not text:
            return
        try:
            async with self.typing(context.bot, chat_id):
                result = await self.run(self.tutor.answer, text)
        except LLMError:
            log.exception("grading failed")
            await self.send(context.bot, chat_id,
                            "Cevabını şu an değerlendiremedim 🙏 Aynı cevabı tekrar gönderir misin?")
            return
        if result is None:
            await self.send(context.bot, chat_id, NO_SESSION)
            return
        await self.send(context.bot, chat_id, feedback_message(result.outcome, text))
        if result.session.finished:
            await self.send(context.bot, chat_id, session_summary(result.session))
        else:
            await self.send_current_exercise(context.bot, chat_id, result.session)

    # ------------------------------------------------------------ daily job

    async def daily_lesson(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Scheduled job: send today's full lesson."""
        await self.deliver_lesson(context.bot, context.job.chat_id, "full")


async def _unknown_chat_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Before TELEGRAM_CHAT_ID is set: tell the user their chat id."""
    await update.message.reply_text(
        f"Chat ID'n: {update.effective_chat.id}\n"
        "Bunu .env dosyasına TELEGRAM_CHAT_ID olarak yaz ve botu yeniden başlat."
    )


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("unhandled error", exc_info=context.error)


def build_application(tutor: Tutor, cfg: Config) -> Application:
    """Create the Telegram application with handlers and the daily job."""
    app = Application.builder().token(cfg.telegram_bot_token).build()
    app.add_error_handler(_error_handler)

    if cfg.telegram_chat_id is None:
        log.warning("TELEGRAM_CHAT_ID not set: only /start answers (with the chat id).")
        app.add_handler(CommandHandler("start", _unknown_chat_start))
        return app

    bot = TutorBot(tutor, cfg)
    only_me = filters.Chat(chat_id=cfg.telegram_chat_id)
    for name, callback in [
        ("start", bot.start), ("lesson", bot.lesson_full), ("busy", bot.lesson_busy),
        ("review", bot.review), ("skip", bot.skip), ("mistakes", bot.mistakes),
        ("stats", bot.stats),
    ]:
        app.add_handler(CommandHandler(name, callback, filters=only_me))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & only_me, bot.answer))

    schedule_daily_lesson(app, bot, cfg)

    async def post_init(application: Application) -> None:
        await application.bot.set_my_commands(COMMANDS)

    app.post_init = post_init
    return app


def schedule_daily_lesson(app: Application, bot: TutorBot, cfg: Config) -> None:
    """Send the full lesson every day at DAILY_LESSON_TIME (in TIMEZONE)."""
    at = cfg.daily_lesson_time.replace(tzinfo=cfg.timezone)
    app.job_queue.run_daily(bot.daily_lesson, time=at, chat_id=cfg.telegram_chat_id,
                            name="daily_lesson")
    log.info("daily lesson scheduled at %s %s", cfg.daily_lesson_time, cfg.timezone)
