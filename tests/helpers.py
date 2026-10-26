from __future__ import annotations

import io
import re
import tempfile
import unittest
from http.cookies import SimpleCookie
from urllib.parse import urlencode, urlsplit
from wsgiref.util import setup_testing_defaults

from pyh.app import Application
from pyh.config import Config


class WSGIClient:
    """A tiny WSGI test client so core flows can run without extra dependencies."""
    def __init__(self, app, remote_addr="127.0.0.1"):
        self.app = app
        self.cookies: dict[str, str] = {}
        self.remote_addr = remote_addr

    def request(self, method="GET", url="/", data=None, headers=None, content_type=None, raw_body=None):
        parsed = urlsplit(url)
        body = raw_body
        hdrs = dict(headers or {})
        if body is None and data is not None:
            if content_type and content_type.startswith("multipart/form-data"):
                raise ValueError("For multipart requests pass raw_body and a complete content_type.")
            body = urlencode(data, doseq=True).encode("utf-8")
            content_type = content_type or "application/x-www-form-urlencoded"
        body = body or b""
        environ = {}
        setup_testing_defaults(environ)
        environ.update({
            "REQUEST_METHOD": method.upper(),
            "PATH_INFO": parsed.path or "/",
            "QUERY_STRING": parsed.query,
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "8000",
            "REMOTE_ADDR": self.remote_addr,
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(body),
            "CONTENT_LENGTH": str(len(body)),
        })
        if content_type:
            environ["CONTENT_TYPE"] = content_type
        if self.cookies:
            environ["HTTP_COOKIE"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        for name, value in hdrs.items():
            key = name.upper().replace("-", "_")
            if key in {"CONTENT_TYPE", "CONTENT_LENGTH"}:
                environ[key] = str(value)
            else:
                environ["HTTP_" + key] = str(value)
        captured = {}
        def start_response(status, response_headers, exc_info=None):
            captured["status"] = int(status.split(" ", 1)[0])
            captured["headers"] = response_headers
        result = self.app(environ, start_response)
        try:
            payload = b"".join(result)
        finally:
            close = getattr(result, "close", None)
            if close:
                close()
        response_headers = captured.get("headers", [])
        for key, value in response_headers:
            if key.lower() != "set-cookie":
                continue
            jar = SimpleCookie()
            try:
                jar.load(value)
            except Exception:
                continue
            for cookie_name, morsel in jar.items():
                if morsel["max-age"] == "0" or morsel.value == "":
                    self.cookies.pop(cookie_name, None)
                else:
                    self.cookies[cookie_name] = morsel.value
        headers_by_name = {}
        for key, value in response_headers:
            headers_by_name.setdefault(key.lower(), []).append(value)
        return TestResponse(captured.get("status", 0), headers_by_name, payload)

    def get(self, url="/", headers=None):
        return self.request("GET", url, headers=headers)

    def csrf(self, path="/", attribute="hidden"):
        response = self.get(path)
        patterns = [
            r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)',
            r'data-csrf=["\']([^"\']+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, response.text, re.I)
            if match:
                return match.group(1)
        raise AssertionError(f"No CSRF token found on {path}; status {response.status}: {response.text[:400]}")

    def post(self, url, data=None, csrf_path="/", headers=None):
        data = dict(data or {})
        data.setdefault("csrf_token", self.csrf(csrf_path))
        return self.request("POST", url, data=data, headers=headers)

    def post_json(self, url, payload, csrf_path="/", headers=None):
        hdrs = dict(headers or {})
        hdrs["X-CSRF-Token"] = self.csrf(csrf_path)
        import json
        return self.request("POST", url, raw_body=json.dumps(payload).encode(), content_type="application/json", headers=hdrs)


class TestResponse:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body
        self.text = body.decode("utf-8", "replace")

    def header(self, name, default=None):
        vals = self.headers.get(name.lower(), [])
        return vals[0] if vals else default


def multipart_body(fields, files, boundary="----PYH-test-boundary"):
    parts = []
    for name, value in fields.items():
        parts.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode(), b"\r\n",
        ])
    for name, (filename, content_type, content) in files.items():
        parts.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            content, b"\r\n",
        ])
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pyh-test-")
        root = self.temp.name
        cfg = type("TestConfig", (Config,), {
            "APP_ENV": "development",
            "APP_NAME": "Plan Your Home Test",
            "APP_URL": "http://localhost:8000",
            "SECRET_KEY": "test-only-secret-key-that-is-long-enough-123456789",
            "DATABASE_PATH": root + "/test.sqlite3",
            "STORAGE_DIR": root + "/uploads",
            "PUBLIC_DEMO_ENABLED": True,
            "DEMO_SESSION_HOURS": 8,
            "SESSION_DAYS": 14,
            "TRIAL_DAYS": 7,
            "MAX_UPLOAD_BYTES": 1024 * 1024,
            "COOKIE_SECURE": False,
            "TRUST_PROXY": False,
            "SUPPORT_EMAIL": "",
            "SMTP_HOST": "",
            "SMTP_PORT": 587,
            "SMTP_USERNAME": "",
            "SMTP_PASSWORD": "",
            "SMTP_STARTTLS": True,
            "EMAIL_FROM": "",
            "RAZORPAY_KEY_ID": "",
            "RAZORPAY_KEY_SECRET": "",
            "RAZORPAY_WEBHOOK_SECRET": "test-webhook-secret",
            "RAZORPAY_PLAN_STARTER": "",
            "RAZORPAY_PLAN_PRO": "",
            "RAZORPAY_PLAN_TEAM": "",
            "PRICE_STARTER_DISPLAY": "",
            "PRICE_PRO_DISPLAY": "",
            "PRICE_TEAM_DISPLAY": "",
        })
        self.app = Application(config=cfg)
        self.client = WSGIClient(self.app)

    def tearDown(self):
        self.temp.cleanup()

    def signup(self, client=None, email="owner@example.com", name="Project Owner", password="correct-horse-battery"):
        client = client or self.client
        response = client.post("/signup", {
            "full_name": name, "email": email, "role": "homeowner", "units": "metric",
            "password": password, "confirm_password": password,
        }, csrf_path="/signup")
        self.assertEqual(response.status, 303, response.text[:500])
        return response

    def create_project(self, client=None, name="Courtyard Home"):
        client = client or self.client
        response = client.post("/projects", {
            "name": name, "project_type": "new_home", "description": "Family home",
            "location": "Test region", "plot_length_m": "24", "plot_width_m": "18",
            "floors": "2", "built_area_m2": "210", "budget": "5200000",
            "currency": "INR", "notes": "Illustrative project requirements.",
        }, csrf_path="/projects/new")
        self.assertEqual(response.status, 303, response.text[:500])
        import re
        match = re.search(r"/projects/(\d+)", response.header("location", ""))
        self.assertIsNotNone(match, response.header("location", ""))
        return int(match.group(1))
