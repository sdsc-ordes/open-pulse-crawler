#!/usr/bin/env bash
# Integration test for the docker-compose stack.
# Builds all three services (api, gui, nginx) and runs basic HTTP checks.
#
# Usage:
#   bash tests/test_integration.sh              # uses port 18080
#   OPC_PORT=9090 bash tests/test_integration.sh  # custom port
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

PROJECT_NAME="opc-test-$$"
TEST_PORT="${OPC_PORT:-18080}"
COMPOSE="docker compose -p $PROJECT_NAME"
CREATED_ENV=""

cleanup() {
    echo "==> Tearing down..."
    OPC_PORT="$TEST_PORT" $COMPOSE down -v --remove-orphans 2>/dev/null || true
    [ -n "$CREATED_ENV" ] && rm -f "$CREATED_ENV"
}
trap cleanup EXIT

# Ensure a .env file exists with the required variables.
if [ ! -f .env ]; then
    cat > .env <<'ENVEOF'
GITHUB_TOKEN=ghp_dummy
API_TOKEN=integration-test-token
ENVEOF
    CREATED_ENV=".env"
    echo "==> Created temporary .env"
fi

echo "==> Building and starting stack (port $TEST_PORT)..."
OPC_PORT="$TEST_PORT" $COMPOSE up -d --build --wait

BASE="http://localhost:$TEST_PORT"
pass=0
fail=0

check() {
    local desc="$1" url="$2" expected="$3" method="${4:-GET}" auth_token="${5:-}"
    local code
    local -a curl_args
    curl_args=(-s -o /dev/null -w "%{http_code}" -X "$method")
    if [ -n "$auth_token" ]; then
        curl_args+=(-H "Authorization: Bearer $auth_token")
    fi
    code=$(curl "${curl_args[@]}" "$url") || true
    if [ "$code" = "$expected" ]; then
        echo "  PASS  $desc  ($code)"
        ((pass++))
    else
        echo "  FAIL  $desc  (got $code, expected $expected)"
        ((fail++))
    fi
}

echo "==> Running integration checks against $BASE ..."
echo ""

check "GET  /api/v1/health (public)"                    "$BASE/api/v1/health" "200"
check "POST /api/v1/crawl  (no auth -> 403)"             "$BASE/api/v1/crawl"  "403" "POST"
check "GET  /api/v1/crawl/nonexistent (no auth -> 403)"  "$BASE/api/v1/crawl/no-such-id" "403"
check "GET  /api/v1/crawl/nonexistent (authed -> 404)"   "$BASE/api/v1/crawl/no-such-id" "404" "GET" "integration-test-token"
check "GET  /               (Streamlit GUI)"              "$BASE/"              "200"

echo ""
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
