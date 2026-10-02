"""Use cases behind the Telegram commands, independent of Telegram.

All methods are blocking (DB + LLM); bot.py runs them in a worker thread.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from src import db, grader, lesson
from src.config import Config
from src.db import Mistake
from src.grader import GradeOutcome
from src.lesson import GeneratedLesson
from src.llm import LLM
from src.session import Session, clear_session, load_session, save_session
from src.stats import Stats, collect_stats


@dataclass
class AnswerResult:
    outcome: GradeOutcome
    session: Session  # already advanced; `finished` tells whether it ended


class Tutor:
    """One learner, one database, one LLM."""

    def __init__(self, conn: sqlite3.Connection, llm: LLM, cfg: Config) -> None:
        self.conn = conn
        self.llm = llm
        self.cfg = cfg

    # ------------------------------------------------------------ time

    def now(self) -> datetime:
        return datetime.now(self.cfg.timezone)

    def today(self) -> date:
        return self.now().date()

    # ------------------------------------------------------------ sessions

    def start_lesson(self, mode: str = "full") -> tuple[GeneratedLesson, Session]:
        """Today's lesson (reused if it exists) and its session (resumed if active)."""
        today_lesson = lesson.get_or_create_today_lesson(
            self.conn, self.llm, model=self.cfg.anthropic_model, today=self.today(),
            mode=mode, default_level=self.cfg.start_level,
        )
        active = load_session(self.conn)
        if active is not None and active.kind == "lesson" and active.lesson_id == today_lesson.lesson_id:
            return today_lesson, active
        session = Session(kind="lesson", lesson_id=today_lesson.lesson_id,
                          exercises=today_lesson.content.exercises)
        save_session(self.conn, session)
        return today_lesson, session

    def start_review(self) -> Session | None:
        """A review-only session, or None if nothing is due."""
        exercises = lesson.generate_review_set(
            self.conn, self.llm, model=self.cfg.anthropic_model, today=self.today())
        if not exercises:
            return None
        session = Session(kind="review", exercises=exercises)
        save_session(self.conn, session)
        return session

    def active_session(self) -> Session | None:
        return load_session(self.conn)

    def answer(self, text: str) -> AnswerResult | None:
        """Grade the answer to the current exercise. None if no session is active.

        On LLM failure the exception propagates and the session is unchanged,
        so the learner can simply send the answer again.
        """
        session = load_session(self.conn)
        if session is None or session.current is None:
            return None
        outcome = grader.grade_answer(
            self.conn, self.llm, model=self.cfg.anthropic_grader_model,
            ex=session.current, answer=text, today=self.today(), now=self.now(),
        )
        session.record(outcome.grade.correct)
        self._store_progress(session)
        return AnswerResult(outcome=outcome, session=session)

    def skip(self) -> Session | None:
        """Skip the current exercise. None if no session is active."""
        session = load_session(self.conn)
        if session is None:
            return None
        session.skip()
        self._store_progress(session)
        return session

    def _store_progress(self, session: Session) -> None:
        if not session.finished:
            save_session(self.conn, session)
            return
        clear_session(self.conn)
        if session.lesson_id is not None and session.score is not None:
            db.set_lesson_score(self.conn, session.lesson_id, session.score)

    # ------------------------------------------------------------ info

    def open_mistakes(self) -> list[Mistake]:
        return db.get_open_mistakes(self.conn, lesson.LANGUAGE)

    def stats(self) -> Stats:
        return collect_stats(self.conn, self.today(), self.cfg.start_level)
