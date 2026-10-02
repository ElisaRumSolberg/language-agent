"""SQLite access layer: connection, schema creation, seed data and queries.

All SQL lives in this module so the rest of the app never builds queries.
Dates are stored as ISO strings ('YYYY-MM-DD'), timestamps as ISO datetimes.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id            INTEGER PRIMARY KEY,
  language      TEXT NOT NULL CHECK (language IN ('no','en')),
  track         TEXT NOT NULL DEFAULT 'daily' CHECK (track IN ('daily','tech','both')),
  kind          TEXT NOT NULL CHECK (kind IN ('word','chunk','grammar')),
  target        TEXT NOT NULL,
  meaning_tr    TEXT NOT NULL,
  forms         TEXT,
  example       TEXT,
  memory_hook   TEXT,
  stage         INTEGER NOT NULL DEFAULT 0,
  ease          REAL    NOT NULL DEFAULT 2.5,
  interval_days INTEGER NOT NULL DEFAULT 0,
  next_review   DATE,               -- NULL = not introduced yet (new item)
  created_at    DATE NOT NULL,
  UNIQUE (language, target)
);

CREATE TABLE IF NOT EXISTS reviews (
  id            INTEGER PRIMARY KEY,
  item_id       INTEGER NOT NULL REFERENCES items(id),
  reviewed_at   TIMESTAMP NOT NULL,
  question_type TEXT NOT NULL,
  correct       INTEGER NOT NULL,
  user_answer   TEXT
);

CREATE TABLE IF NOT EXISTS mistakes (
  id             INTEGER PRIMARY KEY,
  language       TEXT NOT NULL,
  category       TEXT NOT NULL CHECK (category IN ('G','V','WO','SP','N','R')),
  topic          TEXT NOT NULL,
  wrong          TEXT NOT NULL,
  correct        TEXT NOT NULL,
  explanation_tr TEXT NOT NULL,
  count          INTEGER NOT NULL DEFAULT 1,
  correct_streak INTEGER NOT NULL DEFAULT 0,  -- consecutive correct uses; 3 => resolved
  resolved       INTEGER NOT NULL DEFAULT 0,
  last_seen      DATE NOT NULL,
  UNIQUE (language, topic)
);

CREATE TABLE IF NOT EXISTS lessons (
  id           INTEGER PRIMARY KEY,
  date         DATE NOT NULL,
  language     TEXT NOT NULL,
  mode         TEXT NOT NULL CHECK (mode IN ('full','busy')),
  topic        TEXT,
  content_json TEXT NOT NULL,
  score        REAL
);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_due ON items (language, next_review);
"""

RESOLVE_STREAK = 3
RECURRING_COUNT = 3


@dataclass
class Item:
    """One learnable unit (word, chunk or grammar point) with its SRS state."""

    id: int
    language: str
    track: str
    kind: str
    target: str
    meaning_tr: str
    forms: str | None
    example: str | None
    memory_hook: str | None
    stage: int
    ease: float
    interval_days: int
    next_review: date | None
    created_at: date


@dataclass
class Mistake:
    """A logged error pattern, aggregated per (language, topic)."""

    id: int
    language: str
    category: str
    topic: str
    wrong: str
    correct: str
    explanation_tr: str
    count: int
    correct_streak: int
    resolved: bool
    last_seen: date

    @property
    def recurring(self) -> bool:
        """A mistake seen 3+ times must appear in every lesson until resolved."""
        return self.count >= RECURRING_COUNT


# ---------------------------------------------------------------- connection


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a connection with row access by column name and FKs enabled."""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables if they do not exist (idempotent)."""
    conn.executescript(SCHEMA)
    conn.commit()


# ---------------------------------------------------------------- row mapping


def _to_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _row_to_item(row: sqlite3.Row) -> Item:
    return Item(
        id=row["id"],
        language=row["language"],
        track=row["track"],
        kind=row["kind"],
        target=row["target"],
        meaning_tr=row["meaning_tr"],
        forms=row["forms"],
        example=row["example"],
        memory_hook=row["memory_hook"],
        stage=row["stage"],
        ease=row["ease"],
        interval_days=row["interval_days"],
        next_review=_to_date(row["next_review"]),
        created_at=date.fromisoformat(row["created_at"]),
    )


