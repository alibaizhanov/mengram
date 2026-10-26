"""Store-level fixes from the 2026-10-24 backend audit, tested with fake
cursors (no database): DDL once per process, account deletion as one real
transaction that detaches other members' shared entities, the unclaimed
connector account, the profile charge before the model call, and batched
dedup_all."""
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from cloud.store._auth import AuthMixin
from cloud.store._core import CoreMixin
from cloud.store._entities import EntityMixin
from cloud.store._profile import ProfileMixin
from cloud.store._search import SearchMixin
from cloud.store._teams import TeamMixin
from cloud.store._webhooks import WebhookMixin


class _Cur:
    """Records SQL; answers via the owner's `answer(sql, params)`."""

    def __init__(self, owner):
        self.owner = owner
        self.rowcount = 0
        self._result = []

    def execute(self, sql, params=None):
        self.owner.sql.append((" ".join(sql.split()), params))
        self._result = self.owner.answer(sql, params)
        self.rowcount = 1

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None

    def close(self):
        pass


class _Cache:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v, ttl=None):
        self.d[k] = v

    def invalidate(self, prefix=""):
        for k in [k for k in self.d if k.startswith(prefix)]:
            del self.d[k]


class _Base:
    def __init__(self):
        self.sql = []
        self.cache = _Cache()

    def answer(self, sql, params):
        return []

    @contextmanager
    def _cursor(self, dict_cursor=False):
        yield _Cur(self)

    def statements(self, needle):
        return [s for s, _ in self.sql if needle in s]


# ---- 1. DDL at most once per process -----------------------------------------

class _Teams(_Base, TeamMixin):
    pass


def test_team_ddl_runs_once_across_cache_misses():
    s = _Teams()
    s.get_user_team_ids("u1")
    s.cache.invalidate("teams:")  # the 60 s cache expiring
    s.get_user_team_ids("u1")
    s.get_user_team_ids("u2")
    assert len(s.statements("ALTER TABLE entities")) == 1
    assert len(s.statements("CREATE TABLE IF NOT EXISTS teams")) == 1
    assert len(s.statements("SELECT team_id FROM team_members")) == 3  # every miss still reads


def test_team_ddl_is_retried_if_it_failed():
    class Failing(_Teams):
        def answer(self, sql, params):
            if "ALTER TABLE" in sql and not getattr(self, "ok", False):
                raise RuntimeError("lock timeout")
            return []
    s = Failing()
    with pytest.raises(RuntimeError):
        s.ensure_teams_table()
    s.ok = True
    s.ensure_teams_table()
    s.ensure_teams_table()
    assert len(s.statements("ALTER TABLE entities")) == 2


class _Hooks(_Base, WebhookMixin):
    pass


def test_webhook_ddl_runs_once():
    s = _Hooks()
    for _ in range(3):
        s.ensure_webhooks_table()
    assert len(s.statements("ALTER TABLE webhooks")) == 1


# ---- 2. _transaction ----------------------------------------------------------

class _Conn:
    def __init__(self):
        self.autocommit = True
        self.events = []

    def cursor(self, cursor_factory=None):
        conn = self

        class C:
            def execute(self, sql, params=None):
                assert conn.autocommit is False, "statement ran outside the transaction"
                conn.events.append(("execute", sql))

            def close(self):
                conn.events.append(("close",))
        return C()

    def commit(self):
        self.events.append(("commit",))

    def rollback(self):
        self.events.append(("rollback",))


class _Pool:
    def __init__(self, conn):
        self.conn = conn
        self.returned = []

    def getconn(self):
        return self.conn

    def putconn(self, conn, close=False):
        self.returned.append(conn)


def _core(conn):
    s = CoreMixin.__new__(CoreMixin)
    s._pool = _Pool(conn)
    s.conn = None
    return s


def test_transaction_commits_and_restores_autocommit():
    conn = _Conn()
    s = _core(conn)
    with s._transaction() as cur:
        cur.execute("DELETE 1")
        cur.execute("DELETE 2")
    assert [e[0] for e in conn.events] == ["execute", "execute", "commit", "close"]
    assert conn.autocommit is True
    assert s._pool.returned == [conn]


def test_transaction_rolls_back_on_error():
    conn = _Conn()
    s = _core(conn)
    with pytest.raises(RuntimeError):
        with s._transaction() as cur:
            cur.execute("DELETE 1")
            raise RuntimeError("FK violation")
    kinds = [e[0] for e in conn.events]
    assert "commit" not in kinds and "rollback" in kinds
    assert conn.autocommit is True
    assert s._pool.returned == [conn]


# ---- 2. delete_account --------------------------------------------------------

class _Accounts(_Base, EntityMixin):
    ALL = ("entities", "episodes", "procedures", "conversation_chunks", "memory_triggers", "jobs",
           "memory_health", "drip_emails", "checkout_sessions", "webhooks", "agent_runs",
           "oauth_codes", "team_members", "api_keys", "usage_log", "subscriptions",
           "usage_counters", "teams", "email_codes")

    def __init__(self, fail_on=None):
        super().__init__()
        self.fail_on = fail_on
        self.in_transaction = False

    @contextmanager
    def _cursor(self, dict_cursor=False):
        raise AssertionError("delete_account must not write through an autocommit cursor")

    @contextmanager
    def _transaction(self, dict_cursor=False):
        self.in_transaction = True
        yield _Cur(self)

    def answer(self, sql, params):
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("violates foreign key constraint")
        if "unnest" in sql:
            return [(n,) for n in self.ALL]
        if "SELECT key_hash" in sql:
            return [("a" * 64,)]
        return []

    def get_user_email(self, user_id):
        return "a@b.c"

    def _schedule_matview_refresh(self):
        pass


