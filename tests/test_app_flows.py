from __future__ import annotations

import hashlib
import hmac
import json
import re

from tests.helpers import AppTestCase, WSGIClient, multipart_body
from pyh.security import token_digest


class PublicAndAccountFlows(AppTestCase):
    def test_public_pages_and_health_check_render(self):
        paths = ["/", "/features", "/how-it-works", "/pricing", "/about", "/faq", "/contact", "/privacy", "/terms", "/cookies", "/robots.txt", "/sitemap.xml", "/healthz", "/login", "/signup", "/forgot-password", "/demo"]
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status, 200, response.text[:800])
                self.assertTrue(response.body)
        health = self.client.get("/healthz")
        self.assertIn('"status": "ok"', health.text)

    def test_signup_starts_trial_and_dashboard_is_real(self):
        self.signup()
        response = self.client.get("/app")
        self.assertEqual(response.status, 200, response.text[:1000])
        self.assertIn("Project Owner", response.text)
        with self.app.conn() as con:
            user = con.execute("SELECT * FROM users WHERE email=?", ("owner@example.com",)).fetchone()
        self.assertEqual(user["trial_status"], "trialing")
        self.assertIsNotNone(user["trial_started_at"])
        self.assertIsNotNone(user["trial_ends_at"])

    def test_duplicate_email_is_rejected(self):
        self.signup()
        other = WSGIClient(self.app, remote_addr="127.0.0.2")
        response = other.post("/signup", {
            "full_name": "Another Owner", "email": "OWNER@example.com", "role": "homeowner", "units": "metric",
            "password": "correct-horse-battery", "confirm_password": "correct-horse-battery",
        }, csrf_path="/signup")
        self.assertEqual(response.status, 200)
        self.assertIn("already exists", response.text)

    def test_csrf_is_required_for_state_changes(self):
        self.signup()
        response = self.client.request("POST", "/projects", data={"name": "CSRF bypass"})
        self.assertEqual(response.status, 400)
        with self.app.conn() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM projects WHERE owner_id IS NOT NULL").fetchone()[0], 0)

    def test_oversized_request_body_is_rejected(self):
        body = b"x" * (12 * 1024 * 1024 + 32)
        response = self.client.request("POST", "/contact", raw_body=body, content_type="application/json")
        self.assertEqual(response.status, 413)


class ProjectWorkflowTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.project_id = self.create_project()

    def test_project_pages_and_core_modules_render(self):
        paths = [
            f"/projects/{self.project_id}", f"/projects/{self.project_id}/plan",
            f"/projects/{self.project_id}/estimate", f"/projects/{self.project_id}/materials",
            f"/projects/{self.project_id}/expenses", f"/projects/{self.project_id}/tasks",
            f"/projects/{self.project_id}/documents", f"/projects/{self.project_id}/export.csv",
            f"/projects/{self.project_id}/report.pdf", "/settings", "/billing", "/onboarding",
        ]
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status, 200, response.text[:1000])
        csv_response = self.client.get(f"/projects/{self.project_id}/export.csv")
        self.assertIn(b"record_type", csv_response.body)
        pdf_response = self.client.get(f"/projects/{self.project_id}/report.pdf")
        self.assertEqual(pdf_response.header("content-type").split(";", 1)[0], "application/pdf")
        self.assertTrue(pdf_response.body.startswith(b"%PDF"))

    def test_estimate_save_persists_and_math_is_reproducible(self):
        response = self.client.post(f"/projects/{self.project_id}/estimate", {
            "cost_per_m2": "2000", "contingency_pct": "10", "finish_level": "standard", "assumptions": "Test-only estimate rate."
        }, csrf_path=f"/projects/{self.project_id}/estimate")
        self.assertEqual(response.status, 303, response.text[:400])
        with self.app.conn() as con:
            row = con.execute("SELECT e.cost_per_m2,e.contingency_pct,p.built_area_m2 FROM estimates e JOIN projects p ON p.id=e.project_id WHERE p.id=?", (self.project_id,)).fetchone()
        self.assertEqual(row["cost_per_m2"], 2000)
        self.assertEqual(row["contingency_pct"], 10)
        estimate = self.client.get(f"/projects/{self.project_id}/estimate")
        self.assertIn("462,000", estimate.text)  # 210 m² x ₹2,000 + 10% contingency

    def test_material_expense_and_task_records_persist(self):
        pid = self.project_id
        response = self.client.post(f"/projects/{pid}/materials", {
            "name": "Ceramic tile", "quantity": "24", "unit": "m²", "unit_cost": "850", "supplier": "Example supplier", "notes": "Planning placeholder"
        }, csrf_path=f"/projects/{pid}/materials")
        self.assertEqual(response.status, 303)
        response = self.client.post(f"/projects/{pid}/expenses", {
            "category": "Site preparation", "description": "Survey fee", "amount": "2500", "spent_on": "2026-10-01", "vendor": "Survey provider"
        }, csrf_path=f"/projects/{pid}/expenses")
        self.assertEqual(response.status, 303)
        response = self.client.post(f"/projects/{pid}/tasks", {
            "title": "Review requirements", "description": "Review the space brief", "status": "todo", "priority": "high", "due_date": "2026-11-15"
        }, csrf_path=f"/projects/{pid}/tasks")
        self.assertEqual(response.status, 303)
        with self.app.conn() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM materials WHERE project_id=? AND name='Ceramic tile'", (pid,)).fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT amount FROM expenses WHERE project_id=? AND description='Survey fee'", (pid,)).fetchone()[0], 2500)
            self.assertEqual(con.execute("SELECT priority FROM tasks WHERE project_id=? AND title='Review requirements'", (pid,)).fetchone()[0], "high")

    def test_plan_editor_saves_geometry_and_clamps_to_plot(self):
        page = self.client.get(f"/projects/{self.project_id}/plan")
        self.assertEqual(page.status, 200)
        with self.app.conn() as con:
            room = con.execute("SELECT * FROM rooms WHERE project_id=? ORDER BY id LIMIT 1", (self.project_id,)).fetchone()
            if room is None:
                con.execute("INSERT INTO rooms(project_id,name,width_m,depth_m,x_m,y_m) VALUES(?,?,?,?,?,?)", (self.project_id,"Living",4,3,1,1))
                room = con.execute("SELECT * FROM rooms WHERE project_id=? ORDER BY id LIMIT 1", (self.project_id,)).fetchone()
        result = self.client.post_json(f"/projects/{self.project_id}/plan/save", {
            "rooms": [{"id": room["id"], "x_m": 500, "y_m": 500, "width_m": 4, "depth_m": 3}]
        }, csrf_path=f"/projects/{self.project_id}/plan")
        self.assertEqual(result.status, 200, result.text)
        payload = json.loads(result.text)
        self.assertTrue(payload["ok"])
        with self.app.conn() as con:
            updated = con.execute("SELECT x_m,y_m FROM rooms WHERE id=?", (room["id"],)).fetchone()
        self.assertAlmostEqual(updated["x_m"], 14.0)  # plot width 18 m minus 4 m room
        self.assertAlmostEqual(updated["y_m"], 21.0)  # plot length 24 m minus 3 m room

    def test_project_export_contains_real_data_and_blocks_csv_formulas(self):
        self.client.post(f"/projects/{self.project_id}/tasks", {
            "title": "Real exported task", "description": "", "status": "todo", "priority": "normal", "due_date": ""
        }, csrf_path=f"/projects/{self.project_id}/tasks")
        self.client.post(f"/projects/{self.project_id}/tasks", {
            "title": '=HYPERLINK("https://example.invalid","click")', "description": "", "status": "todo", "priority": "normal", "due_date": ""
        }, csrf_path=f"/projects/{self.project_id}/tasks")
        response = self.client.get(f"/projects/{self.project_id}/export.csv")
        self.assertIn(b"Courtyard Home", response.body)
        self.assertIn(b"Real exported task", response.body)
        self.assertIn(b"'=HYPERLINK", response.body)

    def test_pdf_export_handles_user_entered_markup(self):
        with self.app.conn() as con:
            con.execute("UPDATE projects SET name=? WHERE id=?", ("<Terrace> & Patio", self.project_id))
            con.execute("UPDATE estimates SET assumptions=? WHERE project_id=?", ("<script>not markup</script> & assumptions", self.project_id))
        response = self.client.get(f"/projects/{self.project_id}/report.pdf")
        self.assertEqual(response.status, 200, response.text[:500])
        self.assertTrue(response.body.startswith(b"%PDF"))

    def test_private_document_upload_and_tenant_boundary(self):
        body, ctype = multipart_body({"csrf_token": self.client.csrf(f"/projects/{self.project_id}/documents"), "notes": "A private note"}, {
            "file": ("floor-notes.txt", "text/plain", b"Private project drawing notes")
        })
        uploaded = self.client.request("POST", f"/projects/{self.project_id}/documents", raw_body=body, content_type=ctype)
        self.assertEqual(uploaded.status, 303, uploaded.text[:300])
        with self.app.conn() as con:
            doc = con.execute("SELECT * FROM documents WHERE project_id=?", (self.project_id,)).fetchone()
        downloaded = self.client.get(f"/projects/{self.project_id}/documents/{doc['id']}")
        self.assertEqual(downloaded.status, 200)
        self.assertEqual(downloaded.body, b"Private project drawing notes")

        other = WSGIClient(self.app, remote_addr="127.0.0.3")
        self.signup(other, email="other@example.com", name="Other User")
        self.assertEqual(other.get(f"/projects/{self.project_id}").status, 404)
        self.assertEqual(other.get(f"/projects/{self.project_id}/documents/{doc['id']}").status, 404)

    def test_non_admin_cannot_access_admin_console(self):
        response = self.client.get("/admin")
        self.assertEqual(response.status, 403)

    def test_expired_trial_cannot_create_project(self):
        with self.app.conn() as con:
            con.execute("UPDATE users SET trial_status='expired',trial_ends_at='2020-01-01T00:00:00Z' WHERE email='owner@example.com'")
        response = self.client.post("/projects", {
            "name": "Should not save", "project_type": "new_home", "plot_length_m": "20", "plot_width_m": "15",
            "floors": "1", "built_area_m2": "100", "budget": "100000", "currency": "INR"
        }, csrf_path="/projects")
        self.assertEqual(response.status, 303)
        self.assertIn("/billing", response.header("location", ""))
        with self.app.conn() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM projects WHERE name='Should not save'").fetchone()[0], 0)


