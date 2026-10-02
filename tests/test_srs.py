"""Unit tests for the pure SRS rules in src/srs.py."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from src import srs
from src.srs import SrsState

D0 = date(2026, 10, 2)


def days_after_d0(d: date) -> int:
    return (d - D0).days


# ---------------------------------------------------------------- introduce


def test_introduce_schedules_first_review_tomorrow():
    state = srs.introduce(D0)
    assert state == SrsState(stage=0, ease=2.5, interval_days=1,
                             next_review=D0 + timedelta(days=1))


# ---------------------------------------------------------------- correct path


def test_always_correct_follows_base_schedule_then_drifts_up():
    """Early reviews hit D1, D3, D7 exactly; rising ease then stretches gaps."""
    state = srs.introduce(D0)
    review_days = [days_after_d0(state.next_review)]
    for _ in range(6):
        state = srs.review(state, correct=True, today=state.next_review)
        review_days.append(days_after_d0(state.next_review))

    assert review_days[:3] == [1, 3, 7]
    assert review_days == [1, 3, 7, 15, 33, 68, 173]
    assert state.stage == srs.MAX_STAGE


def test_correct_moves_to_next_stage():
    state = SrsState(stage=2, ease=2.5, interval_days=4, next_review=D0)
    assert srs.review(state, True, D0).stage == 3


def test_correct_increases_ease_by_0_1():
    state = SrsState(stage=1, ease=2.5, interval_days=2, next_review=D0)
    assert srs.review(state, True, D0).ease == 2.6


def test_ease_is_capped_at_3():
    state = SrsState(stage=1, ease=3.0, interval_days=2, next_review=D0)
    assert srs.review(state, True, D0).ease == 3.0


def test_low_ease_shrinks_gap_below_base():
    """Base gap for stage 2 is 4 days; ease 1.5 gives round(4 * 0.6) = 2."""
    state = SrsState(stage=1, ease=1.5, interval_days=1, next_review=D0)
    assert srs.review(state, True, D0).interval_days == 2


def test_correct_answer_never_shortens_gap():
    state = SrsState(stage=1, ease=1.3, interval_days=5, next_review=D0)
    assert srs.review(state, True, D0).interval_days == 6


def test_mastered_item_stays_at_max_stage_and_gap_multiplies_by_ease():
    state = SrsState(stage=6, ease=2.5, interval_days=60, next_review=D0)
    new = srs.review(state, True, D0)
    assert new.stage == 6
    assert new.interval_days == 150


def test_gap_is_capped_at_one_year():
    state = SrsState(stage=6, ease=3.0, interval_days=200, next_review=D0)
    assert srs.review(state, True, D0).interval_days == srs.MAX_GAP_DAYS


# ---------------------------------------------------------------- wrong path


@pytest.mark.parametrize(
    ("stage", "expected"),
    [(0, 0), (1, 0), (2, 1), (3, 1), (4, 2), (6, 4)],
)
def test_wrong_drops_one_stage_or_two_if_mature(stage, expected):
    state = SrsState(stage=stage, ease=2.5, interval_days=10, next_review=D0)
    assert srs.review(state, False, D0).stage == expected


def test_wrong_schedules_review_tomorrow():
    state = SrsState(stage=4, ease=2.5, interval_days=16, next_review=D0)
    new = srs.review(state, False, D0)
    assert new.interval_days == 1
    assert new.next_review == D0 + timedelta(days=1)


def test_wrong_decreases_ease_by_0_2():
    state = SrsState(stage=2, ease=2.5, interval_days=4, next_review=D0)
    assert srs.review(state, False, D0).ease == 2.3


@pytest.mark.parametrize("ease", [1.4, 1.3])
def test_ease_has_floor_1_3(ease):
    state = SrsState(stage=2, ease=ease, interval_days=4, next_review=D0)
    assert srs.review(state, False, D0).ease == 1.3


def test_recovery_after_mistake_uses_shorter_gaps():
    """Same stage, but a forgotten item (lower ease) comes back sooner."""
    fresh = SrsState(stage=2, ease=2.7, interval_days=4, next_review=D0)
    forgotten = srs.review(replace(fresh, stage=3), False, D0)  # -> stage 1, ease 2.5
    forgotten = srs.review(forgotten, True, D0)                 # -> stage 2
    forgotten = srs.review(forgotten, True, D0)                 # -> stage 3
    assert forgotten.stage == 3
    assert forgotten.interval_days < srs.review(fresh, True, D0).interval_days


# ---------------------------------------------------------------- timing


def test_late_review_is_counted_from_today():
    state = SrsState(stage=1, ease=2.5, interval_days=2, next_review=D0)
    late = D0 + timedelta(days=5)
    assert srs.review(state, True, late).next_review == late + timedelta(days=4)


def test_review_does_not_mutate_input():
    state = SrsState(stage=1, ease=2.5, interval_days=2, next_review=D0)
    srs.review(state, False, D0)
    assert state == SrsState(stage=1, ease=2.5, interval_days=2, next_review=D0)


# ---------------------------------------------------------------- question types


@pytest.mark.parametrize(
    ("stage", "qtype"),
    [(0, "recognition"), (1, "cloze"), (2, "translation"), (3, "own_sentence"),
     (4, "contextual"), (5, "hidden_use"), (6, "hidden_use")],
)
def test_question_type_by_stage(stage, qtype):
    assert srs.question_type_for_stage(stage) == qtype


def test_question_type_clamps_out_of_range_stage():
    assert srs.question_type_for_stage(-1) == "recognition"
    assert srs.question_type_for_stage(9) == "hidden_use"
