# Language Agent 🇳🇴

A personal language-learning agent that sends a daily Norwegian (Bokmål) lesson via Telegram,
grades answers with Claude, logs mistakes and schedules reviews with adaptive spaced repetition.

The LLM is the *pen*; the SQLite database is the *brain*: it decides what was learned yesterday
and two weeks ago, and what must come back today.

See [CLAUDE.md](CLAUDE.md) for the full project brief.

## Status

Phase 1 (MVP, Norwegian only).

- [x] 1. Project skeleton
- [x] 2. Database schema + seed items
- [x] 3. SRS logic + tests
- [x] 4. LLM wrapper + lesson prompt + pydantic models
- [x] 5. Lesson builder + terminal dry-run
- [x] 6. Telegram bot + grader
- [x] 7. Daily scheduler
- [ ] 8. End-to-end manual test (needs real keys — see checklist below)

## How it works

```
07:30 job / /lesson
  └─ lesson.build_plan      due reviews (question type from SRS stage) + open mistakes + new items
  └─ Claude (Sonnet)        writes the lesson as JSON → validated with pydantic
  └─ new items stored and scheduled for D1, lesson saved
learner answers in Telegram
  └─ Claude (Haiku)         grades the answer as JSON
  └─ grader.apply_grade     SRS reschedule, review log, mistake upsert / resolve
next morning
  └─ yesterday's items are due again, recurring mistakes (3+) are drilled
```

| Module | Role |
|---|---|
| `src/srs.py` | Pure spaced-repetition rules (D1 → D3 → D7 → D14 → D30 → D60, adapted by `ease`) |
| `src/db.py` | All SQL: schema, seed items, queries |
| `src/lesson.py` | Plan → prompt → generate/store lesson; terminal dry-run |
| `src/grader.py` | Grade an answer and apply its consequences |
| `src/tutor.py` | Use cases behind the commands (Telegram-independent) |
| `src/bot.py` | Thin Telegram handlers + daily job |
| `src/llm.py` | Anthropic wrapper: structured output, one retry, token logging |
| `src/prompts/` | Prompt templates |

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env        # macOS/Linux: cp .env.example .env
```

Fill in `.env`:

1. **Telegram bot token** — in Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot`,
   follow the steps and put the token into `TELEGRAM_BOT_TOKEN`.
2. **Anthropic API key** — create one at [platform.claude.com](https://platform.claude.com) and put it into `ANTHROPIC_API_KEY`.
3. **Your chat id** — leave `TELEGRAM_CHAT_ID` empty, run the bot (`python -m src.main`), send `/start`
   to your bot: it replies with your chat id. Put it into `TELEGRAM_CHAT_ID` and restart.
   The bot only ever answers this chat.

Optional: `DAILY_LESSON_TIME` (default `07:30`), `TIMEZONE` (default `Europe/Oslo`), `START_LEVEL` (default `A2`).

## Run

```bash
python -m src.main
```

The bot must keep running for the 07:30 lesson to arrive (e.g. leave a terminal open, or run it on
an always-on machine / small server).

### Commands

| Command | |
|---|---|
| `/start` | Welcome |
| `/lesson` | Today's lesson (full, ~30–40 min). Generated once per day, then reused/resumed |
| `/busy` | Today's lesson in busy mode (~15 min) |
| `/review` | Only the reviews that are due |
| `/skip` | Skip the current question |
| `/mistakes` | Open mistakes, recurring ones first |
| `/stats` | Streak, items per stage, due count |

Any other text is treated as the answer to the current question.

## Terminal dry-run

```bash
python -m src.lesson --plan-only        # what today's lesson will contain (no API key needed)
python -m src.lesson --mode busy        # generate a lesson with Claude, DB untouched
python -m src.lesson --save             # generate and store it (schedules new items for tomorrow)
```

Token usage is logged on every LLM call (`tokens [lesson] model=... in=... out=...`).

## Tests

```bash
pytest
```

## End-to-end checklist (step 8)

1. `python -m src.lesson --mode busy` prints a lesson in Turkish/Norwegian.
2. `python -m src.main`, then `/lesson` in Telegram: lesson messages + first question arrive.
3. Answer one question right and one wrong (e.g. `I dag jeg skal jobbe`): corrections arrive in Turkish.
4. `/mistakes` shows the wrong answer; `/stats` shows streak 1.
5. Scheduler: set `DAILY_LESSON_TIME` a few minutes ahead, restart — the lesson arrives by itself.
6. Next morning at 07:30: the lesson contains yesterday's items as reviews and drills the mistakes.
