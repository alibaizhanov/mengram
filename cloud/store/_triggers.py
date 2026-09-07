"""Smart triggers: reminders, contradictions, patterns.

Part of :class:`cloud.store.CloudStore`; split out of the former single-file store.
"""

import datetime
import re
import time

from ._common import (  # noqa: F401
    logger,
)


class TriggerMixin:
    """Smart triggers: reminders, contradictions, patterns."""


    # ============================================
    # Smart Memory Triggers (v2.6)
    # ============================================

    def ensure_triggers_table(self):
        """Create memory_triggers table if not exists."""
        with self._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS memory_triggers (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    trigger_type VARCHAR(30) NOT NULL,
                    title TEXT NOT NULL,
                    detail TEXT,
                    source_type VARCHAR(30),
                    source_id UUID,
                    fire_at TIMESTAMPTZ,
                    fired BOOLEAN DEFAULT FALSE,
                    fired_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_triggers_pending
                ON memory_triggers(user_id, fired, fire_at)
                WHERE fired = FALSE
            """)

    def create_trigger(self, user_id: str, trigger_type: str, title: str,
                       detail: str = None, source_type: str = None,
                       source_id: str = None, fire_at=None,
                       sub_user_id: str = "default") -> int:
        """Create a new smart trigger."""
        self.ensure_triggers_table()
        with self._cursor(dict_cursor=True) as cur:
            # Avoid duplicate triggers with same title for same user
            cur.execute("""
                SELECT id FROM memory_triggers
                WHERE user_id = %s AND sub_user_id = %s AND title = %s AND fired = FALSE
            """, (user_id, sub_user_id, title))
            if cur.fetchone():
                return -1  # Already exists

            cur.execute("""
                INSERT INTO memory_triggers
                    (user_id, sub_user_id, trigger_type, title, detail, source_type, source_id, fire_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (user_id, sub_user_id, trigger_type, title, detail, source_type,
                  source_id, fire_at))
            row = cur.fetchone()
            return row["id"] if row else -1

    def get_pending_triggers(self, user_id: str = None) -> list:
        """Get triggers that are ready to fire."""
        self.ensure_triggers_table()
        with self._cursor(dict_cursor=True) as cur:
            if user_id:
                cur.execute("""
                    SELECT * FROM memory_triggers
                    WHERE user_id = %s AND fired = FALSE
                    AND (fire_at IS NULL OR fire_at <= NOW())
                    ORDER BY created_at
                    LIMIT 50
                """, (user_id,))
            else:
                cur.execute("""
                    SELECT * FROM memory_triggers
                    WHERE fired = FALSE
                    AND (fire_at IS NULL OR fire_at <= NOW())
                    ORDER BY created_at
                    LIMIT 100
                """)
            return [dict(r) for r in cur.fetchall()]

    def fire_trigger(self, trigger_id: int):
        """Mark trigger as fired and send via webhook."""
        self.ensure_triggers_table()
        with self._cursor(dict_cursor=True) as cur:
            cur.execute("""
                UPDATE memory_triggers SET fired = TRUE, fired_at = NOW()
                WHERE id = %s
                RETURNING *
            """, (trigger_id,))
            trigger = cur.fetchone()
            if not trigger:
                return None

        # Fire webhook
        self.fire_webhooks(trigger["user_id"], "smart_trigger", {
            "trigger_id": trigger["id"],
            "type": trigger["trigger_type"],
            "title": trigger["title"],
            "detail": trigger["detail"],
            "source_type": trigger.get("source_type"),
        })
        return dict(trigger)

    def get_triggers(self, user_id: str, include_fired: bool = False,
                     limit: int = 50, sub_user_id: str = "default") -> list:
        """Get triggers for a user."""
        self.ensure_triggers_table()
        with self._cursor(dict_cursor=True) as cur:
            if include_fired:
                cur.execute("""
                    SELECT * FROM memory_triggers
                    WHERE user_id = %s AND sub_user_id = %s ORDER BY created_at DESC LIMIT %s
                """, (user_id, sub_user_id, limit))
            else:
                cur.execute("""
                    SELECT * FROM memory_triggers
                    WHERE user_id = %s AND sub_user_id = %s AND fired = FALSE
                    ORDER BY fire_at ASC NULLS FIRST, created_at DESC LIMIT %s
                """, (user_id, sub_user_id, limit))
            return [dict(r) for r in cur.fetchall()]

    def detect_reminder_triggers(self, user_id: str, sub_user_id: str = "default"):
        """Scan episodic memory for upcoming events and create reminders."""
        self.ensure_triggers_table()
        import re
        from datetime import timedelta

        with self._cursor(dict_cursor=True) as cur:
            # Find recent episodes that mention future times/dates
            cur.execute("""
                SELECT id, summary, context, outcome, metadata, created_at
                FROM episodes
                WHERE user_id = %s AND sub_user_id = %s
                AND created_at > NOW() - INTERVAL '7 days'
                ORDER BY created_at DESC LIMIT 20
            """, (user_id, sub_user_id))
            episodes = cur.fetchall()

        now = datetime.datetime.now(datetime.timezone.utc)
        created = 0

        for ep in episodes:
            summary = ep["summary"] or ""
            context = ep["context"] or ""
            text = f"{summary} {context}".lower()

            # Simple heuristics for time references
            # "tomorrow", "in X hours", "at HH:MM", weekday names
            fire_at = None

            if "tomorrow" in text:
                fire_at = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0)
            elif "today" in text and now.hour < 20:
                fire_at = now + timedelta(hours=1)
            elif "next week" in text:
                fire_at = (now + timedelta(weeks=1)).replace(hour=9, minute=0, second=0)

            # Match "at HH:MM" patterns
            time_match = re.search(r'(?:at|в)\s+(\d{1,2})[:\.](\d{2})', text)
            if time_match and not fire_at:
                hour, minute = int(time_match.group(1)), int(time_match.group(2))
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    candidate = now.replace(hour=hour, minute=minute, second=0)
                    if candidate < now:
                        candidate += timedelta(days=1)
                    # Only set reminder 1h before the event
                    fire_at = candidate - timedelta(hours=1)
                    if fire_at < now:
                        fire_at = now + timedelta(minutes=5)

            if fire_at and fire_at > now:
                title = f"Reminder: {summary[:100]}"
                detail = f"From your conversation: {summary}"
                if ep.get("outcome"):
                    detail += f"\nOutcome: {ep['outcome']}"
                tid = self.create_trigger(
                    user_id=user_id,
                    trigger_type="reminder",
                    title=title,
                    detail=detail,
                    source_type="episode",
                    source_id=str(ep["id"]),
                    fire_at=fire_at,
                    sub_user_id=sub_user_id,
                )
                if tid > 0:
                    created += 1

        return created

    def detect_contradiction_triggers(self, user_id: str, new_facts: list,
                                       entity_name: str, sub_user_id: str = "default"):
        """Check if new facts contradict existing ones. Uses simple keyword overlap."""
        if not new_facts:
            return 0

        with self._cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT f.content, e.name FROM facts f
                JOIN entities e ON f.entity_id = e.id
                WHERE e.user_id = %s AND e.sub_user_id = %s AND e.name = %s
                AND f.archived = FALSE
            """, (user_id, sub_user_id, entity_name))
            existing = cur.fetchall()

        if not existing:
            return 0

        created = 0
        # Simple contradiction detection via negation patterns
        negation_pairs = [
            ("likes", "dislikes"), ("loves", "hates"),
            ("vegetarian", "meat"), ("vegan", "meat"),
            ("allergic", "enjoys"), ("can't", "can"),
            ("doesn't", "does"), ("never", "always"),
            ("dislikes", "likes"), ("does not eat", "eats"),
            ("allergic to", "likes"),
        ]

        for new_fact in new_facts:
            new_lower = new_fact.lower()
            for old in existing:
                old_lower = old["content"].lower()
                # Check negation patterns
                for pos, neg in negation_pairs:
                    if (pos in new_lower and neg in old_lower) or \
                       (neg in new_lower and pos in old_lower):
                        title = f"Possible contradiction about {entity_name}"
                        detail = f"New: \"{new_fact}\"\nExisting: \"{old['content']}\""
                        tid = self.create_trigger(
                            user_id=user_id,
                            trigger_type="contradiction",
                            title=title,
                            detail=detail,
                            source_type="fact",
                            sub_user_id=sub_user_id,
                        )
                        if tid > 0:
                            created += 1
                        break  # one contradiction per new fact is enough

        return created

    def detect_pattern_triggers(self, user_id: str, sub_user_id: str = "default"):
        """Detect patterns in procedural memory (high fail rates, recurring issues)."""
        with self._cursor(dict_cursor=True) as cur:
            # Procedures with more failures than successes
            cur.execute("""
                SELECT id, name, success_count, fail_count, steps
                FROM procedures
                WHERE user_id = %s AND sub_user_id = %s AND (success_count + fail_count) >= 3
                AND fail_count > success_count
            """, (user_id, sub_user_id))
            risky_procs = cur.fetchall()

        created = 0
        for proc in risky_procs:
            total = proc["success_count"] + proc["fail_count"]
            fail_rate = round(proc["fail_count"] / total * 100)
            title = f"Risky workflow: {proc['name']} ({fail_rate}% failure rate)"
            detail = (
                f"Workflow \"{proc['name']}\" has failed {proc['fail_count']} out of "
                f"{total} times ({fail_rate}% failure rate). Consider revising the approach."
            )
            tid = self.create_trigger(
                user_id=user_id,
                trigger_type="pattern",
                title=title,
                detail=detail,
                source_type="procedure",
                source_id=str(proc["id"]),
                sub_user_id=sub_user_id,
            )
            if tid > 0:
                created += 1

        # ---- At-risk procedures: 3+ failures in last 7 days ----
        try:
            with self._cursor(dict_cursor=True) as cur:
                cur.execute("""
                    SELECT p.id, p.name, p.success_count, p.fail_count,
                           COUNT(e.id) AS recent_failures
                    FROM procedures p
                    LEFT JOIN episodes e ON e.linked_procedure_id = p.id
                        AND e.emotional_valence = 'negative'
                        AND e.created_at > NOW() - INTERVAL '7 days'
                    WHERE p.user_id = %s AND p.sub_user_id = %s AND p.is_current = TRUE
                    GROUP BY p.id
                    HAVING COUNT(e.id) >= 3
                """, (user_id, sub_user_id))
                at_risk = cur.fetchall()
            for proc in at_risk:
                title = f"At-risk workflow: {proc['name']} ({proc['recent_failures']} failures this week)"
                detail = (
                    f"Workflow \"{proc['name']}\" has failed {proc['recent_failures']} times "
                    f"in the last 7 days. It may need a manual review or restructuring."
                )
                tid = self.create_trigger(
                    user_id=user_id,
                    trigger_type="procedure_at_risk",
                    title=title,
                    detail=detail,
                    source_type="procedure",
                    source_id=str(proc["id"]),
                    sub_user_id=sub_user_id,
                )
                if tid > 0:
                    created += 1
        except Exception as e:
            logger.error(f"⚠️ At-risk trigger detection failed: {e}")

        return created

    def create_procedure_evolved_trigger(self, user_id: str,
                                          procedure_name: str,
                                          old_version: int,
                                          new_version: int,
                                          change_description: str,
                                          procedure_id: str,
                                          sub_user_id: str = "default") -> int:
        """Create a trigger notifying the user that a procedure evolved."""
        title = f"Procedure updated: {procedure_name} v{old_version} → v{new_version}"
        detail = (
            f"Your workflow \"{procedure_name}\" was automatically improved based on "
            f"your recent experience.\n"
            f"What changed: {change_description}\n"
            f"Review it in your procedures list."
        )
        tid = self.create_trigger(
            user_id=user_id,
            trigger_type="procedure_evolved",
            title=title,
            detail=detail,
            source_type="procedure",
            source_id=procedure_id,
            sub_user_id=sub_user_id,
        )
        return 1 if tid > 0 else 0

    def create_procedure_suggestion_trigger(self, user_id: str,
                                             suggestion_name: str,
                                             suggestion_steps: list[dict],
                                             episode_count: int,
                                             confidence: float,
                                             sub_user_id: str = "default") -> int:
        """Create a trigger suggesting a procedure the user might want to formalize.

        Called when pattern detection finds a cluster with confidence 0.4-0.6
        (too low to auto-create, but worth surfacing to the user).
        """
        steps_desc = " → ".join((s.get("action", "") if isinstance(s, dict) else str(s)) for s in suggestion_steps[:5])
        title = f"Workflow detected: {suggestion_name}"
        detail = (
            f"Based on {episode_count} similar episodes, you may have a repeatable workflow:\n"
            f"Steps: {steps_desc}\n"
            f"Confidence: {confidence:.0%}\n"
            f"Say 'yes' to create this as a procedure, or dismiss."
        )
        tid = self.create_trigger(
            user_id=user_id,
            trigger_type="procedure_suggestion",
            title=title,
            detail=detail,
            source_type="episode",
            sub_user_id=sub_user_id,
        )
        return 1 if tid > 0 else 0

    def process_user_triggers(self, user_id: str) -> dict:
        """Process pending triggers for a specific user only. Returns stats."""
        pending = self.get_pending_triggers(user_id=user_id)
        fired = 0
        errors = 0
        for trigger in pending:
            try:
                self.fire_trigger(trigger["id"])
                fired += 1
            except Exception as e:
                logger.error(f"⚠️ Trigger {trigger['id']} failed: {e}")
                errors += 1
        return {"processed": len(pending), "fired": fired, "errors": errors}

    def process_all_triggers(self) -> dict:
        """Process all pending triggers across all users. Returns stats."""
        pending = self.get_pending_triggers()
        fired = 0
        errors = 0
        for trigger in pending:
            try:
                self.fire_trigger(trigger["id"])
                fired += 1
            except Exception as e:
                logger.error(f"⚠️ Trigger {trigger['id']} failed: {e}")
                errors += 1
        return {"processed": len(pending), "fired": fired, "errors": errors}
