#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ $# -eq 0 ]]; then
  echo 'Usage: ./run_mac.sh [--passages] "product query"' >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is not installed. Install Python 3 first." >&2
  exit 1
fi

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

PYTHON=".venv/bin/python"
DEPS_MARKER=".venv/.requirements-installed"

if [[ ! -f "$DEPS_MARKER" || "requirements.txt" -nt "$DEPS_MARKER" ]]; then
  "$PYTHON" -m pip install --upgrade pip
  "$PYTHON" -m pip install -r requirements.txt
  touch "$DEPS_MARKER"
fi

if [[ ! -f ".env" ]]; then
  echo "Warning: .env not found. Set YANDEX_API_KEY and YANDEX_FOLDER_ID." >&2
fi

"$PYTHON" main.py "$@"
