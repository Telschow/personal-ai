"""Structured logging.

Rules enforced everywhere in this codebase:
- Never log CV contents, cover letters, job descriptions, prompts, or any
  personal data beyond operationally necessary identifiers (job_id is fine).
- Never log secrets, API keys, tokens or authentication material.
- Log structured key=value fields (source, operation, job_id, duration,
  status, error) so scans are diagnosable without dumping content.
"""

from __future__ import annotations

import logging
import sys
import time
from contextlib import ContextDecorator
from typing import Any

_LOGGER_NAME = "job_agent"

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(level: int = logging.INFO, verbosity: int = 0) -> None:
    """Set up a single console handler. ``verbosity`` raises the level."""
    logger = logging.getLogger(_LOGGER_NAME)
    if verbosity >= 2:
        level = logging.DEBUG
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
    logger.propagate = False


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child logger (e.g. ``job_agent.sources``)."""
    return logging.getLogger(".".join(p for p in [_LOGGER_NAME, name] if p))


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit one structured log line: ``event=... key=value ...``.

    All values are rendered with safe str conversion; callers must never pass
    document/prompt/secret content here.
    """
    parts = [f"event={event}"] + [f"{k}={_fmt(v)}" for k, v in fields.items()]
    logger.info(" ".join(parts))


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "none"
    text = str(value)
    if "\n" in text or len(text) > 200:
        return "<redacted-long>"
    return text


class timed(ContextDecorator):
    """Context manager recording elapsed seconds into a provided dict."""

    def __init__(self, log: logging.Logger, event: str, store: dict[str, float]) -> None:
        self._log = log
        self._event = event
        self._store = store

    def __enter__(self) -> timed:
        self._t0 = time.monotonic()
        return self

    def __exit__(self, *exc: object) -> None:
        self._store[self._event] = time.monotonic() - self._t0
        self._log.info("event=%s duration_ms=%.0f", self._event, self._store[self._event] * 1000)
