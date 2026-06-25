#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${VOX_PYTHON_BIN:-./.venv/Scripts/python.exe}"
if [[ ! -x "$PYTHON_BIN" && -x "./venv/bin/python" ]]; then
  PYTHON_BIN="./venv/bin/python"
fi
if [[ ! -x "$PYTHON_BIN" && -x "./.venv/bin/python" ]]; then
  PYTHON_BIN="./.venv/bin/python"
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "VOX Python environment was not found."
  echo "Set VOX_PYTHON_BIN or create .venv/venv first."
  exit 1
fi

echo "============================================================"
echo "  VOX - Background Worker"
echo "============================================================"
echo

exec "$PYTHON_BIN" worker.py
