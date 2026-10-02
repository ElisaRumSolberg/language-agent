# CLAUDE.md — Language Learning Agent (Norwegian + English → B2)

This file is the project brief. Read it fully before writing any code.

## 1. What we are building

A personal language-learning agent that sends **one learner** a daily lesson via **Telegram**, checks her answers, tracks her mistakes and brings old material back with **adaptive spaced repetition**.

The agent's real intelligence is not generating new content. It is knowing **what she learned yesterday and two weeks ago, and what she needs to see again today.** The LLM is the "pen"; the SQLite database is the "brain".

Core loop:

```
plan → recall old items → teach new items → practice → check answers
→ log mistakes → reschedule (SRS) → measure → adapt next lesson
```

## 2. The learner

- Native language: **Turkish**. All explanations, grammar logic and feedback are in Turkish.
- Lives in **Bergen, Norway**. Mother. Computer engineering student. Time is limited.
- **Priority 1: Norwegian (Bokmål)**, everyday life → B2. Occasionally note Bergen dialect (bergensk) differences.
- **Priority 2: English**, two parallel tracks (Phase 2): `daily` and `tech` (programming, Git, code explanation, professional email, interviews).
- Starting level: set in `config` (default `A2`), updated after placement.
- Learning preferences (must be respected in every lesson):
  - Every new word gets a **memory hook** (association/image) and, where useful, a link to the English cognate (e.g. *vindu* ↔ *window*).
  - Always explain the **logic** of a sentence, not only the rule (e.g. Norwegian V2: the verb is always the 2nd element: *I dag **skal** jeg jobbe*).
  - Organized, direct, encouraging feedback. No fluff.

## 3. Development approach (important)

- Build in **phases**. Each phase must end with something that works end to end.
- **Only build Phase 1 now.** Do not start later phases without asking.
- Small steps. After each step, give a **short explanation in Turkish** of what was done and *why* (the learner is a CS student and wants to understand the code logic).
- Ask before adding dependencies not listed below.
- Commit after every working step with a clear English message (Conventional Commits: `feat:`, `fix:`, `test:`, `docs:`).

## 4. Tech stack

- Python 3.11+
- SQLite (via `sqlite3` standard library; keep SQL explicit and readable)
- `python-telegram-bot` (v21+, async) — delivery and replies
- `anthropic` SDK — lesson generation and answer grading
- `APScheduler` (or the job queue in python-telegram-bot) — daily lesson at a configured time (Europe/Oslo)
- `pydantic` — validate LLM JSON output
- `python-dotenv` — secrets
- `pytest` — tests

Secrets in `.env` (never commit; provide `.env.example`):

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-5-5
ANTHROPIC_GRADER_MODEL=claude-haiku-4-5-20251001
DAILY_LESSON_TIME=07:30
TIMEZONE=Europe/Oslo
```

## 5. Project structure

```
language-agent/
├── CLAUDE.md
├── README.md
├── .env.example
├── requirements.txt
├── data/                  # agent.db lives here (gitignored)
├── src/
│   ├── main.py            # starts bot + scheduler
│   ├── config.py
│   ├── db.py              # connection, schema creation, queries
│   ├── srs.py             # pure SRS logic (no I/O) — heavily tested
│   ├── lesson.py          # builds daily lesson: due items + mistakes + new items
│   ├── grader.py          # sends answer to LLM, parses result
│   ├── llm.py             # thin wrapper around Anthropic client
│   ├── prompts/           # prompt templates as .md files
│   └── bot.py             # Telegram handlers/commands
└── tests/
    ├── test_srs.py
    └── test_lesson.py
```

## 6. Data model

```sql
CREATE TABLE items (
  id            INTEGER PRIMARY KEY,
  language      TEXT NOT NULL CHECK (language IN ('no','en')),
  track         TEXT NOT NULL DEFAULT 'daily' CHECK (track IN ('daily','tech','both')),
  kind          TEXT NOT NULL CHECK (kind IN ('word','chunk','grammar')),
  target        TEXT NOT NULL,          -- "å rekke", "Det kommer an på ..."
  meaning_tr    TEXT NOT NULL,
  forms         TEXT,                   -- "rekker – rakk – har rukket"
  example       TEXT,
  memory_hook   TEXT,
  stage         INTEGER NOT NULL DEFAULT 0,   -- 0..6, see SRS
  ease          REAL    NOT NULL DEFAULT 2.5,
  interval_days INTEGER NOT NULL DEFAULT 0,
  next_review   DATE,
  created_at    DATE NOT NULL,
  UNIQUE (language, target)
);

CREATE TABLE reviews (
  id         INTEGER PRIMARY KEY,
  item_id    INTEGER NOT NULL REFERENCES items(id),
  reviewed_at TIMESTAMP NOT NULL,
  question_type TEXT NOT NULL,
  correct    INTEGER NOT NULL,          -- 0/1
  user_answer TEXT
);

CREATE TABLE mistakes (
  id          INTEGER PRIMARY KEY,
  language    TEXT NOT NULL,
  category    TEXT NOT NULL CHECK (category IN ('G','V','WO','SP','N','R')),
  topic       TEXT NOT NULL,            -- e.g. "V2", "fordi/derfor"
  wrong       TEXT NOT NULL,
  correct     TEXT NOT NULL,
  explanation_tr TEXT NOT NULL,
  count       INTEGER NOT NULL DEFAULT 1,
  resolved    INTEGER NOT NULL DEFAULT 0,
  last_seen   DATE NOT NULL
);