class DemoAndBillingTests(AppTestCase):
    def test_each_demo_visitor_gets_an_isolated_project(self):
        first = WSGIClient(self.app, remote_addr="127.0.0.10")
        second = WSGIClient(self.app, remote_addr="127.0.0.11")
        one = first.get("/demo")
        two = second.get("/demo")
        self.assertEqual(one.status, 200)
        self.assertEqual(two.status, 200)
        with self.app.conn() as con:
            p1 = con.execute("SELECT p.id,p.demo_session_id FROM projects p JOIN demo_sessions d ON d.id=p.demo_session_id WHERE d.token_hash=?", (token_digest(first.cookies['pyh_demo']),)).fetchone()
            p2 = con.execute("SELECT p.id,p.demo_session_id FROM projects p JOIN demo_sessions d ON d.id=p.demo_session_id WHERE d.token_hash=?", (token_digest(second.cookies['pyh_demo']),)).fetchone()
        self.assertIsNotNone(p1)
        self.assertIsNotNone(p2)
        self.assertNotEqual(p1["demo_session_id"], p2["demo_session_id"])
        with self.app.conn() as con:
            count1 = con.execute("SELECT COUNT(*) FROM rooms WHERE project_id=?", (p1["id"],)).fetchone()[0]
            count2 = con.execute("SELECT COUNT(*) FROM rooms WHERE project_id=?", (p2["id"],)).fetchone()[0]
        room_post = first.post("/demo/rooms", {"name":"Private demo room","room_type":"bedroom","width_m":"3","depth_m":"3","doors":"1","windows":"1","floor_no":"1"}, csrf_path="/demo/plan")
        self.assertEqual(room_post.status, 303)
        with self.app.conn() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM rooms WHERE project_id=?", (p1["id"],)).fetchone()[0], count1 + 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM rooms WHERE project_id=?", (p2["id"],)).fetchone()[0], count2)

    def test_unsigned_or_invalid_webhook_is_rejected(self):
        raw = b'{"event":"subscription.activated","payload":{"subscription":{"entity":{"id":"sub_1","status":"active"}}}}'
        response = self.client.request("POST", "/webhooks/razorpay", raw_body=raw, content_type="application/json", headers={"X-Razorpay-Signature":"not-valid"})
        self.assertEqual(response.status, 400)
        self.assertIn("Invalid webhook signature", response.text)

    def test_signed_webhook_updates_subscription_once(self):
        self.signup()
        with self.app.conn() as con:
            user_id = con.execute("SELECT id FROM users WHERE email='owner@example.com'").fetchone()[0]
            con.execute("INSERT INTO subscriptions(user_id,provider,provider_subscription_id,plan_key,status) VALUES(?,?,?,?,?)", (user_id, "razorpay", "sub_test_1", "professional", "pending"))
        raw = json.dumps({"event":"subscription.activated","payload":{"subscription":{"entity":{"id":"sub_test_1","status":"active","current_end":1798761600}}}}).encode()
        signature = hmac.new(b"test-webhook-secret", raw, hashlib.sha256).hexdigest()
        headers = {"X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": "evt_test_1"}
        response = self.client.request("POST", "/webhooks/razorpay", raw_body=raw, content_type="application/json", headers=headers)
        self.assertEqual(response.status, 200, response.text)
        duplicate = self.client.request("POST", "/webhooks/razorpay", raw_body=raw, content_type="application/json", headers=headers)
        self.assertEqual(duplicate.status, 200)
        self.assertIn("duplicate", duplicate.text)
        with self.app.conn() as con:
            sub = con.execute("SELECT status FROM subscriptions WHERE provider_subscription_id='sub_test_1'").fetchone()
            user = con.execute("SELECT trial_status FROM users WHERE id=?", (user_id,)).fetchone()
            events = con.execute("SELECT COUNT(*) FROM billing_events WHERE provider_event_id='evt_test_1'").fetchone()[0]
        self.assertEqual(sub["status"], "active")
        self.assertEqual(user["trial_status"], "converted")
        self.assertEqual(events, 1)


