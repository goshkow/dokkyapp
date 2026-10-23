#!/usr/bin/env bash
# Запуск Докки из исходников на macOS: scripts/run_dev.sh [порт]
set -euo pipefail

cd "$(dirname "$0")/.."
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install -e ".[dev]"
export KODDI_PORT="${1:-8765}"
export PYTHONPATH="$PWD/backend"
exec python -m koddi.desktop.web_host
