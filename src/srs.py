"""Pure spaced-repetition logic (no I/O).

Base schedule after first learning (D0): D1 -> D3 -> D7 -> D14 -> D30 -> D60.
These are days counted from D0, so the *gaps* between reviews are
1, 2, 4, 7, 16, 30 days. Each stage 0..5 owns one gap; stage 6 is "mastered"
and its gap keeps growing by `ease`.

`ease` personalises the schedule: each base gap is scaled by ease / 2.5.
A learner who always answers correctly drifts slightly above the base
schedule (D1, D3, D7, D15, D33, D68, ...); mistakes lower ease and shrink gaps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

MAX_STAGE = 6
BASE_GAPS: tuple[int, ...] = (1, 2, 4, 7, 16, 30)  # gap after reaching stage 0..5
DEFAULT_EASE = 2.5
EASE_STEP_UP = 0.1
EASE_STEP_DOWN = 0.2
MIN_EASE = 1.3
MAX_EASE = 3.0
MAX_GAP_DAYS = 365
BIG_DROP_FROM_STAGE = 3  # wrong answer at stage >= 3 drops 2 stages, otherwise 1

QUESTION_TYPES: dict[int, str] = {
    0: "recognition",
    1: "cloze",
    2: "translation",
    3: "own_sentence",
    4: "contextual",
    5: "hidden_use",
    6: "hidden_use",
}


@dataclass(frozen=True)
class SrsState:
    """The SRS-relevant part of an item."""

    stage: int
    ease: float
    interval_days: int
    next_review: date | None


def question_type_for_stage(stage: int) -> str:
    """Passive -> active: which kind of question to ask at a given stage."""
    return QUESTION_TYPES[max(0, min(stage, MAX_STAGE))]


def introduce(today: date) -> SrsState:
    """State of an item taught for the first time today (D0): review tomorrow."""
    return SrsState(
        stage=0,
        ease=DEFAULT_EASE,
        interval_days=BASE_GAPS[0],
        next_review=today + timedelta(days=BASE_GAPS[0]),
    )


def _gap_for(stage: int, ease: float, previous_gap: int) -> int:
    """Days until the next review after reaching `stage` with the given ease."""
    if stage < len(BASE_GAPS):
        gap = round(BASE_GAPS[stage] * ease / DEFAULT_EASE)
    else:  # mastered: grow from the previous gap
        gap = round(previous_gap * ease)
    gap = max(gap, previous_gap + 1)  # a correct answer never shortens the gap
    return min(max(gap, 1), MAX_GAP_DAYS)


def review(state: SrsState, correct: bool, today: date) -> SrsState:
    """Return the new state after one answer.

    Correct: next stage, gap grows (scaled by the current ease), ease +0.1 (max 3.0).
    Wrong:   drop 1 stage (2 if the item was mature, stage >= 3), review
             tomorrow, ease -0.2 (min 1.3).
    The next review is counted from `today`, so late reviews are handled naturally.
    """
    if correct:
        new_stage = min(state.stage + 1, MAX_STAGE)
        gap = _gap_for(new_stage, state.ease, state.interval_days)
        new_ease = min(state.ease + EASE_STEP_UP, MAX_EASE)
    else:
        drop = 2 if state.stage >= BIG_DROP_FROM_STAGE else 1
        new_stage = max(state.stage - drop, 0)
        gap = 1
        new_ease = max(state.ease - EASE_STEP_DOWN, MIN_EASE)

    return SrsState(
        stage=new_stage,
        ease=round(new_ease, 2),
        interval_days=gap,
        next_review=today + timedelta(days=gap),
    )
