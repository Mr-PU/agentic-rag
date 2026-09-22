#!/usr/bin/env bash
# End-to-end smoke test for the full Postgres/Redis/MinIO/Qdrant/Ollama stack.
# Run this AFTER `docker compose up --build` (or after the k8s deployment) has
# finished starting everything.
#
# Usage: ./scripts/smoke_test.sh [base_url]
# Default base_url: http://localhost:8000

set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
SAMPLE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/samples/leave_policy.txt"

pass() { echo "  ✅ $1"; }
fail() { echo "  ❌ $1"; exit 1; }

echo "== 1. Health check =="
HEALTH_JSON=$(curl -sS -m 10 "$BASE_URL/health") || fail "could not reach $BASE_URL/health"
echo "  $HEALTH_JSON"
echo "$HEALTH_JSON" | grep -q '"status":"ok"' \
  && pass "API, Ollama, Qdrant, Redis, and MinIO are all reachable" \
  || echo "  ⚠️  status is not 'ok' yet — something may still be starting. Continuing anyway."

echo
echo "== 2. Ingest sample document (async: returns immediately, processes in the background) =="
[ -f "$SAMPLE_FILE" ] || fail "sample file not found at $SAMPLE_FILE"
INGEST_JSON=$(curl -sS -m 30 -X POST "$BASE_URL/api/ingest" -F "file=@${SAMPLE_FILE}") \
  || fail "ingest request failed"
echo "  $INGEST_JSON"
DOC_ID=$(echo "$INGEST_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['doc_id'])") \
  || fail "ingest response missing doc_id"
pass "document submitted, doc_id=$DOC_ID"

echo
echo "== 3. Poll until the worker finishes processing it =="
STATUS="pending"
for i in $(seq 1 60); do
  DOC_JSON=$(curl -sS -m 10 "$BASE_URL/api/documents/$DOC_ID") || fail "status poll failed"
  STATUS=$(echo "$DOC_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
  echo "  [$i] status=$STATUS"
  if [ "$STATUS" = "ready" ] || [ "$STATUS" = "failed" ]; then
    break
  fi
  sleep 3
done
[ "$STATUS" = "ready" ] || fail "document did not reach 'ready' status (last status: $STATUS) — check 'docker compose logs worker'"
pass "worker finished processing (chunked, embedded, indexed)"

echo
echo "== 4. List documents =="
DOCS_JSON=$(curl -sS -m 10 "$BASE_URL/api/documents") || fail "documents request failed"
echo "$DOCS_JSON" | grep -q "leave_policy" && pass "sample document shows up in the index" \
  || fail "sample document not found in document list"

echo
echo "== 5. Ask a question (runs the full agent loop: planner -> retrieval -> critic -> generator) =="
echo "  This can take 30-120s on first run while models load into memory..."
CHAT_JSON=$(curl -sS -m 180 -X POST "$BASE_URL/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"query": "How many days of annual leave can an employee carry forward to next year?"}') \
  || fail "chat request failed"
echo "  $CHAT_JSON"
echo "$CHAT_JSON" | grep -q '"answer"' && pass "agent returned an answer" || fail "chat response missing answer"
echo "$CHAT_JSON" | grep -qi "10" && pass "answer appears to contain the correct figure (10 days)" \
  || echo "  ⚠️  answer didn't obviously mention '10' — inspect the output above manually."

echo
echo "== 6. Clean up sample document =="
curl -sS -m 10 -X DELETE "$BASE_URL/api/documents/$DOC_ID" > /dev/null \
  && pass "sample document removed (id=$DOC_ID)"

echo
echo "All smoke tests passed. The full stack is working end-to-end."
