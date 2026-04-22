#!/bin/bash
# Mengram startup dispatcher.
# Dispatches to HTTP server (gunicorn) or cron worker based on MENGRAM_ROLE.
#
# MENGRAM_ROLE values:
#   api  | unset | all  -> run gunicorn HTTP server (default)
#   cron                -> run cron worker (no HTTP)
#
# Used by the Railway start command so a single railway.json config
# works for both the `mengram` (api) and `mengram-cron` services.
set -euo pipefail

ROLE="${MENGRAM_ROLE:-all}"

if [ "$ROLE" = "cron" ]; then
    echo "[start.sh] MENGRAM_ROLE=$ROLE → launching cron worker"
    exec python -m cloud.cron_worker
else
    echo "[start.sh] MENGRAM_ROLE=$ROLE → launching HTTP server (gunicorn)"
    exec gunicorn cloud.api:app \
        -w 2 \
        -k uvicorn.workers.UvicornWorker \
        --bind "0.0.0.0:${PORT:-8000}" \
        --timeout 300 \
        --graceful-timeout 30
fi
