"""The plan a Paddle subscription grants comes from the price paid, never from
custom_data: the browser opening the checkout sets custom_data, so it could
name a $99 plan on a $5 price (audit 2026-10-23)."""
import datetime
import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
from cloud import billing

PRICES = {"starter": "pri_starter", "pro": "pri_pro", "growth": "pri_growth", "business": "pri_business"}
ANNUAL = {"starter": "pri_starter_y", "pro": "pri_pro_y", "growth": "pri_growth_y", "business": "pri_business_y"}
SECRET = "whsec_test"


class FakeStore:
    def __init__(self):
        self.updates = []
        self.current_sub_id = "sub_1"

    def get_subscription(self, user_id):
        return {"paddle_subscription_id": self.current_sub_id, "plan": "pro", "status": "active"}

    def update_subscription(self, user_id, **kwargs):
        self.updates.append((user_id, kwargs))

    def get_user_by_paddle_customer(self, customer_id):
        return None

    def mark_user_checkouts_completed(self, user_id):
        pass


@pytest.fixture
def paddle(monkeypatch):
    monkeypatch.setattr(billing, "PADDLE_PRICES", dict(PRICES))
    monkeypatch.setattr(billing, "PADDLE_PRICES_ANNUAL", dict(ANNUAL))
    monkeypatch.setattr(billing, "PADDLE_WEBHOOK_SECRET", SECRET)
    store = FakeStore()
    app = FastAPI()
    app.include_router(billing.billing_router(store, auth=lambda: None))
    return TestClient(app), store


def _send(client, event_type, data):
    body = json.dumps({"event_type": event_type, "data": data})
    ts = str(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))
    h1 = hmac.new(SECRET.encode(), f"{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    return client.post("/webhooks/paddle", content=body,
                       headers={"Paddle-Signature": f"ts={ts};h1={h1}",
                                "Content-Type": "application/json"})


def _sub(price_id, custom_plan=None):
    custom = {"mengram_user_id": "u1"}
    if custom_plan:
        custom["plan"] = custom_plan
    return {"id": "sub_1", "customer_id": "ctm_1", "custom_data": custom,
            "items": [{"price": {"id": price_id}}]}


def test_plan_for_price(monkeypatch):
    monkeypatch.setattr(billing, "PADDLE_PRICES", dict(PRICES))
    monkeypatch.setattr(billing, "PADDLE_PRICES_ANNUAL", dict(ANNUAL))
    assert billing.plan_for_price("pri_starter") == "starter"
    assert billing.plan_for_price("pri_business_y") == "business"
    assert billing.plan_for_price("pri_unknown") is None
    assert billing.plan_for_price("") is None


def test_an_unset_price_env_never_matches_an_empty_price_id(monkeypatch):
    monkeypatch.setattr(billing, "PADDLE_PRICES", {k: "" for k in PRICES})
    monkeypatch.setattr(billing, "PADDLE_PRICES_ANNUAL", {k: "" for k in ANNUAL})
    assert billing.plan_for_price("") is None


def test_activation_ignores_the_plan_named_in_custom_data(paddle):
    client, store = paddle
    r = _send(client, "subscription.activated", _sub("pri_starter", custom_plan="business"))
    assert r.status_code == 200
    (user_id, updates), = store.updates
    assert user_id == "u1"
    assert updates["plan"] == "starter"


def test_activation_with_an_unknown_price_grants_no_plan(paddle):
    client, store = paddle
    r = _send(client, "subscription.activated", _sub("pri_someone_elses", custom_plan="business"))
    assert r.status_code == 200
    (_, updates), = store.updates
    assert "plan" not in updates
    assert updates["status"] == "active"


def test_update_follows_the_new_price(paddle):
    client, store = paddle
    r = _send(client, "subscription.updated", {**_sub("pri_growth_y"), "status": "active"})
    assert r.status_code == 200
    (_, updates), = store.updates
    assert updates["plan"] == "growth"


# ---- cancel / pause / resume (audit 2026-10-24) -------------------------------
# subscription.canceled fires when access actually ends, and Paddle sends no
# billing period with it; the handler used to keep the paid plan "until the
# period end" it never received, so a canceled subscription stayed paid.

def test_cancel_downgrades_to_free_now(paddle):
    client, store = paddle
    data = {**_sub("pri_pro"), "status": "canceled", "current_billing_period": None,
            "canceled_at": "2026-10-24T10:00:00Z"}
    assert _send(client, "subscription.canceled", data).status_code == 200
    (user_id, updates), = store.updates
    assert user_id == "u1"
    assert updates == {"status": "canceled", "plan": "free", "canceled_at": "2026-10-24T10:00:00Z"}


