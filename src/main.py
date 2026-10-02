"""Entry point: `python -m src.main` starts the Telegram bot and the daily scheduler."""

from __future__ import annotations

import logging
import sys

from src import db
from src.bot import build_application
from src.config import load_config
from src.llm import LLM, LLMError
from src.tutor import Tutor


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # PTB polls every few seconds
    logging.getLogger("httpx2").setLevel(logging.WARNING)


def main() -> None:
    setup_logging()
    cfg = load_config()
    if not cfg.telegram_bot_token:
        sys.exit("TELEGRAM_BOT_TOKEN eksik (.env.example'a bak).")
    try:
        llm = LLM.from_api_key(cfg.anthropic_api_key)
    except LLMError as exc:
        sys.exit(str(exc))

    conn = db.connect(cfg.db_path)
    db.init_db(conn)
    tutor = Tutor(conn, llm, cfg)
    db.seed_items(conn, tutor.today())

    app = build_application(tutor, cfg)
    logging.getLogger(__name__).info("bot started, polling…")
    app.run_polling()


if __name__ == "__main__":
    main()
