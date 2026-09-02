from __future__ import annotations

import logging
import os
import sys

_LOGGER_NAME = "jobdigest"
_CONFIGURED = False


def configure() -> None:
    """Send jobdigest logs to stderr (and optional file). Call once from CLI."""
    global _CONFIGURED
    raw = (os.environ.get("JOBDIGEST_LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, raw, None)
    if not isinstance(level, int):
        level = logging.INFO

    root = logging.getLogger(_LOGGER_NAME)
    root.setLevel(level)
    root.propagate = False

    if _CONFIGURED:
        for handler in root.handlers:
            handler.setLevel(level)
        return

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    stream = logging.StreamHandler(sys.stderr)
    stream.setLevel(level)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    path = (os.environ.get("JOBDIGEST_LOG_FILE") or "").strip()
    if path:
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")
