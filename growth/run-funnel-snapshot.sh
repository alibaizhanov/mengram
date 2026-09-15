#!/usr/bin/env bash
# Runs growth/funnel-snapshot.py inside the production container over Railway SSH.
# Read-only (the connection is opened with readonly=True); prints aggregates only,
# no emails or keys. The app's interpreter needs the running process's library
# path, which an SSH shell does not inherit — hence the /proc/1/environ export.
set -euo pipefail
cd "$(dirname "$0")/.."
B64=$(base64 < growth/funnel-snapshot.py | tr -d '\n')
railway ssh --service mengram -- sh -c "export \$(tr '\\0' '\\n' < /proc/1/environ | grep -E '^(LD_LIBRARY_PATH|PATH|DATABASE_URL)=' | xargs); echo $B64 | base64 -d | /opt/venv/bin/python -" \
  | grep -v "Using SSH key\|railway\|Migrate\|Existing files"
