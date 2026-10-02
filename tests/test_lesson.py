"""Tests for lesson planning, prompt rendering, storage and formatting (fake LLM)."""

from datetime import date, timedelta

import pytest

from src import db, lesson
from src.formatting import exercise_message, lesson_messages, to_plain_text
from src.schemas import Exercise, LessonContent

TODAY = date(2026, 10, 2)
TOMORROW = TODAY + timedelta(days=1)


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_db(connection)
    db.seed_items(connection, TODAY)
    yield connection
    connection.close()


def make_content(**overrides) -> LessonContent:
    data = {
        "topic": "Morgen",
        "intro_tr": "Hadi başlayalım.",
        "new_items": [],
        "grammar": {"title": "V2", "logic_tr": "Fiil 2. sırada.",
                    "correct_example": "I dag skal jeg jobbe.",
                    "wrong_example": "I dag jeg skal jobbe.", "why_tr": "Çünkü V2."},
        "reading": {"text": "Jeg rakk bussen.", "translation_tr": "Otobüse yetiştim."},
        "exercises": [],
    }
    data.update(overrides)
    return LessonContent.model_validate(data)


class FakeLLM:
    def __init__(self, content: LessonContent):
        self.content = content
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self.content


def schedule(conn, item, stage, next_review):
    db.update_item_srs(conn, item.id, stage=stage, ease=2.5, interval_days=1,
                       next_review=next_review)


def add_mistake(conn, topic, times):
    for _ in range(times):
        db.upsert_mistake(conn, language="no", category="G", topic=topic,
                          wrong="w", correct="c", explanation_tr="e", today=TODAY)


# ---------------------------------------------------------------- plan


@pytest.mark.parametrize(("mode", "reviews", "expected"), [
    ("full", 0, 5), ("full", 10, 5), ("full", 30, 8), ("busy", 0, 3), ("busy", 10, 3),
])
def test_new_item_count_respects_mode_range(mode, reviews, expected):
    assert lesson.new_item_count(mode, reviews) == expected


def test_first_day_plan_uses_seed_pool(conn):
    plan = lesson.build_plan(conn, TODAY, "full")
    assert plan.reviews == []
    assert len(plan.pool_new) == 5
    assert plan.extra_new_count == 0
    assert plan.level == "A2"


def test_busy_plan_has_three_new_items(conn):
    assert len(lesson.build_plan(conn, TODAY, "busy").pool_new) == 3


def test_empty_pool_asks_llm_for_extra_items(conn):
    for item in db.get_new_items(conn, "no", limit=100):
        schedule(conn, item, 1, TODAY + timedelta(days=5))
    plan = lesson.build_plan(conn, TODAY, "full")
    assert plan.pool_new == []
    assert plan.extra_new_count == 5


def test_due_items_get_question_type_from_stage(conn):
    a, b = db.get_new_items(conn, "no", limit=2)
    schedule(conn, a, 1, TODAY)
    schedule(conn, b, 3, TODAY - timedelta(days=1))
    plan = lesson.build_plan(conn, TODAY, "full")
    assert [(r.item.id, r.question_type) for r in plan.reviews] == [
        (b.id, "own_sentence"), (a.id, "cloze")]


def test_busy_mode_caps_reviews_at_five(conn):
    for item in db.get_new_items(conn, "no", limit=8):
        schedule(conn, item, 1, TODAY)
    assert len(lesson.build_plan(conn, TODAY, "busy").reviews) == 5


def test_recurring_mistakes_always_included_others_capped(conn):
    add_mistake(conn, "V2", 3)
    for topic in ["a", "b", "c", "d", "e"]:
        add_mistake(conn, topic, 1)
    topics = [m.topic for m in lesson.build_plan(conn, TODAY).mistakes]
    assert topics[0] == "V2"
    assert len(topics) == 1 + lesson.MAX_OTHER_MISTAKES


def test_unknown_mode_is_rejected(conn):
    with pytest.raises(ValueError):
        lesson.build_plan(conn, TODAY, "weekend")


# ---------------------------------------------------------------- prompt


