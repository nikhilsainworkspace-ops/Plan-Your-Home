"""Environment-backed configuration; secrets are never exposed to templates."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Minimal .env reader for local development. Environment variables already set by the
# process take precedence; production secrets should normally be injected by the host.
def _load_local_env():
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    try:
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if key and key.replace("_", "").isalnum() and key not in os.environ:
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
                    value = value[1:-1]
                os.environ[key] = value
    except OSError:
        # Local env files are optional; process environment remains authoritative.
        return


_load_local_env()


def env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
    APP_NAME = os.getenv("APP_NAME", "Plan Your Home")
    APP_URL = os.getenv("APP_URL", "http://127.0.0.1:8000").rstrip("/")
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-this-secret-key-before-production")
    DATABASE_PATH = os.getenv("DATABASE_PATH", str(ROOT / "instance" / "plan-your-home.sqlite3"))
    STORAGE_DIR = os.getenv("STORAGE_DIR", str(ROOT / "instance" / "uploads"))
    PUBLIC_DEMO_ENABLED = env_bool("PUBLIC_DEMO_ENABLED", True)
    DEMO_SESSION_HOURS = max(1, int(os.getenv("DEMO_SESSION_HOURS", "8")))
    SESSION_DAYS = max(1, int(os.getenv("SESSION_DAYS", "14")))
    TRIAL_DAYS = max(1, int(os.getenv("TRIAL_DAYS", "7")))
    MAX_UPLOAD_BYTES = max(1024, int(os.getenv("MAX_UPLOAD_BYTES", str(8 * 1024 * 1024))))
    COOKIE_SECURE = env_bool("COOKIE_SECURE", APP_ENV == "production")
    TRUST_PROXY = env_bool("TRUST_PROXY", False)
    SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "")
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_STARTTLS = env_bool("SMTP_STARTTLS", True)
    EMAIL_FROM = os.getenv("EMAIL_FROM", "")
    RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
    RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    RAZORPAY_PLAN_STARTER = os.getenv("RAZORPAY_PLAN_STARTER", "")
    RAZORPAY_PLAN_PRO = os.getenv("RAZORPAY_PLAN_PRO", "")
    RAZORPAY_PLAN_TEAM = os.getenv("RAZORPAY_PLAN_TEAM", "")
    PRICE_STARTER_DISPLAY = os.getenv("PRICE_STARTER_DISPLAY", "")
    PRICE_PRO_DISPLAY = os.getenv("PRICE_PRO_DISPLAY", "")
    PRICE_TEAM_DISPLAY = os.getenv("PRICE_TEAM_DISPLAY", "")

    @classmethod
    def validate_production(cls) -> list[str]:
        problems = []
        if cls.APP_ENV == "production":
            if len(cls.SECRET_KEY) < 32 or cls.SECRET_KEY.startswith("dev-only"):
                problems.append("SECRET_KEY must be a unique random value of at least 32 characters in production.")
            if not cls.APP_URL.startswith("https://"):
                problems.append("APP_URL must use HTTPS in production.")
            if not cls.COOKIE_SECURE:
                problems.append("COOKIE_SECURE must be enabled in production.")
            if not Path(cls.DATABASE_PATH).is_absolute():
                problems.append("DATABASE_PATH must be an absolute path in production.")
            payment_requested = bool(cls.RAZORPAY_KEY_ID or cls.RAZORPAY_KEY_SECRET or cls.RAZORPAY_PLAN_STARTER or cls.RAZORPAY_PLAN_PRO or cls.RAZORPAY_PLAN_TEAM)
            if payment_requested and not (cls.RAZORPAY_KEY_ID and cls.RAZORPAY_KEY_SECRET and cls.RAZORPAY_WEBHOOK_SECRET):
                problems.append("Razorpay checkout requires RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, and RAZORPAY_WEBHOOK_SECRET together.")
        return problems
