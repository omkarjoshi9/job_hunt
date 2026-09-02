#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH=src
export JOBDIGEST_LOG_LEVEL="${JOBDIGEST_LOG_LEVEL:-DEBUG}"

if command -v python >/dev/null 2>&1; then
  PY=python
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  echo "python or python3 not found" >&2
  exit 1
fi

"$PY" -m jobdigest collect --ats-only --db data/jobs.db
"$PY" -m jobdigest digest --db data/jobs.db
