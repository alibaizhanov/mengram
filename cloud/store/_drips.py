"""Lifecycle drip emails and Paddle checkout-abandonment tracking.

Part of :class:`cloud.store.CloudStore`; split out of the former single-file store.
"""



class DripMixin:
    """Lifecycle drip emails and Paddle checkout-abandonment tracking."""


    # ---- Drip Emails ----

    def ensure_drip_emails_table(self):
        """Create drip_emails table if not exists."""
        with self._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS drip_emails (
                    id SERIAL PRIMARY KEY,
                    email TEXT NOT NULL,
                    user_id UUID,
                    drip_type VARCHAR(30) NOT NULL,
                    sent_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(email, drip_type)
                )
            """)

    def try_record_drip(self, email: str, drip_type: str, user_id: str = None) -> bool:
        """Record a drip email send. Returns True if recorded (=should send), False if duplicate."""
        self.ensure_drip_emails_table()
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO drip_emails (email, user_id, drip_type)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (email, drip_type) DO NOTHING
                   RETURNING id""",
                (email, user_id, drip_type)
            )
            return cur.fetchone() is not None

    def get_silence_report(self) -> dict:
        """Founder ops report: accounts whose SILENCE is the signal.
        - broken_on_install: signed up 48h+ ago (within 30 days), have an API
          key, zero usage_log rows ever — the plugin/setup never worked.
        - gone_quiet: 20+ lifetime calls, newest one 14-45 days old — real
          users slipping away (the mnmilford pattern from the 2026-07 audit)."""
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT u.email, u.created_at::date AS signed_up
                   FROM users u
                   JOIN api_keys k ON k.user_id = u.id
                   LEFT JOIN usage_log l ON l.user_id = u.id
                   WHERE u.created_at < NOW() - INTERVAL '48 hours'
                     AND u.created_at > NOW() - INTERVAL '30 days'
                   GROUP BY u.id, u.email
                   HAVING COUNT(l.id) = 0
                   ORDER BY u.created_at DESC"""
            )
            broken = [{"email": r["email"], "signed_up": str(r["signed_up"])} for r in cur.fetchall()]
            cur.execute(
                """SELECT u.email, MAX(l.created_at)::date AS last_active,
                          COUNT(l.id) AS total_calls
                   FROM users u
                   JOIN usage_log l ON l.user_id = u.id
                   GROUP BY u.id, u.email
                   HAVING COUNT(l.id) >= 20
                      AND MAX(l.created_at) < NOW() - INTERVAL '14 days'
                      AND MAX(l.created_at) > NOW() - INTERVAL '45 days'
                   ORDER BY MAX(l.created_at) DESC"""
            )
            quiet = [{"email": r["email"], "last_active": str(r["last_active"]),
                      "total_calls": r["total_calls"]} for r in cur.fetchall()]
            # half_wired: integrated recall but never capture — searching an
            # empty vault to quota exhaustion (found 2026-07-22: two accounts
            # burned 600+200 searches over zero entities). Warm rescue leads.
            cur.execute(
                """SELECT u.email,
                          COUNT(l.id) FILTER (WHERE l.action IN ('search','search_all')) AS searches
                   FROM users u
                   JOIN usage_log l ON l.user_id = u.id
                       AND l.created_at > NOW() - INTERVAL '14 days'
                   WHERE NOT EXISTS (SELECT 1 FROM entities e WHERE e.user_id = u.id)
                   GROUP BY u.id, u.email
                   HAVING COUNT(l.id) FILTER (WHERE l.action IN ('search','search_all')) > 50
                      AND COUNT(l.id) FILTER (WHERE l.action = 'add') = 0
                   ORDER BY 2 DESC"""
            )
            half_wired = [{"email": r["email"], "searches": r["searches"]} for r in cur.fetchall()]
        return {"broken_on_install": broken, "gone_quiet": quiet, "half_wired": half_wired}

    # Drip sequence — each step requires the previous step to have been sent.
    # Prevents sending 24h/72h/7d in a single cron burst when fixing bugs that
    # caused stale drip_emails records (e.g. the api_key.last_used_at IS NULL
    # bug fixed 2026-05-06).
    _DRIP_PREREQ = {
        "completed_24h": None,
        "completed_72h": "completed_24h",
        "completed_7d":  "completed_72h",
    }

    def get_inactive_completed_signups(self, hours: int, drip_type: str) -> list:
        """Find completed signups with no API activity after N hours.
        Only considers users who signed up within the last 30 days.

        IMPORTANT: checks user-level activity via usage_log, not api_key.last_used_at.
        Old query joined api_keys WHERE last_used_at IS NULL — broke when user rotated
        keys (new key has NULL last_used_at even if user is heavily active). Caused
        spurious "completed_24h/72h/7d" emails to active paying customers right after
        key rotation (e.g., Ben Hartley got 3 drip emails in 2 seconds on April 9
        moments after creating a fresh key). Fixed: check usage_log directly.

        Also enforces drip sequencing — completed_72h only fires if completed_24h
        was already sent; completed_7d only if completed_72h was sent. Without this
        a single cron iteration can send all three to one user."""
        self.ensure_drip_emails_table()
        prereq = self._DRIP_PREREQ.get(drip_type)
        with self._cursor(dict_cursor=True) as cur:
            if prereq:
                # 12-hour gate ensures prereq was sent in a *previous* cron run,
                # not the current one — prevents burst-sending all 3 drips in
                # one iteration after the bug fix backlog clears.
                cur.execute(
                    """SELECT DISTINCT u.id, u.email
                       FROM users u
                       WHERE u.created_at < NOW() - make_interval(hours => %s)
                         AND u.created_at > NOW() - INTERVAL '30 days'
                         AND NOT EXISTS (
                             SELECT 1 FROM usage_log ul
                             WHERE ul.user_id = u.id
                         )
                         AND NOT EXISTS (
                             SELECT 1 FROM drip_emails de
                             WHERE de.email = u.email AND de.drip_type = %s
                         )
                         AND EXISTS (
                             SELECT 1 FROM drip_emails de2
                             WHERE de2.email = u.email
                               AND de2.drip_type = %s
                               AND de2.sent_at < NOW() - INTERVAL '12 hours'
                         )""",
                    (hours, drip_type, prereq)
                )
            else:
                cur.execute(
                    """SELECT DISTINCT u.id, u.email
                       FROM users u
                       WHERE u.created_at < NOW() - make_interval(hours => %s)
                         AND u.created_at > NOW() - INTERVAL '30 days'
                         AND NOT EXISTS (
                             SELECT 1 FROM usage_log ul
                             WHERE ul.user_id = u.id
                         )
                         AND NOT EXISTS (
                             SELECT 1 FROM drip_emails de
                             WHERE de.email = u.email AND de.drip_type = %s
                         )""",
                    (hours, drip_type)
                )
            return [{"id": str(r["id"]), "email": r["email"]} for r in cur.fetchall()]

    def get_incomplete_signups_for_drip(self, hours: int, drip_type: str) -> list:
        """Find incomplete signups (pending verification) after N hours.
        Only considers codes created within the last 7 days to avoid spamming old entries."""
        self.ensure_drip_emails_table()
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT ec.email
                   FROM email_codes ec
                   WHERE ec.created_at < NOW() - make_interval(hours => %s)
                     AND ec.created_at > NOW() - INTERVAL '7 days'
                     AND NOT EXISTS (
                         SELECT 1 FROM users u WHERE u.email = ec.email
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM drip_emails de
                         WHERE de.email = ec.email AND de.drip_type = %s
                     )""",
                (hours, drip_type)
            )
            return [{"email": r["email"]} for r in cur.fetchall()]

    def is_email_unsubscribed(self, email: str) -> bool:
        """Check if an email has unsubscribed from drip emails."""
        with self._cursor() as cur:
            cur.execute(
                """SELECT 1 FROM drip_emails
                   WHERE email = %s AND drip_type = 'unsubscribed'""",
                (email,)
            )
            return cur.fetchone() is not None

    def unsubscribe_email(self, email: str) -> bool:
        """Unsubscribe an email from drip emails. Returns True if newly unsubscribed."""
        self.ensure_drip_emails_table()
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO drip_emails (email, drip_type)
                   VALUES (%s, 'unsubscribed')
                   ON CONFLICT (email, drip_type) DO NOTHING
                   RETURNING id""",
                (email,)
            )
            return cur.fetchone() is not None

    def get_users_added_no_search(self, min_adds: int = 3, drip_type: str = "added_no_search") -> list:
        """Find users who added memories but never searched (likely don't know search exists).

        Only counts real `search` queries — `search_all` is a dashboard browse-all
        action, not a query, so users who only viewed their vault still need the
        nudge to try real semantic search.
        """
        self.ensure_drip_emails_table()
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT u.id, u.email
                   FROM users u
                   JOIN usage_log ac ON ac.user_id = u.id
                   WHERE u.created_at > NOW() - INTERVAL '30 days'
                     AND NOT EXISTS (
                         SELECT 1 FROM drip_emails de
                         WHERE de.email = u.email AND de.drip_type = %s
                     )
                   GROUP BY u.id, u.email
                   HAVING count(*) FILTER (WHERE ac.action = 'add') >= %s
                      AND count(*) FILTER (WHERE ac.action = 'search') = 0
                      AND max(ac.created_at) < NOW() - INTERVAL '24 hours'""",
                (drip_type, min_adds)
            )
            return [{"id": str(r["id"]), "email": r["email"]} for r in cur.fetchall()]

    def get_users_searched_no_add(self, min_searches: int = 3, drip_type: str = "searched_no_add") -> list:
        """Find users who searched but never added memories (empty search results)."""
        self.ensure_drip_emails_table()
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT u.id, u.email
                   FROM users u
                   JOIN usage_log ac ON ac.user_id = u.id
                   WHERE u.created_at > NOW() - INTERVAL '30 days'
                     AND NOT EXISTS (
                         SELECT 1 FROM drip_emails de
                         WHERE de.email = u.email AND de.drip_type = %s
                     )
                   GROUP BY u.id, u.email
                   HAVING count(*) FILTER (WHERE ac.action IN ('search', 'search_all')) >= %s
                      AND count(*) FILTER (WHERE ac.action = 'add') = 0
                      AND max(ac.created_at) < NOW() - INTERVAL '24 hours'""",
                (drip_type, min_searches)
            )
            return [{"id": str(r["id"]), "email": r["email"]} for r in cur.fetchall()]

    def get_churned_active_users(self, min_actions: int = 3, inactive_hours: int = 168, drip_type: str = "churned_7d") -> list:
        """Find users who were actively using the API but stopped for 7+ days.

        min_actions=3 captures low-activity users who tried the product a few
        times then stopped — these are more salvageable than long-term power
        users who churned for explicit reasons.
        """
        self.ensure_drip_emails_table()
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT u.id, u.email
                   FROM users u
                   JOIN usage_log ac ON ac.user_id = u.id
                   WHERE u.created_at > NOW() - INTERVAL '90 days'
                     AND NOT EXISTS (
                         SELECT 1 FROM drip_emails de
                         WHERE de.email = u.email AND de.drip_type = %s
                     )
                   GROUP BY u.id, u.email
                   HAVING count(*) FILTER (WHERE ac.action IN ('add', 'search', 'search_all')) >= %s
                      AND max(ac.created_at) < NOW() - make_interval(hours => %s)""",
                (drip_type, min_actions, inactive_hours)
            )
            return [{"id": str(r["id"]), "email": r["email"]} for r in cur.fetchall()]

    # ---- Paddle Checkout Abandonment Tracking ----

    def ensure_checkout_sessions_table(self):
        """Create checkout_sessions table if not exists. Tracks Paddle checkout
        URLs so we can send drip emails to users who started checkout but did
        not complete payment."""
        with self._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS checkout_sessions (
                    transaction_id TEXT PRIMARY KEY,
                    user_id UUID NOT NULL,
                    email TEXT NOT NULL,
                    plan TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS checkout_sessions_pending_idx
                    ON checkout_sessions(created_at)
                    WHERE completed_at IS NULL
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS checkout_sessions_user_idx
                    ON checkout_sessions(user_id)
            """)

    def record_checkout_session(self, transaction_id: str, user_id: str, email: str, plan: str):
        """Record a Paddle checkout URL creation. Idempotent on transaction_id."""
        if not transaction_id or not user_id or not email or not plan:
            return
        self.ensure_checkout_sessions_table()
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO checkout_sessions (transaction_id, user_id, email, plan)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (transaction_id) DO NOTHING""",
                (transaction_id, user_id, email, plan)
            )

    def mark_user_checkouts_completed(self, user_id: str):
        """Mark ALL pending checkout sessions for a user as completed.
        Called on transaction.completed / subscription.activated webhooks so
        drip abandonment emails are not sent after the user eventually pays."""
        if not user_id:
            return
        self.ensure_checkout_sessions_table()
        with self._cursor() as cur:
            cur.execute(
                """UPDATE checkout_sessions
                   SET completed_at = NOW()
                   WHERE user_id = %s AND completed_at IS NULL""",
                (user_id,)
            )

    def get_abandoned_checkouts(self, hours: int, drip_type: str) -> list:
        """Find checkout sessions created >= N hours ago where payment never
        completed and no drip email of this type was sent.

        Only looks at sessions from the last 7 days to avoid spamming stale
        entries, and skips sessions where the user already has any active
        paid subscription."""
        self.ensure_drip_emails_table()
        self.ensure_checkout_sessions_table()
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT cs.user_id, cs.email, cs.plan
                   FROM checkout_sessions cs
                   LEFT JOIN subscriptions s ON s.user_id = cs.user_id
                   WHERE cs.completed_at IS NULL
                     AND cs.created_at < NOW() - make_interval(hours => %s)
                     AND cs.created_at > NOW() - INTERVAL '7 days'
                     AND COALESCE(s.plan, 'free') = 'free'
                     AND NOT EXISTS (
                         SELECT 1 FROM drip_emails de
                         WHERE de.email = cs.email AND de.drip_type = %s
                     )
                   GROUP BY cs.user_id, cs.email, cs.plan""",
                (hours, drip_type)
            )
            return [
                {"user_id": str(r["user_id"]), "email": r["email"], "plan": r["plan"]}
                for r in cur.fetchall()
            ]
