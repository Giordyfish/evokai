#!/usr/bin/env bash
# All Evokai gates; exits non-zero on the first failure. Usable from any CI with uv (and Node for
# the client). Postgres is optional: with EVOKAI_TEST_DATABASE_URL the suite runs on it too.
#
#   scripts/ci.sh            service + client + Python client + console gates
#   scripts/ci.sh service    service only
#   scripts/ci.sh client     client only
#   scripts/ci.sh python     the Python client only (clients/python, its own uv project)
#   scripts/ci.sh ui         the UI components only (builds the client first)
#   scripts/ci.sh console    the admin console only
#   scripts/ci.sh worker     the capability worker only (workers/capability, its own uv project)
#   NPM="npx -y npm@10" scripts/ci.sh   (another npm)
set -euo pipefail
cd "$(dirname "$0")/.."
what="${1:-all}"

repo() {
  echo "== repo: no em dash in tracked files"
  if git grep -n $'\xe2\x80\x94' -- . ':!LICENSE'; then
    echo "em dash found: use a comma, colon, parentheses or two sentences" >&2
    return 1
  fi
  echo "== repo: no secrets in the committed tree (gitleaks)"
  scripts/check-secrets.sh
}

service() {
  echo "== service: sync"
  uv sync --locked --all-extras --dev
  echo "== service: ruff"
  uv run --no-sync ruff check src tests migrations
  uv run --no-sync ruff format --check src tests migrations
  echo "== service: the assistant's docs skill is current"
  uv run --no-sync python scripts/assistant_docs.py --check
  echo "== service: ty"
  uv run --no-sync ty check
  # A test stuck for 2 minutes prints every thread's stack (which test, where), and the suite is stopped after 20:
  # a hang fails the gate with its cause in the log instead of holding the build until its own timeout.
  local pytest_hang=(-o faulthandler_timeout=120)
  # The suite runs on every core (pytest-xdist): each test has its own database schema and settings.
  pytest_hang+=(-n "${EVOKAI_TEST_WORKERS:-auto}")
  echo "== service: pytest (SQLite)"
  EVOKAI_TEST_DATABASE_URL= timeout --kill-after=30 1200 uv run --no-sync pytest -q "${pytest_hang[@]}"
  if [[ -n "${EVOKAI_TEST_DATABASE_URL:-}" ]]; then
    echo "== service: pytest (Postgres)"
    timeout --kill-after=30 1200 uv run --no-sync pytest -q "${pytest_hang[@]}"
  fi
  echo "== service: OpenAPI schema is current"
  uv run --no-sync evokai openapi --out /tmp/evokai-openapi.json
  diff -q /tmp/evokai-openapi.json clients/typescript/openapi.json
}

client() {
  local dir=clients/typescript
  if [[ ! -f "$dir/package.json" ]]; then
    echo "== client: none"
    return
  fi
  echo "== client: install"
  (cd "$dir" && ${NPM:-npm} ci --no-audit --no-fund)
  echo "== client: lint, typecheck, test, build"
  (cd "$dir" && ${NPM:-npm} run lint && ${NPM:-npm} run typecheck && ${NPM:-npm} test && ${NPM:-npm} run build)
}

python_client() {
  local dir=clients/python
  echo "== python: sync"
  (cd "$dir" && uv sync --locked)
  echo "== python: ruff"
  (cd "$dir" && uv run --no-sync ruff check src tests && uv run --no-sync ruff format --check src tests)
  echo "== python: ty"
  (cd "$dir" && uv run --no-sync ty check)
  echo "== python: pytest"
  (cd "$dir" && uv run --no-sync pytest -q)
  echo "== python: build"
  (cd "$dir" && uv build)
}

ui() {
  local dir=clients/ui
  if [[ ! -f clients/typescript/dist/browser/index.js ]]; then
    echo "== ui: build the client first"
    (cd clients/typescript && ${NPM:-npm} ci --no-audit --no-fund && ${NPM:-npm} run build)
  fi
  echo "== ui: install"
  (cd "$dir" && ${NPM:-npm} ci --no-audit --no-fund)
  echo "== ui: lint, typecheck, test, build (with the size budget)"
  (cd "$dir" && ${NPM:-npm} run lint && ${NPM:-npm} run typecheck && ${NPM:-npm} test && ${NPM:-npm} run build)
}

worker() {
  local dir=workers/capability
  echo "== worker: sync (kubernetes; the browser extra shares the lock)"
  (cd "$dir" && uv sync --locked --extra kubernetes)
  echo "== worker: ruff, pytest"
  (cd "$dir" && uv run --no-sync ruff check src tests && uv run --no-sync ruff format --check src tests)
  (cd "$dir" && uv run --no-sync pytest -q)
}

console() {
  local dir=admin
  echo "== console: install"
  (cd "$dir" && ${NPM:-npm} ci --no-audit --no-fund)
  echo "== console: typecheck, test, build"
  (cd "$dir" && ${NPM:-npm} run typecheck && ${NPM:-npm} test && ${NPM:-npm} run build)
  test -f "$dir/dist/index.html"
}

case "$what" in
  all) repo; service; client; python_client; ui; console; worker ;;
  service) repo; service ;;
  client) client ;;
  python) python_client ;;
  ui) ui ;;
  console) console ;;
  worker) worker ;;
  *) echo "usage: $0 [all|service|client|python|ui|console|worker]" >&2; exit 2 ;;
esac
echo "== all gates green"
