#!/usr/bin/env bash
# Local Linux dev: venv + requirements + Flask app (TVW).
# From this directory. shared_mix_core: app/shared_mix_core, sibling, or upward
# discovery (core/shared_mix_path.py); PYTHONPATH optional for custom installs.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
export PYTHONUTF8="${PYTHONUTF8:-1}"
export TVW_ENABLE_LAB_MODULES="${TVW_ENABLE_LAB_MODULES:-1}"
export FLASK_USE_RELOADER="${FLASK_USE_RELOADER:-0}"

pick_py() {
  if command -v python3.13 &>/dev/null; then echo python3.13; return; fi
  if command -v python3.12 &>/dev/null; then echo python3.12; return; fi
  if command -v python3 &>/dev/null; then echo python3; return; fi
  echo python
}
PY="$(pick_py)"

if [[ ! -f app.py ]]; then echo "ERROR: app.py not found in $DIR"; exit 1; fi
if [[ ! -f requirements.txt ]]; then echo "ERROR: requirements.txt not found"; exit 1; fi

if [[ ! -x venv/bin/python ]]; then
  echo "Creating venv with $PY..."
  "$PY" -m venv venv
fi
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
./venv/bin/python -c "import flask" >/dev/null
exec ./venv/bin/python app.py
