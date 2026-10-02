"""Thin wrapper around the Anthropic client: JSON-only calls validated by pydantic.

Every call:
- asks for structured output matching a pydantic model (`messages.parse`),
- retries once if the answer is unusable (invalid JSON, refusal, truncation, API error),
- logs token usage so costs stay visible.
"""

from __future__ import annotations

import logging
from pathlib import Path
from string import Template
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
ATTEMPTS = 2  # first try + one retry

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Raised when the LLM gives no usable answer after the retry."""


def load_prompt(name: str, **values: Any) -> str:
    """Load `src/prompts/<name>.md` and fill `$placeholders` with `values`.

    `string.Template` is used instead of str.format so JSON braces in
    prompts need no escaping.
    """
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    return Template(text).substitute(values) if values else text


class LLM:
    """Generates pydantic-validated objects from prompts."""

    def __init__(self, client: anthropic.Anthropic) -> None:
        self.client = client

    @classmethod
    def from_api_key(cls, api_key: str) -> "LLM":
        """Build with a real client; fails early with a clear message if the key is missing."""
        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set (see .env.example).")
        return cls(anthropic.Anthropic(api_key=api_key))

    def generate(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = 16000,
        effort: str | None = None,
        purpose: str = "llm",
    ) -> T:
        """Call the model and return a validated `schema` instance.

        `effort` is optional because not every model supports it (Haiku 4.5 does not).
        `purpose` only labels the usage log line (e.g. 'lesson', 'grade').
        """
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_format": schema,
        }
        if effort:
            kwargs["output_config"] = {"effort": effort}

        last_error: str = ""
        for attempt in range(1, ATTEMPTS + 1):
            try:
                response = self.client.messages.parse(**kwargs)
            except ValidationError as exc:
                last_error = f"invalid JSON: {exc.error_count()} validation error(s)"
            except anthropic.APIStatusError as exc:
                if 400 <= exc.status_code < 500 and exc.status_code not in (408, 409, 429):
                    raise LLMError(f"API rejected the request ({exc.status_code}): {exc.message}") from exc
                last_error = f"API error {exc.status_code}"
            except anthropic.APIConnectionError:
                last_error = "connection error"
            else:
                self._log_usage(purpose, model, response)
                problem = self._problem(response)
                if problem is None:
                    return response.parsed_output
                last_error = problem

            log.warning("%s: attempt %d/%d failed (%s)", purpose, attempt, ATTEMPTS, last_error)

        raise LLMError(f"{purpose}: no usable answer after {ATTEMPTS} attempts ({last_error})")

    @staticmethod
    def _problem(response: Any) -> str | None:
        """Why a response can't be used, or None if it is fine."""
        if response.stop_reason == "refusal":
            return "model refused"
        if response.stop_reason == "max_tokens":
            return "output truncated (max_tokens)"
        if response.parsed_output is None:
            return "no parsed output"
        return None

    @staticmethod
    def _log_usage(purpose: str, model: str, response: Any) -> None:
        usage = response.usage
        log.info(
            "tokens [%s] model=%s in=%d out=%d cache_read=%d cache_write=%d",
            purpose,
            model,
            usage.input_tokens,
            usage.output_tokens,
            getattr(usage, "cache_read_input_tokens", 0) or 0,
            getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )
