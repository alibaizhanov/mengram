"""Paddle billing: checkout, plan changes, customer portal, and the signed webhook.

Module level: Paddle configuration and the one-click checkout token used by the quota
and drip emails. `billing_router` mounts the five billing routes on the API.
"""

import datetime
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from cloud.auth import AuthContext
from cloud.plans import PLAN_QUOTAS

logger = logging.getLogger("mengram")

PADDLE_API_KEY = os.environ.get("PADDLE_API_KEY", "")
PADDLE_WEBHOOK_SECRET = os.environ.get("PADDLE_WEBHOOK_SECRET", "")
PADDLE_ENV = os.environ.get("PADDLE_ENVIRONMENT", "sandbox")
PADDLE_API_BASE = "https://api.paddle.com" if PADDLE_ENV == "production" else "https://sandbox-api.paddle.com"
PADDLE_PRICES = {
    "starter": os.environ.get("PADDLE_PRICE_STARTER", ""),
    "pro": os.environ.get("PADDLE_PRICE_PRO", ""),
    "growth": os.environ.get("PADDLE_PRICE_GROWTH", ""),
    "business": os.environ.get("PADDLE_PRICE_BUSINESS", ""),
}
PADDLE_PRICES_ANNUAL = {
    "starter": os.environ.get("PADDLE_PRICE_STARTER_ANNUAL", ""),
    "pro": os.environ.get("PADDLE_PRICE_PRO_ANNUAL", ""),
    "growth": os.environ.get("PADDLE_PRICE_GROWTH_ANNUAL", ""),
    "business": os.environ.get("PADDLE_PRICE_BUSINESS_ANNUAL", ""),
}

def _paddle_request(method: str, path: str, body: dict = None) -> dict:
    """Make authenticated Paddle API request."""
    import urllib.request, urllib.error
    url = f"{PADDLE_API_BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {PADDLE_API_KEY}",
            "Content-Type": "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        logger.error(f"Paddle API error {e.code}: {err_body}")
        raise Exception(f"Paddle API {e.code}: {err_body}")

def _sign_checkout_token(user_id: str, plan: str) -> str:
    """Create HMAC-signed token for one-click checkout from email. Expires monthly."""
    import hmac, hashlib
    if not PADDLE_WEBHOOK_SECRET:
        return ""
    month = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")
    msg = f"{user_id}:{plan}:{month}".encode()
    sig = hmac.new(PADDLE_WEBHOOK_SECRET.encode(), msg, hashlib.sha256).hexdigest()[:16]
    return f"{user_id}:{plan}:{month}:{sig}"

