"""End-to-end use cases through Tutor with a fake LLM and a frozen clock."""

from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src import db
from src.config import Config
from src.schemas import Exercise, GradeResult, LessonContent, ReviewSet
from src.stats import streak
from src.tutor import Tutor

OSLO = ZoneInfo("Europe/Oslo")
DAY1 = datetime(2026, 10, 2, 7, 30, tzinfo=OSLO)


def make_lesson(item_ids: list[int]) -> LessonContent:
    return LessonContent.model_validate({
        "topic": "Morgen", "intro_tr": "Başlayalım.",
        "grammar": {"title": "V2", "logic_tr": "l", "correct_example": "c",
                    "wrong_example": "w", "why_tr": "y"},
        "reading": {"text": "t", "translation_tr": "ç"},
        "exercises": [{"item_id": i, "question_type": "recognition", "instruction_tr": "?",
                       "prompt": "p", "expected_answer": "a"} for i in item_ids],
    })


class FakeLLM:
    """Answers by schema: lesson / review set / queued grades."""

    def __init__(self):
        self.lesson: LessonContent | None = None
        self.review: ReviewSet | None = None
        self.grades: list[GradeResult] = []
        self.calls: list[str] = []

    def generate(self, *, schema, purpose, **kwargs):
        self.calls.append(purpose)
        if schema is LessonContent:
            return self.lesson
        if schema is ReviewSet:
            return self.review
        return self.grades.pop(0)


class FrozenTutor(Tutor):
    clock = DAY1

    def now(self):
        return self.clock


@pytest.fixture
def setup():
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.seed_items(conn, DAY1.date())
    cfg = Config(telegram_bot_token="", telegram_chat_id=1, anthropic_api_key="",
                 anthropic_model="lesson-model", anthropic_grader_model="grader-model",
                 daily_lesson_time=time(7, 30), timezone=OSLO, start_level="A2",
                 db_path=Path(":memory:"))
    llm = FakeLLM()
    tutor = FrozenTutor(conn, llm, cfg)
    tutor.clock = DAY1
    yield conn, llm, tutor
    conn.close()


def ok(**kw):
    return GradeResult(correct=True, corrected=kw.pop("corrected", "a"), explanation_tr="iyi", **kw)


def wrong(topic):
    return GradeResult(correct=False, corrected="doğru", category="WO", topic=topic,
                       explanation_tr="fiil 2. sırada")


def test_lesson_is_generated_once_per_day_and_session_resumes(setup):
    conn, llm, tutor = setup
    pool_ids = [i.id for i in db.get_new_items(conn, "no", limit=5)]
    llm.lesson = make_lesson(pool_ids[:2])

    lesson, session = tutor.start_lesson("full")
    assert session.index == 0 and len(session.exercises) == 2

    llm.grades = [ok()]
    tutor.answer("yetişmek")
    lesson_again, resumed = tutor.start_lesson("busy")  # same day, other mode
    assert llm.calls.count("lesson") == 1
    assert lesson_again.lesson_id == lesson.lesson_id
    assert [i.id for i in lesson_again.new_items] == [i.id for i in lesson.new_items]
    assert resumed.index == 1


def test_full_flow_next_day_contains_yesterdays_items_and_mistakes(setup):
    conn, llm, tutor = setup
    pool_ids = [i.id for i in db.get_new_items(conn, "no", limit=5)]
    llm.lesson = make_lesson(pool_ids[:2])
    tutor.start_lesson("full")

    llm.grades = [ok(), wrong("V2")]
    first = tutor.answer("yetişmek")
    assert not first.session.finished
    last = tutor.answer("I dag jeg skal jobbe")
    assert last.session.finished
    assert last.outcome.mistake.topic == "V2"
    assert tutor.answer("anything") is None  # session over
    assert conn.execute("SELECT score FROM lessons").fetchone()[0] == 0.5

    # --- next morning
    tutor.clock = DAY1 + timedelta(days=1)
    from src.lesson import build_plan
    plan = build_plan(conn, tutor.today())
    assert {r.item.id for r in plan.reviews} == set(pool_ids)
    assert [m.topic for m in plan.mistakes] == ["V2"]
    assert tutor.stats().streak_days == 1


def test_review_session_and_skip(setup):
    conn, llm, tutor = setup
    assert tutor.start_review() is None  # nothing due on day 1
    assert "review" not in llm.calls     # and no LLM call was wasted

    item = db.get_new_items(conn, "no", limit=1)[0]
    db.update_item_srs(conn, item.id, stage=2, ease=2.5, interval_days=4,
                       next_review=tutor.today())
    llm.review = ReviewSet(exercises=[Exercise(
        item_id=item.id, question_type="translation", instruction_tr="çevir",
        prompt="Otobüse yetişemedim.", expected_answer="Jeg rakk ikke bussen.")])
    session = tutor.start_review()
    assert session.kind == "review"
    skipped = tutor.skip()
    assert skipped.finished
    assert tutor.active_session() is None


def test_grading_failure_keeps_the_question(setup):
    conn, llm, tutor = setup
    llm.lesson = make_lesson([db.get_new_items(conn, "no", limit=1)[0].id])
    tutor.start_lesson()

    from src.llm import LLMError

    def boom(**kwargs):
        raise LLMError("down")
    llm.generate = boom
    with pytest.raises(LLMError):
        tutor.answer("x")
    assert tutor.active_session().index == 0


@pytest.mark.parametrize(("days", "expected"), [
    ([], 0),
    ([date(2026, 10, 2)], 1),
    ([date(2026, 10, 1)], 1),                       # yesterday still counts
    ([date(2026, 10, 2), date(2026, 10, 1), date(2026, 9, 30)], 3),
    ([date(2026, 10, 2), date(2026, 9, 30)], 1),    # gap breaks the streak
    ([date(2026, 9, 30)], 0),
])
def test_streak(days, expected):
    assert streak(days, date(2026, 10, 2)) == expected
