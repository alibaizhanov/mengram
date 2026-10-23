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
