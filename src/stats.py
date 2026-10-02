"""Progress numbers for /stats."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from src import db
from src.lesson import LANGUAGE


@dataclass
class Stats:
    level: str
    streak_days: int
    due_today: int
    items_per_stage: dict[int, int]
    new_in_pool: int
    open_mistakes: int
    recurring_mistakes: int


def streak(review_days: list[date], today: date) -> int:
    """Consecutive days with at least one review, ending today or yesterday.

    Ending yesterday still counts, so the streak is not 'broken' at 07:00
    before today's lesson has been done.
    """
    days = set(review_days)
    current = today if today in days else today - timedelta(days=1)
    count = 0
    while current in days:
        count += 1
        current -= timedelta(days=1)
    return count


def collect_stats(conn: sqlite3.Connection, today: date, default_level: str = "A2") -> Stats:
    mistakes = db.get_open_mistakes(conn, LANGUAGE)
    return Stats(
        level=db.get_setting(conn, "level", default_level) or default_level,
        streak_days=streak(db.get_review_dates(conn), today),
        due_today=db.count_due_items(conn, LANGUAGE, today),
        items_per_stage=db.count_items_per_stage(conn, LANGUAGE),
        new_in_pool=len(db.get_new_items(conn, LANGUAGE, limit=10_000)),
        open_mistakes=len(mistakes),
        recurring_mistakes=sum(m.recurring for m in mistakes),
    )
