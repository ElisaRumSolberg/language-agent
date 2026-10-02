"""Builds the daily lesson: due reviews + open mistakes + new items.

Three stages, kept separate so each can be tested on its own:
1. `build_plan`   — reads the DB and decides WHAT to practise (no LLM).
2. `render_prompt` — turns the plan into the user prompt.
3. `generate_lesson` — asks the LLM to WRITE the lesson, then stores it
   and schedules newly introduced items.

Run `python -m src.lesson --help` for the terminal dry-run.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date

from src import db, srs
from src.db import Item, Mistake
from src.llm import LLM, LLMError, load_prompt
from src.schemas import LessonContent, NewItem

log = logging.getLogger(__name__)

LANGUAGE = "no"
NEW_SHARE = 0.3  # roughly 70% review / 30% new
MAX_OTHER_MISTAKES = 3  # non-recurring mistakes passed to the LLM


@dataclass(frozen=True)
class ModeSpec:
    """Size limits and time budget for a lesson mode."""

    max_reviews: int
    new_min: int
    new_max: int
    budget: str


MODES: dict[str, ModeSpec] = {
    "full": ModeSpec(
        max_reviews=10, new_min=5, new_max=8,
        budget="about 30–40 minutes in total.",
    ),
    "busy": ModeSpec(
        max_reviews=5, new_min=3, new_max=3,
        budget=(
            "about 15 minutes: 4 min reviews, 3 min new words, 3 min grammar/sentence, "
            "2 min reading, 3 min writing one sentence. Keep everything short."
        ),
    ),
}


@dataclass(frozen=True)
class ReviewTask:
    """A due item and the question type its SRS stage calls for."""

    item: Item
    question_type: str


@dataclass
class LessonPlan:
    """Everything the lesson will contain, decided from the database."""

    lesson_date: date
    mode: str
    level: str
    reviews: list[ReviewTask]
    pool_new: list[Item]
    extra_new_count: int
    mistakes: list[Mistake]
    recent_grammar: list[str]
    known_targets: list[str]


@dataclass
class GeneratedLesson:
    """A written lesson plus the items it teaches today.

    `new_items` are DB Items when saved; in an unsaved dry-run the LLM-created
    ones are still NewItem objects (same display fields, no id).
    """

    plan: LessonPlan
    content: LessonContent
    new_items: list[Item | NewItem] = field(default_factory=list)
    lesson_id: int | None = None


# ---------------------------------------------------------------- 1. plan


def new_item_count(mode: str, review_count: int) -> int:
    """How many new items to teach: ~30% of the lesson, within the mode's range."""
    spec = MODES[mode]
    wanted = round(review_count * NEW_SHARE / (1 - NEW_SHARE))
    return max(spec.new_min, min(wanted, spec.new_max))


def pick_mistakes(open_mistakes: list[Mistake]) -> list[Mistake]:
    """All recurring mistakes (they must appear) plus a few other open ones."""
    recurring = [m for m in open_mistakes if m.recurring]
    others = [m for m in open_mistakes if not m.recurring][:MAX_OTHER_MISTAKES]
    return recurring + others


def recent_grammar_titles(contents: list[str]) -> list[str]:
    """Grammar titles from stored lesson JSON (unreadable rows are skipped)."""
    titles = []
    for raw in contents:
        try:
            titles.append(json.loads(raw)["grammar"]["title"])
        except (ValueError, KeyError, TypeError):
            continue
    return titles


def build_plan(
    conn: sqlite3.Connection, today: date, mode: str = "full", default_level: str = "A2"
) -> LessonPlan:
    """Decide today's reviews, mistakes and new items from the DB."""
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    spec = MODES[mode]

    due = db.get_due_items(conn, LANGUAGE, today, limit=spec.max_reviews)
    reviews = [ReviewTask(i, srs.question_type_for_stage(i.stage)) for i in due]

    n_new = new_item_count(mode, len(reviews))
    pool_new = db.get_new_items(conn, LANGUAGE, limit=n_new)

    return LessonPlan(
        lesson_date=today,
        mode=mode,
        level=db.get_setting(conn, "level", default_level) or default_level,
        reviews=reviews,
        pool_new=pool_new,
        extra_new_count=n_new - len(pool_new),
        mistakes=pick_mistakes(db.get_open_mistakes(conn, LANGUAGE)),
        recent_grammar=recent_grammar_titles(db.get_recent_lesson_contents(conn, LANGUAGE)),
        known_targets=db.get_known_targets(conn, LANGUAGE),
    )


# ---------------------------------------------------------------- 2. prompt


def _item_line(item: Item) -> str:
    parts = [f"id={item.id}", f"{item.target} — {item.meaning_tr}"]
    if item.forms:
        parts.append(f"forms: {item.forms}")
    if item.example:
        parts.append(f"example: {item.example}")
    return " | ".join(parts)


def _mistake_line(m: Mistake) -> str:
    tag = "[RECURRING] " if m.recurring else ""
    return (f"- {tag}topic={m.topic} ({m.category}, seen {m.count}×): "
            f"wrong \"{m.wrong}\" → correct \"{m.correct}\"")


