"""WSGI entrypoint for local servers and Vercel's Python runtime.

Local: ``python wsgi.py``
Production hosts: import ``app`` or ``application`` from this module.

Vercel Functions have a read-only project filesystem and a writable /tmp area.
The current version of Plan Your Home uses SQLite and local uploads, so those
paths must be moved to /tmp for the app to start in a Vercel Function. Note that
/tmp is ephemeral and is not suitable for durable production user data.
"""
from __future__ import annotations

import os
from pathlib import Path
from wsgiref.simple_server import make_server

from pyh.config import Config


_IS_VERCEL = os.environ.get("VERCEL", "").strip() == "1"
if _IS_VERCEL:
    # Project files are read-only in Vercel Functions. Ignore disk paths which
    # may have been copied from Render/Railway instructions (for example /var/data).
    _runtime_dir = Path("/tmp/plan-your-home")
    Config.DATABASE_PATH = str(_runtime_dir / "plan-your-home.sqlite3")
    Config.STORAGE_DIR = str(_runtime_dir / "uploads")

    # Vercel exposes its deployment hostname. Use it only when APP_URL wasn't
    # configured to HTTPS, so URLs in canonical tags and sitemap are sensible.
    _vercel_host = (
        os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
        or os.environ.get("VERCEL_URL")
        or ""
    ).strip()
    if _vercel_host and not str(Config.APP_URL).startswith("https://"):
        Config.APP_URL = "https://" + _vercel_host.removeprefix("https://").removeprefix("http://").rstrip("/")

# Vercel looks for a top-level ``app`` or ``application`` WSGI object.
# Export both names for compatibility with the documented Python entrypoints.
from pyh.app import Application

app = Application()
application = app

if __name__ == "__main__":
    problems = Config.validate_production()
    if problems:
        raise SystemExit("Configuration errors: " + "; ".join(problems))
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    with make_server(host, port, app) as server:
        print(f"Plan Your Home running on http://{host}:{port} (Ctrl+C to stop)")
        server.serve_forever()
