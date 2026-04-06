#!/usr/bin/env bash
# SECURITY MANIFEST:
# Environment variables accessed: MENGRAM_API_KEY (only)
# External endpoints called: https://mengram.io/v1/add (only)
# Local files read: none
# Local files written: none
set -euo pipefail

MENGRAM_BASE_URL="${MENGRAM_BASE_URL:-https://mengram.io}"
API_KEY="${MENGRAM_API_KEY:-}"

if [ -z "$API_KEY" ]; then
  echo "ERROR: MENGRAM_API_KEY not set. Get your free key at https://mengram.io"
  exit 1
fi

TEXT="${1:-}"
if [ -z "$TEXT" ]; then
  echo "Usage: mengram-add.sh \"text to remember\""
  exit 1
fi

# Sanitize input for JSON
SAFE_TEXT=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$TEXT")

RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X POST "${MENGRAM_BASE_URL}/v1/add" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"messages\": [{\"role\": \"user\", \"content\": ${SAFE_TEXT}}]}")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -eq 402 ]; then
  UPGRADE_URL=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('detail',{}).get('upgrade_url','https://mengram.io/dashboard'))" "$BODY" 2>/dev/null || echo "https://mengram.io/dashboard")
  PLAN=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('detail',{}).get('plan','free'))" "$BODY" 2>/dev/null || echo "free")
  LIMIT=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('detail',{}).get('limit',30))" "$BODY" 2>/dev/null || echo "30")
  echo "IMPORTANT — TELL THE USER: Memory limit reached. The ${PLAN} plan allows ${LIMIT} adds/month and they are all used up. New memories are NOT being saved. Ask the user to upgrade at: ${UPGRADE_URL}"
  exit 1
elif [ "$HTTP_CODE" -ne 200 ] && [ "$HTTP_CODE" -ne 202 ]; then
  echo "ERROR: Mengram API returned HTTP ${HTTP_CODE}"
  echo "$BODY"
  exit 1
fi

echo "Saved to memory. Mengram will extract facts, events, and workflows in background."