def test_prompt_contains_reviews_mistakes_and_no_placeholders(conn):
    item = db.get_new_items(conn, "no", limit=1)[0]
    schedule(conn, item, 1, TODAY)
    add_mistake(conn, "V2", 3)
    prompt = lesson.render_prompt(lesson.build_plan(conn, TODAY, "busy"))
    assert f"id={item.id} | å rekke" in prompt
    assert "stage 1 → cloze" in prompt
    assert "[RECURRING] topic=V2" in prompt
    assert "Mode: busy" in prompt
    assert "$" not in prompt


# ---------------------------------------------------------------- generate + store


def test_generate_saves_lesson_and_schedules_new_items(conn):
    pool = db.get_new_items(conn, "no", limit=4)  # first 4 of the 5 pool items
    content = make_content(
        new_items=[{"kind": "word", "target": "en frokost", "meaning_tr": "kahvaltı",
                    "example": "Jeg spiser frokost.", "memory_hook": "hook"}],
        exercises=[
            {"item_id": pool[0].id, "question_type": "recognition", "instruction_tr": "?",
             "prompt": "å rekke", "expected_answer": "yetişmek"},
            {"target": "en frokost", "question_type": "recognition", "instruction_tr": "?",
             "prompt": "en frokost", "expected_answer": "kahvaltı"},
        ],
    )
    llm = FakeLLM(content)
    result = lesson.generate_lesson(conn, llm, model="m", today=TODAY, mode="full")

    assert llm.calls[0]["schema"] is LessonContent
    assert result.lesson_id is not None
    assert len(result.new_items) == 6  # 5 pool + 1 created

    frokost = db.get_item_by_target(conn, "no", "en frokost")
    assert result.content.exercises[1].item_id == frokost.id
    assert all(db.get_item(conn, i.id).next_review == TOMORROW for i in result.new_items)
    assert db.get_new_items(conn, "no", limit=100) != []  # rest of the pool still new

    # next day: yesterday's items come back as reviews, grammar is remembered
    next_plan = lesson.build_plan(conn, TOMORROW)
    assert {r.item.id for r in next_plan.reviews} == {i.id for i in result.new_items}
    assert all(r.question_type == "recognition" for r in next_plan.reviews)
    assert next_plan.recent_grammar == ["V2"]


def test_created_duplicate_of_taught_item_is_not_reintroduced(conn):
    rekke = db.get_item_by_target(conn, "no", "å rekke")
    schedule(conn, rekke, 3, TODAY + timedelta(days=7))
    content = make_content(new_items=[{
        "kind": "word", "target": "å rekke", "meaning_tr": "x", "example": "x",
        "memory_hook": "x"}])
    lesson.generate_lesson(conn, FakeLLM(content), model="m", today=TODAY)
    again = db.get_item(conn, rekke.id)
    assert again.stage == 3
    assert again.next_review == TODAY + timedelta(days=7)


def test_dry_run_without_save_leaves_db_untouched(conn):
    content = make_content(new_items=[{
        "kind": "word", "target": "en frokost", "meaning_tr": "kahvaltı",
        "example": "x", "memory_hook": "x"}])
    result = lesson.generate_lesson(conn, FakeLLM(content), model="m", today=TODAY, save=False)
    assert result.lesson_id is None
    assert db.get_item_by_target(conn, "no", "en frokost") is None
    assert db.get_due_items(conn, "no", TOMORROW) == []
    assert [i.target for i in result.new_items][-1] == "en frokost"


# ---------------------------------------------------------------- formatting


def test_lesson_messages_escape_html_and_hide_translation(conn):
    content = make_content(intro_tr="a < b & c")
    msgs = lesson_messages(content, db.get_new_items(conn, "no", limit=2))
    assert "a &lt; b &amp; c" in msgs[0]
    assert any("<b>å rekke</b>" in m for m in msgs)
    assert any("<tg-spoiler>Otobüse yetiştim.</tg-spoiler>" in m for m in msgs)


def test_exercise_message_keeps_cloze_blank():
    ex = Exercise(item_id=1, question_type="cloze", instruction_tr="Doldur",
                  prompt="Jeg ___ ikke bussen.", expected_answer="rakk")
    msg = exercise_message(ex, 2, 7)
    assert "2/7 · Boşluğu doldur" in msg
    assert to_plain_text(msg).endswith("Jeg ___ ikke bussen.")
