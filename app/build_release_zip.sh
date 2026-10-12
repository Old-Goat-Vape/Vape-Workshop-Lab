#!/usr/bin/env bash
# Build a clean Pauly-ready TVW release tree and TVW_Calculator_Release.zip (Linux/macOS).
# Creates ../release/TVW_Calculator/app/ from this directory, copies shared_mix_core, verifies, zips.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_SOURCE="$SCRIPT_DIR"
REPO_ROOT="$(cd "$APP_SOURCE/.." && pwd)"
RELEASE_ROOT="$REPO_ROOT/release"
RELEASE_TREE="$RELEASE_ROOT/TVW_Calculator"
RELEASE_APP="$RELEASE_TREE/app"
ZIP_PATH="$RELEASE_ROOT/TVW_Calculator_Release.zip"

info() { echo "[INFO] $*"; }
ok() { echo "[OK] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

find_shared_mix_core() {
  local calc_root parent try
  calc_root="$(cd "$APP_SOURCE/.." && pwd)"
  parent="$(cd "$calc_root/.." && pwd)"
  for try in "$parent/shared_mix_core" "$calc_root/shared_mix_core" "$APP_SOURCE/shared_mix_core"; do
    if [[ -f "$try/__init__.py" ]]; then echo "$try"; return 0; fi
  done
  local cur="$calc_root"
  local i=0
  while [[ $i -lt 12 ]]; do
    try="$cur/shared_mix_core"
    if [[ -f "$try/__init__.py" ]]; then echo "$try"; return 0; fi
    next="$(cd "$cur/.." && pwd)"
    [[ "$next" == "$cur" ]] && break
    cur="$next"
    i=$((i + 1))
  done
  return 1
}

[[ -f "$APP_SOURCE/app.py" ]] || fail "app.py not found. Run from The_Vape_Workshop_Calculator/app/"

info "App source: $APP_SOURCE"
info "Release app: $RELEASE_APP"

SHARED_MIX_SOURCE="$(find_shared_mix_core)" || fail "shared_mix_core not found (need __init__.py). Place beside repo or under app/."
info "shared_mix_core source: $SHARED_MIX_SOURCE"

rm -rf "$RELEASE_TREE"
mkdir -p "$RELEASE_APP"

RSYNC_EXCLUDES=(
  "--exclude=venv/"
  "--exclude=.venv/"
  "--exclude=__pycache__/"
  "--exclude=backups/"
  "--exclude=logs/"
  "--exclude=backup/"
  "--exclude=_archive/"
  "--exclude=.pytest_cache/"
  "--exclude=.mypy_cache/"
  "--exclude=.ruff_cache/"
  "--exclude=htmlcov/"
  "--exclude=.git/"
  "--exclude=.github/"
  "--exclude=.env"
  "--exclude=*.pyc"
  "--exclude=*.pyo"
  "--exclude=*.pyd"
  "--exclude=*.log"
  "--exclude=*.bak"
  "--exclude=*.backup"
  "--exclude=*_backup.db"
  "--exclude=*.sqlite-shm"
  "--exclude=*.sqlite-wal"
  "--exclude=.coverage"
  "--exclude=*.tmp"
  "--exclude=*.swp"
)

info "Copying app (rsync)..."
rsync -a "${RSYNC_EXCLUDES[@]}" "$APP_SOURCE/" "$RELEASE_APP/"

rm -rf "$RELEASE_APP/shared_mix_core"
info "Copying shared_mix_core -> app/shared_mix_core/..."
mkdir -p "$RELEASE_APP/shared_mix_core"
rsync -a \
  --exclude=__pycache__/ \
  --exclude=.git/ \
  --exclude=*.pyc \
  --exclude=*.pyo \
  --exclude=.env \
  "$SHARED_MIX_SOURCE/" "$RELEASE_APP/shared_mix_core/"

verify_file() {
  local rel="$1"
  [[ -f "$RELEASE_APP/$rel" ]] || { fail "Missing: app/$rel"; return 1; }
  ok "app/$rel"
}

verify_file "app.py"
verify_file "requirements.txt"
verify_file "shared_mix_core/__init__.py"
verify_file "core/shared_mix_path.py"
verify_file "Start_TVW_Calculator.bat"

rm -f "$ZIP_PATH"
info "Creating zip: $ZIP_PATH"
(
  cd "$RELEASE_ROOT" || exit 1
  zip -r -q "$(basename "$ZIP_PATH")" "TVW_Calculator"
)

[[ -f "$ZIP_PATH" ]] || fail "Zip was not created."
ok "Release build succeeded."
info "Zip: $ZIP_PATH ($(wc -c < "$ZIP_PATH" | tr -d ' ') bytes)"
info "Unpacked layout: $RELEASE_TREE/app/"
exit 0
