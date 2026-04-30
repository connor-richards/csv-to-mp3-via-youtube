#!/usr/bin/env bash
set -euo pipefail

# Resolve script directory so the script works from any working directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# Prefer a local venv python if present (ensures updated yt-dlp is used)
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PY="$ROOT_DIR/.venv/bin/python"
elif [ -x ".venv/bin/python" ]; then
  PY=.venv/bin/python
else
  PY=$(command -v python3 || true)
fi

if [ -z "${PY:-}" ]; then
  echo "Python not found. Install Python 3 to run the Python downloader."
  exit 1
fi

exec "$PY" src/download_from_csv.py "$@"
