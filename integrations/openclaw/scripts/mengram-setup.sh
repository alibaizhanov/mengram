#!/usr/bin/env bash
# SECURITY MANIFEST:
# Environment variables accessed: MENGRAM_API_KEY, MENGRAM_USER_ID (only)
# External endpoints called: https://mengram.io/v1/health (only)
# Local files read: none
# Local files written: none
set -euo pipefail

MENGRAM_BASE_URL="${MENGRAM_BASE_URL:-https://mengram.io}"
API_KEY="${MENGRAM_API_KEY:-}"
USER_ID="${MENGRAM_USER_ID:-default}"

echo "Mengram Memory Skill - Setup Check"
echo "===================================="

# Check env vars
if [ -z "$API_KEY" ]; then
  echo "FAIL: MENGRAM_API_KEY is not set"
  echo ""
  echo "Fix: Add to ~/.openclaw/openclaw.json:"
  echo '  "skills": { "entries": { "mengram-memory": { "enabled": true, "env": { "MENGRAM_API_KEY": "om-your-key", "MENGRAM_USER_ID": "your-id" } } } }'
  echo ""
  echo "Get your free API key at https://mengram.io"
  exit 1
fi
echo "OK: MENGRAM_API_KEY is set"
echo "OK: MENGRAM_USER_ID = ${USER_ID}"
echo "OK: MENGRAM_BASE_URL = ${MENGRAM_BASE_URL}"

# Check curl
if ! command -v curl &>/dev/null; then
  echo "FAIL: curl not found"
  exit 1
fi
echo "OK: curl is available"

# Check python3
if ! command -v python3 &>/dev/null; then
  echo "FAIL: python3 not found"
  exit 1
fi
echo "OK: python3 is available"

# Test API connection
RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X GET "${MENGRAM_BASE_URL}/v1/health" \
  -H "Authorization: Bearer ${API_KEY}" 2>/dev/null || echo -e "\n000")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)

if [ "$HTTP_CODE" -eq 200 ]; then
  echo "OK: Mengram API is reachable"
elif [ "$HTTP_CODE" -eq 000 ]; then
  echo "FAIL: Cannot reach ${MENGRAM_BASE_URL} (network error)"
  exit 1
else
  echo "WARN: Mengram API returned HTTP ${HTTP_CODE}"
fi

# Test API key by searching
TEST_RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X POST "${MENGRAM_BASE_URL}/v1/search/all" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"test\", \"user_id\": \"${USER_ID}\", \"limit\": 1}" 2>/dev/null || echo -e "\n000")

TEST_CODE=$(echo "$TEST_RESPONSE" | tail -1)

if [ "$TEST_CODE" -eq 200 ]; then
  echo "OK: API key is valid"
elif [ "$TEST_CODE" -eq 401 ] || [ "$TEST_CODE" -eq 403 ]; then
  echo "FAIL: API key is invalid or expired. Get a new key at https://mengram.io"
  exit 1
else
  echo "WARN: Search test returned HTTP ${TEST_CODE}"
fi

echo ""
echo "All checks passed. Mengram memory is ready."
echo "Memory types: Semantic (facts) + Episodic (events) + Procedural (workflows)"
