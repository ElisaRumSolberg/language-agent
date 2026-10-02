"""Tests for the LLM wrapper and schemas, using a fake Anthropic client (no network)."""

import logging
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.llm import LLM, LLMError, load_prompt
from src.schemas import GradeResult, LessonContent

VALID_LESSON = {
    "topic": "Morgenrutine",
    "intro_tr": "Bugün sabah rutini.",
    "new_items": [{
        "kind": "word", "target": "en frokost", "meaning_tr": "kahvaltı",
        "forms": "en frokost – frokosten", "example": "Jeg spiser frokost klokka sju.",
        "memory_hook": "fro(m) + kost → İng. *breakfast* değil ama 'erken yemek'.",
    }],
    "grammar": {
        "title": "V2", "logic_tr": "Fiil 2. sırada.",
        "correct_example": "I dag skal jeg jobbe.", "wrong_example": "I dag jeg skal jobbe.",
        "why_tr": "Zaman zarfı başa gelince özne fiilin arkasına geçer.",
    },
    "reading": {"text": "Jeg rakk ikke bussen.", "translation_tr": "Otobüse yetişemedim."},
    "exercises": [{
        "item_id": 1, "question_type": "cloze", "instruction_tr": "Boşluğu doldur.",
        "prompt": "Jeg ___ ikke bussen.", "expected_answer": "rakk",
    }],
}


def usage() -> SimpleNamespace:
    return SimpleNamespace(input_tokens=1200, output_tokens=800,
                           cache_read_input_tokens=0, cache_creation_input_tokens=0)


def ok_response(parsed) -> SimpleNamespace:
    return SimpleNamespace(parsed_output=parsed, stop_reason="end_turn", usage=usage())


def validation_error() -> ValidationError:
    try:
        LessonContent.model_validate({})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a ValidationError")


class FakeMessages:
    """Returns (or raises) the queued outcomes in order and records the calls."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_llm(*outcomes) -> tuple[LLM, FakeMessages]:
    messages = FakeMessages(outcomes)
    return LLM(SimpleNamespace(messages=messages)), messages


def call(llm: LLM, **overrides):
    kwargs = dict(model="m", system="s", user="u", schema=LessonContent, purpose="lesson")
    kwargs.update(overrides)
    return llm.generate(**kwargs)


# ---------------------------------------------------------------- schemas


def test_valid_lesson_json_validates():
    lesson = LessonContent.model_validate(VALID_LESSON)
    assert lesson.exercises[0].question_type == "cloze"
    assert lesson.mistake_notes_tr == []


def test_unknown_question_type_is_rejected():
    bad = {**VALID_LESSON, "exercises": [{**VALID_LESSON["exercises"][0], "question_type": "essay"}]}
    with pytest.raises(ValidationError):
        LessonContent.model_validate(bad)


def test_grade_result_example_from_brief_validates():
    grade = GradeResult.model_validate({
        "item_id": 12, "correct": False, "corrected": "I morgen skal jeg jobbe.",
        "category": "WO", "topic": "V2", "explanation_tr": "Fiil 2. sırada.",
    })
    assert grade.category == "WO"
    assert grade.topics_used_correctly == []


# ---------------------------------------------------------------- wrapper


def test_success_returns_parsed_object_and_passes_schema():
    lesson = LessonContent.model_validate(VALID_LESSON)
    llm, messages = make_llm(ok_response(lesson))
    assert call(llm) is lesson
    assert messages.calls[0]["output_format"] is LessonContent
    assert "output_config" not in messages.calls[0]  # no effort -> not sent (Haiku-safe)


def test_effort_is_passed_when_given():
    llm, messages = make_llm(ok_response(LessonContent.model_validate(VALID_LESSON)))
    call(llm, effort="medium")
    assert messages.calls[0]["output_config"] == {"effort": "medium"}


@pytest.mark.parametrize("first_failure", [
    validation_error(),
    SimpleNamespace(parsed_output=None, stop_reason="refusal", usage=usage()),
    SimpleNamespace(parsed_output=None, stop_reason="max_tokens", usage=usage()),
    SimpleNamespace(parsed_output=None, stop_reason="end_turn", usage=usage()),
])
def test_retries_once_then_succeeds(first_failure):
    lesson = LessonContent.model_validate(VALID_LESSON)
    llm, messages = make_llm(first_failure, ok_response(lesson))
    assert call(llm) is lesson
    assert len(messages.calls) == 2


def test_raises_after_second_failure():
    llm, messages = make_llm(validation_error(), validation_error())
    with pytest.raises(LLMError, match="2 attempts"):
        call(llm)
    assert len(messages.calls) == 2


def test_token_usage_is_logged(caplog):
    llm, _ = make_llm(ok_response(LessonContent.model_validate(VALID_LESSON)))
    with caplog.at_level(logging.INFO, logger="src.llm"):
        call(llm)
    assert "tokens [lesson] model=m in=1200 out=800" in caplog.text


def test_missing_api_key_fails_clearly():
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        LLM.from_api_key("")


# ---------------------------------------------------------------- prompts


def test_lesson_prompts_load_and_fill():
    assert "Turkish" in load_prompt("lesson_system")
    user = load_prompt(
        "lesson_user", date="2026-10-02", level="A2", mode="busy", mode_budget="~15 min",
        review_items="-", pool_items="-", extra_new_count=0, mistakes="-",
        recent_grammar="-", known_targets="-",
    )
    assert "Mode: busy" in user
    assert "$" not in user  # every placeholder filled