def _row_to_mistake(row: sqlite3.Row) -> Mistake:
    return Mistake(
        id=row["id"],
        language=row["language"],
        category=row["category"],
        topic=row["topic"],
        wrong=row["wrong"],
        correct=row["correct"],
        explanation_tr=row["explanation_tr"],
        count=row["count"],
        correct_streak=row["correct_streak"],
        resolved=bool(row["resolved"]),
        last_seen=date.fromisoformat(row["last_seen"]),
    )


# ---------------------------------------------------------------- items


def insert_item(
    conn: sqlite3.Connection,
    *,
    language: str,
    kind: str,
    target: str,
    meaning_tr: str,
    today: date,
    forms: str | None = None,
    example: str | None = None,
    memory_hook: str | None = None,
    track: str = "daily",
) -> int | None:
    """Insert a new (not yet introduced) item.

    Returns the new id, or None if an item with the same target already
    exists for that language (UNIQUE constraint) — the LLM may repeat words.
    """
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO items
          (language, track, kind, target, meaning_tr, forms, example, memory_hook, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (language, track, kind, target, meaning_tr, forms, example, memory_hook,
         today.isoformat()),
    )
    conn.commit()
    return cur.lastrowid if cur.rowcount else None


def get_item(conn: sqlite3.Connection, item_id: int) -> Item | None:
    """Fetch one item by id."""
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return _row_to_item(row) if row else None


def get_due_items(
    conn: sqlite3.Connection, language: str, today: date, limit: int = 10
) -> list[Item]:
    """Items already introduced whose review date has arrived, oldest first."""
    rows = conn.execute(
        """
        SELECT * FROM items
        WHERE language = ? AND next_review IS NOT NULL AND next_review <= ?
        ORDER BY next_review ASC, stage ASC
        LIMIT ?
        """,
        (language, today.isoformat(), limit),
    ).fetchall()
    return [_row_to_item(r) for r in rows]


def count_due_items(conn: sqlite3.Connection, language: str, today: date) -> int:
    """Number of items due for review today."""
    row = conn.execute(
        """
        SELECT COUNT(*) FROM items
        WHERE language = ? AND next_review IS NOT NULL AND next_review <= ?
        """,
        (language, today.isoformat()),
    ).fetchone()
    return row[0]


def get_new_items(conn: sqlite3.Connection, language: str, limit: int) -> list[Item]:
    """Items that exist in the pool but have never been taught (next_review IS NULL)."""
    rows = conn.execute(
        """
        SELECT * FROM items
        WHERE language = ? AND next_review IS NULL
        ORDER BY id ASC
        LIMIT ?
        """,
        (language, limit),
    ).fetchall()
    return [_row_to_item(r) for r in rows]


def get_known_targets(conn: sqlite3.Connection, language: str) -> list[str]:
    """All targets for a language, so the LLM can avoid suggesting duplicates."""
    rows = conn.execute(
        "SELECT target FROM items WHERE language = ? ORDER BY id", (language,)
    ).fetchall()
    return [r["target"] for r in rows]


def update_item_srs(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    stage: int,
    ease: float,
    interval_days: int,
    next_review: date,
) -> None:
    """Persist a new SRS state computed by srs.py."""
    conn.execute(
        """
        UPDATE items
        SET stage = ?, ease = ?, interval_days = ?, next_review = ?
        WHERE id = ?
        """,
        (stage, ease, interval_days, next_review.isoformat(), item_id),
    )
    conn.commit()


def count_items_per_stage(conn: sqlite3.Connection, language: str) -> dict[int, int]:
    """Introduced items grouped by SRS stage, for /stats."""
    rows = conn.execute(
        """
        SELECT stage, COUNT(*) AS n FROM items
        WHERE language = ? AND next_review IS NOT NULL
        GROUP BY stage ORDER BY stage
        """,
        (language,),
    ).fetchall()
    return {r["stage"]: r["n"] for r in rows}


# ---------------------------------------------------------------- reviews


def add_review(
    conn: sqlite3.Connection,
    *,
    item_id: int,
    question_type: str,
    correct: bool,
    user_answer: str | None,
    reviewed_at: datetime,
) -> int:
    """Log one answered question."""
    cur = conn.execute(
        """
        INSERT INTO reviews (item_id, reviewed_at, question_type, correct, user_answer)
        VALUES (?, ?, ?, ?, ?)
        """,
        (item_id, reviewed_at.isoformat(timespec="seconds"), question_type,
         int(correct), user_answer),
    )
    conn.commit()
    return cur.lastrowid


