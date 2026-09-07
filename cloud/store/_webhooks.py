"""Outbound webhooks.

Part of :class:`cloud.store.CloudStore`; split out of the former single-file store.
"""

import datetime
import hashlib
import json
import time

from ._common import (  # noqa: F401
    logger,
)


class WebhookMixin:
    """Outbound webhooks."""


    # =====================================================
    # WEBHOOKS
    # =====================================================

    def ensure_webhooks_table(self):
        """Create webhooks table if not exists."""
        with self._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS webhooks (
                    id SERIAL PRIMARY KEY,
                    user_id VARCHAR(255) NOT NULL,
                    url TEXT NOT NULL,
                    name VARCHAR(255) DEFAULT '',
                    event_types JSONB DEFAULT '["memory_add","memory_update","memory_delete"]',
                    secret VARCHAR(255) DEFAULT '',
                    active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    last_triggered TIMESTAMPTZ,
                    trigger_count INTEGER DEFAULT 0,
                    last_error TEXT,
                    consecutive_failures INTEGER DEFAULT 0
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_webhooks_user
                ON webhooks(user_id, active)
            """)
            # Migration: add column for existing tables
            cur.execute("""
                ALTER TABLE webhooks ADD COLUMN IF NOT EXISTS
                consecutive_failures INTEGER DEFAULT 0
            """)

    def create_webhook(self, user_id: str, url: str, name: str = "",
                       event_types: list = None, secret: str = "") -> dict:
        """Create a new webhook."""
        self.ensure_webhooks_table()
        if not event_types:
            event_types = ["memory_add", "memory_update", "memory_delete"]

        # Validate event types
        valid = {"memory_add", "memory_update", "memory_delete"}
        for et in event_types:
            if et not in valid:
                raise ValueError(f"Invalid event type: {et}. Valid: {', '.join(valid)}")

        with self._cursor(dict_cursor=True) as cur:
            cur.execute("""
                INSERT INTO webhooks (user_id, url, name, event_types, secret)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, user_id, url, name, event_types, secret, active, created_at
            """, (user_id, url, name, json.dumps(event_types), secret))
            row = cur.fetchone()
            return {
                "id": row["id"],
                "url": row["url"],
                "name": row["name"],
                "event_types": row["event_types"],
                "active": row["active"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None
            }

    def get_webhooks(self, user_id: str) -> list:
        """Get all webhooks for a user."""
        self.ensure_webhooks_table()
        with self._cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT id, url, name, event_types, active, created_at,
                       last_triggered, trigger_count, last_error
                FROM webhooks WHERE user_id = %s ORDER BY created_at DESC
            """, (user_id,))
            return [{
                "id": r["id"],
                "url": r["url"],
                "name": r["name"],
                "event_types": r["event_types"] if isinstance(r["event_types"], list) else json.loads(r["event_types"]),
                "active": r["active"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "last_triggered": r["last_triggered"].isoformat() if r["last_triggered"] else None,
                "trigger_count": r["trigger_count"],
                "last_error": r["last_error"]
            } for r in cur.fetchall()]

    def update_webhook(self, user_id: str, webhook_id: int,
                       url: str = None, name: str = None,
                       event_types: list = None, active: bool = None) -> dict:
        """Update a webhook."""
        self.ensure_webhooks_table()
        updates = []
        params = []
        if url is not None:
            updates.append("url = %s")
            params.append(url)
        if name is not None:
            updates.append("name = %s")
            params.append(name)
        if event_types is not None:
            updates.append("event_types = %s")
            params.append(json.dumps(event_types))
        if active is not None:
            updates.append("active = %s")
            params.append(active)

        if not updates:
            return {"status": "no changes"}

        params.extend([webhook_id, user_id])
        with self._cursor() as cur:
            cur.execute(
                f"UPDATE webhooks SET {', '.join(updates)} WHERE id = %s AND user_id = %s",
                params
            )
            return {"status": "updated", "id": webhook_id}

    def delete_webhook(self, user_id: str, webhook_id: int) -> bool:
        """Delete a webhook."""
        self.ensure_webhooks_table()
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM webhooks WHERE id = %s AND user_id = %s",
                (webhook_id, user_id)
            )
            return cur.rowcount > 0

    # Shared thread pool for webhook delivery (limits concurrent outbound connections)
    _webhook_pool = None

    def _get_webhook_pool(self):
        if self._webhook_pool is None:
            from concurrent.futures import ThreadPoolExecutor
            self._webhook_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="webhook")
        return self._webhook_pool

    def fire_webhooks(self, user_id: str, event_type: str, payload: dict):
        """Fire all active webhooks for this event type. Non-blocking, thread-pool limited."""
        self.ensure_webhooks_table()
        import urllib.request, urllib.error

        with self._cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT id, url, secret FROM webhooks
                WHERE user_id = %s AND active = TRUE
                AND event_types ? %s
            """, (user_id, event_type))
            hooks = cur.fetchall()

        if not hooks:
            return

        data = json.dumps({
            "event": event_type,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": payload
        }).encode("utf-8")

        def _send(hook_id, url, secret):
            # Validate URL before sending (prevent SSRF via DNS rebinding)
            import urllib.parse
            import socket
            import ipaddress as _ipa
            parsed = urllib.parse.urlparse(url)
            hostname = parsed.hostname or ""
            if hostname in ("localhost", "0.0.0.0", "metadata.google.internal") or hostname.endswith(".internal") or hostname.endswith(".local"):
                logger.warning(f"⚠️ Webhook {hook_id} blocked: internal hostname {hostname}")
                return
            try:
                resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                for family, _, _, _, sockaddr in resolved:
                    ip = _ipa.ip_address(sockaddr[0])
                    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                        logger.warning(f"⚠️ Webhook {hook_id} blocked: {hostname} → private IP {ip}")
                        return
            except (socket.gaierror, ValueError):
                pass
            try:
                req = urllib.request.Request(
                    url, data=data,
                    headers={"Content-Type": "application/json"}
                )
                if secret:
                    import hmac as _hmac
                    sig = _hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()
                    req.add_header("X-Mengram-Signature", sig)

                import time
                # Retry with exponential backoff on transient errors (429, 5xx)
                last_err = None
                for attempt in range(3):  # up to 3 attempts
                    try:
                        if attempt > 0:
                            time.sleep(min(2 ** attempt, 8))  # 2s, 4s backoff
                        urllib.request.urlopen(req, timeout=10)
                        # Success — record it
                        with self._cursor() as cur2:
                            cur2.execute("""
                                UPDATE webhooks SET last_triggered = NOW(),
                                trigger_count = trigger_count + 1,
                                last_error = NULL, consecutive_failures = 0
                                WHERE id = %s
                            """, (hook_id,))
                        return  # delivered
                    except urllib.error.HTTPError as he:
                        last_err = he
                        if he.code in (429, 500, 502, 503, 504):
                            continue  # transient — retry
                        break  # 4xx (not 429) — don't retry
                    except Exception as ex:
                        last_err = ex
                        break  # network error — don't retry

                # All retries failed
                err_msg = str(last_err)[:500]
                logger.error(f"⚠️ Webhook {hook_id} failed after retries: {last_err}")
                try:
                    with self._cursor() as cur2:
                        cur2.execute("""
                            UPDATE webhooks SET last_error = %s,
                            consecutive_failures = consecutive_failures + 1
                            WHERE id = %s
                        """, (err_msg, hook_id))
                        # Auto-disable after 20 consecutive failures
                        cur2.execute("""
                            UPDATE webhooks SET active = FALSE
                            WHERE id = %s AND consecutive_failures >= 20
                        """, (hook_id,))
                except Exception:
                    pass
            except Exception as exc:
                logger.error(f"⚠️ Webhook {hook_id} unexpected error: {exc}")

        def _send_all_sequential():
            """Send webhooks sequentially with 150ms delay to prevent rate limiting."""
            import time
            for i, hook in enumerate(hooks):
                if i > 0:
                    time.sleep(0.15)
                _send(hook["id"], hook["url"], hook["secret"] or "")

        pool = self._get_webhook_pool()
        pool.submit(_send_all_sequential)

        logger.info(f"🔔 Fired {len(hooks)} webhooks for {event_type} ({user_id})")
