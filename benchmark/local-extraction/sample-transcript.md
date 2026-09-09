User: I need to get the notifications service deployed to Railway today. It's a FastAPI app, Postgres on Supabase, and the last deploy failed with "too many connections" during the health check. Repo is ~/work/notify-svc.

Assistant: Let's look at what happened. Can you show me the Railway logs from the failed deploy and your database settings?

User: Here's the tail:

```
[migrate] applying 0007_add_delivery_log
psycopg2.OperationalError: FATAL: remaining connection slots are reserved for non-replication superuser connections
[health] GET /health -> 503 after 30s
```

We're on the Supabase free tier, pooler is on port 6543 in transaction mode. Gunicorn runs 4 workers, and each worker opens its own pool of 5.

Assistant: That's 20 connections from the app alone, plus the migration step, plus whatever the previous deploy's workers still hold while Railway overlaps the two versions. The free tier pooler caps around 15 client connections for one project. Two things fix this reliably:

1. Run migrations as a separate pre-deploy command, not inside the app startup, so they never compete with workers for slots.
2. Drop to 2 workers with a pool of 3 each. For a notifications service that's IO-bound, 2 workers with async handlers is plenty.

User: Migrations run in `app/startup.py` right now, on every worker boot. That's dumb, each worker tries to migrate.

Assistant: Yes, and with 4 workers you get 4 concurrent migration attempts. Move it out. In Railway, set the pre-deploy command to `python -m app.migrate`, and remove the call from startup. Here's the change:

```python
# app/startup.py
async def on_startup():
    # migrations now run via `python -m app.migrate` before deploy
    await init_pool(min_size=1, max_size=3)
```

And in `railway.json`:

```json
{
  "deploy": {
    "preDeployCommand": "python -m app.migrate",
    "startCommand": "gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 2"
  }
}
```

User: Done. Deploying now.

User: Health check passed, but the migration step printed a warning: "0006_backfill_timezones already applied, skipping" and then 0007 applied fine. Is that skipping a problem?

Assistant: No, that's the migration runner being idempotent. 0006 was applied by one of the workers during the previous failed deploy before the pool ran out. The runner tracks applied versions in `schema_migrations`, so it skips it. Nothing to fix.

User: Ok. One more thing, I keep forgetting the order for a Railway deploy on this service. Can you write down the steps so I stop breaking it?

Assistant: Here's the checklist for deploying notify-svc:

1. Run the test suite locally: `pytest -q`. Don't deploy on red.
2. Check pending migrations: `python -m app.migrate --dry-run`. If a migration touches a big table, run it manually during low traffic instead of via pre-deploy.
3. Push to `main`. Railway auto-deploys from `main`.
4. Watch the pre-deploy step in the Railway dashboard until you see "migrations up to date".
5. Wait for `/health` to return 200 on the new deployment before closing the tab. Railway keeps the old version up until then.
6. If the health check fails, check pooler connection count in Supabase first. That's been the cause both times.

User: Save that. Also, note for the future: I want to stay on the Supabase free tier until we have paying customers, so any fix that says "upgrade the plan" is a no.

Assistant: Noted. The constraint is: Supabase free tier until first paying customer, so connection budget is hard capped. The 2-worker, pool-of-3 setup leaves headroom for the migration step and one overlapping deploy.

User: Also I switched my editor from VS Code to Zed last week, so if you suggest editor settings, use Zed's config format.

Assistant: Got it, Zed's `settings.json` from now on. Anything else for today?

User: No, that's it. Deploy is green, moving on to the email templates tomorrow with Marcus, he owns the design side.

Assistant: Sounds good. Summary of today: migrations moved to a Railway pre-deploy command, worker count dropped from 4 to 2 with pool size 3, health check passing, deploy checklist written down. Tomorrow: email templates with Marcus.
