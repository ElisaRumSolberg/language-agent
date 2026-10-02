"""Turns lesson objects into short Telegram messages (HTML parse mode).

HTML is used instead of legacy Markdown because lesson text is full of
underscores (cloze blanks: 'Jeg ___ ikke'), which break Telegram Markdown.
Only <b>, <i> and <tg-spoiler> are used; all dynamic text is escaped.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from typing import Protocol

from src.schemas import Exercise, LessonContent

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