def get_review_dates(conn: sqlite3.Connection) -> list[date]:
    """Distinct days on which at least one review happened, newest first (for streak)."""
    rows = conn.execute(
        "SELECT DISTINCT substr(reviewed_at, 1, 10) AS d FROM reviews ORDER BY d DESC"
    ).fetchall()
    return [date.fromisoformat(r["d"]) for r in rows]


# ---------------------------------------------------------------- mistakes


def upsert_mistake(
    conn: sqlite3.Connection,
    *,
    language: str,
    category: str,
    topic: str,
    wrong: str,
    correct: str,
    explanation_tr: str,
    today: date,
) -> Mistake:
    """Log a mistake. Same (language, topic) => count += 1 and streak resets.

    A previously resolved topic is reopened, because the error came back.
    The latest example/explanation replaces the older one.
    """
    conn.execute(
        """
        INSERT INTO mistakes
          (language, category, topic, wrong, correct, explanation_tr, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (language, topic) DO UPDATE SET
          count          = count + 1,
          correct_streak = 0,
          resolved       = 0,
          category       = excluded.category,
          wrong          = excluded.wrong,
          correct        = excluded.correct,
          explanation_tr = excluded.explanation_tr,
          last_seen      = excluded.last_seen
        """,
        (language, category, topic, wrong, correct, explanation_tr, today.isoformat()),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM mistakes WHERE language = ? AND topic = ?", (language, topic)
    ).fetchone()
    return _row_to_mistake(row)


def record_correct_use(
    conn: sqlite3.Connection, language: str, topic: str, today: date
) -> Mistake | None:
    """Count a correct use of an open mistake topic; resolve it after 3 in a row.

    Returns the updated mistake, or None if there is no open mistake for the topic.
    """
    conn.execute(
        """
        UPDATE mistakes
        SET correct_streak = correct_streak + 1,
            resolved       = CASE WHEN correct_streak + 1 >= ? THEN 1 ELSE 0 END,
            last_seen      = ?
        WHERE language = ? AND topic = ? AND resolved = 0
        """,
        (RESOLVE_STREAK, today.isoformat(), language, topic),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM mistakes WHERE language = ? AND topic = ?", (language, topic)
    ).fetchone()
    return _row_to_mistake(row) if row else None


def get_open_mistakes(conn: sqlite3.Connection, language: str) -> list[Mistake]:
    """Unresolved mistakes, recurring (count >= 3) first, then most frequent/recent."""
    rows = conn.execute(
        """
        SELECT * FROM mistakes
        WHERE language = ? AND resolved = 0
        ORDER BY (count >= ?) DESC, count DESC, last_seen DESC
        """,
        (language, RECURRING_COUNT),
    ).fetchall()
    return [_row_to_mistake(r) for r in rows]


# ---------------------------------------------------------------- lessons