def render_prompt(plan: LessonPlan) -> str:
    """Fill the lesson_user template with the plan."""
    return load_prompt(
        "lesson_user",
        date=plan.lesson_date.isoformat(),
        level=plan.level,
        mode=plan.mode,
        mode_budget=MODES[plan.mode].budget,
        review_items="\n".join(
            f"- {_item_line(r.item)} | stage {r.item.stage} → {r.question_type}"
            for r in plan.reviews
        ) or "(none)",
        pool_items="\n".join(f"- {_item_line(i)}" for i in plan.pool_new) or "(none)",
        extra_new_count=plan.extra_new_count,
        mistakes="\n".join(_mistake_line(m) for m in plan.mistakes) or "(none)",
        recent_grammar=", ".join(plan.recent_grammar) or "(none)",
        known_targets=", ".join(plan.known_targets) or "(none)",
    )


# ---------------------------------------------------------------- 3. generate + store


def _store_new_items(
    conn: sqlite3.Connection, content: LessonContent, today: date
) -> list[Item]:
    """Insert LLM-created items; return the ones that are really new today."""
    created = []
    for new in content.new_items:
        db.insert_item(
            conn, language=LANGUAGE, kind=new.kind, target=new.target,
            meaning_tr=new.meaning_tr, forms=new.forms, example=new.example,
            memory_hook=new.memory_hook, today=today,
        )
        item = db.get_item_by_target(conn, LANGUAGE, new.target)
        if item is not None and item.next_review is None:  # skip already-taught duplicates
            created.append(item)
    return created


def _attach_item_ids(content: LessonContent, items: list[Item]) -> None:
    """Fill exercise.item_id for exercises that only name a target."""
    by_target = {i.target: i.id for i in items}
    for ex in content.exercises:
        if ex.item_id is None and ex.target in by_target:
            ex.item_id = by_target[ex.target]


def _introduce(conn: sqlite3.Connection, items: list[Item], today: date) -> None:
    """Schedule first review (D1) for items taught today."""
    for item in items:
        state = srs.introduce(today)
        db.update_item_srs(conn, item.id, stage=state.stage, ease=state.ease,
                           interval_days=state.interval_days, next_review=state.next_review)


def _check_coverage(plan: LessonPlan, content: LessonContent) -> None:
    """Warn (don't fail) when the LLM skipped a review item or recurring mistake."""
    practised_ids = {ex.item_id for ex in content.exercises}
    practised_topics = {ex.mistake_topic for ex in content.exercises}
    for r in plan.reviews:
        if r.item.id not in practised_ids:
            log.warning("lesson has no exercise for review item %s (%s)", r.item.id, r.item.target)
    for m in plan.mistakes:
        if m.recurring and m.topic not in practised_topics:
            log.warning("lesson does not drill recurring mistake %r", m.topic)


def generate_lesson(
    conn: sqlite3.Connection,
    llm: LLM,
    *,
    model: str,
    today: date,
    mode: str = "full",
    default_level: str = "A2",
    save: bool = True,
) -> GeneratedLesson:
    """Plan, write and (optionally) store today's lesson.

    With save=True: new items are inserted, today's new items are scheduled
    for tomorrow, and the lesson JSON is stored. With save=False the DB is untouched.
    """
    plan = build_plan(conn, today, mode, default_level)
    content = llm.generate(
        model=model,
        system=load_prompt("lesson_system"),
        user=render_prompt(plan),
        schema=LessonContent,
        effort="medium",
        purpose="lesson",
    )
    _check_coverage(plan, content)

    if not save:
        return GeneratedLesson(plan=plan, content=content,
                               new_items=[*plan.pool_new, *content.new_items])

    new_items = plan.pool_new + _store_new_items(conn, content, today)
    _attach_item_ids(content, new_items)
    _introduce(conn, new_items, today)
    lesson_id = db.save_lesson(
        conn, lesson_date=today, language=LANGUAGE, mode=mode,
        topic=content.topic, content_json=content.model_dump_json(),
    )
    return GeneratedLesson(plan=plan, content=content, new_items=new_items, lesson_id=lesson_id)


# ---------------------------------------------------------------- dry-run CLI


def main(argv: list[str] | None = None) -> None:
    """Terminal dry-run: print a lesson without Telegram."""
    from src.config import load_config
    from src.formatting import exercise_message, lesson_messages, to_plain_text

    parser = argparse.ArgumentParser(description="Print today's lesson in the terminal.")
    parser.add_argument("--mode", choices=list(MODES), default="full")
    parser.add_argument("--plan-only", action="store_true",
                        help="only show the plan/prompt, do not call the LLM")
    parser.add_argument("--save", action="store_true",
                        help="store the lesson and schedule new items (default: DB untouched)")
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config()
    conn = db.connect(cfg.db_path)
    db.init_db(conn)
    db.seed_items(conn, date.today())
    today = date.today()

    if args.plan_only:
        print(render_prompt(build_plan(conn, today, args.mode, cfg.start_level)))
        return

    try:
        lesson = generate_lesson(
            conn, LLM.from_api_key(cfg.anthropic_api_key), model=cfg.anthropic_model,
            today=today, mode=args.mode, default_level=cfg.start_level, save=args.save,
        )
    except LLMError as exc:
        sys.exit(f"Ders üretilemedi: {exc}")
    for msg in lesson_messages(lesson.content, lesson.new_items):
        print(to_plain_text(msg), end="\n\n" + "─" * 40 + "\n\n")
    total = len(lesson.content.exercises)
    for n, ex in enumerate(lesson.content.exercises, start=1):
        print(to_plain_text(exercise_message(ex, n, total)))
        print(f"   ↳ beklenen: {ex.expected_answer}  [item_id={ex.item_id}]\n")


if __name__ == "__main__":
    main()
