"""Struktureret logging: én JSON-linje pr. hændelse på stdout.

Containerplatformen (Docker eller Azure) opsamler stdout, så logs kan søges og
overvåges centralt (12-factor: logs som event streams).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

_EXTRA_FIELDS = (
    "request_id",
    "client_ip",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "event_type",
    "asset_id",
    "metric",
    "severity",
    "alarm_id",
    "action",
    "error",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for name in _EXTRA_FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                entry[name] = str(value) if not isinstance(value, int | float) else value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    # Udskift kun vores egen handler, så andre handlere (fx testværktøjets) bevares
    for existing in [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)]:
        root.removeHandler(existing)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
