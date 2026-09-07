"""Users, API keys, capture policy, email codes, OAuth codes.

Part of :class:`cloud.store.CloudStore`; split out of the former single-file store.
"""

import hashlib
import json
import secrets
from typing import Optional


class AuthMixin:
    """Users, API keys, capture policy, email codes, OAuth codes."""


    # ---- Auth ----

    # Built-in keyword packs for the capture boundary. Deterministic,
    # word-boundary substring matching — NOT a probabilistic classifier
    # (a churned Pro user's explicit requirement: enforceable controls, not
    # a prompt asking the model to behave). Opt-in per category; users tune
    # with their own deny_keywords. Conservative on purpose; documented as
    # keyword-based so operators know to review coverage.
    CAPTURE_CATEGORY_PACKS = {
        "health": [
            "diagnosis", "diagnosed", "prescription", "prescribed", "medication",
            "symptom", "disease", "illness", "patient", "medical record", "therapy",
            "therapist", "mental health", "depression", "anxiety disorder",
            "blood pressure", "diabetes", "cancer", "hiv", "pregnancy", "pregnant",
            "surgery", "clinical", "psychiatric", "antidepressant",
        ],
        "legal": [
            "lawsuit", "litigation", "attorney", "plaintiff", "defendant",
            "settlement agreement", "subpoena", "court case", "legal proceeding",
            "deposition", "indictment", "restraining order", "custody battle",
            "criminal charge", "plea deal", "confidential settlement",
        ],
        "financial": [
            "bank account", "credit card number", "social security number", "ssn",
            "net worth", "tax return", "account number", "routing number",
            "salary is", "annual income", "debit card", "iban", "wire transfer",
        ],
        "credentials": [
            "password is", "api key", "api_key", "secret key", "access token",
            "private key", "ssh key", "-----begin", "bearer ", "client secret",
            "2fa code", "otp code", "recovery code", "seed phrase", "mnemonic phrase",
        ],
        "location": [
            "home address", "lives at", "street address", "zip code", "postal code",
            "gps coordinates", "latitude", "longitude", "my address is",
            "apartment number", "house number",
        ],
        "relationships": [
            "my wife", "my husband", "my girlfriend", "my boyfriend", "my partner",
            "my ex", "divorce", "affair", "custody", "my son", "my daughter",
            "my child", "family conflict", "estranged",
        ],
    }

    @staticmethod
    def _compile_capture_policy(policy: dict) -> list:
        """Build the effective deny-keyword list (lowercased) from a policy's
        enabled category packs plus custom deny_keywords. Pure/deterministic."""
        if not policy:
            return []
        kws: list = []
        for cat in (policy.get("deny_categories") or []):
            kws.extend(AuthMixin.CAPTURE_CATEGORY_PACKS.get(cat, []))
        for kw in (policy.get("deny_keywords") or []):
            if isinstance(kw, str) and kw.strip():
                kws.append(kw.strip())
        return [k.lower() for k in kws]

    @staticmethod
    def apply_capture_policy_to_facts(facts: list, deny_keywords: list) -> tuple:
        """Split facts into (kept, dropped) by the compiled deny-keyword list.
        Word-boundary-ish: matches keyword as a substring after lowercasing,
        so multi-word phrases ('bank account') and single terms ('ssn') both
        work. Deterministic — same input always yields same split."""
        if not deny_keywords or not facts:
            return list(facts), []
        kept, dropped = [], []
        for f in facts:
            text = (f if isinstance(f, str) else str(f)).lower()
            if any(kw in text for kw in deny_keywords):
                dropped.append(f)
            else:
                kept.append(f)
        return kept, dropped

    def get_capture_policy(self, user_id: str) -> dict:
        """Read the account's capture policy (empty dict = capture everything)."""
        with self._cursor() as cur:
            cur.execute("SELECT settings->'capture_policy' FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            return (row[0] if row and row[0] else {}) or {}

    def set_capture_policy(self, user_id: str, policy: dict) -> dict:
        """Persist the account's capture policy. Merges into settings JSONB."""
        with self._cursor() as cur:
            cur.execute(
                """UPDATE users
                   SET settings = COALESCE(settings, '{}'::jsonb)
                                  || jsonb_build_object('capture_policy', %s::jsonb)
                   WHERE id = %s
                   RETURNING settings->'capture_policy'""",
                (json.dumps(policy), user_id)
            )
            row = cur.fetchone()
        self.cache.invalidate(f"capture_policy:{user_id}")
        return (row[0] if row else policy) or policy

    def create_user(self, email: str, source: str = None) -> str:
        """Create user, return user_id.

        `source` records where they came from — a campaign tag off the landing
        URL, or the flow they signed up through. It is written once, at
        creation, and never updated: attribution that changes later is not
        attribution.
        """
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO users (email, signup_source) VALUES (%s, %s) RETURNING id",
                (email, source or None)
            )
            return str(cur.fetchone()[0])

    def get_user_by_email(self, email: str) -> Optional[str]:
        """Get user_id by email."""
        with self._cursor() as cur:
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
            return str(row[0]) if row else None

    def get_user_email(self, user_id: str) -> Optional[str]:
        """Get email by user_id."""
        with self._cursor() as cur:
            cur.execute("SELECT email FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            return row[0] if row else None

    def create_api_key(self, user_id: str, name: str = "default") -> str:
        """Generate API key, store hash, return raw key."""
        raw_key = f"om-{secrets.token_urlsafe(32)}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_prefix = raw_key[:10]

        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO api_keys (user_id, key_hash, key_prefix, name)
                   VALUES (%s, %s, %s, %s)""",
                (user_id, key_hash, key_prefix, name)
            )
        return raw_key

    def verify_api_key(self, raw_key: str) -> Optional[str]:
        """Verify API key, return user_id or None. Cached 60s."""
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        # Check cache first
        cache_key = f"auth:{key_hash[:16]}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        with self._cursor() as cur:
            cur.execute(
                """SELECT user_id FROM api_keys 
                   WHERE key_hash = %s AND is_active = TRUE""",
                (key_hash,)
            )
            row = cur.fetchone()
            if row:
                user_id = str(row[0])
                # Cache the result for 60s
                self.cache.set(cache_key, user_id, ttl=60)
                # Update last_used (non-blocking, skip if fails)
                try:
                    cur.execute(
                        "UPDATE api_keys SET last_used_at = NOW() WHERE key_hash = %s",
                        (key_hash,)
                    )
                except Exception:
                    pass
                return user_id
            # Cache negative result too (prevents brute force DB hits)
            self.cache.set(cache_key, False, ttl=30)
            return None

    def update_last_mcp_call(self, raw_key: str) -> None:
        """Mark that this API key was used for an MCP call. Non-blocking — failure is silent.

        Currently unused (previously powered the /connect/claude health check).
        Kept for future "last active" indicators. Safe to call — never affects auth.
        """
        try:
            key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
            with self._cursor() as cur:
                cur.execute(
                    "UPDATE api_keys SET last_mcp_call_at = NOW() WHERE key_hash = %s",
                    (key_hash,)
                )
        except Exception:
            pass  # never break MCP traffic on a tracking failure

    def get_last_mcp_call(self, user_id: str) -> Optional[str]:
        """Return ISO timestamp of most recent MCP call across user's active keys, or None."""
        try:
            with self._cursor() as cur:
                cur.execute(
                    """SELECT MAX(last_mcp_call_at) FROM api_keys
                       WHERE user_id = %s AND is_active = TRUE""",
                    (user_id,)
                )
                row = cur.fetchone()
                if row and row[0]:
                    return row[0].isoformat()
        except Exception:
            pass
        return None

    def list_api_keys(self, user_id: str) -> list:
        """List all API keys for a user (without hashes)."""
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT id, name, key_prefix, is_active, created_at, last_used_at
                   FROM api_keys WHERE user_id = %s
                   ORDER BY created_at DESC""",
                (user_id,)
            )
            return [{
                "id": r["id"],
                "name": r["name"],
                "prefix": r["key_prefix"],
                "active": r["is_active"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "last_used": r["last_used_at"].isoformat() if r["last_used_at"] else None,
            } for r in cur.fetchall()]

    def revoke_api_key(self, user_id: str, key_id: str) -> bool:
        """Revoke a specific API key by ID."""
        with self._cursor() as cur:
            cur.execute(
                """UPDATE api_keys SET is_active = FALSE
                   WHERE id = %s AND user_id = %s AND is_active = TRUE
                   RETURNING key_hash""",
                (key_id, user_id)
            )
            row = cur.fetchone()
            if row:
                # Invalidate cache for this key
                self.cache.invalidate(f"auth:{row[0][:16]}")
                return True
            return False

    def rename_api_key(self, user_id: str, key_id: str, new_name: str) -> bool:
        """Rename an API key."""
        with self._cursor() as cur:
            cur.execute(
                "UPDATE api_keys SET name = %s WHERE id = %s AND user_id = %s",
                (new_name, key_id, user_id)
            )
            return cur.rowcount > 0

    def reset_api_key(self, user_id: str) -> str:
        """Deactivate all old keys and create a new one."""
        with self._cursor() as cur:
            cur.execute(
                "UPDATE api_keys SET is_active = FALSE WHERE user_id = %s",
                (user_id,)
            )
        # Invalidate all auth cache
        self.cache.invalidate("auth:")
        return self.create_api_key(user_id)

    # ---- OAuth ----

    def save_email_code(self, email: str, code: str):
        """Save email verification code (expires in 10 min)."""
        with self._cursor() as cur:
            cur.execute(
                """CREATE TABLE IF NOT EXISTS email_codes (
                    email TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )"""
            )
            cur.execute(
                """INSERT INTO email_codes (email, code, created_at) 
                   VALUES (%s, %s, NOW())
                   ON CONFLICT (email) DO UPDATE SET code = %s, created_at = NOW()""",
                (email, code, code)
            )

    def verify_email_code(self, email: str, code: str) -> bool:
        """Verify email code (valid for 10 min)."""
        with self._cursor() as cur:
            cur.execute(
                """SELECT 1 FROM email_codes 
                   WHERE email = %s AND code = %s 
                   AND created_at > NOW() - INTERVAL '10 minutes'""",
                (email, code)
            )
            if cur.fetchone():
                cur.execute("DELETE FROM email_codes WHERE email = %s", (email,))
                return True
            return False

    def save_oauth_code(self, code: str, user_id: str, redirect_uri: str, state: str,
                        code_challenge: str = None, code_challenge_method: str = None):
        """Save OAuth authorization code (expires in 5 min).

        Stores the PKCE challenge (RFC 7636) when provided so the token exchange
        can verify the ``code_verifier``. Columns are added idempotently so the
        table upgrades in place for the legacy (non-PKCE) ChatGPT flow.
        """
        with self._cursor() as cur:
            cur.execute(
                """CREATE TABLE IF NOT EXISTS oauth_codes (
                    code TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    redirect_uri TEXT,
                    state TEXT,
                    code_challenge TEXT,
                    code_challenge_method TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )"""
            )
            cur.execute("ALTER TABLE oauth_codes ADD COLUMN IF NOT EXISTS code_challenge TEXT")
            cur.execute("ALTER TABLE oauth_codes ADD COLUMN IF NOT EXISTS code_challenge_method TEXT")
            cur.execute(
                """INSERT INTO oauth_codes (code, user_id, redirect_uri, state, code_challenge, code_challenge_method)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (code, user_id, redirect_uri, state, code_challenge, code_challenge_method)
            )

    def verify_oauth_code(self, code: str) -> Optional[dict]:
        """Verify and consume OAuth code. Returns
        {user_id, redirect_uri, state, code_challenge, code_challenge_method} or None."""
        with self._cursor(dict_cursor=True) as cur:
            # Idempotent guard: a pre-deploy code redeemed before the first new
            # save_oauth_code ran would otherwise hit a missing column.
            cur.execute("ALTER TABLE oauth_codes ADD COLUMN IF NOT EXISTS code_challenge TEXT")
            cur.execute("ALTER TABLE oauth_codes ADD COLUMN IF NOT EXISTS code_challenge_method TEXT")
            cur.execute(
                """SELECT user_id, redirect_uri, state, code_challenge, code_challenge_method
                   FROM oauth_codes
                   WHERE code = %s AND created_at > NOW() - INTERVAL '5 minutes'""",
                (code,)
            )
            row = cur.fetchone()
            if row:
                cur.execute("DELETE FROM oauth_codes WHERE code = %s", (code,))
                return {
                    "user_id": str(row["user_id"]),
                    "redirect_uri": row["redirect_uri"],
                    "state": row["state"],
                    "code_challenge": row["code_challenge"],
                    "code_challenge_method": row["code_challenge_method"],
                }
            return None
