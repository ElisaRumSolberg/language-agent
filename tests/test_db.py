"""Tests for the SQLite layer, using an in-memory database."""

from datetime import date, datetime, timedelta

import pytest

from src import db

TODAY = date(2026, 10, 2)


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_db(connection)
    yield connection
    connection.close()


def test_seed_inserts_ten_items_once(conn):
    assert db.seed_items(conn, TODAY) == 10
    assert db.seed_items(conn, TODAY) == 0  # idempotent
    assert len(db.get_known_targets(conn, "no")) == 10


def test_seed_items_are_new_not_due(conn):
    db.seed_items(conn, TODAY)
    assert db.get_due_items(conn, "no", TODAY) == []
    assert len(db.get_new_items(conn, "no", limit=5)) == 5


def test_duplicate_target_is_ignored(conn):
    first = db.insert_item(conn, language="no", kind="word", target="hus",
                           meaning_tr="ev", today=TODAY)
    second = db.insert_item(conn, language="no", kind="word", target="hus",
                            meaning_tr="ev", today=TODAY)
    assert first is not None
    assert second is None


def test_due_items_respect_date_and_order(conn):
    db.seed_items(conn, TODAY)
    a, b, c = db.get_new_items(conn, "no", limit=3)
    db.update_item_srs(conn, a.id, stage=1, ease=2.5, interval_days=1,
                       next_review=TODAY)
    db.update_item_srs(conn, b.id, stage=2, ease=2.5, interval_days=3,
                       next_review=TODAY - timedelta(days=2))
    db.update_item_srs(conn, c.id, stage=1, ease=2.5, interval_days=1,
                       next_review=TODAY + timedelta(days=1))

    due = db.get_due_items(conn, "no", TODAY)
    assert [i.id for i in due] == [b.id, a.id]  # oldest first, future excluded
    assert db.count_due_items(conn, "no", TODAY) == 2
    assert db.count_items_per_stage(conn, "no") == {1: 2, 2: 1}


def test_mistake_upsert_increments_and_becomes_recurring(conn):
    for _ in range(3):
        m = db.upsert_mistake(conn, language="no", category="WO", topic="V2",
                              wrong="I dag jeg skal jobbe.",
                              correct="I dag skal jeg jobbe.",
                              explanation_tr="Fiil 2. sırada.", today=TODAY)
    assert m.count == 3
    assert m.recurring
    assert [x.topic for x in db.get_open_mistakes(conn, "no")] == ["V2"]


def test_mistake_resolves_after_three_correct_uses(conn):
    db.upsert_mistake(conn, language="no", category="WO", topic="V2",
                      wrong="x", correct="y", explanation_tr="z", today=TODAY)
    for _ in range(2):
        m = db.record_correct_use(conn, "no", "V2", TODAY)
        assert not m.resolved
    m = db.record_correct_use(conn, "no", "V2", TODAY)
    assert m.resolved
    assert db.get_open_mistakes(conn, "no") == []


def test_new_mistake_resets_streak_and_reopens(conn):
    kwargs = dict(language="no", category="WO", topic="V2", wrong="x",
                  correct="y", explanation_tr="z", today=TODAY)
    db.upsert_mistake(conn, **kwargs)
    db.record_correct_use(conn, "no", "V2", TODAY)
    db.record_correct_use(conn, "no", "V2", TODAY)
    m = db.upsert_mistake(conn, **kwargs)
    assert m.correct_streak == 0
    assert m.count == 2


def test_open_mistakes_put_recurring_first(conn):
    base = dict(language="no", wrong="x", correct="y", explanation_tr="z", today=TODAY)
    db.upsert_mistake(conn, category="G", topic="adjektiv", **base)
    for _ in range(3):
        db.upsert_mistake(conn, category="WO", topic="V2", **base)
    topics = [m.topic for m in db.get_open_mistakes(conn, "no")]
    assert topics == ["V2", "adjektiv"]


def test_reviews_and_review_dates(conn):
    db.seed_items(conn, TODAY)
    item = db.get_new_items(conn, "no", limit=1)[0]
    db.add_review(conn, item_id=item.id, question_type="recognition", correct=True,
                  user_answer="yetişmek", reviewed_at=datetime(2026, 10, 1, 8, 0))
    db.add_review(conn, item_id=item.id, question_type="cloze", correct=False,
                  user_answer="rekker", reviewed_at=datetime(2026, 10, 2, 8, 0))
    assert db.get_review_dates(conn) == [date(2026, 10, 2), date(2026, 10, 1)]


def test_settings_roundtrip(conn):
    assert db.get_setting(conn, "level", "A2") == "A2"
    db.set_setting(conn, "level", "B1")
    assert db.get_setting(conn, "level") == "B1"
