"""Usage log, subscriptions, usage counters and quotas.

Part of :class:`cloud.store.CloudStore`; split out of the former single-file store.
"""

import datetime
from typing import Optional


class BillingMixin:
    """Usage log, subscriptions, usage counters and quotas."""


    # ---- Usage tracking ----

    def log_usage(self, user_id: str, action: str, tokens: int = 0,
                  query_score: float = None, query_language: str = None,
                  result_quality: str = None):
        """Log API usage. Optional query_score + query_language let search
        callers feed Memory Health monitoring (v2.22). result_quality is the
        scale-aware label (query_score mixes cosine and RRF scales)."""
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO usage_log
                       (user_id, action, tokens_used, query_score, query_language, result_quality)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (user_id, action, tokens, query_score, query_language, result_quality)
            )

    # ---- Subscriptions ----

    def get_subscription(self, user_id: str) -> dict:
        """Get user's subscription. Lazy-creates 'free' plan for existing users."""
        cache_key = f"sub:{user_id}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                "SELECT * FROM subscriptions WHERE user_id = %s",
                (user_id,)
            )
            row = cur.fetchone()
            if row:
                result = dict(row)
                self.cache.set(cache_key, result, ttl=300)  # cache 5 min
                return result

            # Lazy-create free subscription for existing users
            cur.execute(
                """INSERT INTO subscriptions (user_id, plan, status)
                   VALUES (%s, 'free', 'active')
                   ON CONFLICT (user_id) DO NOTHING
                   RETURNING *""",
                (user_id,)
            )
            row = cur.fetchone()
            if not row:
                # Race condition: another worker created it
                cur.execute(
                    "SELECT * FROM subscriptions WHERE user_id = %s",
                    (user_id,)
                )
                row = cur.fetchone()
            result = dict(row) if row else {"plan": "free", "status": "active"}
            self.cache.set(cache_key, result, ttl=300)
            return result

    def update_subscription(self, user_id: str, **kwargs) -> None:
        """Update subscription fields. Invalidates cache."""
        if not kwargs:
            return
        allowed = {"plan", "paddle_customer_id", "paddle_subscription_id",
                   "status", "current_period_start", "current_period_end",
                   "canceled_at"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return

        set_parts = [f"{k} = %s" for k in fields]
        set_parts.append("updated_at = NOW()")
        values = list(fields.values()) + [user_id]

        with self._cursor() as cur:
            # Ensure subscription row exists (lazy-create if needed)
            cur.execute(
                """INSERT INTO subscriptions (user_id, plan, status)
                   VALUES (%s, 'free', 'active')
                   ON CONFLICT (user_id) DO NOTHING""",
                (user_id,)
            )
            cur.execute(
                f"UPDATE subscriptions SET {', '.join(set_parts)} WHERE user_id = %s",
                values
            )
        self.cache.invalidate(f"sub:{user_id}")

    def get_user_by_paddle_customer(self, paddle_customer_id: str) -> Optional[str]:
        """Get user_id by Paddle customer ID (for webhook handling)."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT user_id FROM subscriptions WHERE paddle_customer_id = %s",
                (paddle_customer_id,)
            )
            row = cur.fetchone()
            return str(row[0]) if row else None

    # ---- Usage Counters ----

    def increment_usage(self, user_id: str, action: str, count: int = 1) -> int:
        """Atomically increment usage counter for current billing period.
        Returns new count after increment."""
        column = f"{action}_count"
        # Validate column name to prevent SQL injection
        valid_columns = {"add_count", "search_count", "agent_count",
                        "reflect_count", "dedup_count", "reindex_count",
                        "rules_count"}
        if column not in valid_columns:
            raise ValueError(f"Invalid action: {action}")

        period_start = datetime.date.today().replace(day=1)
        with self._cursor() as cur:
            cur.execute(
                f"""INSERT INTO usage_counters (user_id, period_start, {column})
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, period_start)
                    DO UPDATE SET {column} = usage_counters.{column} + %s
                    RETURNING {column}""",
                (user_id, period_start, count, count)
            )
            result = cur.fetchone()[0]
        # Invalidate cached count
        self.cache.invalidate(f"usage:{user_id}:")
        return result

    def check_and_increment(self, user_id: str, action: str, max_allowed: int, count: int = 1) -> int:
        """Atomic check-and-increment: only increments if within quota.
        Returns new count after increment.
        Raises ValueError if quota would be exceeded."""
        if max_allowed == -1:
            return self.increment_usage(user_id, action, count)

        column = f"{action}_count"
        valid_columns = {"add_count", "search_count", "agent_count",
                        "reflect_count", "dedup_count", "reindex_count",
                        "rules_count"}
        if column not in valid_columns:
            raise ValueError(f"Invalid action: {action}")

        period_start = datetime.date.today().replace(day=1)
        with self._cursor() as cur:
            # A call bigger than the whole allowance can never fit, and the
            # INSERT arm below carries no bound — it fires on the period's
            # first call, when no counter row exists yet — so an oversized
            # request would otherwise sail through once a month.
            if count > max_allowed:
                cur.execute(
                    f"SELECT {column} FROM usage_counters WHERE user_id = %s AND period_start = %s",
                    (user_id, period_start)
                )
                r = cur.fetchone()
                current = r[0] if r else 0
                raise ValueError(f"quota_exceeded:{action}:{current}:{max_allowed}")

            # Atomic: increment only if current value < max_allowed
            cur.execute(
                f"""INSERT INTO usage_counters (user_id, period_start, {column})
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, period_start)
                    DO UPDATE SET {column} = usage_counters.{column} + %s
                    WHERE usage_counters.{column} + %s <= %s
                    RETURNING {column}""",
                (user_id, period_start, count, count, count, max_allowed)
            )
            row = cur.fetchone()
            if row is None:
                # Quota exceeded — read current value for error message
                cur.execute(
                    f"SELECT {column} FROM usage_counters WHERE user_id = %s AND period_start = %s",
                    (user_id, period_start)
                )
                r = cur.fetchone()
                current = r[0] if r else 0
                raise ValueError(f"quota_exceeded:{action}:{current}:{max_allowed}")
            result = row[0]
        self.cache.invalidate(f"usage:{user_id}:")
        return result

    def count_distinct_sub_users(self, user_id: str) -> int:
        """Count distinct sub_user_ids used by this user (across entities table).
        Cached 60s to avoid repeated COUNT DISTINCT queries."""
        cache_key = f"sub_users:{user_id}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        with self._cursor() as cur:
            cur.execute("""
                SELECT COUNT(DISTINCT sub_user_id) FROM entities
                WHERE user_id = %s AND sub_user_id != 'default'
            """, (user_id,))
            row = cur.fetchone()
            count = row[0] if row else 0
        self.cache.set(cache_key, count, ttl=60)
        return count

    def is_known_sub_user(self, user_id: str, sub_user_id: str) -> bool:
        """Check if a sub_user_id already exists for this user."""
        with self._cursor() as cur:
            cur.execute("""
                SELECT 1 FROM entities
                WHERE user_id = %s AND sub_user_id = %s
                LIMIT 1
            """, (user_id, sub_user_id))
            return cur.fetchone() is not None

    def get_usage_count(self, user_id: str, action: str) -> int:
        """Get current month's usage count for an action. Cached 10s."""
        column = f"{action}_count"
        valid_columns = {"add_count", "search_count", "agent_count",
                        "reflect_count", "dedup_count", "reindex_count",
                        "rules_count"}
        if column not in valid_columns:
            return 0

        cache_key = f"usage:{user_id}:{action}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        period_start = datetime.date.today().replace(day=1)
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {column} FROM usage_counters WHERE user_id = %s AND period_start = %s",
                (user_id, period_start)
            )
            row = cur.fetchone()
            val = row[0] if row else 0
        self.cache.set(cache_key, val, ttl=10)
        return val

    def get_all_usage_counts(self, user_id: str) -> dict:
        """Get all usage counters for current billing period."""
        period_start = datetime.date.today().replace(day=1)
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT add_count, search_count, agent_count,
                          reflect_count, dedup_count, reindex_count,
                          rules_count
                   FROM usage_counters
                   WHERE user_id = %s AND period_start = %s""",
                (user_id, period_start)
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            return {
                "add_count": 0, "search_count": 0, "agent_count": 0,
                "reflect_count": 0, "dedup_count": 0, "reindex_count": 0,
                "rules_count": 0,
            }