def test_delete_account_detaches_other_members_entities_before_deleting_teams():
    s = _Accounts()
    counts = s.delete_account("u1")
    assert s.in_transaction
    order = [sql for sql, _ in s.sql]
    detach = next(i for i, q in enumerate(order) if q.startswith("UPDATE entities SET team_id = NULL"))
    members = next(i for i, q in enumerate(order)
                   if q.startswith("DELETE FROM team_members WHERE team_id IN"))
    teams = next(i for i, q in enumerate(order) if q.startswith("DELETE FROM teams"))
    users = next(i for i, q in enumerate(order) if q.startswith("DELETE FROM users"))
    assert detach < teams and members < teams < users
    assert "entities_unshared" in counts and counts["users"] == 1


def test_a_failing_delete_propagates_so_the_transaction_rolls_back():
    s = _Accounts(fail_on="DELETE FROM teams")
    with pytest.raises(RuntimeError):
        s.delete_account("u1")
    # nothing after the failure ran, and caches were left alone
    assert not s.statements("DELETE FROM users")


# ---- 6. unclaimed connector accounts ------------------------------------------

class _Auth(_Base, AuthMixin):
    def __init__(self, row):
        super().__init__()
        self.row = row

    def answer(self, sql, params):
        return [self.row] if self.row else []


@pytest.mark.parametrize("row, unclaimed", [
    (("oauth-connector", ["default"], False), True),       # made by send-code, never used
    (("oauth-connector", [], False), True),
    (("oauth-connector", ["default"], True), False),        # the key was used
    (("oauth-connector", ["default", "oauth-connector"], False), False),  # token issued
    (("oauth-connector", ["default", "dashboard"], False), False),        # signed in
    (("landing", ["default"], False), False),               # a normal signup
    (("github-oauth", ["github-oauth"], False), False),
    (None, False),                                          # no such user
])
def test_is_unclaimed_account(row, unclaimed):
    assert _Auth(row).is_unclaimed_account("u1") is unclaimed


# ---- 7. profile: charge before the model call ---------------------------------

class _Profile(_Base, ProfileMixin):
    def __init__(self, n_facts):
        super().__init__()
        self.n_facts = n_facts

    def get_all_entities_full(self, user_id, sub_user_id="default"):
        return [{"entity": "Ali", "type": "person",
                 "facts": [f"fact {i}" for i in range(self.n_facts)], "relations": []}]

    def get_episodes(self, user_id, limit=10, sub_user_id="default"):
        return []

    def get_procedures(self, user_id, limit=10, sub_user_id="default"):
        return []


class _QuotaExceeded(Exception):
    pass


@pytest.fixture
def fake_openai(monkeypatch):
    import openai
    calls = []

    class Client:
        def __init__(self, **k):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **k):
            calls.append(k)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="prompt"))])

    monkeypatch.setattr(openai, "OpenAI", Client)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    return calls


def test_exhausted_quota_stops_the_model_call(fake_openai):
    s = _Profile(5)

    def charge():
        raise _QuotaExceeded()
    with pytest.raises(_QuotaExceeded):
        s.get_profile("u1", charge=charge)
    assert fake_openai == []
    assert s.cache.d == {}  # nothing cached for free


def test_generation_is_charged_once_and_cached_reads_are_free(fake_openai):
    s = _Profile(5)
    charges = []
    assert s.get_profile("u1", charge=lambda: charges.append(1))["status"] == "ok"
    assert s.get_profile("u1", charge=lambda: charges.append(1))["status"] == "ok"
    assert charges == [1] and len(fake_openai) == 1


def test_too_little_memory_is_not_charged(fake_openai):
    charges = []
    assert _Profile(1).get_profile("u1", charge=lambda: charges.append(1))["status"] == "too_few_facts"
    assert charges == [] and fake_openai == []


# ---- 9. dedup_all in batches --------------------------------------------------

class _Dedup(_Base, SearchMixin):
    def __init__(self, ids):
        super().__init__()
        self.ids = ids
        self.deduped = []

    def answer(self, sql, params):
        after = params[2]
        return [(i, f"E{i}") for i in self.ids if after is None or i > after]

    def dedup_entity_facts(self, entity_id, name, llm):
        self.deduped.append(entity_id)
        return {"kept": [], "archived": ["dup"] if entity_id.endswith("1") else []}


def test_dedup_all_caps_the_model_calls_and_says_what_is_left():
    ids = [f"{i:04d}" for i in range(7)]
    s = _Dedup(ids)
    r = s.dedup_all_entities("u1", llm_client=None, batch=3)
    assert s.deduped == ids[:3]
    assert r["processed"] == 3 and r["remaining"] == 4 and r["next_after"] == "0002"
    assert r["total_archived"] == 1 and r["entities"] == [{"entity": "E0001", "archived": 1}]

    r = s.dedup_all_entities("u1", llm_client=None, after=r["next_after"], batch=3)
    r = s.dedup_all_entities("u1", llm_client=None, after=r["next_after"], batch=3)
    assert r["processed"] == 1 and r["remaining"] == 0 and r["next_after"] is None
    assert s.deduped == ids


def test_dedup_all_only_considers_entities_with_three_live_facts():
    s = _Dedup([])
    s.dedup_all_entities("u1", llm_client=None)
    (sql, _), = s.sql
    assert "f.archived = FALSE" in sql and ">= 3" in sql
    assert s.DEDUP_ALL_BATCH <= 50
