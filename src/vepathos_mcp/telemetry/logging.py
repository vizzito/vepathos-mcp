"""Structured JSON logs with redaction.

Logs carry operational fields only (request id, trace id, optimization id, hashed account key, tool,
outcome, latency). Tokens, credentials, coordinates and payloads are never logged.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

LOGGER_NAME = "vepathos_mcp"

_SENSITIVE_KEYS = re.compile(
    r"^(.*[_-])?(authorization|token|secret|password|passwd|credential|cookie|api[_-]?key|service[_-]?key)$",
    re.I,
)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/:=-]+")
_VPT_SECRET = re.compile(r"vpt_sk_(test|live)_[0-9a-f]+")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")

REDACTED = "[redacted]"


def redact_text(text: str) -> str:
    text = _BEARER.sub("Bearer " + REDACTED, text)
    text = _VPT_SECRET.sub(REDACTED, text)
    return _JWT.sub(REDACTED, text)


def redact(value: Any, key: str | None = None) -> Any:
    if key is not None and _SENSITIVE_KEYS.search(key):
        return REDACTED
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": redact_text(record.getMessage()),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            entry.update(redact(fields))
        if record.exc_info and record.levelno >= logging.ERROR:
            # Exception type only: messages and tracebacks may contain request data.
            entry["exception"] = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    # stderr: stdout carries the protocol in stdio mode; containers capture both streams.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers[:] = [handler]
    logger.setLevel(level.upper())
    logger.propagate = False
    # Third-party request logs would duplicate our structured events (and print URLs); keep warnings only.
    for noisy in ("httpx", "httpcore", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    logging.getLogger(LOGGER_NAME).log(level, event, extra={"fields": fields})
