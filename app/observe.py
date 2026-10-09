"""Structured events for crawl, classification, and operator actions.

Secrets are removed before a line is printed or written. A failed log write
is reported on stderr and does not stop the job that tried to log.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.database import DATA_DIR

_SECRET_NAMES = ("GEMINI_API_KEY",)
_SECRET_HINTS = ("key", "token", "password", "secret", "authorization")


def _scrub(value: object) -> object:
    if not isinstance(value, str):
        return value
    cleaned = value
    for name in _SECRET_NAMES:
        secret = os.environ.get(name, "")
        if secret and secret in cleaned:
            cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned


def _log_path() -> Path:
    configured = os.environ.get("OBSERVE_LOG", "").strip()
    if configured:
        return Path(configured)
    return DATA_DIR / "logs" / "events.jsonl"


def log_event(event: str, **fields: object) -> None:
    record: dict[str, object] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event,
    }
    for key, value in fields.items():
        if any(hint in key.lower() for hint in _SECRET_HINTS):
            record[key] = "[redacted]"
        else:
            record[key] = _scrub(value)
    line = json.dumps(record, ensure_ascii=False, default=str)
    if os.environ.get("OBSERVE_ECHO", "1") != "0":
        print(line, flush=True)
    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        print(f"log write failed: {exc}", file=sys.stderr)
