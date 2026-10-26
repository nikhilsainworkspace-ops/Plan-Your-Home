"""Razorpay subscription helpers. Subscription access is granted only by verified server events."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .config import Config
from .security import verify_hmac_sha256

PLAN_ENV = {
    "starter": ("RAZORPAY_PLAN_STARTER", "PRICE_STARTER_DISPLAY", "Starter"),
    "professional": ("RAZORPAY_PLAN_PRO", "PRICE_PRO_DISPLAY", "Professional"),
    "team": ("RAZORPAY_PLAN_TEAM", "PRICE_TEAM_DISPLAY", "Studio / Team"),
}


def webhook_signature_valid(raw_body: bytes, signature: str | None, secret: str | None = None) -> bool:
    return verify_hmac_sha256(raw_body, signature, secret if secret is not None else Config.RAZORPAY_WEBHOOK_SECRET)


def configured_plan(plan_key: str, config=Config) -> dict:
    if plan_key not in PLAN_ENV:
        raise ValueError("Unknown subscription plan.")
    id_attr, price_attr, label = PLAN_ENV[plan_key]
    provider_id = getattr(config, id_attr, "")
    display = getattr(config, price_attr, "")
    return {"key": plan_key, "label": label, "provider_plan_id": provider_id, "price_display": display, "configured": bool(provider_id and display)}


def create_subscription(plan_key: str, user_id: int, user_email: str, config=Config) -> dict:
    """Create a provider subscription; fails closed unless API and plan are configured."""
    plan = configured_plan(plan_key, config)
    if not (config.RAZORPAY_KEY_ID and config.RAZORPAY_KEY_SECRET and plan["provider_plan_id"]):
        raise RuntimeError("Online payments are not configured. Add the Razorpay API credentials and selected plan ID in the server environment.")
    body = {
        "plan_id": plan["provider_plan_id"],
        "customer_notify": 1,
        "total_count": 12,
        "notes": {"user_id": str(user_id), "plan_key": plan_key, "email": user_email[:254]},
    }
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        "https://api.razorpay.com/v1/subscriptions", data=payload, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    import base64
    auth = base64.b64encode(f"{config.RAZORPAY_KEY_ID}:{config.RAZORPAY_KEY_SECRET}".encode()).decode()
    request.add_header("Authorization", f"Basic {auth}")
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Never include credential-bearing request headers or full provider payload in errors.
        raise RuntimeError("Razorpay rejected the checkout request. Verify the configured plan ID and test-mode credentials.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("Razorpay could not be reached. Please try again later.") from exc
    if not result.get("id") or not result.get("short_url"):
        raise RuntimeError("Razorpay did not return a valid subscription checkout URL.")
    return {"id": result["id"], "short_url": result["short_url"], "status": result.get("status", "created")}


def parse_period_end(entity: dict) -> str | None:
    raw = entity.get("current_end") or entity.get("ended_at")
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError):
        return None
