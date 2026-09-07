"""Memory Health monitor and digest queues.

Part of :class:`cloud.store.CloudStore`; split out of the former single-file store.
"""

import json
from typing import Optional


class HealthMixin:
    """Memory Health monitor and digest queues."""


    # ---- Memory Health weekly digest queue (Day 4 of Memory Health Monitor) ----

    def get_users_for_health_digest(self) -> list:
        """Return users with degraded/critical health snapshots, with a
        ready-to-render summary line and a joined recommendations string.

        Called by the drip cron Mondays 09:00–10:00 UTC. Dedup is handled
        upstream via `try_record_drip` with an ISO-week suffix in the
        drip_type, so each user gets at most one digest per week.

        Only users with a real subscription (not lazy-created free) get
        the digest — we don't want to spam ghost signups.
        """
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT u.id::text AS user_id, u.email,
                          mh.overall_status, mh.details, mh.recommendations
                   FROM memory_health mh
                   JOIN users u ON u.id = mh.user_id
                   WHERE mh.overall_status IN ('degraded', 'critical')
                     AND mh.updated_at > NOW() - INTERVAL '7 days'"""
            )
            out = []
            for r in cur.fetchall():
                details = r["details"]
                if isinstance(details, str):
                    import json as _json
                    try:
                        details = _json.loads(details)
                    except Exception:
                        details = {}
                summary = (
                    f"Status: {r['overall_status'].upper()} · "
                    f"Searches: {details.get('searches', '?')} · "
                    f"Mean relevance: {details.get('mean_score', '?'):.3f} "
                    f"(target ≥ 0.60) · "
                    f"Low-quality: {details.get('low_quality_count', 0)}"
                ) if isinstance(details, dict) else f"Status: {r['overall_status'].upper()}"
                recs = " ".join(r["recommendations"] or []) or "Review your recently-added content for noise."
                out.append({
                    "user_id": r["user_id"],
                    "email": r["email"],
                    "summary": summary,
                    "recommendations": recs,
                })
            return out

    def get_users_for_insights_digest(self, min_insights: int = 3,
                                       window_days: int = 7,
                                       max_samples: int = 5) -> list:
        """Return users whose reflection layer was refreshed in the trailing
        window — ready-to-render payload for the weekly Insights digest email.

        Pairs with the Dream Cycle cron: when reflection generated/refreshed
        N >= min_insights entries for a user in the last week, they get a
        digest. Stale or skipped users (quota_skipped, dormant > 30d) won't
        appear because their refreshed_at didn't move.

        Samples carry the top max_samples reflections by recency, so the
        email body can render a real preview instead of a count-only nudge.
        """
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT e.user_id::text AS user_id, u.email,
                          COUNT(k.id) AS new_insights,
                          (SELECT array_agg(row_to_json(t))
                           FROM (
                               SELECT k2.scope, k2.title, k2.content,
                                      k2.confidence, k2.refreshed_at
                               FROM knowledge k2
                               JOIN entities e2 ON e2.id = k2.entity_id
                               WHERE e2.user_id = e.user_id
                                 AND e2.sub_user_id = e.sub_user_id
                                 AND k2.type = 'reflection'
                                 AND k2.refreshed_at > NOW() - make_interval(days => %s)
                               ORDER BY k2.refreshed_at DESC
                               LIMIT %s
                           ) t
                          ) AS samples
                   FROM knowledge k
                   JOIN entities e ON e.id = k.entity_id
                   JOIN users u ON u.id = e.user_id
                   WHERE k.type = 'reflection'
                     AND k.refreshed_at > NOW() - make_interval(days => %s)
                     AND u.email IS NOT NULL
                     AND e.sub_user_id = 'default'
                   GROUP BY e.user_id, e.sub_user_id, u.email
                   HAVING COUNT(k.id) >= %s
                   ORDER BY new_insights DESC""",
                (window_days, max_samples, window_days, min_insights)
            )
            return [
                {
                    "user_id": r["user_id"],
                    "email": r["email"],
                    "new_insights": int(r["new_insights"]),
                    "samples": r["samples"] or [],
                }
                for r in cur.fetchall()
            ]

    # ---- Memory Health snapshot read (Day 5 of Memory Health Monitor) ----

    def get_memory_health(self, user_id: str) -> Optional[dict]:
        """Return the latest health snapshot for a user, or None if no
        snapshot has been computed yet (fewer than 5 scored searches in
        the trailing 24h window when the cron last ran)."""
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT user_id, computed_at, overall_status, details,
                          recommendations, updated_at
                   FROM memory_health
                   WHERE user_id = %s::uuid""",
                (user_id,)
            )
            row = cur.fetchone()
            if not row:
                return None
            details = row["details"]
            if isinstance(details, str):
                import json as _json
                try:
                    details = _json.loads(details)
                except Exception:
                    pass
            return {
                "user_id": str(row["user_id"]),
                "computed_at": row["computed_at"].isoformat() if row["computed_at"] else None,
                "overall_status": row["overall_status"],
                "details": details,
                "recommendations": list(row["recommendations"] or []),
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }

    # ---- Memory Health Aggregation (Day 2 of Memory Health Monitor) ----

    # Status thresholds — mean score over recent searches
    _HEALTH_THRESHOLD_HEALTHY = 0.6   # mean ≥ 0.6 = healthy
    _HEALTH_THRESHOLD_DEGRADED = 0.4  # mean ≥ 0.4 = degraded; below = critical
    _LOW_QUALITY_SCORE = 0.4          # individual searches below this = low-quality

    def aggregate_memory_health(self, window_hours: int = 24) -> dict:
        """Compute per-user retrieval health over the last `window_hours` of
        scored searches, upsert into `memory_health` table.

        Skips users with fewer than 5 scored searches in the window — too
        little signal to draw conclusions.

        Returns: {users_updated, healthy, degraded, critical}.
        """
        import json as _json
        stats = {"users_updated": 0, "healthy": 0, "degraded": 0, "critical": 0}

        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT user_id,
                          COUNT(*) AS n,
                          AVG(query_score) AS mean,
                          STDDEV_POP(query_score) AS stddev,
                          MIN(query_score) AS min_score,
                          MAX(query_score) AS max_score,
                          COUNT(*) FILTER (WHERE query_score < %s) AS low_count,
                          COUNT(DISTINCT query_language) AS lang_count
                   FROM usage_log
                   WHERE action LIKE 'search%%'
                     AND query_score IS NOT NULL
                     AND created_at >= NOW() - make_interval(hours => %s)
                   GROUP BY user_id
                   HAVING COUNT(*) >= 5""",
                (self._LOW_QUALITY_SCORE, window_hours)
            )
            per_user = cur.fetchall()

            for row in per_user:
                uid = str(row["user_id"])
                mean = float(row["mean"] or 0)

                if mean >= self._HEALTH_THRESHOLD_HEALTHY:
                    status = "healthy"
                elif mean >= self._HEALTH_THRESHOLD_DEGRADED:
                    status = "degraded"
                else:
                    status = "critical"

                # Per-language breakdown
                cur.execute(
                    """SELECT COALESCE(query_language, 'unknown') AS lang,
                              COUNT(*) AS n,
                              AVG(query_score) AS mean
                       FROM usage_log
                       WHERE user_id = %s::uuid
                         AND action LIKE 'search%%'
                         AND query_score IS NOT NULL
                         AND created_at >= NOW() - make_interval(hours => %s)
                       GROUP BY query_language""",
                    (uid, window_hours)
                )
                lang_breakdown = [
                    {"lang": r["lang"], "count": r["n"], "mean_score": float(r["mean"] or 0)}
                    for r in cur.fetchall()
                ]

                # Recommendations
                recs = []
                if status == "critical":
                    recs.append("Retrieval relevance is below 0.4 — likely silent quality drop. Review recent additions for noise.")
                if status == "degraded":
                    recs.append("Mean relevance 0.4–0.6 — some queries returning weak matches. Consider running `dedup` to clean similar entities.")
                if row["low_count"] >= 5:
                    recs.append(f"{row['low_count']} searches under 0.4 in window — flag for content audit.")
                if row["lang_count"] and row["lang_count"] >= 2:
                    weakest = min(lang_breakdown, key=lambda x: x["mean_score"])
                    if weakest["mean_score"] < self._HEALTH_THRESHOLD_DEGRADED:
                        recs.append(f"Lowest-quality language: {weakest['lang']} ({weakest['mean_score']:.2f} mean). "
                                     "May need more content in that language.")

                details = {
                    "window_hours": window_hours,
                    "searches": row["n"],
                    "mean_score": round(mean, 4),
                    "stddev_score": round(float(row["stddev"] or 0), 4),
                    "min_score": round(float(row["min_score"] or 0), 4),
                    "max_score": round(float(row["max_score"] or 0), 4),
                    "low_quality_count": row["low_count"],
                    "lang_breakdown": lang_breakdown,
                }

                cur.execute(
                    """INSERT INTO memory_health (user_id, computed_at, overall_status, details, recommendations, updated_at)
                       VALUES (%s::uuid, NOW(), %s, %s::jsonb, %s, NOW())
                       ON CONFLICT (user_id) DO UPDATE SET
                           computed_at = EXCLUDED.computed_at,
                           overall_status = EXCLUDED.overall_status,
                           details = EXCLUDED.details,
                           recommendations = EXCLUDED.recommendations,
                           updated_at = NOW()""",
                    (uid, status, _json.dumps(details), recs)
                )
                stats["users_updated"] += 1
                stats[status] += 1

        return stats
