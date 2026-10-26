from __future__ import annotations

import csv
import hashlib
import ipaddress
import io
import json
import logging
import mimetypes
import os
import re
import secrets
import sqlite3
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from http import cookies
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, unquote
from wsgiref.util import FileWrapper

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import billing
from .calculations import DEFAULT_CATEGORIES, estimate_cost, positive_number, room_area, area_from_plot, to_display_length
from .config import Config
from .db import connect, init_db, transaction
from .mailer import send_email, configured as email_configured
from .security import hash_password, random_token, token_digest, validate_email, verify_password

LOG = logging.getLogger("pyh")

PROJECT_TYPES = {
    "new_home": "New home", "renovation": "Renovation", "extension": "Extension",
    "interior_remodel": "Interior remodel", "room_refresh": "Room refresh",
}
ROOM_TYPES = {
    "living": "Living room", "bedroom": "Bedroom", "kitchen": "Kitchen", "bathroom": "Bathroom",
    "dining": "Dining", "study": "Study", "utility": "Utility", "other": "Other",
}
ROOM_COLORS = {"living": "#DDEAE4", "bedroom": "#E8E2F0", "kitchen": "#F4E8D4", "bathroom": "#DCE8F4", "dining": "#F2E2D9", "study": "#E3E8D4", "utility": "#E8E8E8", "other": "#DDEAE4"}
ALLOWED_UPLOADS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".csv", ".txt", ".docx", ".xlsx"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def dt_text(value: datetime | None = None) -> str:
    return (value or utcnow()).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def safe_filename(name: str) -> str:
    # Keep only the last path component; never trust a client-supplied path.
    name = (name or "document").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    return (name or "document")[:160]


class SafeCSVWriter:
    """Escape spreadsheet formula-leading text while retaining ordinary CSV values."""
    def __init__(self, writer):
        self.writer = writer

    @staticmethod
    def _safe(value):
        if not isinstance(value, str):
            return value
        if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    def writerow(self, values):
        self.writer.writerow([self._safe(value) for value in values])


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


class Request:
    def __init__(self, environ):
        self.environ = environ
        self.method = environ.get("REQUEST_METHOD", "GET").upper()
        self.path = unquote(environ.get("PATH_INFO", "/")) or "/"
        self.query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
        self.headers = {}
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                self.headers[key[5:].replace("_", "-").lower()] = value
        if "CONTENT_TYPE" in environ:
            self.headers["content-type"] = environ["CONTENT_TYPE"]
        if "CONTENT_LENGTH" in environ:
            self.headers["content-length"] = environ["CONTENT_LENGTH"]
        jar = cookies.SimpleCookie()
        try:
            jar.load(environ.get("HTTP_COOKIE", ""))
        except cookies.CookieError:
            pass
        self.cookies = {k: v.value for k, v in jar.items()}
        self._body = None
        self._form = None
        self._json = None
        self.max_body_bytes = int(environ.get("pyh.max_body_bytes", 12 * 1024 * 1024))
        try:
            self.request_id = self.headers.get("x-request-id", "")[:80] or uuid.uuid4().hex[:16]
        except Exception:
            self.request_id = uuid.uuid4().hex[:16]

    @property
    def body(self) -> bytes:
        if self._body is None:
            raw_length = self.environ.get("CONTENT_LENGTH", "0") or "0"
            try:
                length = int(raw_length)
            except ValueError:
                raise HttpError(400, "Invalid request content length.")
            if length < 0:
                raise HttpError(400, "Invalid request content length.")
            if length > self.max_body_bytes:
                raise HttpError(413, "The request is larger than the configured request limit.")
            self._body = self.environ["wsgi.input"].read(length) if length else b""
        return self._body

    @property
    def form(self) -> dict:
        if self._form is not None:
            return self._form
        self._form = {}
        ctype = self.headers.get("content-type", "")
        if ctype.startswith("application/x-www-form-urlencoded"):
            parsed = parse_qs(self.body.decode("utf-8", "replace"), keep_blank_values=True)
            self._form = {k: v[-1] for k, v in parsed.items()}
        elif ctype.startswith("multipart/form-data"):
            header = f"Content-Type: {ctype}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii", "replace")
            try:
                message = BytesParser(policy=policy.default).parsebytes(header + self.body)
                for part in message.iter_parts():
                    if part.get_content_disposition() != "form-data":
                        continue
                    name = part.get_param("name", header="content-disposition")
                    if not name:
                        continue
                    payload = part.get_payload(decode=True) or b""
                    filename = part.get_filename()
                    if filename is not None:
                        self._form[name] = {"filename": filename, "content_type": part.get_content_type(), "data": payload}
                    else:
                        charset = part.get_content_charset() or "utf-8"
                        self._form[name] = payload.decode(charset, "replace")
            except HttpError:
                raise
            except Exception:
                self._form = {}
        elif ctype.startswith("application/json"):
            try:
                obj = json.loads(self.body.decode("utf-8"))
                self._json = obj
                if isinstance(obj, dict):
                    self._form = obj
            except (UnicodeError, json.JSONDecodeError):
                self._form = {}
        return self._form

    @property
    def json(self):
        if self._json is None:
            ctype = self.headers.get("content-type", "")
            if ctype.startswith("application/json"):
                try:
                    self._json = json.loads(self.body.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError):
                    self._json = None
        return self._json

    def arg(self, key: str, default: str = "") -> str:
        values = self.query.get(key)
        return values[-1] if values else default


class Response:
    def __init__(self, body=b"", status=200, content_type="text/html; charset=utf-8", headers=None):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.content_type = content_type
        self.headers = headers or []


