#!/usr/bin/env bash
# SECURITY MANIFEST:
# Environment variables accessed: MENGRAM_API_KEY, MENGRAM_USER_ID (only)
# External endpoints called: https://mengram.io/v1/profile/{user_id} (only)
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

# URL-encode user_id
SAFE_USER_ID=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$USER_ID")

RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X GET "${MENGRAM_BASE_URL}/v1/profile/${SAFE_USER_ID}" \
  -H "Authorization: Bearer ${API_KEY}")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ne 200 ]; then
  echo "ERROR: Mengram API returned HTTP ${HTTP_CODE}"
  echo "$BODY"
  exit 1
fi

# Extract system_prompt field
python3 -c "
import json, sys
try:
    data = json.loads(sys.argv[1])
    profile = data.get('system_prompt', '')
    if profile:
        print(profile)
    else:
        print('No profile available yet. Start saving memories and a profile will be generated.')
except json.JSONDecodeError:
    print('Could not parse profile response.')
" "$BODY"
