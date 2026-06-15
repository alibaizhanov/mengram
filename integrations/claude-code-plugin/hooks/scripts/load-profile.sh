#!/usr/bin/env bash
# SessionStart hook — prepend the user's cognitive profile to Claude's context.
# Fails silently (returns empty output) so a Mengram outage never blocks
# Claude Code from starting.
set -u

KEY="${MENGRAM_API_KEY:-}"
if [ -z "$KEY" ]; then
  exit 0  # no key configured; behave like the plugin isn't installed
fi

URL="${MENGRAM_URL:-https://mengram.io}/v1/profile"

# 5-second budget — anything slower delays session start more than the value
# of the profile is worth on a cold open.
RESPONSE=$(curl -fsS --max-time 5 \
  -H "Authorization: Bearer $KEY" \
  -H "User-Agent: mengram-plugin/0.1.0" \
  "$URL" 2>/dev/null) || exit 0

# Profile is JSON. Use jq if available (most users have it via Claude Code
# install), otherwise fall back to a minimal Python one-liner.
if command -v jq >/dev/null 2>&1; then
  SUMMARY=$(printf '%s' "$RESPONSE" | jq -r '.summary // empty' 2>/dev/null)
elif command -v python3 >/dev/null 2>&1; then
  SUMMARY=$(printf '%s' "$RESPONSE" | python3 -c 'import json,sys
try: print(json.loads(sys.stdin.read()).get("summary",""))
except: pass' 2>/dev/null)
else
  SUMMARY=""
fi

if [ -n "$SUMMARY" ]; then
  echo "[Mengram memory — what you remember about this user]"
  echo "$SUMMARY"
fi
