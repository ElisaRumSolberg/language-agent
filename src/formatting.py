"""Turns lesson objects into short Telegram messages (HTML parse mode).

HTML is used instead of legacy Markdown because lesson text is full of
underscores (cloze blanks: 'Jeg ___ ikke'), which break Telegram Markdown.
Only <b>, <i> and <tg-spoiler> are used; all dynamic text is escaped.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from src.schemas import Exercise, LessonContent

if TYPE_CHECKING:
    from src.db import Mistake
    from src.grader import GradeOutcome
    from src.session import Session
    from src.stats import Stats

QUESTION_LABELS_TR: dict[str, str] = {
    "recognition": "Anlamı ne?",
    "cloze": "Boşluğu doldur",
    "translation": "Norveççeye çevir",
    "own_sentence": "Kendi cümleni yaz",
    "contextual": "Soruyu cevapla",
    "hidden_use": "Serbest yazma",
}


class Teachable(Protocol):
    """Fields shared by db.Item and schemas.NewItem."""

    target: str
    meaning_tr: str
    forms: str | None
    example: str | None
    memory_hook: str | None


def esc(text: str | None) -> str:
    """Escape dynamic text for Telegram HTML."""
    return html.escape(text or "", quote=False)


def to_plain_text(message: str) -> str:
    """Strip our HTML tags for terminal output."""
    return html.unescape(re.sub(r"</?(b|i|tg-spoiler)>", "", message))


def _item_block(item: Teachable) -> str:
    lines = [f"<b>{esc(item.target)}</b> — {esc(item.meaning_tr)}"]
    if item.forms:
        lines.append(f"<i>{esc(item.forms)}</i>")
    if item.example:
        lines.append(f"🇳🇴 {esc(item.example)}")
    if item.memory_hook:
        lines.append(f"💡 {esc(item.memory_hook)}")
    return "\n".join(lines)


def lesson_messages(content: LessonContent, new_items: Sequence[Teachable]) -> list[str]:
    """The teaching part of the lesson, split into short messages."""
    messages = [f"🇳🇴 <b>{esc(content.topic)}</b>\n\n{esc(content.intro_tr)}"]

    if new_items:
        blocks = "\n\n".join(_item_block(i) for i in new_items)
        messages.append(f"<b>Yeni kelimeler</b>\n\n{blocks}")

    if content.mistake_notes_tr:
        notes = "\n".join(f"• {esc(n)}" for n in content.mistake_notes_tr)
        messages.append(f"<b>Tekrarlayan hatalar</b>\n\n{notes}")

    g = content.grammar
    messages.append(
        f"<b>Gramer: {esc(g.title)}</b>\n\n{esc(g.logic_tr)}\n\n"
        f"✅ {esc(g.correct_example)}\n❌ {esc(g.wrong_example)}\n\n{esc(g.why_tr)}"
    )

    r = content.reading
    reading = f"<b>Okuma</b>\n\n{esc(r.text)}\n\n<tg-spoiler>{esc(r.translation_tr)}</tg-spoiler>"
    if r.bergensk_note:
        reading += f"\n\n💡 <i>Bergensk:</i> {esc(r.bergensk_note)}"
    messages.append(reading)
    return messages


def exercise_message(ex: Exercise, number: int, total: int) -> str:
    """One exercise as a Telegram message."""
    label = QUESTION_LABELS_TR.get(ex.question_type, ex.question_type)
    return (f"<b>{number}/{total} · {label}</b>\n"
            f"<i>{esc(ex.instruction_tr)}</i>\n\n{esc(ex.prompt)}")


def _days_text(days: int) -> str:
    return "yarın" if days == 1 else f"{days} gün sonra"


def feedback_message(outcome: GradeOutcome, answer: str) -> str:
    """Verdict, correction, explanation and what happens next for the item."""
    g = outcome.grade
    lines = ["✅ <b>Doğru!</b>" if g.correct else "❌ <b>Tam değil</b>"]
    if g.corrected.strip() != answer.strip():
        lines.append(f"Doğrusu: <b>{esc(g.corrected)}</b>")
    lines.append(esc(g.explanation_tr))

    extra = []
    if outcome.item is not None and outcome.new_state is not None:
        extra.append(f"📅 <i>{esc(outcome.item.target)}</i> tekrar: "
                     f"{_days_text(outcome.new_state.interval_days)}")
    if outcome.mistake is not None and outcome.mistake.recurring:
        extra.append(f"⚠️ <i>{esc(outcome.mistake.topic)}</i> hatası {outcome.mistake.count}. kez — "
                     "her derste çalışacağız.")
    for topic in outcome.resolved_topics:
        extra.append(f"🎉 <i>{esc(topic)}</i> hatası çözüldü (3 kez üst üste doğru)!")
    if extra:
        lines.append("\n".join(extra))
    return "\n\n".join(lines)


def session_summary(session: Session) -> str:
    """End-of-session message."""
    if not session.answered:
        return "Bitti. Bu sefer cevap yoktu — yarın görüşürüz 👋"
    pct = round(100 * (session.score or 0))
    return (f"🏁 <b>Bitti!</b> {session.correct}/{session.answered} doğru ({pct}%).\n"
            "Yanlışlar yarın tekrar karşına çıkacak. Görüşürüz 👋")


def mistakes_message(mistakes: Sequence[Mistake]) -> str:
    """Open mistakes, recurring first (already sorted by the query)."""
    if not mistakes:
        return "Açık hata yok 🎉"
    lines = ["<b>Açık hatalar</b>"]
    for m in mistakes:
        flag = "🔁 " if m.recurring else ""
        lines.append(
            f"\n{flag}<b>{esc(m.topic)}</b> ({m.category}, {m.count}×, seri {m.correct_streak}/3)\n"
            f"❌ {esc(m.wrong)}\n✅ {esc(m.correct)}\n💡 {esc(m.explanation_tr)}"
        )
    return "\n".join(lines)


def stats_message(s: Stats) -> str:
    """Progress overview."""
    stage_line = " · ".join(f"S{stage}: {n}" for stage, n in sorted(s.items_per_stage.items()))
    return (
        "<b>İlerleme</b>\n\n"
        f"🔥 Seri: {s.streak_days} gün\n"
        f"📚 Seviye: {esc(s.level)}\n"
        f"🔄 Bugün tekrar: {s.due_today}\n"
        f"🧠 Aşamalar: {stage_line or '—'}\n"
        f"🆕 Havuzda bekleyen: {s.new_in_pool}\n"
        f"⚠️ Açık hata: {s.open_mistakes} (tekrarlayan: {s.recurring_mistakes})"
    )