def test_cancel_without_canceled_at_still_records_when(paddle):
    client, store = paddle
    _send(client, "subscription.canceled", {**_sub("pri_pro"), "status": "canceled"})
    (_, updates), = store.updates
    assert updates["plan"] == "free" and updates["canceled_at"]


def test_pause_downgrades_to_free(paddle):
    client, store = paddle
    _send(client, "subscription.paused", {**_sub("pri_pro"), "status": "paused"})
    (_, updates), = store.updates
    assert updates == {"status": "paused", "plan": "free"}


@pytest.mark.parametrize("status", ["canceled", "paused"])
def test_update_into_canceled_or_paused_does_not_regrant_the_price(paddle, status):
    client, store = paddle
    _send(client, "subscription.updated", {**_sub("pri_business"), "status": status})
    (_, updates), = store.updates
    assert updates["plan"] == "free"
    assert updates["status"] == status


def test_resume_rederives_the_plan_from_the_price(paddle):
    client, store = paddle
    data = {**_sub("pri_growth", custom_plan="business"), "status": "active",
            "current_billing_period": {"starts_at": "2026-10-24T00:00:00Z", "ends_at": "2026-11-24T00:00:00Z"}}
    _send(client, "subscription.resumed", data)
    (_, updates), = store.updates
    assert updates["plan"] == "growth"
    assert updates["status"] == "active"
    assert updates["current_period_end"] == "2026-11-24T00:00:00Z"


def test_past_due_keeps_the_plan(paddle):
    client, store = paddle
    _send(client, "subscription.past_due", {**_sub("pri_pro"), "status": "past_due"})
    (_, updates), = store.updates
    assert updates == {"status": "past_due"}


def test_event_for_a_replaced_subscription_is_ignored(paddle):
    client, store = paddle
    store.current_sub_id = "sub_new"
    _send(client, "subscription.canceled", {**_sub("pri_pro"), "status": "canceled"})
    assert store.updates == []


# ---- the plan auth() grants ---------------------------------------------------
NOW = datetime.datetime(2026, 10, 24, tzinfo=datetime.timezone.utc)
KNOWN = {"free": {}, "pro": {}, "business": {}}


def test_effective_plan_active_and_unknown():
    assert billing.effective_plan({"plan": "pro", "status": "active"}, KNOWN, NOW) == "pro"
    assert billing.effective_plan({"plan": "team", "status": "active"}, KNOWN, NOW) == "free"
    assert billing.effective_plan(None, KNOWN, NOW) == "free"
    # dunning keeps access
    assert billing.effective_plan({"plan": "pro", "status": "past_due"}, KNOWN, NOW) == "pro"


def test_a_legacy_canceled_row_without_a_period_end_is_free():
    # what the old handler left behind: status canceled, paid plan, no end
    sub = {"plan": "pro", "status": "canceled", "current_period_end": None}
    assert billing.effective_plan(sub, KNOWN, NOW) == "free"
    assert billing.effective_plan({**sub, "status": "paused"}, KNOWN, NOW) == "free"


def test_a_canceled_row_keeps_its_plan_until_a_future_period_end():
    future = {"plan": "pro", "status": "canceled", "current_period_end": "2026-11-01T00:00:00Z"}
    past = {"plan": "pro", "status": "canceled",
            "current_period_end": datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc)}
    assert billing.effective_plan(future, KNOWN, NOW) == "pro"
    assert billing.effective_plan(past, KNOWN, NOW) == "free"


# ---- Paddle calls are bounded and off the event loop ---------------------------

def test_paddle_request_has_a_timeout(monkeypatch):
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"data": {}}'

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _Resp()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert billing._paddle_request("GET", "/x") == {"data": {}}
    assert seen["timeout"] == 30


def test_portal_runs_the_paddle_call_in_a_worker_thread(monkeypatch):
    import asyncio
    seen = {}

    def fake_request(method, path, body=None):
        # On the event loop thread a loop is running; in a worker thread none is.
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return {"data": {"urls": {"general": {"overview": "https://portal"}}}}

    class _Store:
        def get_subscription(self, user_id):
            return {"paddle_customer_id": "ctm_1"}

    from cloud.auth import AuthContext
    monkeypatch.setattr(billing, "PADDLE_API_KEY", "k")
    monkeypatch.setattr(billing, "_paddle_request", fake_request)
    app = FastAPI()
    ctx = AuthContext(user_id="u1", plan="pro", rate_limit=120)
    app.include_router(billing.billing_router(_Store(), auth=lambda: ctx))
    r = TestClient(app).post("/v1/billing/portal")
    assert r.status_code == 200, r.text
    assert r.json() == {"portal_url": "https://portal"}
    assert seen["on_loop"] is False
