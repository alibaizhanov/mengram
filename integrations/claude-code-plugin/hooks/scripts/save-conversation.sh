#!/usr/bin/env bash
# Stop hook — persist the conversation transcript to Mengram for fact /
# episode / procedure extraction. Fires when Claude Code finishes its turn.
# Reads transcript via CLAUDE_TRANSCRIPT_PATH if Claude exposes it; otherwise
# silently no-ops (Stop hook fires regardless, but without a transcript path
# there is nothing to save).
set -u

# Credentials: env var → ~/.mengram/config.json → give up silently.
# sed (not python3) for the config parse: on Windows Git Bash, python3 can be
# the Microsoft Store stub that passes `command -v` but can't run (issue #55).
KEY="${MENGRAM_API_KEY:-}"
BASE="${MENGRAM_URL:-}"
CFG="$HOME/.mengram/config.json"
if [ -f "$CFG" ]; then
  [ -z "$KEY" ] && KEY=$(sed -n 's/.*"api_key"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$CFG" | head -1)
  [ -z "$BASE" ] && BASE=$(sed -n 's/.*"base_url"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$CFG" | head -1)
fi
BASE="${BASE:-https://mengram.io}"
if [ -z "$KEY" ]; then
  exit 0
fi

# Claude Code passes the transcript JSONL path via this env var on Stop.
# If it's missing or empty we exit cleanly.
TRANSCRIPT="${CLAUDE_TRANSCRIPT_PATH:-}"
if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
  exit 0
fi

URL="${BASE%/}/v1/add_text"

# Pull the last ~8000 chars of the transcript — enough to capture this turn's
# user message + Claude's response without bloating extraction tokens. The
# extraction pipeline on the backend will dedupe against existing facts.
TEXT=$(tail -c 8000 "$TRANSCRIPT" 2>/dev/null) || exit 0
if [ -z "$TEXT" ]; then
  exit 0
fi

# JSON-encode the payload. Probe tools by actually running them — the Windows
# Store python3 stub passes `command -v` but exits non-zero on real use, which
# previously made this hook silently send an empty body (issue #55).
_json_payload() {
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$TEXT" | jq -Rs '{"text": ., "source": "claude_code"}' 2>/dev/null && return 0
  fi
  for PY in python3 python; do
    if "$PY" -c "import json" >/dev/null 2>&1; then
      printf '%s' "$TEXT" | "$PY" -c 'import json,sys; print(json.dumps({"text": sys.stdin.read(), "source": "claude_code"}))' 2>/dev/null && return 0
    fi
  done
  return 1
}
PAYLOAD=$(_json_payload) || exit 0
if [ -z "$PAYLOAD" ]; then
  exit 0
fi

# Send as plain text. /v1/add_text wraps it as a user message and runs full
# extraction (entities + facts + episodes + procedures). source=claude_code
# lets us filter analytics later without affecting retrieval ranking.
# 8s budget, errors swallowed.
if curl -fsS --max-time 8 \
  -X POST "$URL" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -H "User-Agent: mengram-plugin/0.1.2" \
  -d "$PAYLOAD" >/dev/null 2>&1; then
  # Opt-in heartbeat: every N *successful* saves, surface one "still working"
  # line. Absence of the heartbeat then means something. Enable via
  # MENGRAM_HEARTBEAT=N env or "heartbeat": N in ~/.mengram/config.json.
  HB="${MENGRAM_HEARTBEAT:-}"
  if [ -z "$HB" ] && [ -f "$CFG" ]; then
    HB=$(sed -n 's/.*"heartbeat"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$CFG" | head -1)
  fi
  case "$HB" in (*[!0-9]*|"") HB="";; esac
  if [ -n "$HB" ] && [ "$HB" -gt 0 ]; then
    CNT_FILE="$HOME/.mengram/.save-count"
    CNT=$(cat "$CNT_FILE" 2>/dev/null || echo 0)
    case "$CNT" in (*[!0-9]*|"") CNT=0;; esac
    CNT=$((CNT + 1))
    echo "$CNT" > "$CNT_FILE" 2>/dev/null
    if [ $((CNT % HB)) -eq 0 ]; then
      printf '{"systemMessage": "[mengram] heartbeat: %s conversations saved to memory so far — everything is working"}\n' "$CNT"
      exit 0
    fi
  fi
fi
