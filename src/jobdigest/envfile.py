from __future__ import annotations

import os
from pathlib import Path

from jobdigest.db import repo_root


def load_dotenv(path: Path | None = None) -> None:
    env_path = path if path is not None else repo_root() / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val