def save_lesson(
    conn: sqlite3.Connection,
    *,
    lesson_date: date,
    language: str,
    mode: str,
    topic: str | None,
    content_json: str,
) -> int:
    """Store a generated lesson (the raw validated JSON)."""
    cur = conn.execute(
        """
        INSERT INTO lessons (date, language, mode, topic, content_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (lesson_date.isoformat(), language, mode, topic, content_json),
    )
    conn.commit()
    return cur.lastrowid


def get_latest_lesson(
    conn: sqlite3.Connection, language: str, lesson_date: date
) -> sqlite3.Row | None:
    """Most recent lesson for a given day, if any."""
    return conn.execute(
        """
        SELECT * FROM lessons WHERE language = ? AND date = ?
        ORDER BY id DESC LIMIT 1
        """,
        (language, lesson_date.isoformat()),
    ).fetchone()


def set_lesson_score(conn: sqlite3.Connection, lesson_id: int, score: float) -> None:
    """Store the share of correct answers (0..1) for a lesson."""
    conn.execute("UPDATE lessons SET score = ? WHERE id = ?", (score, lesson_id))
    conn.commit()


# ---------------------------------------------------------------- settings


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    """Read a key/value setting (e.g. current level)."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Write a key/value setting."""
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    conn.commit()


# ---------------------------------------------------------------- seed

SEED_ITEMS_NO: list[dict[str, str]] = [
    {
        "kind": "word",
        "target": "å rekke",
        "meaning_tr": "yetişmek (otobüse, bir işe zamanında)",
        "forms": "rekker – rakk – har rukket",
        "example": "Jeg rakk ikke bussen i dag.",
        "memory_hook": "rekke ≈ İng. *reach*: otobüse elin 'uzanıyor' mu, yetişiyor musun?",
    },
    {
        "kind": "word",
        "target": "et vindu",
        "meaning_tr": "pencere",
        "forms": "et vindu – vinduet – vinduer – vinduene",
        "example": "Kan du åpne vinduet?",
        "memory_hook": "vind (rüzgâr) + auga (göz) = 'rüzgâr gözü' ↔ İng. *window* aynı kökten.",
    },
    {
        "kind": "word",
        "target": "å glemme",
        "meaning_tr": "unutmak",
        "forms": "glemmer – glemte – har glemt",
        "example": "Jeg glemte nøklene mine hjemme.",
        "memory_hook": "'gel-me': unuttuğu için toplantıya gelmedi. Almanca'yı bilirsen: (ver)gessen değil, glemme!",
    },
    {
        "kind": "word",
        "target": "kanskje",
        "meaning_tr": "belki",
        "forms": None,
        "example": "Kanskje det regner i morgen.",
        "memory_hook": "kan + skje = 'can happen' → olabilir → belki. (Bergen'de bu çok lazım 🌧️)",
    },
    {
        "kind": "word",
        "target": "å trenge",
        "meaning_tr": "ihtiyaç duymak, gerekmek",
        "forms": "trenger – trengte – har trengt",
        "example": "Jeg trenger mer tid.",
        "memory_hook": "'Trene ihtiyacım var' — trenge = ihtiyaç duymak.",
    },
    {
        "kind": "chunk",
        "target": "Det kommer an på ...",
        "meaning_tr": "...-e bağlı, duruma göre değişir",
        "forms": None,
        "example": "Skal vi gå på tur? – Det kommer an på været.",
        "memory_hook": "Kelime kelime: 'o ...-in üstüne gelir' ↔ İng. *it comes down to*.",
    },
    {
        "kind": "word",
        "target": "å handle",
        "meaning_tr": "alışveriş yapmak",
        "forms": "handler – handlet – har handlet",
        "example": "Jeg må handle mat etter jobb.",
        "memory_hook": "⚠️ Yalancı arkadaş: İng. *handle* değil! 'handel' = ticaret → alışveriş.",
    },
    {
        "kind": "word",
        "target": "en barnehage",
        "meaning_tr": "anaokulu, kreş",
        "forms": "en barnehage – barnehagen – barnehager – barnehagene",
        "example": "Jeg henter barnet i barnehagen klokka fire.",
        "memory_hook": "barn (çocuk) + hage (bahçe) = 'çocuk bahçesi' ↔ *kindergarten*.",
    },
    {
        "kind": "word",
        "target": "egentlig",
        "meaning_tr": "aslında, gerçekte",
        "forms": None,
        "example": "Egentlig liker jeg ikke kaffe.",
        "memory_hook": "egen = kendi (İng. *own*) → 'işin kendisinde' → aslında.",
    },
    {
        "kind": "grammar",
        "target": "V2-regelen (inversjon)",
        "meaning_tr": "Ana cümlede çekimli fiil her zaman 2. sıradadır; cümle başka bir öğeyle başlarsa özne fiilin arkasına geçer.",
        "forms": "Jeg skal jobbe i dag. → I dag skal jeg jobbe.",
        "example": "I morgen skal jeg handle.",
        "memory_hook": "Fiil hep gümüş madalya 🥈: ne gelirse gelsin 2. sırada durur.",
    },
]


def seed_items(conn: sqlite3.Connection, today: date) -> int:
    """Insert the starter A2 Norwegian items. Safe to run repeatedly.

    Returns how many items were actually inserted.
    """
    inserted = 0
    for item in SEED_ITEMS_NO:
        if insert_item(conn, language="no", today=today, **item) is not None:
            inserted += 1
    return inserted


if __name__ == "__main__":
    from src.config import load_config

    cfg = load_config()
    connection = connect(cfg.db_path)
    init_db(connection)
    added = seed_items(connection, date.today())
    print(f"DB ready at {cfg.db_path} — {added} seed items inserted.")
