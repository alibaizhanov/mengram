#!/usr/bin/env bash
# SECURITY MANIFEST:
# Environment variables accessed: MENGRAM_API_KEY, MENGRAM_USER_ID (only)
# External endpoints called: https://mengram.io/v1/add (only)
# Local files read: none
# Local files written: none
set -euo pipefail

MENGRAM_BASE_URL="${MENGRAM_BASE_URL:-https://mengram.io}"
API_KEY="${MENGRAM_API_KEY:-}"
USER_ID="${MENGRAM_USER_ID:-default}"

if [ -z "$API_KEY" ]; then
  echo "ERROR: MENGRAM_API_KEY not set. Get your free key at https://mengram.io"
  exit 1
fi

TEXT="${1:-}"
if [ -z "$TEXT" ]; then
  echo "Usage: mengram-add.sh \"text to remember\""
  exit 1
fi

# Sanitize text for JSON
SAFE_TEXT=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$TEXT")

RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X POST "${MENGRAM_BASE_URL}/v1/add" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"messages\": [{\"role\": \"user\", \"content\": ${SAFE_TEXT}}], \"user_id\": \"${USER_ID}\"}")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ne 200 ] && [ "$HTTP_CODE" -ne 202 ]; then
  echo "ERROR: Mengram API returned HTTP ${HTTP_CODE}"
  echo "$BODY"
  exit 1
fi

echo "Saved to memory. Mengram will extract facts, events, and procedures automatically."
