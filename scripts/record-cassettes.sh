#!/usr/bin/env bash
# Record the HTTP cassettes of tests/test_recorded.py again (docs/deployment.md#recorded-http-cassettes).
# The suite only replays them; recording is this explicit step, never part of CI.
#
#   scripts/record-cassettes.sh mcp       the MCP cassette, against the reference SDK's server on
#                                         127.0.0.1:8765 (no credentials, nothing leaves the machine)
#   scripts/record-cassettes.sh bedrock   the Bedrock cassettes: real calls to Claude Haiku 4.5 in
#                                         eu-north-1 with tiny prompts (three calls, a fraction of a
#                                         cent); needs credentials with Bedrock access, for example
#                                         AWS_PROFILE=<profile> scripts/record-cassettes.sh bedrock
#   scripts/record-cassettes.sh speech    the speech engines of tests/test_speech_recorded.py: ElevenLabs
#                                         and OpenAI speech and transcription of one short sentence
#                                         (a fraction of a cent); needs ELEVENLABS_API_KEY and
#                                         OPENAI_API_KEY
#
# Afterwards the suite replays what was recorded with the network unused, and the cassettes must
# pass a secrets scan (gitleaks dir tests/cassettes) before they are committed.
set -euo pipefail
cd "$(dirname "$0")/.."
dir=tests/cassettes/test_recorded

case "${1:-}" in
  mcp)
    port=8765
    uv run --no-project --with "mcp>=1.20,<2" python scripts/cassettes/mcp_reference_server.py \
      --port "$port" &
    server=$!
    trap 'kill "$server" 2>/dev/null || true' EXIT
    for _ in $(seq 1 120); do
      curl -s -o /dev/null --max-time 1 "http://127.0.0.1:$port/mcp" && break
      sleep 0.5
    done
    rm -f "$dir"/test_mcp_*.yaml
    uv run --no-sync pytest -q tests/test_recorded.py -k mcp --record-mode=new_episodes
    kill "$server" 2>/dev/null || true
    ;;
  bedrock)
    if [[ -z "${AWS_PROFILE:-}${AWS_ACCESS_KEY_ID:-}" ]]; then
      echo "Bedrock credentials needed: set AWS_PROFILE (or export the keys) first." >&2
      exit 2
    fi
    if command -v aws >/dev/null; then
      aws sts get-caller-identity --query Account --output text >/dev/null  # expired login: stop here
    fi
    rm -f "$dir"/test_bedrock_*.yaml
    # The seeds go in place before pytest starts: the vcr fixture loads a cassette before the test's own
    # fixtures run, so a seed copied by the test would be read too late and the throttled primary would
    # really be called. new_episodes then keeps the seed's hand-written interactions.
    cp tests/cassettes/seeds/test_bedrock_*.yaml "$dir"/
    uv run --no-sync pytest -q tests/test_recorded.py -k bedrock --record-mode=new_episodes
    ;;
  speech)
    if [[ -z "${ELEVENLABS_API_KEY:-}" || -z "${OPENAI_API_KEY:-}" ]]; then
      echo "Speech keys needed: export ELEVENLABS_API_KEY and OPENAI_API_KEY first." >&2
      exit 2
    fi
    rm -f tests/cassettes/test_speech_recorded/*.yaml
    uv run --no-sync pytest -q tests/test_speech_recorded.py --record-mode=new_episodes
    ;;
  *)
    echo "usage: $0 mcp|bedrock|speech" >&2
    exit 2
    ;;
esac

echo "== replay, offline"
if [[ "${1:-}" == speech ]]; then
  env -u ELEVENLABS_API_KEY -u OPENAI_API_KEY uv run --no-sync pytest -q tests/test_speech_recorded.py
else
  uv run --no-sync pytest -q tests/test_recorded.py
fi
if command -v gitleaks >/dev/null; then
  gitleaks dir tests/cassettes --no-banner
else
  echo "== gitleaks is not installed: scan tests/cassettes before committing them" >&2
fi
