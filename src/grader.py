"""Answer checking: ask the LLM for a verdict, then update SRS, reviews and mistakes.

`grade_answer` = LLM call + `apply_grade`. `apply_grade` holds all the
bookkeeping and needs no LLM, so it is tested directly.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime

from src import db, srs
from src.db import Item, Mistake
from src.lesson import LANGUAGE
from src.llm import LLM, load_prompt
from src.schemas import Exercise, GradeResult


@dataclass
class GradeOutcome:
    """What happened after one answer, for feedback and logging."""

    grade: GradeResult
    item: Item | None = None
    new_state: srs.SrsState | None = None  # None when the item was not due (no reschedule)
    mistake: Mistake | None = None
    resolved_topics: list[str] = field(default_factory=list)


def render_grader_prompt(ex: Exercise, answer: str, item: Item | None, open_topics: list[str]) -> str:
    """Fill the grader_user template."""
    item_text = "(none)"
    if item is not None:
        item_text = f"{item.target} — {item.meaning_tr}"
        if item.forms:
            item_text += f" | forms: {item.forms}"
    return load_prompt(
        "grader_user",
        question_type=ex.question_type,
        instruction=ex.instruction_tr,
        prompt=ex.prompt,
        expected_answer=ex.expected_answer,
        item=item_text,
        mistake_topic=ex.mistake_topic or "(none)",
        open_topics=", ".join(open_topics) or "(none)",
        answer=answer,
    )


def apply_grade(
    conn: sqlite3.Connection,
    ex: Exercise,
    answer: str,
    grade: GradeResult,
    today: date,
    now: datetime,
) -> GradeOutcome:
    """Persist the consequences of a verdict.

    - Item practised: log a review. Reschedule with SRS only if the item was
      due (items taught today are practised but keep their D1 review).
    - Wrong answer with a category+topic: upsert the mistake (count += 1).
    - Open mistake topics used correctly: advance their streak (3 => resolved).
    """
    outcome = GradeOutcome(grade=grade)

    item = db.get_item(conn, ex.item_id) if ex.item_id is not None else None
    if item is not None:
        outcome.item = item
        item_ok = grade.item_used_correctly if grade.item_used_correctly is not None else grade.correct
        db.add_review(conn, item_id=item.id, question_type=ex.question_type,
                      correct=item_ok, user_answer=answer, reviewed_at=now)
        if item.next_review is not None and item.next_review <= today:
            state = srs.review(
                srs.SrsState(item.stage, item.ease, item.interval_days, item.next_review),
                item_ok, today,
            )
            db.update_item_srs(conn, item.id, stage=state.stage, ease=state.ease,
                               interval_days=state.interval_days, next_review=state.next_review)
            outcome.new_state = state

    if not grade.correct and grade.category and grade.topic:
        outcome.mistake = db.upsert_mistake(
            conn, language=LANGUAGE, category=grade.category, topic=grade.topic,
            wrong=answer, correct=grade.corrected, explanation_tr=grade.explanation_tr,
            today=today,
        )

    used = set(grade.topics_used_correctly)
    if grade.correct and ex.mistake_topic:
        used.add(ex.mistake_topic)
    if outcome.mistake is not None:
        used.discard(outcome.mistake.topic)
    for topic in sorted(used):
        m = db.record_correct_use(conn, LANGUAGE, topic, today)
        if m is not None and m.resolved:
            outcome.resolved_topics.append(topic)

    return outcome


def grade_answer(
    conn: sqlite3.Connection,
    llm: LLM,
    *,
    model: str,
    ex: Exercise,
    answer: str,
    today: date,
    now: datetime,
) -> GradeOutcome:
    """Grade one answer with the LLM and apply the result. Raises LLMError on failure."""
    item = db.get_item(conn, ex.item_id) if ex.item_id is not None else None
    open_topics = [m.topic for m in db.get_open_mistakes(conn, LANGUAGE)]
    grade = llm.generate(
        model=model,
        system=load_prompt("grader_system"),
        user=render_grader_prompt(ex, answer, item, open_topics),
        schema=GradeResult,
        max_tokens=2000,
        purpose="grade",
    )
    return apply_grade(conn, ex, answer, grade, today, now)
