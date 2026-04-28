#!/usr/bin/env bash
set -euo pipefail

# Prefer a local venv python if present
if [ -x ".venv/bin/python" ]; then
  PY=.venv/bin/python
else
  PY=$(command -v python3 || true)
fi

if [ -z "${PY:-}" ]; then
  echo "Python not found. Install Python 3 to run the Python downloader."
  exit 1
fi

exec "$PY" src/download_from_csv.py "$@"