def _verify_checkout_token(token: str) -> tuple:
    """Verify and parse checkout token. Returns (user_id, plan) or raises."""
    import hmac, hashlib
    parts = token.split(":")
    if len(parts) != 4:
        raise HTTPException(status_code=400, detail="Invalid checkout token")
    user_id, plan, month, sig = parts
    if plan not in ("starter", "pro", "growth", "business"):
        raise HTTPException(status_code=400, detail="Invalid plan")
    if not PADDLE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Billing not configured")
    # Token valid for current and previous month (grace period)
    now = datetime.datetime.now(datetime.timezone.utc)
    valid_months = [now.strftime("%Y-%m"), (now.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")]
    if month not in valid_months:
        raise HTTPException(status_code=410, detail="Checkout link expired")
    msg = f"{user_id}:{plan}:{month}".encode()
    expected = hmac.new(PADDLE_WEBHOOK_SECRET.encode(), msg, hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=403, detail="Invalid token signature")
    return user_id, plan



def billing_router(store, auth) -> APIRouter:
    """Billing routes bound to the API's store and auth dependency."""
    router = APIRouter()

    @router.get("/checkout", tags=["Billing"])
    async def one_click_checkout(token: str = Query(...)):
        """One-click checkout from quota email. Redirects to Paddle checkout."""
        user_id, plan = _verify_checkout_token(token)
        if not PADDLE_API_KEY:
            raise HTTPException(status_code=503, detail="Billing not configured")
        price_id = PADDLE_PRICES.get(plan, "")
        if not price_id:
            raise HTTPException(status_code=400, detail=f"Unknown plan: {plan}")

        sub = store.get_subscription(user_id)
        customer_id = sub.get("paddle_customer_id")

        txn_body = {
            "items": [{"price_id": price_id, "quantity": 1}],
            "custom_data": {"mengram_user_id": user_id, "plan": plan},
        }
        if customer_id:
            txn_body["customer_id"] = customer_id

        try:
            result = _paddle_request("POST", "/transactions", txn_body)
            data = result.get("data", {})
            checkout_url = data.get("checkout", {}).get("url", "")
            transaction_id = data.get("id", "")
            if not checkout_url:
                raise HTTPException(status_code=502, detail="Paddle did not return checkout URL")
            # Record abandoned-checkout tracking row
            try:
                email = store.get_user_email(user_id)
                if email and transaction_id:
                    store.record_checkout_session(transaction_id, user_id, email, plan)
            except Exception as tracking_err:
                logger.warning(f"Checkout session tracking failed: {tracking_err}")
            from starlette.responses import RedirectResponse
            return RedirectResponse(url=checkout_url, status_code=303)
        except Exception as e:
            logger.error(f"One-click checkout error: {e}")
            # Fallback to dashboard billing page
            from starlette.responses import RedirectResponse
            return RedirectResponse(url="/dashboard?tab=billing", status_code=303)

    @router.get("/v1/billing", tags=["Billing"])
    async def get_billing(ctx: AuthContext = Depends(auth)):
        """Current subscription plan, usage, and quotas."""
        user_id = ctx.user_id
        sub = store.get_subscription(user_id)
        usage = store.get_all_usage_counts(user_id)
        quotas = PLAN_QUOTAS.get(ctx.plan, PLAN_QUOTAS["free"])
        annual_available = any(PADDLE_PRICES_ANNUAL.get(p) for p in ("starter", "pro", "growth", "business"))
        return {
            "plan": ctx.plan,
            "status": sub.get("status", "active"),
            "current_period_end": sub.get("current_period_end"),
            "usage": usage,
            "quotas": {k: v for k, v in quotas.items() if k != "rate_limit"},
            "rate_limit": quotas["rate_limit"],
            "annual_available": annual_available,
        }

    @router.post("/v1/billing/checkout", tags=["Billing"])
    async def create_checkout(
        plan: str = Query(..., pattern="^(starter|pro|growth|business)$"),
        billing: str = Query("monthly", pattern="^(monthly|annual)$"),
        ctx: AuthContext = Depends(auth),
    ):
        """Create Paddle checkout or update existing subscription for plan change."""
        user_id = ctx.user_id
        if not PADDLE_API_KEY:
            raise HTTPException(status_code=503, detail="Billing not configured")
        # Use annual price if available, fall back to monthly
        if billing == "annual":
            price_id = PADDLE_PRICES_ANNUAL.get(plan, "") or PADDLE_PRICES.get(plan, "")
        else:
            price_id = PADDLE_PRICES.get(plan, "")
        if not price_id:
            raise HTTPException(status_code=400, detail=f"Unknown plan: {plan}")

        # Prevent same-plan purchase and downgrades
        plan_order = {"free": 0, "starter": 1, "pro": 2, "growth": 3, "business": 4}
        if plan_order.get(plan, 0) <= plan_order.get(ctx.plan, 0):
            raise HTTPException(status_code=400, detail=f"Already on {ctx.plan} plan. Can only upgrade to a higher plan.")

        sub = store.get_subscription(user_id)
        subscription_id = sub.get("paddle_subscription_id")
        customer_id = sub.get("paddle_customer_id")

        # If user already has an active subscription → update it (change plan)
        if subscription_id and sub.get("status") in ("active", "past_due"):
            try:
                result = _paddle_request("PATCH", f"/subscriptions/{subscription_id}", {
                    "items": [{"price_id": price_id, "quantity": 1}],
                    "proration_billing_mode": "prorated_immediately",
                    "custom_data": {"mengram_user_id": user_id, "plan": plan},
                })
                data = result.get("data", {})
                # Update DB immediately so dashboard reflects new plan
                store.update_subscription(user_id, plan=plan)
                logger.info(f"Subscription updated via API: user={user_id} plan={plan}")
                return {"updated": True, "plan": plan, "subscription_id": subscription_id}
            except Exception as e:
                logger.error(f"Paddle subscription update error: {e}")
                raise HTTPException(status_code=502, detail=f"Paddle error: {e}")

        # No existing subscription → create new checkout
        txn_body = {
            "items": [{"price_id": price_id, "quantity": 1}],
            "custom_data": {"mengram_user_id": user_id, "plan": plan},
        }
        if customer_id:
            txn_body["customer_id"] = customer_id

        try:
            result = _paddle_request("POST", "/transactions", txn_body)
            data = result.get("data", {})
            checkout_url = data.get("checkout", {}).get("url", "")
            transaction_id = data.get("id", "")
            if not checkout_url:
                raise HTTPException(status_code=502, detail="Paddle did not return checkout URL")
            # Record abandoned-checkout tracking row
            try:
                email = store.get_user_email(user_id)
                if email and transaction_id:
                    store.record_checkout_session(transaction_id, user_id, email, plan)
            except Exception as tracking_err:
                logger.warning(f"Checkout session tracking failed: {tracking_err}")
            return {"checkout_url": checkout_url, "transaction_id": transaction_id}
        except Exception as e:
            logger.error(f"Paddle checkout error: {e}")
            raise HTTPException(status_code=502, detail=f"Paddle error: {e}")

    @router.post("/v1/billing/portal", tags=["Billing"])
    async def create_portal(ctx: AuthContext = Depends(auth)):
        """Create Paddle customer portal session for managing subscription."""
        user_id = ctx.user_id
        if not PADDLE_API_KEY:
            raise HTTPException(status_code=503, detail="Billing not configured")

        sub = store.get_subscription(user_id)
        customer_id = sub.get("paddle_customer_id")
        if not customer_id:
            raise HTTPException(status_code=400, detail="No billing account. Subscribe first.")

        try:
            result = _paddle_request(
                "POST",
                f"/customers/{customer_id}/portal-sessions",
                {}
            )
            urls = result.get("data", {}).get("urls", {})
            overview_url = urls.get("general", {}).get("overview", "")
            if not overview_url:
                raise HTTPException(status_code=502, detail="Paddle did not return portal URL")
            return {"portal_url": overview_url}
        except Exception as e:
            logger.error(f"Paddle portal error: {e}")
            raise HTTPException(status_code=502, detail=f"Paddle error: {e}")

    @router.post("/webhooks/paddle", tags=["Billing"])
    async def paddle_webhook(request: Request):
        """Paddle webhook handler. No auth — verified by HMAC signature."""
        if not PADDLE_WEBHOOK_SECRET:
            raise HTTPException(status_code=503, detail="Billing not configured")

        import hmac, hashlib

        raw_body = await request.body()
        sig_header = request.headers.get("Paddle-Signature", "")

        # Parse ts=...;h1=... from header
        sig_parts = {}
        for part in sig_header.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                sig_parts[k] = v

        ts = sig_parts.get("ts", "")
        h1 = sig_parts.get("h1", "")
        if not ts or not h1:
            raise HTTPException(status_code=400, detail="Invalid Paddle-Signature")

        # Reject replayed webhooks older than 5 minutes
        try:
            ts_age = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - int(ts)
            if ts_age > 300:
                logger.warning(f"Paddle webhook rejected: timestamp too old ({ts_age}s)")
                raise HTTPException(status_code=400, detail="Webhook timestamp too old")
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid timestamp")

        # Verify HMAC-SHA256
        signed_payload = f"{ts}:{raw_body.decode('utf-8')}"
        computed = hmac.new(
            PADDLE_WEBHOOK_SECRET.encode("utf-8"),
            signed_payload.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(computed, h1):
            raise HTTPException(status_code=400, detail="Invalid signature")

        event = json.loads(raw_body)
        event_type = event.get("event_type", "")
        data = event.get("data", {})

        if event_type == "transaction.completed":
            # Save customer_id → user mapping early (before subscription events)
            custom = data.get("custom_data") or {}
            user_id = custom.get("mengram_user_id")
            customer_id = data.get("customer_id", "")
            if user_id and customer_id:
                store.update_subscription(user_id, paddle_customer_id=customer_id)
                logger.info(f"Payment completed: user={user_id} customer={customer_id}")
            # Clear pending checkout-abandonment rows so drip emails stop
            if user_id:
                try:
                    store.mark_user_checkouts_completed(user_id)
                except Exception as tracking_err:
                    logger.warning(f"mark_user_checkouts_completed failed: {tracking_err}")

        elif event_type == "subscription.activated":
            custom = data.get("custom_data") or {}
            user_id = custom.get("mengram_user_id")
            customer_id = data.get("customer_id", "")
            subscription_id = data.get("id", "")

            if not user_id and customer_id:
                user_id = store.get_user_by_paddle_customer(customer_id)

            # Detect plan from custom_data or items price_id
            plan = custom.get("plan")
            if not plan:
                items = data.get("items", [])
                if items:
                    price_id = items[0].get("price", {}).get("id", "")
                    if price_id in (PADDLE_PRICES.get("business"), PADDLE_PRICES_ANNUAL.get("business")):
                        plan = "business"
                    elif price_id in (PADDLE_PRICES.get("growth"), PADDLE_PRICES_ANNUAL.get("growth")):
                        plan = "growth"
                    elif price_id in (PADDLE_PRICES.get("pro"), PADDLE_PRICES_ANNUAL.get("pro")):
                        plan = "pro"
                    elif price_id in (PADDLE_PRICES.get("starter"), PADDLE_PRICES_ANNUAL.get("starter")):
                        plan = "starter"
            if not plan:
                plan = "pro"

            if user_id:
                updates = {
                    "plan": plan,
                    "status": "active",
                    "paddle_customer_id": customer_id,
                    "paddle_subscription_id": subscription_id,
                }
                current_period = data.get("current_billing_period") or {}
                if current_period.get("starts_at"):
                    updates["current_period_start"] = current_period["starts_at"]
                if current_period.get("ends_at"):
                    updates["current_period_end"] = current_period["ends_at"]
                store.update_subscription(user_id, **updates)
                logger.info(f"Subscription activated: user={user_id} plan={plan}")
                # Clear pending checkout-abandonment rows so drip emails stop
                try:
                    store.mark_user_checkouts_completed(user_id)
                except Exception as tracking_err:
                    logger.warning(f"mark_user_checkouts_completed failed: {tracking_err}")
            else:
                logger.error(f"Subscription activated but no user found: customer={customer_id}")

        elif event_type == "subscription.canceled":
            custom = data.get("custom_data") or {}
            user_id = custom.get("mengram_user_id")
            customer_id = data.get("customer_id", "")
            if not user_id and customer_id:
                user_id = store.get_user_by_paddle_customer(customer_id)
            if user_id:
                # Keep current plan until period ends — only change status
                updates = {"status": "canceled"}
                current_period = data.get("current_billing_period") or {}
                if current_period.get("ends_at"):
                    updates["current_period_end"] = current_period["ends_at"]
                store.update_subscription(user_id, **updates)
                logger.info(f"Subscription canceled: user={user_id} (access until period end)")

        elif event_type == "subscription.past_due":
            custom = data.get("custom_data") or {}
            user_id = custom.get("mengram_user_id")
            customer_id = data.get("customer_id", "")
            if not user_id and customer_id:
                user_id = store.get_user_by_paddle_customer(customer_id)
            if user_id:
                store.update_subscription(user_id, status="past_due")
                logger.warning(f"Payment past due: user={user_id}")

        elif event_type == "subscription.updated":
            # Handle plan changes (upgrade/downgrade) and status updates
            custom = data.get("custom_data") or {}
            user_id = custom.get("mengram_user_id")
            customer_id = data.get("customer_id", "")
            if not user_id and customer_id:
                user_id = store.get_user_by_paddle_customer(customer_id)
            if user_id:
                updates = {"status": data.get("status", "active")}
                # Detect plan from items → price_id
                items = data.get("items", [])
                if items:
                    price_id = items[0].get("price", {}).get("id", "")
                    if price_id in (PADDLE_PRICES.get("business"), PADDLE_PRICES_ANNUAL.get("business")):
                        updates["plan"] = "business"
                    elif price_id in (PADDLE_PRICES.get("growth"), PADDLE_PRICES_ANNUAL.get("growth")):
                        updates["plan"] = "growth"
                    elif price_id in (PADDLE_PRICES.get("pro"), PADDLE_PRICES_ANNUAL.get("pro")):
                        updates["plan"] = "pro"
                    elif price_id in (PADDLE_PRICES.get("starter"), PADDLE_PRICES_ANNUAL.get("starter")):
                        updates["plan"] = "starter"
                current_period = data.get("current_billing_period") or {}
                if current_period.get("starts_at"):
                    updates["current_period_start"] = current_period["starts_at"]
                if current_period.get("ends_at"):
                    updates["current_period_end"] = current_period["ends_at"]
                store.update_subscription(user_id, **updates)
                logger.info(f"Subscription updated: user={user_id} updates={updates}")

        return {"received": True}

    return router