class Application:
    def __init__(self, database_path: str | None = None, storage_dir: str | None = None, config=Config, initialize: bool = True):
        self.config = config
        production_problems = config.validate_production() if hasattr(config, "validate_production") else []
        if production_problems:
            raise RuntimeError("Production configuration is invalid: " + " ".join(production_problems))
        self.storage_dir = Path(storage_dir or config.STORAGE_DIR)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        remote_database = bool(
            database_path is None
            and getattr(config, "TURSO_DATABASE_URL", "")
            and getattr(config, "TURSO_AUTH_TOKEN", "")
        )
        # None tells pyh.db to use the configured shared Turso database. An
        # explicit database_path always opts into local SQLite (especially tests).
        self.database_path = database_path if database_path is not None else (None if remote_database else config.DATABASE_PATH)
        if self.database_path is not None:
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        if initialize:
            init_db(self.database_path)
        self.templates = Environment(
            loader=FileSystemLoader(str(Path(__file__).resolve().parent.parent / "templates")),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.templates.filters["money"] = self.money
        self.templates.filters["pretty_date"] = self.pretty_date
        self.templates.filters["human_status"] = lambda s: (s or "").replace("_", " ").title()
        self.templates.filters["number"] = lambda n: f"{float(n or 0):,.2f}".rstrip("0").rstrip(".")
        self.templates.globals["project_types"] = PROJECT_TYPES
        self.templates.globals["room_types"] = ROOM_TYPES
        self.templates.globals["csrf_field"] = lambda token: f'<input type="hidden" name="csrf_token" value="{token}">'

    def conn(self):
        return connect(self.database_path)

    def money(self, amount, currency="INR"):
        try:
            amount = float(amount or 0)
        except (TypeError, ValueError):
            amount = 0.0
        symbols = {"INR": "₹", "USD": "$", "GBP": "£", "EUR": "€", "AUD": "A$", "CAD": "C$"}
        return f"{symbols.get(currency, currency + ' ')}{amount:,.2f}".rstrip("0").rstrip(".") if amount else f"{symbols.get(currency, currency + ' ')}0"

    @staticmethod
    def pretty_date(value):
        dt = parse_dt(value)
        if not dt:
            try:
                dt = datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                return value or "Not set"
        return dt.strftime("%d %b %Y")

    def _create_session(self, user_id=None):
        token = random_token()
        csrf = random_token(24)
        expires = dt_text(utcnow() + timedelta(days=self.config.SESSION_DAYS))
        with self.conn() as con:
            con.execute("INSERT INTO sessions(token_hash,csrf_token,user_id,expires_at) VALUES(?,?,?,?)", (token_digest(token), csrf, user_id, expires))
        return {"token": token, "csrf_token": csrf, "user_id": user_id, "expires_at": expires}

    def _load_session(self, req: Request, cookie_headers: list[str]):
        raw = req.cookies.get("pyh_session", "")
        row = None
        if raw:
            with self.conn() as con:
                row = con.execute("SELECT s.*, u.email, u.full_name, u.role, u.units, u.currency, u.is_admin, u.onboarding_complete, u.trial_started_at, u.trial_ends_at, u.trial_status FROM sessions s LEFT JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?", (token_digest(raw), dt_text())).fetchone()
        if row:
            session = dict(row)
            session["token"] = raw
            return session
        session = self._create_session()
        cookie_headers.append(self._cookie("pyh_session", session["token"], self.config.SESSION_DAYS * 86400))
        return session

    def _rotate_session(self, req: Request, cookie_headers: list[str], user_id: int):
        old = req.session.get("token", "")
        with self.conn() as con:
            if old:
                con.execute("DELETE FROM sessions WHERE token_hash=?", (token_digest(old),))
        new_session = self._create_session(user_id)
        cookie_headers.append(self._cookie("pyh_session", new_session["token"], self.config.SESSION_DAYS * 86400))
        req.session.update(new_session)
        req.session.update(self._user_columns(user_id))

    def _user_columns(self, user_id):
        with self.conn() as con:
            row = con.execute("SELECT id AS user_id,email,full_name,role,units,currency,is_admin,onboarding_complete,trial_started_at,trial_ends_at,trial_status FROM users WHERE id=?", (user_id,)).fetchone()
            return dict(row) if row else {}

    def _cookie(self, name: str, value: str, max_age: int):
        bits = [f"{name}={value}", "Path=/", f"Max-Age={int(max_age)}", "HttpOnly", "SameSite=Lax"]
        if getattr(self.config, "COOKIE_SECURE", False):
            bits.append("Secure")
        return "; ".join(bits)

    def _delete_cookie(self, name: str):
        bits = [f"{name}=", "Path=/", "Max-Age=0", "HttpOnly", "SameSite=Lax"]
        if getattr(self.config, "COOKIE_SECURE", False):
            bits.append("Secure")
        return "; ".join(bits)

    def _csrf_ok(self, req: Request):
        supplied = req.form.get("csrf_token") or req.headers.get("x-csrf-token") or ""
        return secrets.compare_digest(str(req.session.get("csrf_token", "")), str(supplied)) and bool(supplied)

    def _check_csrf(self, req: Request):
        if not self._csrf_ok(req):
            raise HttpError(400, "Your form expired or could not be verified. Reload the page and try again.")

    def _html(self, body: str, status=200, headers=None):
        return Response(body, status, "text/html; charset=utf-8", headers)

    def _redirect(self, location: str, status=303):
        if not location.startswith("/") or location.startswith("//"):
            location = "/app"
        return Response(b"", status, "text/plain; charset=utf-8", [("Location", location)])

    def _json(self, obj, status=200):
        return Response(json.dumps(obj, ensure_ascii=False), status, "application/json; charset=utf-8", [("Cache-Control", "no-store")])

    def _render(self, name: str, req: Request, **context):
        session = req.session
        user = None
        if session.get("user_id"):
            with self.conn() as con:
                user = con.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        notice = req.arg("notice", "")[:240]
        error = context.pop("error", "")
        template = self.templates.get_template(name)
        return self._html(template.render(
            app_name=self.config.APP_NAME,
            app_url=self.config.APP_URL,
            user=user,
            csrf_token=session.get("csrf_token", ""),
            notice=notice,
            error=error,
            support_email=self.config.SUPPORT_EMAIL,
            request_id=req.request_id,
            today=datetime.now().strftime("%Y-%m-%d"),
            **context,
        ))

    def _require_user(self, req: Request):
        if not req.session.get("user_id"):
            next_url = req.path if req.path.startswith("/") and not req.path.startswith("//") else "/app"
            raise HttpError(303, "/login?" + urlencode({"next": next_url}))
        with self.conn() as con:
            user = con.execute("SELECT * FROM users WHERE id=?", (req.session["user_id"],)).fetchone()
        if not user:
            raise HttpError(401, "Please sign in again.")
        return user

    def _is_paid(self, user_id: int) -> bool:
        now = dt_text()
        with self.conn() as con:
            row = con.execute("SELECT status,current_period_end FROM subscriptions WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
        if not row:
            return False
        if row["status"] == "active":
            return True
        return row["status"] == "cancelled" and bool(row["current_period_end"] and row["current_period_end"] > now)

    def _trial_active(self, user) -> bool:
        end = parse_dt(user["trial_ends_at"] if user else None)
        active = bool(end and end > utcnow() and (user["trial_status"] in ("trialing", "converted")))
        if user and user["trial_status"] == "trialing" and end and end <= utcnow():
            with self.conn() as con:
                con.execute("UPDATE users SET trial_status='expired',updated_at=? WHERE id=? AND trial_status='trialing'", (dt_text(), user["id"]))
        return active

    def _can_write(self, user) -> bool:
        return self._trial_active(user) or self._is_paid(user["id"])

    def _require_write(self, user):
        if not self._can_write(user):
            raise HttpError(303, "/billing?reason=trial-expired")

    def _require_admin(self, req: Request):
        user = self._require_user(req)
        if not user["is_admin"]:
            raise HttpError(403, "Administrator access is required.")
        return user

    def _project(self, user_id: int, project_id: int):
        with self.conn() as con:
            row = con.execute("SELECT * FROM projects WHERE id=? AND owner_id=? AND demo_session_id IS NULL", (project_id, user_id)).fetchone()
        if not row:
            raise HttpError(404, "Project not found.")
        return row

    def _demo_context(self, req: Request, cookie_headers: list[str]):
        if not self._demo_enabled():
            raise HttpError(404, "The public demo is currently unavailable.")
        raw = req.cookies.get("pyh_demo", "")
        row = None
        if raw:
            with self.conn() as con:
                row = con.execute("SELECT * FROM demo_sessions WHERE token_hash=? AND expires_at>?", (token_digest(raw), dt_text())).fetchone()
        if row:
            return dict(row)
        token = random_token()
        expires = dt_text(utcnow() + timedelta(hours=self.config.DEMO_SESSION_HOURS))
        with self.conn() as con:
            cur = con.execute("INSERT INTO demo_sessions(token_hash,expires_at) VALUES(?,?) RETURNING id", (token_digest(token), expires))
            demo_id = cur.fetchone()["id"]
        demo = {"id": demo_id, "token": token, "expires_at": expires}
        cookie_headers.append(self._cookie("pyh_demo", token, self.config.DEMO_SESSION_HOURS * 3600))
        self._seed_demo(demo_id)
        return demo

    def _demo_enabled(self):
        with self.conn() as con:
            row = con.execute("SELECT value FROM app_settings WHERE key='public_demo_enabled'").fetchone()
        if row:
            return row["value"] == "1"
        return bool(self.config.PUBLIC_DEMO_ENABLED)

    def _seed_demo(self, demo_id: int):
        with transaction(self.database_path) as con:
            cur = con.execute("""INSERT INTO projects(owner_id,demo_session_id,name,project_type,description,location,
                plot_length_m,plot_width_m,floors,built_area_m2,budget,currency,status,notes)
                VALUES(NULL,?,'Willow Courtyard Home','new_home','Illustrative planning project for exploring the workspace.','Sample location',24,18,2,210,5200000,'INR','in_progress','Synthetic demo data only. Rates and figures are examples, not verified local market prices.') RETURNING id""", (demo_id,))
            pid = cur.fetchone()["id"]
            rooms = [
                ("Living room", "living", 5.2, 4.2, 0.5, 0.5, 1, "#DDEAE4"),
                ("Kitchen", "kitchen", 3.4, 3.2, 6.1, 0.5, 1, "#F4E8D4"),
                ("Bedroom 1", "bedroom", 4.0, 3.6, 0.5, 5.2, 1, "#E8E2F0"),
                ("Bathroom", "bathroom", 2.2, 2.0, 4.8, 5.2, 1, "#DCE8F4"),
                ("Study", "study", 3.0, 3.0, 9.9, 0.5, 1, "#E3E8D4"),
            ]
            con.executemany("INSERT INTO rooms(project_id,name,room_type,width_m,depth_m,x_m,y_m,floor_no,color) VALUES(?,?,?,?,?,?,?,?,?)", [(pid, *room) for room in rooms])
            con.execute("INSERT INTO estimates(project_id,cost_per_m2,contingency_pct,finish_level,assumptions) VALUES(?,?,?,?,?)", (pid, 19000, 12, "standard", "Illustrative rate only — replace with local contractor quotes."))
            con.executemany("INSERT INTO estimate_lines(project_id,category,pct,notes) VALUES(?,?,?,?)", [(pid, name, pct, "Example allocation") for name, pct in DEFAULT_CATEGORIES])
            tasks = [
                ("Confirm family space requirements", "Brief", "done", "normal", "2026-10-29"),
                ("Review concept floor plan", "Design", "in_progress", "high", "2026-11-03"),
                ("Collect comparable contractor quotes", "Procurement", "todo", "normal", "2026-11-12"),
            ]
            con.executemany("INSERT INTO tasks(project_id,title,description,status,priority,due_date) VALUES(?,?,?,?,?,?)", [(pid, *t) for t in tasks])
            con.executemany("INSERT INTO materials(project_id,name,quantity,unit,unit_cost,supplier,notes) VALUES(?,?,?,?,?,?,?)", [
                (pid, "Cement", 80, "bag", 380, "Example supplier", "Example quantity and price"),
                (pid, "Floor tile", 45, "m²", 650, "Example supplier", "Confirm specification locally"),
            ])
            con.execute("INSERT INTO activity(owner_id,demo_session_id,project_id,action,summary) VALUES(NULL,?,?,?,?)", (demo_id, pid, "demo.seeded", "Synthetic sample project created"))

    def _demo_project(self, demo_id: int, project_id: int | None = None):
        with self.conn() as con:
            if project_id is None:
                row = con.execute("SELECT * FROM projects WHERE demo_session_id=? ORDER BY id DESC LIMIT 1", (demo_id,)).fetchone()
            else:
                row = con.execute("SELECT * FROM projects WHERE id=? AND demo_session_id=? AND owner_id IS NULL", (project_id, demo_id)).fetchone()
        if not row:
            raise HttpError(404, "Demo project not found. Reset the demo to create a fresh sample.")
        return row

    def _activity(self, user_id: int | None, project_id: int | None, action: str, summary: str, demo_session_id=None):
        with self.conn() as con:
            con.execute("INSERT INTO activity(owner_id,demo_session_id,project_id,action,summary) VALUES(?,?,?,?,?)", (user_id, demo_session_id, project_id, action[:80], summary[:240]))

    def _project_estimate(self, project_id: int):
        with self.conn() as con:
            estimate = con.execute("SELECT * FROM estimates WHERE project_id=?", (project_id,)).fetchone()
            lines = con.execute("SELECT * FROM estimate_lines WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
            project = con.execute("SELECT built_area_m2 FROM projects WHERE id=?", (project_id,)).fetchone()
        if not estimate or not project:
            return None, [], None
        percentages = [(line["category"], float(line["pct"])) for line in lines]
        if lines and abs(sum(x[1] for x in percentages) - 100) < 0.1:
            calc = estimate_cost(project["built_area_m2"], estimate["cost_per_m2"], estimate["contingency_pct"], percentages)
        else:
            calc = estimate_cost(project["built_area_m2"], estimate["cost_per_m2"], estimate["contingency_pct"], DEFAULT_CATEGORIES)
        return estimate, lines, calc

    def _make_project(self, user, values: dict):
        name = (values.get("name") or "").strip()
        if not name or len(name) > 120:
            raise ValueError("Project name is required and must be 120 characters or fewer.")
        project_type = values.get("project_type", "new_home")
        if project_type not in PROJECT_TYPES:
            project_type = "new_home"
        plot_length = positive_number(values.get("plot_length_m", "20"), "Plot length", 10000)
        plot_width = positive_number(values.get("plot_width_m", "15"), "Plot width", 10000)
        built_area = positive_number(values.get("built_area_m2", "120"), "Built-up area", 10_000_000)
        budget = positive_number(values.get("budget", "0"), "Budget", 1_000_000_000_000)
        try:
            floors = int(values.get("floors", "1"))
        except (ValueError, TypeError):
            raise ValueError("Floors must be a whole number between 1 and 20.")
        if not 1 <= floors <= 20:
            raise ValueError("Floors must be a whole number between 1 and 20.")
        start_date = (values.get("start_date") or "").strip() or None
        target_date = (values.get("target_date") or "").strip() or None
        for date_value, label in ((start_date, "Start date"), (target_date, "Target date")):
            if date_value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value):
                raise ValueError(f"{label} must use YYYY-MM-DD format.")
            if date_value:
                datetime.strptime(date_value, "%Y-%m-%d")
        if start_date and target_date and target_date < start_date:
            raise ValueError("Target date cannot be before the start date.")
        currency = values.get("currency", user["currency"] if "currency" in user.keys() else "INR")
        if currency not in {"INR", "USD", "GBP", "EUR", "AUD", "CAD"}:
            currency = "INR"
        with transaction(self.database_path) as con:
            cur = con.execute("""INSERT INTO projects(owner_id,demo_session_id,name,project_type,description,location,plot_length_m,plot_width_m,floors,built_area_m2,budget,currency,status,notes,start_date,target_date)
                VALUES(?,NULL,?,?,?,?,?,?,?,?,?,?,'planning',?,?,?) RETURNING id""", (user["id"], name, project_type, (values.get("description") or "")[:2000], (values.get("location") or "")[:160], plot_length, plot_width, floors, built_area, budget, currency, (values.get("notes") or "")[:4000], start_date, target_date))
            pid = cur.fetchone()["id"]
            con.execute("INSERT INTO estimates(project_id) VALUES(?)", (pid,))
            con.executemany("INSERT INTO estimate_lines(project_id,category,pct,notes) VALUES(?,?,?,?)", [(pid, category, pct, "Default planning allocation; edit based on quotations.") for category, pct in DEFAULT_CATEGORIES])
            starter_tasks = [
                ("Record the space brief", "Capture room needs and must-haves", "todo", "normal"),
                ("Review the first floor plan", "Check the room layout and circulation", "todo", "normal"),
                ("Gather local quotations", "Use comparable scopes and specifications", "todo", "high"),
            ]
            con.executemany("INSERT INTO tasks(project_id,title,description,status,priority) VALUES(?,?,?,?,?)", [(pid, *t) for t in starter_tasks])
            con.execute("UPDATE users SET updated_at=? WHERE id=?", (dt_text(), user["id"]))
            con.execute("INSERT INTO audit_log(actor_user_id,action,subject_type,subject_id,details) VALUES(?,?,?,?,?)", (user["id"], "project.created", "project", str(pid), "Project created"))
        self._activity(user["id"], pid, "project.created", f"Created project: {name}")
        return pid

    def _valid_return(self, value):
        return value if value.startswith("/") and not value.startswith("//") and "\\" not in value else "/app"

    def _client_address(self, req: Request) -> str:
        address = req.environ.get("REMOTE_ADDR", "unknown")[:80]
        if getattr(self.config, "TRUST_PROXY", False):
            forwarded = req.headers.get("x-forwarded-for", "")
            candidate = forwarded.split(",", 1)[0].strip()
            try:
                ipaddress.ip_address(candidate)
                return candidate[:80]
            except ValueError:
                pass
        return address

    def _rate_limit_login(self, req: Request, succeeded: bool):
        address = self._client_address(req)
        key = hashlib.sha256((str(self.config.SECRET_KEY) + ":" + address).encode()).hexdigest()
        now = utcnow()
        with transaction(self.database_path) as con:
            row = con.execute("SELECT attempts,window_started_at FROM login_attempts WHERE key_hash=?", (key,)).fetchone()
            start = parse_dt(row["window_started_at"]) if row else None
            if succeeded:
                con.execute("DELETE FROM login_attempts WHERE key_hash=?", (key,))
                return False
            if row and start and start >= now - timedelta(minutes=15) and int(row["attempts"]) >= 8:
                return True
            if not row or not start or start < now - timedelta(minutes=15):
                con.execute("INSERT INTO login_attempts(key_hash,attempts,window_started_at) VALUES(?,?,?) ON CONFLICT(key_hash) DO UPDATE SET attempts=excluded.attempts,window_started_at=excluded.window_started_at", (key, 0, dt_text(now)))
            return False

    def _record_login_failure(self, req: Request):
        address = self._client_address(req)
        key = hashlib.sha256((str(self.config.SECRET_KEY) + ":" + address).encode()).hexdigest()
        now = utcnow()
        with transaction(self.database_path) as con:
            row = con.execute("SELECT attempts,window_started_at FROM login_attempts WHERE key_hash=?", (key,)).fetchone()
            start = parse_dt(row["window_started_at"]) if row else None
            if not row or not start or start < now - timedelta(minutes=15):
                con.execute("INSERT INTO login_attempts(key_hash,attempts,window_started_at) VALUES(?,?,?) ON CONFLICT(key_hash) DO UPDATE SET attempts=excluded.attempts,window_started_at=excluded.window_started_at", (key, 1, dt_text(now)))
            else:
                con.execute("UPDATE login_attempts SET attempts=attempts+1 WHERE key_hash=?", (key,))

    def __call__(self, environ, start_response):
        # Bound memory use while allowing a small multipart overhead above the upload cap.
        try:
            configured_upload = int(self.config.MAX_UPLOAD_BYTES)
        except (TypeError, ValueError):
            configured_upload = 8 * 1024 * 1024
        environ["pyh.max_body_bytes"] = min(100 * 1024 * 1024, max(12 * 1024 * 1024, configured_upload + 1024 * 1024))
        req = Request(environ)
        cookie_headers: list[str] = []
        req.session = self._load_session(req, cookie_headers)
        try:
            response = self.dispatch(req, cookie_headers)
        except HttpError as exc:
            if exc.status == 303 and exc.message.startswith("/"):
                response = self._redirect(exc.message)
            else:
                response = self._error_page(req, exc.status, exc.message)
        except ValueError as exc:
            response = self._error_page(req, 400, str(exc)[:300] or "Please check the submitted values.")
        except Exception as exc:
            LOG.error("request_failed request_id=%s path=%s type=%s", req.request_id, req.path[:120], type(exc).__name__)
            response = self._error_page(req, 500, "Something went wrong. The team can use the request reference below to investigate.")
        headers = [
            ("Content-Type", response.content_type),
            ("Content-Length", str(len(response.body))),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "strict-origin-when-cross-origin"),
            ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
            ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; connect-src 'self'"),
            ("Cache-Control", "no-store" if req.path.startswith(("/app", "/projects", "/demo", "/admin", "/billing", "/settings")) else "no-cache"),
        ]
        if self.config.APP_ENV == "production":
            headers.append(("Strict-Transport-Security", "max-age=31536000; includeSubDomains"))
        headers.extend(response.headers)
        headers.extend(("Set-Cookie", cookie) for cookie in cookie_headers)
        status_text = {200:"OK",201:"Created",204:"No Content",303:"See Other",400:"Bad Request",401:"Unauthorized",403:"Forbidden",404:"Not Found",405:"Method Not Allowed",413:"Payload Too Large",429:"Too Many Requests",500:"Internal Server Error",503:"Service Unavailable"}.get(response.status, "OK")
        start_response(f"{response.status} {status_text}", headers)
        return [response.body]

    def _error_page(self, req, status, message):
        # Keep the rendered error page and the actual HTTP status consistent. A 404/403/400
        # that returns 200 can bypass client-side error handling and mislead monitors/caches.
        try:
            response = self._render("error.html", req, status=status, message=message)
            response.status = status
            return response
        except Exception:
            safe = (message or "Request failed").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            return self._html(f"<!doctype html><title>Error</title><h1>Request could not be completed</h1><p>{safe}</p><p>Reference: {req.request_id}</p>", status)

    def dispatch(self, req: Request, cookie_headers: list[str]):
        path, method = req.path.rstrip("/") or "/", req.method
        if method in {"POST", "PUT", "PATCH", "DELETE"} and path != "/webhooks/razorpay":
            self._check_csrf(req)

        # Public marketing and account journeys.
        if path == "/healthz" and method == "GET":
            try:
                with self.conn() as con:
                    con.execute("SELECT 1").fetchone()
                return self._json({"status": "ok", "database": "ok"})
            except sqlite3.Error:
                return self._json({"status": "unavailable", "database": "error"}, 503)
        if path == "/" and method == "GET":
            return self._render("landing.html", req, demo_enabled=self._demo_enabled())
        public_pages = {"/features":"features.html", "/how-it-works":"how.html", "/pricing":"pricing.html", "/about":"about.html", "/faq":"faq.html", "/privacy":"privacy.html", "/terms":"terms.html", "/cookies":"cookies.html"}
        if path in public_pages and method == "GET":
            return self._render(public_pages[path], req, demo_enabled=self._demo_enabled(), plans=self._plans())
        if path == "/contact":
            if method == "GET":
                return self._render("contact.html", req)
            if method == "POST":
                name = (req.form.get("name") or "").strip()[:120]
                email = (req.form.get("email") or "").strip().lower()
                message = (req.form.get("message") or "").strip()
                if not name or not validate_email(email) or len(message) < 8 or len(message) > 4000:
                    return self._render("contact.html", req, error="Enter your name, a valid email address, and a message between 8 and 4,000 characters." )
                with self.conn() as con:
                    con.execute("INSERT INTO contact_requests(name,email,message) VALUES(?,?,?)", (name, email, message))
                if self.config.SUPPORT_EMAIL and email_configured(self.config):
                    try:
                        send_email(self.config.SUPPORT_EMAIL, "Plan Your Home support request", f"From: {name} <{email}>\n\n{message}", self.config)
                    except Exception as exc:
                        LOG.warning("contact_email_delivery_failed request_id=%s type=%s", req.request_id, type(exc).__name__)
                return self._render("contact.html", req, success="Your message has been saved for the site administrator.")
        if path == "/signup":
            if method == "GET":
                return self._render("signup.html", req)
            if method == "POST":
                full_name = (req.form.get("full_name") or "").strip()
                email = (req.form.get("email") or "").strip().lower()
                password = req.form.get("password") or ""
                confirm = req.form.get("confirm_password") or ""
                role = req.form.get("role", "homeowner")
                units = req.form.get("units", "metric")
                if not full_name or len(full_name) > 120 or not validate_email(email):
                    return self._render("signup.html", req, error="Enter your name and a valid email address.")
                if password != confirm:
                    return self._render("signup.html", req, error="The passwords do not match.")
                if role not in {"homeowner","architect","civil_engineer","contractor","interior_designer","other"} or units not in {"metric","imperial"}:
                    return self._render("signup.html", req, error="Choose a valid role and unit preference.")
                try:
                    password_hash = hash_password(password)
                except ValueError as exc:
                    return self._render("signup.html", req, error=str(exc))
                try:
                    with transaction(self.database_path) as con:
                        trial_setting = con.execute("SELECT value FROM app_settings WHERE key='trial_days'").fetchone()
                        try:
                            trial_days = max(1, min(60, int(trial_setting["value"]))) if trial_setting else self.config.TRIAL_DAYS
                        except (ValueError, TypeError):
                            trial_days = self.config.TRIAL_DAYS
                        start = utcnow()
                        end = start + timedelta(days=trial_days)
                        cur = con.execute("INSERT INTO users(email,full_name,password_hash,role,units,trial_started_at,trial_ends_at,trial_status) VALUES(?,?,?,?,?,?,?,'trialing') RETURNING id", (email, full_name, password_hash, role, units, dt_text(start), dt_text(end)))
                        uid = cur.fetchone()["id"]
                        con.execute("INSERT INTO audit_log(actor_user_id,action,subject_type,subject_id,details) VALUES(?,?,?,?,?)", (uid, "account.created", "user", str(uid), "New account registered; trial started"))
                except sqlite3.IntegrityError:
                    return self._render("signup.html", req, error="An account with that email already exists. Please sign in instead.")
                self._rotate_session(req, cookie_headers, uid)
                return self._redirect("/onboarding?notice=" + quote(f"Your {trial_days}-day free trial has started."))
        if path == "/login":
            if method == "GET":
                if req.session.get("user_id"):
                    return self._redirect("/app")
                return self._render("login.html", req, next_url=self._valid_return(req.arg("next", "/app")))
            if method == "POST":
                email = (req.form.get("email") or "").strip().lower()
                password = req.form.get("password") or ""
                throttle = self._rate_limit_login(req, succeeded=False)
                if throttle:
                    return self._render("login.html", req, error="Too many sign-in attempts. Wait 15 minutes and try again.", next_url=self._valid_return(req.form.get("next", "/app")))
                with self.conn() as con:
                    user = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone() if validate_email(email) else None
                if not user or not verify_password(password, user["password_hash"]):
                    self._record_login_failure(req)
                    return self._render("login.html", req, error="Email or password was not recognized.", next_url=self._valid_return(req.form.get("next", "/app")))
                self._rate_limit_login(req, succeeded=True)
                self._rotate_session(req, cookie_headers, user["id"])
                return self._redirect(self._valid_return(req.form.get("next", "/app")))
        if path == "/logout" and method == "POST":
            if req.session.get("token"):
                with self.conn() as con:
                    con.execute("DELETE FROM sessions WHERE token_hash=?", (token_digest(req.session["token"]),))
            cookie_headers.append(self._delete_cookie("pyh_session"))
            req.session = {}
            return self._redirect("/?notice=" + quote("You have signed out."))
        if path == "/forgot-password":
            if method == "GET":
                return self._render("forgot.html", req)
            if method == "POST":
                email = (req.form.get("email") or "").strip().lower()
                reset_url = None
                if validate_email(email):
                    with self.conn() as con:
                        user = con.execute("SELECT id,email,full_name FROM users WHERE email=?", (email,)).fetchone()
                    if user:
                        token = random_token()
                        expires = dt_text(utcnow() + timedelta(minutes=30))
                        with self.conn() as con:
                            con.execute("INSERT INTO password_resets(user_id,token_hash,expires_at) VALUES(?,?,?)", (user["id"], token_digest(token), expires))
                        candidate = self.config.APP_URL.rstrip("/") + "/reset-password/" + token
                        try:
                            if email_configured(self.config):
                                send_email(user["email"], "Reset your Plan Your Home password", f"Hello {user['full_name']},\n\nUse this link within 30 minutes to choose a new password:\n{candidate}\n\nIf you did not request this, ignore this message.", self.config)
                            elif self.config.APP_ENV == "development":
                                # Never disclose account recovery tokens in a staging/public response.
                                reset_url = "/reset-password/" + token
                        except Exception as exc:
                            LOG.warning("reset_email_failed request_id=%s type=%s", req.request_id, type(exc).__name__)
                return self._render("forgot.html", req, submitted=True, reset_url=reset_url)
        reset_match = re.fullmatch(r"/reset-password/([A-Za-z0-9_-]{30,80})", path)
        if reset_match:
            token = reset_match.group(1)
            with self.conn() as con:
                reset = con.execute("SELECT * FROM password_resets WHERE token_hash=? AND used_at IS NULL AND expires_at>?", (token_digest(token), dt_text())).fetchone()
            if not reset:
                if method == "GET":
                    return self._render("reset.html", req, token="", error="This reset link is invalid or expired. Request a new one.")
                return self._render("forgot.html", req, error="This reset link is invalid or expired.")
            if method == "GET":
                return self._render("reset.html", req, token=token)
            if method == "POST":
                password, confirm = req.form.get("password", ""), req.form.get("confirm_password", "")
                if password != confirm:
                    return self._render("reset.html", req, token=token, error="The passwords do not match.")
                try:
                    password_hash = hash_password(password)
                except ValueError as exc:
                    return self._render("reset.html", req, token=token, error=str(exc))
                with transaction(self.database_path) as con:
                    con.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (password_hash, dt_text(), reset["user_id"]))
                    con.execute("UPDATE password_resets SET used_at=? WHERE user_id=? AND used_at IS NULL", (dt_text(), reset["user_id"]))
                    con.execute("DELETE FROM sessions WHERE user_id=?", (reset["user_id"],))
                cookie_headers.append(self._delete_cookie("pyh_session"))
                return self._redirect("/login?notice=" + quote("Password updated. Please sign in again."))

        # Public, per-visitor isolated demo workspace.
        if path == "/demo" or path.startswith("/demo/"):
            return self._dispatch_demo(req, cookie_headers)

        # Authenticated application shell and account settings.
        if path == "/onboarding":
            user = self._require_user(req)
            if method == "GET":
                return self._render("onboarding.html", req, user_record=user, can_write=self._can_write(user))
            if method == "POST":
                self._require_write(user)
                role = req.form.get("role", user["role"])
                units = req.form.get("units", user["units"])
                currency = req.form.get("currency", user["currency"])
                if role not in {"homeowner","architect","civil_engineer","contractor","interior_designer","other"} or units not in {"metric","imperial"} or currency not in {"INR","USD","GBP","EUR","AUD","CAD"}:
                    return self._render("onboarding.html", req, user_record=user, error="Choose valid profile preferences.", can_write=True)
                with self.conn() as con:
                    con.execute("UPDATE users SET role=?,units=?,currency=?,onboarding_complete=1,updated_at=? WHERE id=?", (role, units, currency, dt_text(), user["id"]))
                return self._redirect("/projects/new?notice=" + quote("Your workspace is ready. Create your first project."))
        if path == "/app" and method == "GET":
            user = self._require_user(req)
            return self._dashboard(req, user)
        if path == "/projects" and method in {"GET", "POST"}:
            user = self._require_user(req)
            if method == "POST":
                self._require_write(user)
                try:
                    pid = self._make_project(user, req.form)
                except ValueError as exc:
                    return self._render("project_form.html", req, project=None, error=str(exc), user_record=user, can_write=True)
                return self._redirect(f"/projects/{pid}?notice=" + quote("Project created."))
            q = req.arg("q", "")[:120].strip()
            status_filter = req.arg("status", "")
            query = "SELECT p.*, (SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id AND t.status!='done') AS open_tasks FROM projects p WHERE p.owner_id=? AND p.demo_session_id IS NULL"
            params = [user["id"]]
            if q:
                query += " AND (p.name LIKE ? OR p.location LIKE ? OR p.description LIKE ?)"
                wildcard = f"%{q}%"
                params += [wildcard, wildcard, wildcard]
            if status_filter in {"planning", "in_progress", "on_hold", "completed", "archived"}:
                query += " AND p.status=?"
                params.append(status_filter)
            else:
                query += " AND p.status!='archived'"
            query += " ORDER BY p.updated_at DESC LIMIT 100"
            with self.conn() as con:
                projects = con.execute(query, tuple(params)).fetchall()
            return self._render("projects.html", req, projects=projects, query=q, status_filter=status_filter, user_record=user, can_write=self._can_write(user))
        if path == "/projects/new" and method == "GET":
            user = self._require_user(req)
            if not self._can_write(user):
                return self._redirect("/billing?reason=trial-expired")
            return self._render("project_form.html", req, project=None, user_record=user, can_write=True)

        # Project-level routes use ownership checks for every object lookup.
        match = re.fullmatch(r"/projects/(\d+)(?:/(.*))?", path)
        if match:
            user = self._require_user(req)
            project_id = int(match.group(1))
            action = match.group(2) or ""
            project = self._project(user["id"], project_id)
            can_write = self._can_write(user)
            if action == "" and method == "GET":
                with self.conn() as con:
                    rooms = con.execute("SELECT * FROM rooms WHERE project_id=? ORDER BY floor_no,name", (project_id,)).fetchall()
                    tasks = con.execute("SELECT * FROM tasks WHERE project_id=? ORDER BY CASE status WHEN 'todo' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,due_date", (project_id,)).fetchall()
                    material_total = con.execute("SELECT COALESCE(SUM(quantity*unit_cost),0) AS total FROM materials WHERE project_id=?", (project_id,)).fetchone()["total"]
                    spent = con.execute("SELECT COALESCE(SUM(amount),0) AS total FROM expenses WHERE project_id=?", (project_id,)).fetchone()["total"]
                    docs_count = con.execute("SELECT COUNT(*) AS n FROM documents WHERE project_id=?", (project_id,)).fetchone()["n"]
                est, lines, estimate = self._project_estimate(project_id)
                return self._render("project_detail.html", req, project=project, rooms=rooms, tasks=tasks, estimate=estimate, estimate_record=est, material_total=material_total, spent=spent, docs_count=docs_count, can_write=can_write, user_record=user)
            if action == "edit":
                if method == "GET":
                    return self._render("project_form.html", req, project=project, user_record=user, can_write=can_write)
                if method == "POST":
                    self._require_write(user)
                    values = self._project_form_values(req.form)
                    if values.get("error"):
                        return self._render("project_form.html", req, project=project, user_record=user, can_write=True, error=values["error"])
                    with self.conn() as con:
                        con.execute("""UPDATE projects SET name=?,project_type=?,description=?,location=?,plot_length_m=?,plot_width_m=?,floors=?,built_area_m2=?,budget=?,currency=?,notes=?,start_date=?,target_date=?,updated_at=? WHERE id=? AND owner_id=?""", (*values["values"], dt_text(), project_id, user["id"]))
                    self._activity(user["id"], project_id, "project.updated", f"Updated project: {values['values'][0]}")
                    return self._redirect(f"/projects/{project_id}?notice=" + quote("Project details saved."))
            if action == "archive" and method == "POST":
                self._require_write(user)
                with self.conn() as con:
                    con.execute("UPDATE projects SET status='archived',archived_at=?,updated_at=? WHERE id=? AND owner_id=?", (dt_text(), dt_text(), project_id, user["id"]))
                self._activity(user["id"], project_id, "project.archived", f"Archived project: {project['name']}")
                return self._redirect("/projects?notice=" + quote("Project archived."))
            if action == "restore" and method == "POST":
                self._require_write(user)
                with self.conn() as con:
                    con.execute("UPDATE projects SET status='planning',archived_at=NULL,updated_at=? WHERE id=? AND owner_id=?", (dt_text(), project_id, user["id"]))
                return self._redirect(f"/projects/{project_id}?notice=" + quote("Project restored."))
            if action == "delete" and method == "POST":
                self._require_write(user)
                if (req.form.get("confirm_name") or "").strip() != project["name"]:
                    return self._render("error.html", req, status=400, message="Enter the project name exactly to confirm deletion.")
                # Documents are private files and must be removed with their rows.
                with self.conn() as con:
                    files = con.execute("SELECT stored_name FROM documents WHERE project_id=?", (project_id,)).fetchall()
                    con.execute("DELETE FROM projects WHERE id=? AND owner_id=?", (project_id, user["id"]))
                    con.execute("INSERT INTO audit_log(actor_user_id,action,subject_type,subject_id,details) VALUES(?,?,?,?,?)", (user["id"], "project.deleted", "project", str(project_id), "Project deleted by owner"))
                for f in files:
                    (self.storage_dir / f["stored_name"]).unlink(missing_ok=True)
                return self._redirect("/projects?notice=" + quote("Project deleted."))
            if action == "duplicate" and method == "POST":
                self._require_write(user)
                with transaction(self.database_path) as con:
                    cur = con.execute("""INSERT INTO projects(owner_id,demo_session_id,name,project_type,description,location,plot_length_m,plot_width_m,floors,built_area_m2,budget,currency,status,notes,start_date,target_date)
                        SELECT owner_id,NULL,substr(name||' (copy)',1,120),project_type,description,location,plot_length_m,plot_width_m,floors,built_area_m2,budget,currency,'planning',notes,start_date,target_date FROM projects WHERE id=? AND owner_id=? RETURNING id""", (project_id,user["id"]))
                    new_id = cur.fetchone()["id"]
                    con.execute("INSERT INTO estimates(project_id,cost_per_m2,contingency_pct,finish_level,assumptions) SELECT ?,cost_per_m2,contingency_pct,finish_level,assumptions FROM estimates WHERE project_id=?", (new_id, project_id))
                    con.execute("INSERT INTO estimate_lines(project_id,category,pct,notes) SELECT ?,category,pct,notes FROM estimate_lines WHERE project_id=?", (new_id, project_id))
                    con.execute("INSERT INTO rooms(project_id,name,room_type,width_m,depth_m,x_m,y_m,floor_no,color,doors,windows) SELECT ?,name,room_type,width_m,depth_m,x_m,y_m,floor_no,color,doors,windows FROM rooms WHERE project_id=?", (new_id, project_id))
                    con.execute("INSERT INTO tasks(project_id,title,description,status,priority,due_date) SELECT ?,title,description,'todo',priority,due_date FROM tasks WHERE project_id=?", (new_id, project_id))
                self._activity(user["id"], new_id, "project.duplicated", f"Duplicated project {project_id}")
                return self._redirect(f"/projects/{new_id}?notice=" + quote("Project duplicated."))
            if action == "plan" and method == "GET":
                with self.conn() as con:
                    rooms = con.execute("SELECT * FROM rooms WHERE project_id=? ORDER BY floor_no,id", (project_id,)).fetchall()
                return self._render("plan_editor.html", req, project=project, rooms=rooms, can_write=can_write, is_demo=False, save_url=f"/projects/{project_id}/plan/save", room_url=f"/projects/{project_id}/rooms", user_record=user)
            if action == "plan/save" and method == "POST":
                self._require_write(user)
                payload = req.json
                if not isinstance(payload, dict) or not isinstance(payload.get("rooms"), list) or len(payload["rooms"]) > 200:
                    return self._json({"ok": False, "error": "Invalid plan data."}, 400)
                updated = 0
                with transaction(self.database_path) as con:
                    current = con.execute("SELECT * FROM projects WHERE id=? AND owner_id=?", (project_id,user["id"])).fetchone()
                    room_rows = {r["id"]: r for r in con.execute("SELECT * FROM rooms WHERE project_id=?", (project_id,)).fetchall()}
                    for item in payload["rooms"]:
                        if not isinstance(item, dict) or str(item.get("id", "")).isdigit() is False:
                            continue
                        rid = int(item["id"])
                        if rid not in room_rows:
                            continue
                        x = positive_number(item.get("x_m", room_rows[rid]["x_m"]), "Room x", 500)
                        y = positive_number(item.get("y_m", room_rows[rid]["y_m"]), "Room y", 500)
                        width = positive_number(item.get("width_m", room_rows[rid]["width_m"]), "Room width", 100)
                        depth = positive_number(item.get("depth_m", room_rows[rid]["depth_m"]), "Room depth", 100)
                        if width <= 0 or depth <= 0 or width > current["plot_width_m"] or depth > current["plot_length_m"]:
                            raise ValueError("Room dimensions must fit within the plot dimensions.")
                        x = min(x, max(0, current["plot_width_m"] - width))
                        y = min(y, max(0, current["plot_length_m"] - depth))
                        con.execute("UPDATE rooms SET x_m=?,y_m=?,width_m=?,depth_m=?,updated_at=? WHERE id=? AND project_id=?", (round(x,2),round(y,2),round(width,2),round(depth,2),dt_text(),rid,project_id))
                        updated += 1
                    con.execute("UPDATE projects SET updated_at=? WHERE id=?", (dt_text(), project_id))
                self._activity(user["id"], project_id, "plan.saved", "Saved floor plan layout")
                return self._json({"ok": True, "updated": updated, "saved_at": dt_text()})
            if action == "rooms" and method == "POST":
                self._require_write(user)
                return self._add_room(req, project, user, None)
            room_match = re.fullmatch(r"rooms/(\d+)/(delete|update)", action)
            if room_match:
                rid, room_action = int(room_match.group(1)), room_match.group(2)
                with self.conn() as con:
                    room = con.execute("SELECT * FROM rooms WHERE id=? AND project_id=?", (rid, project_id)).fetchone()
                if not room:
                    raise HttpError(404, "Room not found.")
                if method == "POST" and room_action == "delete":
                    self._require_write(user)
                    with self.conn() as con:
                        con.execute("DELETE FROM rooms WHERE id=? AND project_id=?", (rid, project_id))
                    self._activity(user["id"], project_id, "room.deleted", f"Removed room {room['name']}")
                    return self._redirect(f"/projects/{project_id}/plan?notice=" + quote("Room removed."))
                if method == "POST" and room_action == "update":
                    self._require_write(user)
                    return self._update_room(req, project, user, room, None)
            if action == "estimate" and method == "GET":
                est, lines, calc = self._project_estimate(project_id)
                return self._render("estimate.html", req, project=project, estimate_record=est, lines=lines, calculation=calc, can_write=can_write, user_record=user)
            if action == "estimate" and method == "POST":
                self._require_write(user)
                rate = positive_number(req.form.get("cost_per_m2"), "Cost per square metre", 100_000_000)
                contingency = positive_number(req.form.get("contingency_pct"), "Contingency", 100)
                finish = req.form.get("finish_level", "standard")
                if finish not in {"economy", "standard", "premium"}:
                    raise ValueError("Choose an available finish level.")
                assumption = (req.form.get("assumptions") or "").strip()[:1000]
                with self.conn() as con:
                    con.execute("UPDATE estimates SET cost_per_m2=?,contingency_pct=?,finish_level=?,assumptions=?,updated_at=? WHERE project_id=?", (rate, contingency, finish, assumption, dt_text(), project_id))
                    con.execute("UPDATE projects SET updated_at=? WHERE id=?", (dt_text(), project_id))
                self._activity(user["id"], project_id, "estimate.updated", "Updated cost estimate assumptions")
                return self._redirect(f"/projects/{project_id}/estimate?notice=" + quote("Estimate assumptions saved."))
            if action == "materials" and method in {"GET", "POST"}:
                if method == "GET":
                    with self.conn() as con:
                        materials = con.execute("SELECT * FROM materials WHERE project_id=? ORDER BY id DESC", (project_id,)).fetchall()
                    total = sum(float(m["quantity"]) * float(m["unit_cost"]) for m in materials)
                    return self._render("materials.html", req, project=project, materials=materials, total=total, can_write=can_write, user_record=user)
                self._require_write(user)
                name = (req.form.get("name") or "").strip()[:120]
                unit = (req.form.get("unit") or "unit").strip()[:30]
                qty = positive_number(req.form.get("quantity", "1"), "Quantity", 10_000_000)
                price = positive_number(req.form.get("unit_cost", "0"), "Unit cost", 1_000_000_000)
                if not name or qty <= 0:
                    return self._redirect(f"/projects/{project_id}/materials?notice=" + quote("Add a material name and a quantity greater than zero."))
                with self.conn() as con:
                    con.execute("INSERT INTO materials(project_id,name,quantity,unit,unit_cost,supplier,notes) VALUES(?,?,?,?,?,?,?)", (project_id, name, qty, unit or "unit", price, (req.form.get("supplier") or "")[:160], (req.form.get("notes") or "")[:1000]))
                self._activity(user["id"], project_id, "material.added", f"Added material: {name}")
                return self._redirect(f"/projects/{project_id}/materials?notice=" + quote("Material added."))
            material_delete = re.fullmatch(r"materials/(\d+)/delete", action)
            if material_delete and method == "POST":
                self._require_write(user)
                with self.conn() as con:
                    cur = con.execute("DELETE FROM materials WHERE id=? AND project_id=?", (int(material_delete.group(1)), project_id))
                    if cur.rowcount == 0:
                        raise HttpError(404, "Material not found.")
                return self._redirect(f"/projects/{project_id}/materials?notice=" + quote("Material removed."))
            if action == "expenses" and method in {"GET", "POST"}:
                if method == "GET":
                    with self.conn() as con:
                        expenses = con.execute("SELECT * FROM expenses WHERE project_id=? ORDER BY spent_on DESC,id DESC", (project_id,)).fetchall()
                        totals = con.execute("SELECT COALESCE(SUM(amount),0) AS total FROM expenses WHERE project_id=?", (project_id,)).fetchone()["total"]
                    return self._render("expenses.html", req, project=project, expenses=expenses, total=totals, can_write=can_write, user_record=user)
                self._require_write(user)
                category = (req.form.get("category") or "General").strip()[:100]
                description = (req.form.get("description") or "").strip()[:200]
                amount = positive_number(req.form.get("amount", "0"), "Expense amount", 1_000_000_000_000)
                spent_on = (req.form.get("spent_on") or datetime.now().strftime("%Y-%m-%d"))
                if not description or amount <= 0 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", spent_on):
                    return self._redirect(f"/projects/{project_id}/expenses?notice=" + quote("Enter a description, valid date, and an amount greater than zero."))
                with self.conn() as con:
                    con.execute("INSERT INTO expenses(project_id,category,description,amount,spent_on,vendor) VALUES(?,?,?,?,?,?)", (project_id, category, description, amount, spent_on, (req.form.get("vendor") or "")[:160]))
                self._activity(user["id"], project_id, "expense.added", f"Recorded expense: {description}")
                return self._redirect(f"/projects/{project_id}/expenses?notice=" + quote("Expense recorded."))
            expense_delete = re.fullmatch(r"expenses/(\d+)/delete", action)
            if expense_delete and method == "POST":
                self._require_write(user)
                with self.conn() as con:
                    cur = con.execute("DELETE FROM expenses WHERE id=? AND project_id=?", (int(expense_delete.group(1)), project_id))
                    if cur.rowcount == 0:
                        raise HttpError(404, "Expense not found.")
                return self._redirect(f"/projects/{project_id}/expenses?notice=" + quote("Expense removed."))
            if action == "tasks" and method in {"GET", "POST"}:
                if method == "GET":
                    with self.conn() as con:
                        tasks = con.execute("SELECT * FROM tasks WHERE project_id=? ORDER BY CASE status WHEN 'todo' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END, due_date, id", (project_id,)).fetchall()
                    return self._render("tasks.html", req, project=project, tasks=tasks, can_write=can_write, user_record=user)
                self._require_write(user)
                title = (req.form.get("title") or "").strip()[:180]
                description = (req.form.get("description") or "").strip()[:1200]
                status = req.form.get("status", "todo")
                priority = req.form.get("priority", "normal")
                due_date = (req.form.get("due_date") or "").strip() or None
                if not title or status not in {"todo","in_progress","done"} or priority not in {"low","normal","high"}:
                    return self._redirect(f"/projects/{project_id}/tasks?notice=" + quote("Enter a task title and valid task status/priority."))
                if due_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due_date):
                    return self._redirect(f"/projects/{project_id}/tasks?notice=" + quote("Due date must use YYYY-MM-DD format."))
                with self.conn() as con:
                    con.execute("INSERT INTO tasks(project_id,title,description,status,priority,due_date) VALUES(?,?,?,?,?,?)", (project_id, title, description, status, priority, due_date))
                self._activity(user["id"], project_id, "task.created", f"Added task: {title}")
                return self._redirect(f"/projects/{project_id}/tasks?notice=" + quote("Task added."))
            task_action = re.fullmatch(r"tasks/(\d+)/(status|delete)", action)
            if task_action and method == "POST":
                self._require_write(user)
                tid, t_action = int(task_action.group(1)), task_action.group(2)
                with self.conn() as con:
                    task = con.execute("SELECT * FROM tasks WHERE id=? AND project_id=?", (tid, project_id)).fetchone()
                    if not task:
                        raise HttpError(404, "Task not found.")
                    if t_action == "delete":
                        con.execute("DELETE FROM tasks WHERE id=? AND project_id=?", (tid, project_id))
                    else:
                        status = req.form.get("status", "todo")
                        if status not in {"todo","in_progress","done"}:
                            raise ValueError("Invalid task status.")
                        con.execute("UPDATE tasks SET status=?,updated_at=? WHERE id=? AND project_id=?", (status, dt_text(), tid, project_id))
                return self._redirect(f"/projects/{project_id}/tasks?notice=" + quote("Task updated."))
            if action == "documents" and method in {"GET", "POST"}:
                if method == "GET":
                    with self.conn() as con:
                        documents = con.execute("SELECT d.*, u.full_name AS uploader FROM documents d LEFT JOIN users u ON u.id=d.uploader_id WHERE d.project_id=? ORDER BY d.created_at DESC", (project_id,)).fetchall()
                    return self._render("documents.html", req, project=project, documents=documents, can_write=can_write, user_record=user)
                self._require_write(user)
                upload = req.form.get("file")
                note = (req.form.get("notes") or "").strip()[:1000]
                if not isinstance(upload, dict) or not upload.get("filename") or not upload.get("data"):
                    return self._redirect(f"/projects/{project_id}/documents?notice=" + quote("Choose a file to upload."))
                data = upload["data"]
                if len(data) > self.config.MAX_UPLOAD_BYTES:
                    raise HttpError(413, f"Files must be {self.config.MAX_UPLOAD_BYTES // (1024*1024)} MB or smaller.")
                original = safe_filename(upload["filename"])
                ext = Path(original).suffix.lower()
                if ext not in ALLOWED_UPLOADS:
                    return self._redirect(f"/projects/{project_id}/documents?notice=" + quote("That file type is not allowed. Use PDF, image, CSV, text, DOCX or XLSX files."))
                # Block common executable/script signatures even if renamed.
                sample = data[:512].lstrip().lower()
                if sample.startswith((b"<!doctype html", b"<html", b"<script", b"#!", b"<?php", b"mz")):
                    return self._redirect(f"/projects/{project_id}/documents?notice=" + quote("That file does not look like a supported document."))
                stored = secrets.token_hex(20) + ext
                (self.storage_dir / stored).write_bytes(data)
                ctype = mimetypes.guess_type(original)[0] or "application/octet-stream"
                try:
                    with self.conn() as con:
                        con.execute("INSERT INTO documents(project_id,uploader_id,original_name,stored_name,content_type,size_bytes,notes) VALUES(?,?,?,?,?,?,?)", (project_id, user["id"], original, stored, ctype, len(data), note))
                except Exception:
                    (self.storage_dir / stored).unlink(missing_ok=True)
                    raise
                self._activity(user["id"], project_id, "document.uploaded", f"Uploaded document: {original}")
                return self._redirect(f"/projects/{project_id}/documents?notice=" + quote("Document uploaded to private project storage."))
            doc_match = re.fullmatch(r"documents/(\d+)", action)
            if doc_match and method == "GET":
                with self.conn() as con:
                    doc = con.execute("SELECT * FROM documents WHERE id=? AND project_id=?", (int(doc_match.group(1)), project_id)).fetchone()
                if not doc:
                    raise HttpError(404, "Document not found.")
                filepath = self.storage_dir / doc["stored_name"]
                if not filepath.is_file():
                    raise HttpError(404, "The stored document is unavailable.")
                body = filepath.read_bytes()
                return Response(body, 200, doc["content_type"], [("Content-Disposition", f"attachment; filename=\"{safe_filename(doc['original_name'])}\""), ("X-Content-Type-Options", "nosniff")])
            if action == "export.csv" and method == "GET":
                output = io.StringIO(newline="")
                writer = SafeCSVWriter(csv.writer(output))
                writer.writerow(["record_type","name_or_description","quantity","unit","amount","status_or_date"])
                with self.conn() as con:
                    writer.writerow(["project", project["name"], "", "", project["budget"], project["status"]])
                    writer.writerow(["estimate", "Estimated total", project["built_area_m2"], "m²", self._project_estimate(project_id)[2]["total"], "planning assumption"])
                    for m in con.execute("SELECT name,quantity,unit,unit_cost FROM materials WHERE project_id=?", (project_id,)).fetchall():
                        writer.writerow(["material", m["name"], m["quantity"], m["unit"], m["quantity"] * m["unit_cost"], ""])
                    for e in con.execute("SELECT description,category,amount,spent_on FROM expenses WHERE project_id=?", (project_id,)).fetchall():
                        writer.writerow(["expense", e["description"], "", e["category"], e["amount"], e["spent_on"]])
                    for t in con.execute("SELECT title,status,due_date FROM tasks WHERE project_id=?", (project_id,)).fetchall():
                        writer.writerow(["task", t["title"], "", "", "", f"{t['status']} / {t['due_date'] or ''}"])
                filename = re.sub(r"[^A-Za-z0-9_-]", "-", project["name"]).strip("-")[:50] or "project"
                return Response(output.getvalue().encode("utf-8-sig"), 200, "text/csv; charset=utf-8", [("Content-Disposition", f"attachment; filename=\"{filename}-report.csv\"")])
            if action == "report.pdf" and method == "GET":
                return self._project_pdf(project, user)

        if path == "/settings":
            user = self._require_user(req)
            if method == "GET":
                return self._render("settings.html", req, user_record=user, can_write=self._can_write(user))
            if method == "POST":
                name = (req.form.get("full_name") or "").strip()[:120]
                role = req.form.get("role", user["role"])
                units = req.form.get("units", user["units"])
                currency = req.form.get("currency", user["currency"])
                old_password = req.form.get("current_password", "")
                new_password = req.form.get("new_password", "")
                if not name or role not in {"homeowner","architect","civil_engineer","contractor","interior_designer","other"} or units not in {"metric","imperial"} or currency not in {"INR","USD","GBP","EUR","AUD","CAD"}:
                    return self._render("settings.html", req, user_record=user, error="Check your profile fields and try again.", can_write=self._can_write(user))
                new_hash = user["password_hash"]
                if new_password:
                    if not verify_password(old_password, user["password_hash"]):
                        return self._render("settings.html", req, user_record=user, error="Your current password is not correct.", can_write=self._can_write(user))
                    try:
                        new_hash = hash_password(new_password)
                    except ValueError as exc:
                        return self._render("settings.html", req, user_record=user, error=str(exc), can_write=self._can_write(user))
                with self.conn() as con:
                    con.execute("UPDATE users SET full_name=?,role=?,units=?,currency=?,password_hash=?,updated_at=? WHERE id=?", (name, role, units, currency, new_hash, dt_text(), user["id"]))
                return self._redirect("/settings?notice=" + quote("Account settings saved."))

        if path == "/billing":
            user = self._require_user(req)
            with self.conn() as con:
                subscription = con.execute("SELECT * FROM subscriptions WHERE user_id=? ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
            plans = self._plans()
            if method == "GET":
                return self._render("billing.html", req, user_record=user, subscription=subscription, plans=plans, can_write=self._can_write(user), reason=req.arg("reason", ""))
            if method == "POST" and req.form.get("action") == "checkout":
                plan_key = req.form.get("plan_key", "")
                try:
                    checkout = billing.create_subscription(plan_key, user["id"], user["email"], self.config)
                except (ValueError, RuntimeError) as exc:
                    return self._render("billing.html", req, user_record=user, subscription=subscription, plans=plans, can_write=self._can_write(user), error=str(exc), reason="")
                with self.conn() as con:
                    con.execute("INSERT INTO subscriptions(user_id,provider,provider_subscription_id,plan_key,status,checkout_url) VALUES(?, 'razorpay', ?, ?, 'pending', ?)", (user["id"], checkout["id"], plan_key, checkout["short_url"]))
                self._activity(user["id"], None, "billing.checkout_started", f"Started checkout for {plan_key} plan")
                return self._redirect(checkout["short_url"])
        if path == "/webhooks/razorpay" and method == "POST":
            return self._razorpay_webhook(req)

        if path == "/admin" or path.startswith("/admin/"):
            admin = self._require_admin(req)
            if path == "/admin" and method == "GET":
                with self.conn() as con:
                    counts = {
                        "users": con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"],
                        "projects": con.execute("SELECT COUNT(*) n FROM projects WHERE owner_id IS NOT NULL").fetchone()["n"],
                        "trialing": con.execute("SELECT COUNT(*) n FROM users WHERE trial_status='trialing' AND trial_ends_at>?", (dt_text(),)).fetchone()["n"],
                        "paid": con.execute("SELECT COUNT(DISTINCT user_id) n FROM subscriptions WHERE status='active'").fetchone()["n"],
                        "contacts": con.execute("SELECT COUNT(*) n FROM contact_requests").fetchone()["n"],
                    }
                    audit = con.execute("SELECT a.*,u.email FROM audit_log a LEFT JOIN users u ON u.id=a.actor_user_id ORDER BY a.id DESC LIMIT 15").fetchall()
                    settings = {r["key"]: r["value"] for r in con.execute("SELECT key,value FROM app_settings").fetchall()}
                return self._render("admin.html", req, admin=admin, counts=counts, audit=audit, settings=settings, users=None, contacts=None)
            if path == "/admin/settings" and method == "POST":
                trial_days = req.form.get("trial_days", "7")
                demo_enabled = req.form.get("public_demo_enabled", "0")
                try:
                    days = int(trial_days)
                except ValueError:
                    days = 0
                if not 1 <= days <= 60 or demo_enabled not in {"0", "1"}:
                    raise ValueError("Trial duration must be 1–60 days and demo must be enabled or disabled.")
                with self.conn() as con:
                    con.execute("INSERT INTO app_settings(key,value,updated_at) VALUES('trial_days',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (str(days), dt_text()))
                    con.execute("INSERT INTO app_settings(key,value,updated_at) VALUES('public_demo_enabled',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (demo_enabled, dt_text()))
                    con.execute("INSERT INTO audit_log(actor_user_id,action,subject_type,subject_id,details) VALUES(?,?,?,?,?)", (admin["id"], "settings.updated", "app_settings", "trial_days", f"trial_days={days}; public_demo_enabled={demo_enabled}"))
                return self._redirect("/admin?notice=" + quote("Application settings updated."))
            if path == "/admin/users" and method == "GET":
                with self.conn() as con:
                    users = con.execute("SELECT u.id,u.email,u.full_name,u.role,u.is_admin,u.trial_status,u.trial_ends_at,u.created_at,(SELECT COUNT(*) FROM projects p WHERE p.owner_id=u.id) project_count,(SELECT s.status FROM subscriptions s WHERE s.user_id=u.id ORDER BY s.id DESC LIMIT 1) subscription_status FROM users u ORDER BY u.created_at DESC LIMIT 200").fetchall()
                return self._render("admin.html", req, admin=admin, counts=None, audit=None, settings=None, users=users, contacts=None)
            if path == "/admin/contacts" and method == "GET":
                with self.conn() as con:
                    contacts = con.execute("SELECT * FROM contact_requests ORDER BY id DESC LIMIT 200").fetchall()
                return self._render("admin.html", req, admin=admin, counts=None, audit=None, settings=None, users=None, contacts=contacts)
        if path == "/robots.txt" and method == "GET":
            return Response("User-agent: *\nAllow: /\nDisallow: /app\nDisallow: /projects\nDisallow: /settings\nDisallow: /billing\nDisallow: /admin\n", 200, "text/plain; charset=utf-8")
        if path == "/sitemap.xml" and method == "GET":
            urls = ["/", "/features", "/how-it-works", "/pricing", "/about", "/faq", "/contact", "/privacy", "/terms"]
            body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(f"<url><loc>{self.config.APP_URL}{u}</loc></url>" for u in urls) + "</urlset>"
            return Response(body, 200, "application/xml; charset=utf-8")
        if path == "/favicon.svg" and method == "GET":
            svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="#1F6F6B"/><path d="M7 15.5 16 8l9 7.5M10 14.5V24h12v-9.5M14 24v-5h4v5" fill="none" stroke="#fff" stroke-width="2.2" stroke-linejoin="round"/></svg>'
            return Response(svg, 200, "image/svg+xml")
        if path.startswith("/static/") and method == "GET":
            relative = path[len("/static/"):]
            target = (Path(__file__).resolve().parent.parent / "static" / relative).resolve()
            base = (Path(__file__).resolve().parent.parent / "static").resolve()
            if base not in target.parents or not target.is_file():
                raise HttpError(404, "Asset not found.")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            return Response(target.read_bytes(), 200, ctype)
        raise HttpError(404, "We couldn't find that page.")

    def _project_form_values(self, form):
        try:
            name = (form.get("name") or "").strip()
            if not name or len(name) > 120:
                raise ValueError("Project name is required and must be 120 characters or fewer.")
            ptype = form.get("project_type", "new_home")
            if ptype not in PROJECT_TYPES:
                raise ValueError("Choose a valid project type.")
            plot_length = positive_number(form.get("plot_length_m", "20"), "Plot length", 10000)
            plot_width = positive_number(form.get("plot_width_m", "15"), "Plot width", 10000)
            floors = int(form.get("floors", "1"))
            if not 1 <= floors <= 20:
                raise ValueError("Floors must be between 1 and 20.")
            built_area = positive_number(form.get("built_area_m2", "120"), "Built-up area", 10_000_000)
            budget = positive_number(form.get("budget", "0"), "Budget", 1_000_000_000_000)
            currency = form.get("currency", "INR")
            if currency not in {"INR","USD","GBP","EUR","AUD","CAD"}:
                raise ValueError("Choose a supported currency.")
            start = (form.get("start_date") or "").strip() or None
            target = (form.get("target_date") or "").strip() or None
            for v in (start,target):
                if v:
                    datetime.strptime(v, "%Y-%m-%d")
            if start and target and target < start:
                raise ValueError("Target date cannot be before the start date.")
            vals = (name, ptype, (form.get("description") or "")[:2000], (form.get("location") or "")[:160], plot_length, plot_width, floors, built_area, budget, currency, (form.get("notes") or "")[:4000], start, target)
            return {"values": vals, "error": ""}
        except (ValueError, TypeError) as exc:
            return {"values": (), "error": str(exc) or "Please check the project details."}

    def _add_room(self, req, project, user, demo_id):
        name = (req.form.get("name") or "").strip()[:100]
        room_type = req.form.get("room_type", "other")
        if room_type not in ROOM_TYPES:
            room_type = "other"
        try:
            width = positive_number(req.form.get("width_m", "3"), "Room width", 100)
            depth = positive_number(req.form.get("depth_m", "3"), "Room depth", 100)
            if not name or width <= 0 or depth <= 0 or width > project["plot_width_m"] or depth > project["plot_length_m"]:
                raise ValueError("Enter a room name and dimensions that fit within the plot.")
            doors = int(req.form.get("doors", "1")); windows = int(req.form.get("windows", "1")); floor = int(req.form.get("floor_no", "1"))
            if not 0 <= doors <= 20 or not 0 <= windows <= 50 or not 1 <= floor <= project["floors"]:
                raise ValueError("Check door/window counts and floor number.")
        except (ValueError, TypeError) as exc:
            return self._redirect(f"{('/demo' if demo_id else f'/projects/{project['id']}')}/plan?notice=" + quote(str(exc)))
        table_scope, params = ("demo_session_id=?", (demo_id,)) if demo_id else ("owner_id=? AND demo_session_id IS NULL", (user["id"],))
        with self.conn() as con:
            count = con.execute(f"SELECT COUNT(*) n FROM projects WHERE id=? AND {table_scope}", (project["id"], *params)).fetchone()["n"]
            if not count:
                raise HttpError(404, "Project not found.")
            room_count = con.execute("SELECT COUNT(*) n FROM rooms WHERE project_id=?", (project["id"],)).fetchone()["n"]
            x = min(max(0, (room_count % 4) * min(4, width + 0.5)), max(0, project["plot_width_m"] - width))
            y = min(max(0, (room_count // 4) * min(4, depth + 0.5)), max(0, project["plot_length_m"] - depth))
            con.execute("INSERT INTO rooms(project_id,name,room_type,width_m,depth_m,x_m,y_m,floor_no,color,doors,windows) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (project["id"], name, room_type, width, depth, x, y, floor, ROOM_COLORS[room_type], doors, windows))
            con.execute("UPDATE projects SET updated_at=? WHERE id=?", (dt_text(), project["id"]))
        if demo_id:
            self._activity(None, project["id"], "demo.room_added", f"Added demo room: {name}", demo_id)
            return self._redirect("/demo/plan?notice=" + quote("Room added to your isolated demo."))
        self._activity(user["id"], project["id"], "room.added", f"Added room: {name}")
        return self._redirect(f"/projects/{project['id']}/plan?notice=" + quote("Room added."))

    def _update_room(self, req, project, user, room, demo_id):
        name = (req.form.get("name") or room["name"]).strip()[:100]
        room_type = req.form.get("room_type", room["room_type"])
        if room_type not in ROOM_TYPES:
            room_type = "other"
        try:
            width = positive_number(req.form.get("width_m", room["width_m"]), "Room width", 100)
            depth = positive_number(req.form.get("depth_m", room["depth_m"]), "Room depth", 100)
            x = positive_number(req.form.get("x_m", room["x_m"]), "Room x position", 500)
            y = positive_number(req.form.get("y_m", room["y_m"]), "Room y position", 500)
            doors = int(req.form.get("doors", room["doors"])); windows = int(req.form.get("windows", room["windows"]))
            floor = int(req.form.get("floor_no", room["floor_no"]))
            if not name or width <= 0 or depth <= 0 or width > project["plot_width_m"] or depth > project["plot_length_m"]:
                raise ValueError("Room name and dimensions must be valid and fit within the plot.")
            if not 0 <= doors <= 20 or not 0 <= windows <= 50 or not 1 <= floor <= project["floors"]:
                raise ValueError("Check door/window counts and floor number.")
            x = min(x, project["plot_width_m"] - width); y = min(y, project["plot_length_m"] - depth)
        except (ValueError, TypeError) as exc:
            prefix = "/demo" if demo_id else f"/projects/{project['id']}"
            return self._redirect(f"{prefix}/plan?notice=" + quote(str(exc)))
        with self.conn() as con:
            con.execute("UPDATE rooms SET name=?,room_type=?,width_m=?,depth_m=?,x_m=?,y_m=?,floor_no=?,color=?,doors=?,windows=?,updated_at=? WHERE id=? AND project_id=?", (name,room_type,width,depth,x,y,floor,ROOM_COLORS[room_type],doors,windows,dt_text(),room["id"],project["id"]))
        prefix = "/demo" if demo_id else f"/projects/{project['id']}"
        return self._redirect(f"{prefix}/plan?notice=" + quote("Room details saved."))

    def _dashboard(self, req, user):
        with self.conn() as con:
            projects = con.execute("SELECT * FROM projects WHERE owner_id=? AND demo_session_id IS NULL AND status!='archived' ORDER BY updated_at DESC LIMIT 5", (user["id"],)).fetchall()
            totals = con.execute("SELECT COUNT(*) count, SUM(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) active, COALESCE(SUM(budget),0) budget FROM projects WHERE owner_id=? AND status!='archived'", (user["id"],)).fetchone()
            tasks = con.execute("SELECT t.*,p.name project_name,p.id project_id FROM tasks t JOIN projects p ON p.id=t.project_id WHERE p.owner_id=? AND t.status!='done' AND p.status!='archived' ORDER BY COALESCE(t.due_date,'9999-12-31'),t.id LIMIT 8", (user["id"],)).fetchall()
            activity = con.execute("SELECT a.*,p.name project_name FROM activity a LEFT JOIN projects p ON p.id=a.project_id WHERE a.owner_id=? ORDER BY a.id DESC LIMIT 8", (user["id"],)).fetchall()
            spent = con.execute("SELECT COALESCE(SUM(e.amount),0) total FROM expenses e JOIN projects p ON p.id=e.project_id WHERE p.owner_id=?", (user["id"],)).fetchone()["total"]
        trial_active = self._trial_active(user)
        return self._render("dashboard.html", req, projects=projects, totals=totals, tasks=tasks, activity=activity, spent=spent, can_write=self._can_write(user), trial_active=trial_active, user_record=user)

    def _plans(self):
        return [billing.configured_plan(key, self.config) for key in ("starter", "professional", "team")]

    def _razorpay_webhook(self, req):
        raw = req.body
        signature = req.headers.get("x-razorpay-signature", "")
        event_id = req.headers.get("x-razorpay-event-id", "")[:200]
        if not self.config.RAZORPAY_WEBHOOK_SECRET or not billing.webhook_signature_valid(raw, signature, self.config.RAZORPAY_WEBHOOK_SECRET):
            return self._json({"ok": False, "error": "Invalid webhook signature."}, 400)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return self._json({"ok": False, "error": "Invalid JSON payload."}, 400)
        if not isinstance(payload, dict):
            return self._json({"ok": False, "error": "Invalid JSON payload."}, 400)
        event_type = str(payload.get("event", "unknown"))[:160]
        # Razorpay sends an event ID header; derive a deterministic digest only as a fallback.
        if not event_id:
            event_id = hashlib.sha256(raw).hexdigest()
        if event_type not in {"subscription.activated", "subscription.charged", "subscription.authenticated", "subscription.pending", "subscription.halted", "subscription.cancelled", "subscription.completed", "subscription.paused", "subscription.resumed"}:
            with self.conn() as con:
                try:
                    con.execute("INSERT INTO billing_events(provider_event_id,event_type) VALUES(?,?)", (event_id, event_type))
                except sqlite3.IntegrityError:
                    return self._json({"ok": True, "duplicate": True})
            return self._json({"ok": True, "ignored": True})
        payloads = payload.get("payload", {})
        entity_block = payloads.get("subscription", {}) if isinstance(payloads, dict) else {}
        entity = entity_block.get("entity", {}) if isinstance(entity_block, dict) else {}
        provider_id = entity.get("id")
        if not provider_id:
            return self._json({"ok": False, "error": "Subscription identifier is missing."}, 400)
        status_by_event = {
            "subscription.activated": "active", "subscription.charged": "active", "subscription.authenticated": "authenticated",
            "subscription.pending": "pending", "subscription.halted": "halted", "subscription.cancelled": "cancelled",
            "subscription.completed": "completed", "subscription.paused": "past_due", "subscription.resumed": "active",
        }
        new_status = status_by_event[event_type]
        period_end = billing.parse_period_end(entity)
        with transaction(self.database_path) as con:
            try:
                con.execute("INSERT INTO billing_events(provider_event_id,event_type) VALUES(?,?)", (event_id, event_type))
            except sqlite3.IntegrityError:
                return self._json({"ok": True, "duplicate": True})
            row = con.execute("SELECT * FROM subscriptions WHERE provider_subscription_id=?", (provider_id,)).fetchone()
            if row:
                # Ignore older pending/authenticated events after an active state; the provider may retry events out of order.
                current_status = row["status"]
                if current_status == "active" and new_status in {"pending", "authenticated"}:
                    new_status = current_status
                con.execute("UPDATE subscriptions SET status=?,current_period_end=COALESCE(?,current_period_end),updated_at=? WHERE id=?", (new_status, period_end, dt_text(), row["id"]))
                if new_status == "active":
                    con.execute("UPDATE users SET trial_status='converted',updated_at=? WHERE id=?", (dt_text(), row["user_id"]))
                con.execute("INSERT INTO audit_log(actor_user_id,action,subject_type,subject_id,details) VALUES(?,?,?,?,?)", (row["user_id"], "billing.webhook", "subscription", str(row["id"]), f"Verified event {event_type}; status {new_status}"))
        return self._json({"ok": True})

    def _project_pdf(self, project, user):
        try:
            from xml.sax.saxutils import escape as xml_escape
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import mm
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
        except ImportError:
            raise HttpError(503, "PDF export is unavailable because the optional reportlab dependency is not installed. Use CSV export instead.")
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=18*mm, bottomMargin=18*mm, title=f"{project['name']} — Project report")
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name="SmallMuted", parent=styles["BodyText"], textColor=colors.HexColor("#5B6975"), fontSize=8, leading=11))
        story = [Paragraph("PLAN YOUR HOME", styles["Kicker"] if "Kicker" in styles else styles["Heading3"]), Spacer(1, 3*mm), Paragraph(xml_escape(str(project["name"])), styles["Title"]), Paragraph("Project planning report · illustrative information, not certified design advice", styles["SmallMuted"]), Spacer(1, 7*mm)]
        area = area_from_plot(project["plot_length_m"], project["plot_width_m"])
        data = [["Project type", PROJECT_TYPES.get(project["project_type"], project["project_type"])], ["Status", project["status"].replace("_", " ").title()], ["Location", project["location"] or "Not set"], ["Plot size", f"{project['plot_length_m']:.1f} m × {project['plot_width_m']:.1f} m ({area:,.1f} m²)"], ["Floors", str(project["floors"])], ["Built-up area", f"{project['built_area_m2']:,.1f} m²"], ["Budget", self.money(project["budget"], project["currency"])]]
        table = Table(data, colWidths=[45*mm, 112*mm])
        table.setStyle(TableStyle([("BACKGROUND",(0,0),(0,-1),colors.HexColor("#F0F3EF")),("TEXTCOLOR",(0,0),(-1,-1),colors.HexColor("#172B3A")),("FONTNAME",(0,0),(0,-1),"Helvetica-Bold"),("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#DCE2E0")),("VALIGN",(0,0),(-1,-1),"TOP"),("PADDING",(0,0),(-1,-1),7)]))
        story += [table, Spacer(1, 7*mm), Paragraph("Cost estimate", styles["Heading2"])]
        est, lines, calc = self._project_estimate(project["id"])
        if calc:
            story.append(Paragraph(f"Planning rate: {self.money(est['cost_per_m2'], project['currency'])} per m² · Contingency: {est['contingency_pct']}% · Finish: {est['finish_level'].title()}", styles["BodyText"]))
            e_data = [["Category", "Allocation", "Indicative amount"]] + [[line["category"], f"{line['percent']:.1f}%", self.money(line["amount"], project["currency"])] for line in calc["lines"]]
            e_data += [["Base estimate", "100%", self.money(calc["base_cost"], project["currency"])], ["Contingency", f"{calc['contingency_pct']}%", self.money(calc["contingency_amount"], project["currency"])], ["Estimated total", "", self.money(calc["total"], project["currency"])]]
            t = Table(e_data, colWidths=[72*mm, 27*mm, 58*mm], repeatRows=1)
            t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1F6F6B")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("GRID",(0,0),(-1,-1),0.25,colors.HexColor("#DCE2E0")),("ALIGN",(1,1),(-1,-1),"RIGHT"),("PADDING",(0,0),(-1,-1),6),("BACKGROUND",(0,-1),(-1,-1),colors.HexColor("#E8F1EC")),("FONTNAME",(0,-1),(-1,-1),"Helvetica-Bold")]))
            story += [Spacer(1, 3*mm), t, Spacer(1, 3*mm), Paragraph(xml_escape(str(est["assumptions"] or "")), styles["SmallMuted"])]
        with self.conn() as con:
            rooms = con.execute("SELECT * FROM rooms WHERE project_id=? ORDER BY floor_no,id", (project["id"],)).fetchall()
            tasks = con.execute("SELECT * FROM tasks WHERE project_id=? ORDER BY id", (project["id"],)).fetchall()
            expenses = con.execute("SELECT * FROM expenses WHERE project_id=? ORDER BY spent_on DESC,id DESC LIMIT 20", (project["id"],)).fetchall()
        story += [Spacer(1, 7*mm), Paragraph("Space programme", styles["Heading2"])]
        r_data = [["Floor", "Room", "Dimensions", "Area"]] + [[str(r["floor_no"]), r["name"], f"{r['width_m']:.1f} × {r['depth_m']:.1f} m", f"{room_area(r['width_m'],r['depth_m']):,.1f} m²"] for r in rooms]
        if len(r_data) == 1: r_data.append(["—", "No rooms added", "—", "—"])
        rt = Table(r_data, colWidths=[18*mm, 67*mm, 44*mm, 28*mm], repeatRows=1)
        rt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#E8F1EC")),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("GRID",(0,0),(-1,-1),0.25,colors.HexColor("#DCE2E0")),("PADDING",(0,0),(-1,-1),6)]))
        story.append(rt)
        story += [Spacer(1, 7*mm), Paragraph("Tasks and milestones", styles["Heading2"])]
        t_data = [["Task", "Status", "Priority", "Due date"]] + [[t["title"], t["status"].replace("_", " ").title(), t["priority"].title(), t["due_date"] or "Not set"] for t in tasks]
        if len(t_data) == 1: t_data.append(["No tasks added", "—", "—", "—"])
        tt = Table(t_data, colWidths=[75*mm, 26*mm, 25*mm, 31*mm], repeatRows=1)
        tt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#E8F1EC")),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("GRID",(0,0),(-1,-1),0.25,colors.HexColor("#DCE2E0")),("PADDING",(0,0),(-1,-1),6)]))
        story.append(tt)
        story += [Spacer(1, 7*mm), Paragraph("Recent expenses", styles["Heading2"])]
        x_data = [["Date", "Description", "Category", "Amount"]] + [[e["spent_on"], e["description"], e["category"], self.money(e["amount"], project["currency"])] for e in expenses]
        if len(x_data) == 1: x_data.append(["—", "No expenses recorded", "—", "—"])
        xt = Table(x_data, colWidths=[25*mm, 61*mm, 38*mm, 33*mm], repeatRows=1)
        xt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#E8F1EC")),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("GRID",(0,0),(-1,-1),0.25,colors.HexColor("#DCE2E0")),("PADDING",(0,0),(-1,-1),6)]))
        story.append(xt)
        story += [Spacer(1, 8*mm), Paragraph("Planning estimates are not quotations. Verify quantities, local rates, permits, structural details, and building-code requirements with qualified local professionals.", styles["SmallMuted"])]
        doc.build(story)
        filename = re.sub(r"[^A-Za-z0-9_-]", "-", project["name"]).strip("-")[:50] or "project"
        return Response(buffer.getvalue(), 200, "application/pdf", [("Content-Disposition", f"attachment; filename=\"{filename}-report.pdf\"")])

    def _dispatch_demo(self, req, cookie_headers):
        path = req.path.rstrip("/") or "/demo"
        if path == "/demo" and req.method == "GET":
            demo = self._demo_context(req, cookie_headers)
            project = self._demo_project(demo["id"])
            with self.conn() as con:
                rooms = con.execute("SELECT * FROM rooms WHERE project_id=? ORDER BY floor_no,id", (project["id"],)).fetchall()
                tasks = con.execute("SELECT * FROM tasks WHERE project_id=? ORDER BY id", (project["id"],)).fetchall()
                materials = con.execute("SELECT * FROM materials WHERE project_id=? ORDER BY id", (project["id"],)).fetchall()
                activity = con.execute("SELECT * FROM activity WHERE demo_session_id=? ORDER BY id DESC LIMIT 6", (demo["id"],)).fetchall()
            est, lines, calc = self._project_estimate(project["id"])
            return self._render("demo.html", req, project=project, rooms=rooms, tasks=tasks, materials=materials, estimate=calc, estimate_record=est, demo_expires=demo["expires_at"], activity=activity, can_write=True)
        demo = self._demo_context(req, cookie_headers)
        project = self._demo_project(demo["id"])
        if path == "/demo/reset" and req.method == "POST":
            # Each visitor's isolated demo namespace is reset only within its own demo session.
            with self.conn() as con:
                con.execute("DELETE FROM projects WHERE demo_session_id=?", (demo["id"],))
            self._seed_demo(demo["id"])
            return self._redirect("/demo?notice=" + quote("Your isolated demo has been reset to sample data."))
        if path == "/demo/plan" and req.method == "GET":
            with self.conn() as con:
                rooms = con.execute("SELECT * FROM rooms WHERE project_id=? ORDER BY floor_no,id", (project["id"],)).fetchall()
            return self._render("plan_editor.html", req, project=project, rooms=rooms, can_write=True, is_demo=True, save_url="/demo/plan/save", room_url="/demo/rooms", demo_expires=demo["expires_at"])
        if path == "/demo/plan/save" and req.method == "POST":
            payload = req.json
            if not isinstance(payload, dict) or not isinstance(payload.get("rooms"), list) or len(payload["rooms"]) > 200:
                return self._json({"ok": False, "error": "Invalid plan data."}, 400)
            updated = 0
            with transaction(self.database_path) as con:
                current = con.execute("SELECT * FROM projects WHERE id=? AND demo_session_id=? AND owner_id IS NULL", (project["id"], demo["id"])).fetchone()
                room_rows = {r["id"]: r for r in con.execute("SELECT * FROM rooms WHERE project_id=?", (project["id"],)).fetchall()}
                for item in payload["rooms"]:
                    if not isinstance(item, dict) or not str(item.get("id", "")).isdigit():
                        continue
                    rid = int(item["id"])
                    if rid not in room_rows:
                        continue
                    x = positive_number(item.get("x_m", room_rows[rid]["x_m"]), "x position", 500)
                    y = positive_number(item.get("y_m", room_rows[rid]["y_m"]), "y position", 500)
                    width = positive_number(item.get("width_m", room_rows[rid]["width_m"]), "width", 100)
                    depth = positive_number(item.get("depth_m", room_rows[rid]["depth_m"]), "depth", 100)
                    if width <= 0 or depth <= 0 or width > current["plot_width_m"] or depth > current["plot_length_m"]:
                        raise ValueError("Room dimensions must fit within the plot.")
                    x=min(x,current["plot_width_m"]-width); y=min(y,current["plot_length_m"]-depth)
                    con.execute("UPDATE rooms SET x_m=?,y_m=?,width_m=?,depth_m=?,updated_at=? WHERE id=? AND project_id=?", (round(x,2),round(y,2),round(width,2),round(depth,2),dt_text(),rid,project["id"]))
                    updated += 1
                con.execute("UPDATE projects SET updated_at=? WHERE id=?", (dt_text(),project["id"]))
            self._activity(None, project["id"], "demo.plan_saved", "Saved isolated demo floor plan layout", demo["id"])
            return self._json({"ok": True, "updated": updated, "saved_at": dt_text()})
        if path == "/demo/rooms" and req.method == "POST":
            return self._add_room(req, project, {"id": None}, demo["id"])
        room_match = re.fullmatch(r"/demo/rooms/(\d+)/(delete|update)", path)
        if room_match:
            with self.conn() as con:
                room = con.execute("SELECT r.* FROM rooms r JOIN projects p ON p.id=r.project_id WHERE r.id=? AND p.id=? AND p.demo_session_id=? AND p.owner_id IS NULL", (int(room_match.group(1)),project["id"],demo["id"])).fetchone()
            if not room:
                raise HttpError(404, "Room not found in this demo session.")
            if req.method == "POST" and room_match.group(2) == "delete":
                with self.conn() as con:
                    con.execute("DELETE FROM rooms WHERE id=? AND project_id=?", (room["id"],project["id"]))
                return self._redirect("/demo/plan?notice=" + quote("Room removed from this demo."))
            if req.method == "POST" and room_match.group(2) == "update":
                return self._update_room(req, project, {"id": None}, room, demo["id"])
        if path == "/demo/estimate" and req.method in {"GET", "POST"}:
            if req.method == "POST":
                rate = positive_number(req.form.get("cost_per_m2", "19000"), "Cost per square metre", 100_000_000)
                contingency = positive_number(req.form.get("contingency_pct", "12"), "Contingency", 100)
                with self.conn() as con:
                    con.execute("UPDATE estimates SET cost_per_m2=?,contingency_pct=?,updated_at=? WHERE project_id=?", (rate,contingency,dt_text(),project["id"]))
                return self._redirect("/demo/estimate?notice=" + quote("Demo estimate updated in this isolated session."))
            est, lines, calc = self._project_estimate(project["id"])
            return self._render("estimate.html", req, project=project, estimate_record=est, lines=lines, calculation=calc, can_write=True, user_record=None, is_demo=True)
        if path == "/demo/tasks" and req.method == "GET":
            with self.conn() as con:
                tasks = con.execute("SELECT * FROM tasks WHERE project_id=? ORDER BY id", (project["id"],)).fetchall()
            return self._render("tasks.html", req, project=project, tasks=tasks, can_write=False, user_record=None, is_demo=True)
        if path == "/demo/report.csv" and req.method == "GET":
            # Keep the export tied strictly to this session's sample project.
            output = io.StringIO(newline="")
            writer = SafeCSVWriter(csv.writer(output))
            writer.writerow(["record_type","name","quantity","unit","amount","status"])
            est,lines,calc=self._project_estimate(project["id"])
            writer.writerow(["project",project["name"],"","",project["budget"],project["status"]])
            writer.writerow(["estimate","Estimated total",project["built_area_m2"],"m²",calc["total"],"illustrative"])
            with self.conn() as con:
                for room in con.execute("SELECT name,width_m,depth_m FROM rooms WHERE project_id=?", (project["id"],)).fetchall():
                    writer.writerow(["room",room["name"],room_area(room["width_m"],room["depth_m"]),"m²","",""])
            return Response(output.getvalue().encode("utf-8-sig"),200,"text/csv; charset=utf-8",[("Content-Disposition","attachment; filename=\"plan-your-home-demo.csv\"")])
        if path == "/demo" and req.method == "POST":
            return self._redirect("/demo")
        raise HttpError(404, "That demo action is not available.")
