"""Shared helpers for the ATS token research scripts (importable from sibling scripts)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SLUG_DIR_ENV = "JOBDIGEST_SLUG_DIR"

# File names expected inside the slug directory.
LEVER_SLUGS_FILE = "lever_slugs.txt"
ASHBY_SLUGS_FILE = "ashby_slugs.txt"
HIRE_SIGNAL_FILE = "hire_signal.txt"
WORKABLE_CAND_FILE = "workable_candidates.txt"


def add_slug_dir_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--slug-dir",
        default=None,
        help=(
            "directory holding the curated slug lists "
            f"({LEVER_SLUGS_FILE}, {ASHBY_SLUGS_FILE}, {HIRE_SIGNAL_FILE}, "
            f"{WORKABLE_CAND_FILE}); falls back to ${SLUG_DIR_ENV}"
        ),
    )


def resolve_slug_dir(value: str | None) -> Path:
    """--slug-dir wins, then $JOBDIGEST_SLUG_DIR; otherwise exit with a clear message."""
    raw = value or os.environ.get(SLUG_DIR_ENV)
    if not raw:
        sys.exit(f"error: pass --slug-dir DIR or set ${SLUG_DIR_ENV} (no default slug directory)")
    path = Path(raw).expanduser()
    if not path.is_dir():
        sys.exit(f"error: slug directory not found: {path}")
    return path


def confidence(jobs: int, notable: bool) -> str:
    if jobs > 0:
        return "high"
    return "medium" if notable else "low"
