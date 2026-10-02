"""Tests for apply_grade bookkeeping (no LLM) and the grader prompt."""

from datetime import date, datetime, timedelta

import pytest

from src import db
from src.grader import apply_grade, render_grader_prompt
from src.schemas import Exercise, GradeResult

TODAY = date(2026, 10, 2)
NOW = datetime(2026, 10, 2, 8, 0)


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_db(connection)
    db.seed_items(connection, TODAY)
    yield connection
    connection.close()


@pytest.fixture
def rekke(conn):
    item = db.get_item_by_target(conn, "no", "å rekke")
    db.update_item_srs(conn, item.id, stage=1, ease=2.5, interval_days=1, next_review=TODAY)
    return db.get_item(conn, item.id)


def exercise(item_id=None, mistake_topic=None, qtype="cloze") -> Exercise:
    return Exercise(item_id=item_id, mistake_topic=mistake_topic, question_type=qtype,
                    instruction_tr="x", prompt="Jeg ___ ikke bussen.", expected_answer="rakk")


def grade(correct, **kw) -> GradeResult:
    return GradeResult(correct=correct, corrected=kw.pop("corrected", "rakk"),
                       explanation_tr="açıklama", **kw)


def test_correct_due_item_advances_and_is_logged(conn, rekke):
    out = apply_grade(conn, exercise(rekke.id), "rakk", grade(True), TODAY, NOW)
    assert out.new_state.stage == 2
    assert db.get_item(conn, rekke.id).next_review == TODAY + timedelta(days=4)
    assert db.get_review_dates(conn) == [TODAY]


def test_wrong_due_item_drops_and_logs_mistake(conn, rekke):
    g = grade(False, category="G", topic="preteritum")
    out = apply_grade(conn, exercise(rekke.id), "rekker", g, TODAY, NOW)
    assert out.new_state.stage == 0
    assert db.get_item(conn, rekke.id).next_review == TODAY + timedelta(days=1)
    assert out.mistake.topic == "preteritum"
    assert out.mistake.wrong == "rekker"
    assert out.mistake.correct == "rakk"


def test_item_correct_but_sentence_wrong_keeps_item_progress(conn, rekke):
    g = grade(False, item_used_correctly=True, category="WO", topic="V2",
              corrected="I dag rakk jeg bussen.")
    out = apply_grade(conn, exercise(rekke.id, qtype="own_sentence"),
                      "I dag jeg rakk bussen.", g, TODAY, NOW)
    assert out.new_state.stage == 2  # item advanced
    assert out.mistake.topic == "V2"  # but the V2 error is counted


def test_item_taught_today_is_not_rescheduled(conn):
    item = db.get_new_items(conn, "no", limit=1)[0]
    db.update_item_srs(conn, item.id, stage=0, ease=2.5, interval_days=1,
                       next_review=TODAY + timedelta(days=1))
    out = apply_grade(conn, exercise(item.id, qtype="recognition"), "x", grade(False), TODAY, NOW)
    assert out.new_state is None
    assert db.get_item(conn, item.id).next_review == TODAY + timedelta(days=1)
    assert db.get_review_dates(conn) == [TODAY]  # still logged


def test_three_correct_drills_resolve_mistake(conn):
    db.upsert_mistake(conn, language="no", category="WO", topic="V2", wrong="w",
                      correct="c", explanation_tr="e", today=TODAY)
    ex = exercise(mistake_topic="V2", qtype="translation")
    results = [apply_grade(conn, ex, "ok", grade(True), TODAY, NOW) for _ in range(3)]
    assert [r.resolved_topics for r in results] == [[], [], ["V2"]]
    assert db.get_open_mistakes(conn, "no") == []


def test_topics_used_correctly_count_but_failed_topic_does_not(conn):
    for topic in ["V2", "fordi/derfor"]:
        db.upsert_mistake(conn, language="no", category="WO", topic=topic, wrong="w",
                          correct="c", explanation_tr="e", today=TODAY)
    g = grade(False, category="WO", topic="V2", topics_used_correctly=["V2", "fordi/derfor"])
    apply_grade(conn, exercise(), "x", g, TODAY, NOW)
    by_topic = {m.topic: m for m in db.get_open_mistakes(conn, "no")}
    assert by_topic["V2"].correct_streak == 0
    assert by_topic["V2"].count == 2
    assert by_topic["fordi/derfor"].correct_streak == 1


def test_already_resolved_topic_is_not_reported_again(conn):
    db.upsert_mistake(conn, language="no", category="WO", topic="V2", wrong="w",
                      correct="c", explanation_tr="e", today=TODAY)
    ex = exercise(mistake_topic="V2")
    for _ in range(3):
        apply_grade(conn, ex, "ok", grade(True), TODAY, NOW)
    assert apply_grade(conn, ex, "ok", grade(True), TODAY, NOW).resolved_topics == []


def test_grader_prompt_is_fully_filled(conn, rekke):
    prompt = render_grader_prompt(exercise(rekke.id), "rakk", rekke, ["V2"])
    assert "å rekke — yetişmek" in prompt
    assert "Open mistake topics\nV2" in prompt
    assert "$" not in prompt
