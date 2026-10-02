"""Pydantic models for everything the LLM returns.

The LLM is only trusted after its JSON validates against these models.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ItemKind = Literal["word", "chunk", "grammar"]
QuestionType = Literal[
    "recognition", "cloze", "translation", "own_sentence", "contextual", "hidden_use"
]
MistakeCategory = Literal["G", "V", "WO", "SP", "N", "R"]


class NewItem(BaseModel):
    """A word/chunk the LLM proposes to add to the learner's item pool."""

    kind: ItemKind
    target: str = Field(description="Bokmål form as it should be learned, e.g. 'å rekke', 'et vindu'.")
    meaning_tr: str
    forms: str | None = Field(default=None, description="Inflection, e.g. 'rekker – rakk – har rukket'.")
    example: str = Field(description="One natural A2-B1 example sentence.")
    memory_hook: str = Field(description="Turkish memory hook; mention an English cognate if one exists.")


class GrammarPoint(BaseModel):
    """One small grammar point, explained through its logic."""

    title: str
    logic_tr: str = Field(description="Why the sentence works this way, in Turkish.")
    correct_example: str = Field(description="✅ sentence")
    wrong_example: str = Field(description="❌ typical learner error")
    why_tr: str = Field(description="Short Turkish explanation of the difference.")


class ReadingText(BaseModel):
    """A 3–5 sentence text that uses today's items."""

    text: str
    translation_tr: str
    bergensk_note: str | None = Field(
        default=None, description="Optional Bergen dialect note, only when genuinely useful."
    )


class Exercise(BaseModel):
    """A single question the learner answers in Telegram."""

    item_id: int | None = Field(default=None, description="Id of an existing item, if the exercise practices one.")
    target: str | None = Field(default=None, description="Target text of the practiced item (for items without id).")
    mistake_topic: str | None = Field(default=None, description="Open mistake topic this exercise drills, if any.")
    question_type: QuestionType
    instruction_tr: str = Field(description="What to do, in Turkish.")
    prompt: str = Field(description="The question itself as shown to the learner.")
    expected_answer: str = Field(description="A model answer; hidden from the learner, used by the grader.")


class LessonContent(BaseModel):
    """The full JSON the lesson prompt must return."""

    topic: str = Field(description="Short theme of the day, e.g. 'Morgenrutine'.")
    intro_tr: str = Field(description="1–2 sentence encouraging intro in Turkish.")
    new_items: list[NewItem] = Field(
        default_factory=list, description="Only the extra items you were asked to create."
    )
    mistake_notes_tr: list[str] = Field(
        default_factory=list, description="One short reminder per recurring mistake."
    )
    grammar: GrammarPoint
    reading: ReadingText
    exercises: list[Exercise]


class ReviewSet(BaseModel):
    """Exercises only, for the /review command."""

    exercises: list[Exercise]


class GradeResult(BaseModel):
    """Grader verdict for one answer."""

    item_id: int | None = None
    correct: bool = Field(description="Whole answer acceptable (no significant error).")
    item_used_correctly: bool | None = Field(
        default=None,
        description="Was the practised item itself understood/used correctly? Null if no item.",
    )
    corrected: str = Field(description="Corrected version of the learner's answer (same as answer if correct).")
    category: MistakeCategory | None = Field(default=None, description="Error category; null if correct.")
    topic: str | None = Field(default=None, description="Short error topic, e.g. 'V2', 'fordi/derfor'; null if correct.")
    explanation_tr: str = Field(description="Direct, encouraging feedback in Turkish.")
    topics_used_correctly: list[str] = Field(
        default_factory=list,
        description="Open mistake topics (from the given list) that the answer used correctly.",
    )
