"""Structured logging configuration for TUM Lecture Finder.

Call :func:`setup_logging` once at application startup.  The output format
is controlled by the ``TLF_JSON_LOGS`` environment variable:

- ``TLF_JSON_LOGS=1`` → structured JSON (production / Docker)
- unset or ``0``       → coloured console output (development)

All stdlib loggers (uvicorn, httpx, etc.) are routed through structlog so
every log line shares the same format and context.
"""

from __future__ import annotations

import logging
import os
import sys

import structlog


def _reorder_keys(
    _logger: object,
    _method: str,
    event_dict: dict[str, object],
) -> dict[str, object]:
    """Move timestamp, level, and event to the front of the event dict.

    This ensures JSON output reads naturally: timestamp first, then severity,
    then the event name, then any extra fields.
    """
    ordered: dict[str, object] = {}
    for key in ("timestamp", "level", "event"):
        if key in event_dict:
            ordered[key] = event_dict.pop(key)
    ordered.update(event_dict)
    return ordered


def setup_logging(*, json_logs: bool | None = None) -> None:
    """Configure structlog and stdlib logging.

    Args:
        json_logs: ``True`` for JSON output, ``False`` for console.
            Defaults to reading ``TLF_JSON_LOGS`` env var.

    """
    if json_logs is None:
        json_logs = os.environ.get("TLF_JSON_LOGS", "0") == "1"

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_logs:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                _reorder_keys,
                renderer,
            ],
            foreign_pre_chain=shared_processors,
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)

    # Route uvicorn error logs through structlog; suppress access logs
    # (our request middleware provides a structured equivalent).
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    # Suppress noisy httpx per-request logging (our own middleware and
    # endpoint handlers provide structured equivalents).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
