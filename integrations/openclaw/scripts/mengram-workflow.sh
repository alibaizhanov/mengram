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

WORKFLOW="${1:-}"
if [ -z "$WORKFLOW" ]; then
  echo "Usage: mengram-workflow.sh \"description of completed workflow with steps\""
  echo "Example: mengram-workflow.sh \"Deployed app: 1) Ran tests 2) Built Docker image 3) Pushed to registry 4) Updated Kubernetes\""
  exit 1
fi

# Sanitize for JSON
SAFE_WORKFLOW=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$WORKFLOW")

# Send as assistant message so extraction pipeline identifies it as a completed workflow
RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X POST "${MENGRAM_BASE_URL}/v1/add" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"messages\": [{\"role\": \"assistant\", \"content\": \"I completed the following workflow: \"}, {\"role\": \"assistant\", \"content\": ${SAFE_WORKFLOW}}], \"user_id\": \"${USER_ID}\"}")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ne 200 ] && [ "$HTTP_CODE" -ne 202 ]; then
  echo "ERROR: Mengram API returned HTTP ${HTTP_CODE}"
  echo "$BODY"
  exit 1
fi

echo "Workflow saved as a procedure. It will appear in future memory searches with success/failure tracking."
