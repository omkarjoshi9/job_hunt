from __future__ import annotations

import os
from pathlib import Path

from jobdigest.paths import repo_root


def _parse_value(raw: str) -> str:
    val = raw.strip()
    if not val:
        return ""
    if val[0] in ("'", '"'):
        quote = val[0]
        end = val.find(quote, 1)
        if end != -1:
            return val[1:end]
        return val
    for idx, ch in enumerate(val):
        if ch == "#" and idx > 0 and val[idx - 1].isspace():
            return val[:idx].rstrip()
    return val


def parse_dotenv_line(raw: str) -> tuple[str, str] | None:
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, _, val = line.partition("=")
    key = key.strip()
    if key.startswith("export") and key[6:7].isspace():
        key = key[6:].strip()
    if not key:
        return None
    return key, _parse_value(val)


def load_dotenv(path: Path | None = None) -> None:
    env_path = path if path is not None else repo_root() / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        parsed = parse_dotenv_line(raw)
        if parsed is None:
            continue
        key, val = parsed
        if key not in os.environ:
            os.environ[key] = val
