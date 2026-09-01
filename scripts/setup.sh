#!/usr/bin/env bash
#
# Install or update the Kryova backend.
#
#   ./scripts/setup.sh              install, or bring an existing install up to date
#   ./scripts/setup.sh --update     the same thing, said out loud
#   ./scripts/setup.sh --no-index   skip building the reference index
#
# There is deliberately no separate "update" script. Every step below is
# idempotent, so the second run is the update: the venv is reused, pip installs
# only what changed, Alembic applies only new migrations, and the reference
# index rebuilds only when the documents on disk have actually changed. A
# separate update path is a second thing to keep correct, and it is always the
# one that rots.
#
# Nothing here is destructive. It never overwrites .env, never drops a database,
# and never deletes a document.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

BUILD_INDEX=1
for argument in "$@"; do
  case "$argument" in
    --no-index) BUILD_INDEX=0 ;;
    --update) ;;  # accepted and ignored: every run is an update
    # Print the header comment as the help text, stopping at the first line
    # that is not a comment. A fixed line range drifts the moment the header is
    # edited, and prints `set -euo pipefail` at the user.
    -h|--help)
      awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
      exit 0
      ;;
    *) echo "Unknown option: $argument" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$1" >&2; }

echo "╔════════════════════════════════════╗"
echo "║   Kryova backend — setup / update  ║"
echo "╚════════════════════════════════════╝"

# --- Python -----------------------------------------------------------------
step "Python"
PYTHON=""
for candidate in python3.12 python3.13 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    # 3.12 or newer. The codebase uses PEP 695 generics and `StrEnum`, and the
    # numpy stubs mypy reads need 3.12 as a floor.
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  fail "Python 3.12 or newer is required and was not found."
  echo "     macOS:  brew install python@3.12"
  echo "     Ubuntu: sudo apt install python3.12 python3.12-venv"
  exit 1
fi
ok "$($PYTHON --version)"

# --- Virtual environment ----------------------------------------------------
step "Virtual environment"
if [ -d venv ] && [ -x venv/bin/python ]; then
  ok "reusing ./venv"
else
  # A venv directory that exists but has no interpreter is a failed previous
  # run, not an install. Replacing it is the only way forward, and it holds
  # nothing but packages.
  [ -d venv ] && { warn "./venv is incomplete; recreating it"; rm -rf venv; }
  "$PYTHON" -m venv venv
  ok "created ./venv"
fi
VENV_PY="./venv/bin/python"

# --- Dependencies -----------------------------------------------------------
step "Dependencies"
"$VENV_PY" -m pip install --upgrade pip --quiet
if [ -f requirements-dev.txt ]; then
  "$VENV_PY" -m pip install -r requirements-dev.txt --quiet
  ok "runtime + development dependencies"
else
  "$VENV_PY" -m pip install -r requirements.txt --quiet
  ok "runtime dependencies"
fi

# --- Configuration ----------------------------------------------------------
step "Configuration"
if [ -f .env ]; then
  ok ".env already exists (left untouched)"
else
  cp .env.example .env
  ok "created .env from .env.example"
  warn "set DATABASE_URL and SECRET_KEY in .env before starting the server"
fi

# --- Database ---------------------------------------------------------------
step "Database"
if "$VENV_PY" -m alembic upgrade head 2>/tmp/kryova-alembic.log; then
  ok "migrations applied"
else
  warn "could not reach the database; skipping migrations"
  warn "$(tail -n 1 /tmp/kryova-alembic.log 2>/dev/null || echo 'see /tmp/kryova-alembic.log')"
  warn "set DATABASE_URL in .env, then re-run this script"
fi
rm -f /tmp/kryova-alembic.log

# --- Reference manuals ------------------------------------------------------
step "Reference manuals"
if [ "$BUILD_INDEX" -eq 0 ]; then
  warn "skipped (--no-index)"
else
  # `--check` exits 0 when the index is current or when there is nothing to
  # index, and non-zero when a build is needed. Building only on non-zero is
  # what makes re-running this script cheap: a corpus that has not changed is
  # not re-read, and these are hundreds of megabytes of PDFs.
  if "$VENV_PY" -m app.retrieval.build --check >/dev/null 2>&1; then
    ok "index is up to date"
  else
    echo "  documents have changed; rebuilding…"
    # stdout only. The per-file warnings go to stderr and are already summarised
    # concisely on stdout ("skipped 4: … (scanned, no text layer)"); showing
    # both means four paragraphs of duplicate advice in the middle of setup.
    if "$VENV_PY" -m app.retrieval.build 2>/dev/null | sed 's/^/  /'; then
      ok "index rebuilt"
    else
      # The assistant works without it, so this is a warning and not a failure.
      warn "could not build the index; the assistant will answer without it"
    fi
  fi
fi

# --- Done -------------------------------------------------------------------
step "Ready"
cat <<'EOF'
  Start the server:
    source venv/bin/activate
    uvicorn app.main:app --reload        # with --reload also set INLINE_JOBS=true

  Run the fast offline tests:
    venv/bin/python -m pytest tests/test_solver.py tests/test_mesh.py \
                             tests/test_retrieval.py tests/test_ai_continuity.py

  Add reference manuals:
    put PDFs in data/bm25/sources/ and re-run this script
EOF
