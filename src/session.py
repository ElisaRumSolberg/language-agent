"""The learner's current practice session (which exercise is next).

Stored as JSON in the `settings` table, so a bot restart does not lose
progress in the middle of a lesson.
"""

from __future__ import annotations

import json
import sqlite3

from pydantic import BaseModel

from src import db
from src.schemas import Exercise

SESSION_KEY = "session"


class Session(BaseModel):
    """Exercises being worked through, and how it is going."""

    kind: str  # "lesson" | "review"
    lesson_id: int | None = None
    exercises: list[Exercise]
    index: int = 0
    answered: int = 0
    correct: int = 0

    @property
    def current(self) -> Exercise | None:
        """The exercise waiting for an answer, or None when finished."""
        return self.exercises[self.index] if self.index < len(self.exercises) else None

    @property
    def finished(self) -> bool:
        return self.current is None

    @property
    def score(self) -> float | None:
        """Share of correct answers (0..1), None if nothing was answered."""
        return self.correct / self.answered if self.answered else None

    def record(self, correct: bool) -> None:
        """Count an answer and move to the next exercise."""
        self.answered += 1
        self.correct += int(correct)
        self.index += 1

    def skip(self) -> None:
        """Move on without answering."""
        self.index += 1


def save_session(conn: sqlite3.Connection, session: Session) -> None:
    db.set_setting(conn, SESSION_KEY, session.model_dump_json())


def load_session(conn: sqlite3.Connection) -> Session | None:
    """The active session, or None if there is none (or it is unreadable)."""
    raw = db.get_setting(conn, SESSION_KEY)
    if not raw:
        return None
    try:
        session = Session.model_validate(json.loads(raw))
    except ValueError:
        return None
    return None if session.finished else session


def clear_session(conn: sqlite3.Connection) -> None:
    db.set_setting(conn, SESSION_KEY, "")
