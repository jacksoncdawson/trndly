#!/usr/bin/env bash
set -euo pipefail

# Bootstrap the venv: pip deps + playwright browser binaries.
# Run from the trndly/ inner package dir after creating .venv.

VENV="${VENV:-.venv}"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "error: $VENV/bin/python not found. create it first:" >&2
  echo "  python3.11 -m venv $VENV" >&2
  exit 1
fi

"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r requirements.txt
"$VENV/bin/python" -m playwright install chromium