CREATE TABLE lessons (
  id        INTEGER PRIMARY KEY,
  date      DATE NOT NULL,
  language  TEXT NOT NULL,
  mode      TEXT NOT NULL CHECK (mode IN ('full','busy')),
  topic     TEXT,
  content_json TEXT NOT NULL,
  score     REAL
);
```

Error categories: **G** grammar, **V** vocabulary, **WO** word order, **SP** spelling, **N** naturalness, **R** register.

## 7. Spaced repetition (`srs.py`)

Base schedule after first learning (D0): **D1 → D3 → D7 → D14 → D30 → D60**.

Adaptive rules:
- Correct → move to next stage; interval grows (multiply by `ease`, ease +0.1, max 3.0).
- Wrong → drop back 1–2 stages; interval shrinks (next review tomorrow), ease −0.2 (min 1.3).
- The schedule above is the default; ease makes it personal.

**The question type depends on the stage** (passive → active):

| Stage | Question type | Example (`å rekke`) |
|---|---|---|
| 0 | recognition | *å rekke = ?* |
| 1 | cloze | *Jeg ___ ikke bussen.* |
| 2 | TR → target translation | "Otobüse yetişemiyorum." |
| 3 | own sentence | Write your own sentence with *å rekke*. |
| 4 | contextual answer | *Hvorfor kom du for sent?* (use *rekke*) |
| 5–6 | hidden use | Item is required in a free writing task without being named. |

Keep `srs.py` pure (input: item state + result + today; output: new state). Write unit tests for every rule.

## 8. Daily lesson (`lesson.py`)

Composition (Norwegian, Phase 1):
1. **Due reviews** (`next_review <= today`), max 10.
2. **Unresolved mistakes** — any with `count >= 3` is a **recurring error** and must appear.
3. **New items**: full mode 5–8, busy mode 3. Roughly 70% review / 30% new.
4. One small grammar point with its **logic** explained in Turkish and a ✅/❌ comparison.
5. A short text (3–5 sentences) using today's items.

Modes:
- `full` ≈ 30–40 min (default).
- `busy` ≈ 15 min: 4 min reviews, 3 min new words, 3 min grammar/sentence, 2 min reading, 3 min writing one sentence.

The LLM must return **JSON only**, validated with pydantic. New items in the response are inserted into `items`. Prompt templates live in `src/prompts/`. Telegram messages: split into short, readable messages; use simple Markdown; emojis sparingly (🇳🇴 ✅ ❌ 💡).

## 9. Answer checking (`grader.py`)

The learner replies in Telegram. The grader returns JSON:

```json
{
  "item_id": 12,
  "correct": false,
  "corrected": "I morgen skal jeg jobbe.",
  "category": "WO",
  "topic": "V2",
  "explanation_tr": "Norveççede çekimli fiil her zaman 2. sıradadır..."
}
```

Then: update `items` via `srs.py`, log to `reviews`, upsert `mistakes` (same language+topic → `count += 1`). A mistake becomes `resolved` after 3 consecutive correct uses of that topic.

## 10. Telegram commands (Phase 1)

- `/start` — register chat, short welcome in Turkish
- `/lesson` — today's lesson now (full)
- `/busy` — today's lesson in busy mode
- `/review` — only due reviews
- `/mistakes` — open mistakes, recurring ones first
- `/stats` — streak, items per stage, due count

Only respond to `TELEGRAM_CHAT_ID`.

## 11. Phase 1 tasks (MVP — Norwegian only)

1. Project skeleton, `requirements.txt`, `.env.example`, `.gitignore`, `README.md`.
2. `db.py`: schema + basic queries. Seed 10 starter A2 Norwegian items.
3. `srs.py` + full tests.
4. `llm.py` + lesson prompt + pydantic models.
5. `lesson.py`: build lesson from DB; dry-run command that prints a lesson to the terminal (no Telegram).
6. `bot.py`: commands above, answer flow with grader.
7. Scheduler: daily lesson at `DAILY_LESSON_TIME`.
8. End-to-end manual test, then update README with setup steps.

**Done when:** the learner receives a lesson at 07:30, answers in Telegram, gets corrections in Turkish, and the next day's lesson contains yesterday's items and mistakes.

## 12. Later phases (do not build yet)

- **Phase 2 — English, two tracks**: `track` = daily/tech/both; same grammar point shown in both contexts (e.g. present perfect: *I've just moved to Bergen* / *I've pushed the fix*). Ratio by level: A2–B1 70/30, B1+ 50/50, B2 40/60 (daily/tech).
- **Phase 3 — Audio**: TTS for listening texts, voice messages → STT for speaking feedback, shadowing.
- **Phase 4 — Measurement & visuals**: weekly review + test, monthly assessment, CEFR roadmap, dashboard, PDF/A4 export, visual grammar pages.

## 13. Code style

- Type hints everywhere, small functions, docstrings in English.
- No business logic inside Telegram handlers; handlers call `lesson.py` / `grader.py`.
- Handle LLM failures gracefully (retry once, then send a friendly error).
- Log token usage per call to keep costs visible.
