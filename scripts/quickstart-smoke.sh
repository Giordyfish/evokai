#!/usr/bin/env bash
# The quick start, end to end, as a newcomer runs it: build and start docker-compose.yml, read the operator key from
# the logs, create an application and an agent on the test model, mint a session token and chat once.
#
#   scripts/quickstart-smoke.sh            # uses the compose project "evokai-smoke" and removes it at the end
#   KEEP=1 scripts/quickstart-smoke.sh     # leaves it running
#
# Needs Docker with Compose v2, curl and python3. CI runs it on every push (.github/workflows/ci.yml).
set -euo pipefail

cd "$(dirname "$0")/.."
project=${PROJECT:-evokai-smoke}
port=${EVOKAI_HOST_PORT:-18787}
base="http://localhost:$port/agent"
compose=(docker compose -p "$project")
export EVOKAI_HOST_PORT=$port EVOKAI_PUBLIC_URL=$base

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then "${compose[@]}" logs --tail 80 evokai || true; fi
  [ -n "${KEEP:-}" ] || "${compose[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT

json() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }

"${compose[@]}" up -d --build --wait --wait-timeout 300

operator=$("${compose[@]}" logs evokai 2>/dev/null | sed -n 's/.*Operator key: *\(evk_op_[A-Za-z0-9_-]*\).*/\1/p' | head -1)
[ -n "$operator" ] || { echo "no operator key in the logs" >&2; exit 1; }

curl -fsS "$base/health" >/dev/null
curl -fsS "$base/admin/" | grep -qi '<html' || { echo "the console does not load" >&2; exit 1; }

app=$(curl -fsS -X POST "$base/v1/operator/applications" -H "Authorization: Bearer $operator" \
  -H 'Content-Type: application/json' -d '{"slug": "smoke", "name": "Smoke"}')
server_key=$(json "['serverKey']" <<<"$app")
admin_key=$(json "['adminKey']" <<<"$app")

curl -fsS -X PUT "$base/v1/admin/agents/assistant" -H "Authorization: Bearer $admin_key" \
  -H 'Content-Type: application/json' \
  -d '{"name": "assistant", "model": "test:canned", "instructions": "Answer briefly."}' >/dev/null

token=$(curl -fsS -X POST "$base/v1/session-tokens" -H "Authorization: Bearer $server_key" \
  -H 'Content-Type: application/json' -d '{"subject": "user-1", "agent": "assistant"}' | json "['token']")

reply=$(curl -fsS -N --max-time 60 -X POST "$base/v1/session/chat" -H "Authorization: Bearer $token" \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' -d '{"text": "Hello"}')
# The stream must carry answer text and end with the turn's done event.
python3 -c '
import json, sys
events = [json.loads(l[5:]) for l in sys.stdin.read().splitlines() if l.startswith("data:") and l[5:].strip().startswith("{")]
types = [e.get("type") for e in events]
assert "text" in types and types[-1] == "done", types
' <<<"$reply" || { echo "the chat did not answer:" >&2; echo "$reply" | tail -20 >&2; exit 1; }

echo "quick start: OK (health, console, application, agent, session token, one chat turn)"
