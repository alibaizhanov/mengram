#!/usr/bin/env bash
# Stop hook — persist the conversation transcript to Mengram for fact /
# episode / procedure extraction. Fires when Claude Code finishes its turn.
# Reads transcript via CLAUDE_TRANSCRIPT_PATH if Claude exposes it; otherwise
# silently no-ops (Stop hook fires regardless, but without a transcript path
# there is nothing to save).
set -u

KEY="${MENGRAM_API_KEY:-}"
if [ -z "$KEY" ]; then
  exit 0
fi

# Claude Code passes the transcript JSONL path via this env var on Stop.
# If it's missing or empty we exit cleanly.
TRANSCRIPT="${CLAUDE_TRANSCRIPT_PATH:-}"
if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
  exit 0
fi

URL="${MENGRAM_URL:-https://mengram.io}/v1/add_text"

# Pull the last ~8000 chars of the transcript — enough to capture this turn's
# user message + Claude's response without bloating extraction tokens. The
# extraction pipeline on the backend will dedupe against existing facts.
TEXT=$(tail -c 8000 "$TRANSCRIPT" 2>/dev/null) || exit 0
if [ -z "$TEXT" ]; then
  exit 0
fi

# Send as plain text. /v1/add_text wraps it as a user message and runs full
# extraction (entities + facts + episodes + procedures). source=claude_code
# lets us filter analytics later without affecting retrieval ranking.
# Fire-and-forget: 8s budget, errors swallowed.
curl -fsS --max-time 8 \
  -X POST "$URL" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -H "User-Agent: mengram-plugin/0.1.0" \
  -d "$(printf '%s' "$TEXT" | python3 -c '
import json, sys
text = sys.stdin.read()
print(json.dumps({"text": text, "source": "claude_code"}))
' 2>/dev/null)" >/dev/null 2>&1 || true