if __name__ == "__main__":
    import unittest
    unittest.main()

class AccountAdministrationAndLifecycleTests(AppTestCase):
    def test_logout_login_and_password_reset_flow(self):
        self.signup()
        signed_out = self.client.post("/logout", {}, csrf_path="/settings")
        self.assertEqual(signed_out.status, 303)
        self.assertIn("/", signed_out.header("location", ""))
        signed_out_dashboard = self.client.get("/app")
        self.assertEqual(signed_out_dashboard.status, 303)

        forgot = self.client.post("/forgot-password", {"email": "owner@example.com"}, csrf_path="/forgot-password")
        self.assertEqual(forgot.status, 200)
        reset_match = re.search(r'href="(/reset-password/[A-Za-z0-9_-]{30,80})"', forgot.text)
        self.assertIsNotNone(reset_match, "Development/test mode should expose a local reset link")
        reset_path = reset_match.group(1)
        reset = self.client.post(reset_path, {
            "password": "new-correct-horse-battery", "confirm_password": "new-correct-horse-battery"
        }, csrf_path=reset_path)
        self.assertEqual(reset.status, 303, reset.text[:500])
        login = self.client.post("/login", {"email": "owner@example.com", "password": "new-correct-horse-battery", "next": "/app"}, csrf_path="/login")
        self.assertEqual(login.status, 303, login.text[:500])
        self.assertIn("/app", login.header("location", ""))
        self.assertEqual(self.client.get("/app").status, 200)

    def test_project_duplicate_archive_restore_and_delete(self):
        self.signup()
        project_id = self.create_project()
        duplicated = self.client.post(f"/projects/{project_id}/duplicate", {}, csrf_path=f"/projects/{project_id}")
        self.assertEqual(duplicated.status, 303)
        new_id_match = re.search(r"/projects/(\d+)", duplicated.header("location", ""))
        self.assertIsNotNone(new_id_match)
        duplicate_id = int(new_id_match.group(1))
        self.assertNotEqual(duplicate_id, project_id)
        self.assertEqual(self.client.get(f"/projects/{duplicate_id}").status, 200)

        archived = self.client.post(f"/projects/{project_id}/archive", {}, csrf_path=f"/projects/{project_id}")
        self.assertEqual(archived.status, 303)
        self.assertIn("archived", archived.header("location", ""))
        restored = self.client.post(f"/projects/{project_id}/restore", {}, csrf_path=f"/projects/{project_id}")
        self.assertEqual(restored.status, 303)
        self.assertEqual(self.client.get(f"/projects/{project_id}").status, 200)
        deleted = self.client.post(f"/projects/{project_id}/delete", {"confirm_name": "Courtyard Home"}, csrf_path=f"/projects/{project_id}")
        self.assertEqual(deleted.status, 303)
        self.assertEqual(self.client.get(f"/projects/{project_id}").status, 404)

    def test_contact_message_is_persisted_and_admin_settings_are_protected(self):
        contact = self.client.post("/contact", {"name":"Site visitor","email":"visitor@example.com","message":"Please tell me about pricing options."}, csrf_path="/contact")
        self.assertEqual(contact.status, 200)
        self.assertIn("saved for the site administrator", contact.text)
        with self.app.conn() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM contact_requests WHERE email='visitor@example.com'").fetchone()[0], 1)
        self.signup()
        self.assertEqual(self.client.get("/admin").status, 403)
        with self.app.conn() as con:
            con.execute("UPDATE users SET is_admin=1 WHERE email='owner@example.com'")
        dashboard = self.client.get("/admin")
        self.assertEqual(dashboard.status, 200)
        updated = self.client.post("/admin/settings", {"trial_days":"14","public_demo_enabled":"0"}, csrf_path="/admin")
        self.assertEqual(updated.status, 303)
        self.assertEqual(self.client.get("/demo").status, 404)
        with self.app.conn() as con:
            days = con.execute("SELECT value FROM app_settings WHERE key='trial_days'").fetchone()[0]
        self.assertEqual(days, "14")

    def test_invalid_upload_type_is_not_saved(self):
        self.signup()
        project_id = self.create_project()
        body, ctype = multipart_body({"csrf_token": self.client.csrf(f"/projects/{project_id}/documents"), "notes":"not a document"}, {
            "file": ("payload.exe", "application/octet-stream", b"MZ executable signature")
        })
        response = self.client.request("POST", f"/projects/{project_id}/documents", raw_body=body, content_type=ctype)
        self.assertEqual(response.status, 303)
        with self.app.conn() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM documents WHERE project_id=?", (project_id,)).fetchone()[0], 0)

    def test_demo_reset_and_csv_export(self):
        demo = WSGIClient(self.app, remote_addr="127.0.0.18")
        page = demo.get("/demo")
        self.assertEqual(page.status, 200)
        export = demo.get("/demo/report.csv")
        self.assertEqual(export.status, 200)
        self.assertIn(b"Willow Courtyard Home", export.body)
        with self.app.conn() as con:
            demo_id = con.execute("SELECT id FROM demo_sessions WHERE token_hash=?", (token_digest(demo.cookies['pyh_demo']),)).fetchone()[0]
            before = con.execute("SELECT COUNT(*) FROM rooms r JOIN projects p ON p.id=r.project_id WHERE p.demo_session_id=?", (demo_id,)).fetchone()[0]
        self.assertGreaterEqual(before, 5)
        reset = demo.post("/demo/reset", {}, csrf_path="/demo")
        self.assertEqual(reset.status, 303)
        with self.app.conn() as con:
            after = con.execute("SELECT COUNT(*) FROM rooms r JOIN projects p ON p.id=r.project_id WHERE p.demo_session_id=?", (demo_id,)).fetchone()[0]
        self.assertEqual(after, 5)
