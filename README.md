# Language Agent 🇳🇴

A personal language-learning agent that sends a daily Norwegian (Bokmål) lesson via Telegram,
grades answers with Claude, logs mistakes and schedules reviews with adaptive spaced repetition.

See [CLAUDE.md](CLAUDE.md) for the full project brief.

## Status

Phase 1 (MVP, Norwegian only) — in progress.

- [x] 1. Project skeleton
- [x] 2. Database schema + seed items
- [x] 3. SRS logic + tests
- [x] 4. LLM wrapper + lesson prompt + pydantic models
- [x] 5. Lesson builder + terminal dry-run
- [ ] 6. Telegram bot + grader
- [ ] 7. Daily scheduler
- [ ] 8. End-to-end test

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env          # then fill in the secrets
```

## Terminal dry-run

```bash
python -m src.lesson --plan-only        # show what today's lesson will contain (no API key needed)
python -m src.lesson --mode busy        # generate a lesson with Claude, DB untouched
python -m src.lesson --save             # generate and store it (schedules new items for tomorrow)
```

## Tests

```bash
pytest
```
