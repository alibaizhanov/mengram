#!/usr/bin/env bash
# SessionStart hook — prepend the user's cognitive profile to Claude's context.
# Fails silently (returns empty output) so a Mengram outage never blocks
# Claude Code from starting.
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

# First-run self-check ("auto-doctor"): until the plugin has verified one
# successful round-trip, failures are LOUD — a one-line systemMessage telling
# the user exactly what's broken. After the first success (marker file) all
# failures go back to silent, as a hook should be. Rationale: silent failure
# on a fresh install is indistinguishable from churn — for the user and for us.
MARKER="$HOME/.mengram/.plugin-verified"
_first_run_warn() {
  printf '{"systemMessage": "%s"}\n' "$1"
  exit 0
}

if [ -z "$KEY" ]; then
  if [ ! -f "$MARKER" ]; then
    _first_run_warn "Mengram plugin is installed but no API key was found, so memory is OFF. Get a free key at https://mengram.io and save it to ~/.mengram/config.json (see plugin README for the one-liner)."
  fi
  exit 0  # verified before; silent as usual
fi

URL="${BASE%/}/v1/profile"

# 5-second budget — anything slower delays session start more than the value
# of the profile is worth on a cold open.
RESPONSE=$(curl -fsS --max-time 5 \
  -H "Authorization: Bearer $KEY" \
  -H "User-Agent: mengram-plugin/0.1.2" \
  "$URL" 2>/dev/null) || {
  if [ ! -f "$MARKER" ]; then
    _first_run_warn "Mengram: an API key was found but verification against $BASE failed (network issue or invalid key). Memory is OFF until this works. Check the key or see the plugin README troubleshooting table."
  fi
  exit 0
}

# Round-trip verified — record it so future failures stay silent.
mkdir -p "$HOME/.mengram" 2>/dev/null && touch "$MARKER" 2>/dev/null

# Profile is JSON. Use jq if available, otherwise a REAL python3/python —
# probe with an actual import, not `command -v` (Windows Store stub).
SUMMARY=""
if command -v jq >/dev/null 2>&1; then
  SUMMARY=$(printf '%s' "$RESPONSE" | jq -r '.summary // empty' 2>/dev/null)
else
  for PY in python3 python; do
    if "$PY" -c "import json" >/dev/null 2>&1; then
      SUMMARY=$(printf '%s' "$RESPONSE" | "$PY" -c 'import json,sys
try: print(json.loads(sys.stdin.read()).get("summary",""))
except: pass' 2>/dev/null)
      break
    fi
  done
fi

if [ -n "$SUMMARY" ]; then
  echo "[Mengram memory — what you remember about this user]"
  echo "$SUMMARY"
fi
