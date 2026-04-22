"""Dedicated cron worker entrypoint for Mengram.

Runs the background cron jobs (smart triggers + onboarding drip emails)
WITHOUT starting the HTTP server. Used on a separate Railway service so
cron work never blocks HTTP workers.

Importing `cloud.api` triggers module-level `app = create_cloud_api()`,
which starts the daemon cron threads (guarded by MENGRAM_ROLE). This
process then blocks forever; the daemon threads do the real work.

Run with:
    MENGRAM_ROLE=cron python -m cloud.cron_worker

Safety:
- Advisory locks (900001, 900002) prevent double-execution across
  instances, so this is safe to run alongside MENGRAM_ROLE=all
  instances during rollout.
- SIGTERM handler exits cleanly; daemon threads are killed by Python.
  try_record_drip() UNIQUE constraint in DB prevents duplicate emails
  if a cycle is interrupted mid-send.
"""
import logging
import os
import signal
import sys

# Ensure cron threads start even if env isn't set at runtime.
# (Railway injects env, but belt-and-suspenders for local testing.)
os.environ.setdefault("MENGRAM_ROLE", "cron")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mengram.cron_worker")


def _shutdown(signum, frame):
    logger.info(f"Received signal {signum}, cron worker shutting down")
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    role = os.environ.get("MENGRAM_ROLE", "").lower()
    logger.info(f"🔧 Starting Mengram cron worker (MENGRAM_ROLE={role})")

    # Importing cloud.api triggers create_cloud_api() at module level,
    # which starts the daemon cron threads if MENGRAM_ROLE in (all, cron).
    import cloud.api  # noqa: F401

    logger.info("🔧 Cron worker ready — daemon threads running, blocking on signal")

    # Block forever. Daemon threads do the work.
    # signal.pause() is POSIX only but Railway runs Linux.
    signal.pause()


if __name__ == "__main__":
    main()
