# Plan Your Home

**Plan smarter. Build better. Live beautifully.**

Plan Your Home is a responsive residential-planning and project-management SaaS starter. It helps homeowners and small construction/design teams keep a project brief, room layout, cost assumptions, materials, site expenses, tasks, files, and a project report together.

This ZIP contains the actual Python source, templates, CSS/JavaScript, SQL schema migration, automated tests, and setup documentation. It was created independently from the uploaded product brief and Claude build transcript; the actual repository mentioned in that transcript was not included as an attachment.

## Included in this release

- Public marketing pages: home, features, how it works, pricing, about, FAQ, contact, privacy, terms, and cookies.
- Account registration and sign-in, salted scrypt password hashes, database-backed sessions, CSRF protection, sign-out, password reset token flow, and basic IP-based login throttling.
- A separate seven-day trial flow, onboarding, account settings, and server-side trial expiry enforcement.
- A per-visitor isolated demo sandbox with synthetic data, edit/save floor-plan layout, editable demo estimate, reset, and CSV export.
- Project create/edit/archive/restore/duplicate/delete, room/space records, a browser-based SVG floor-plan editor with persisted geometry, cost estimate assumptions, materials, expense tracking, tasks, private project document storage, CSV export, PDF report, and dashboard activity/totals.
- Razorpay subscription checkout initiation and signed, idempotent webhook processing. Access is not granted merely because checkout was opened. Real checkout needs merchant test/live credentials and plan IDs configured by you.
- Administrator dashboard, protected user/settings/contact views, operational health endpoint, audit entries, and CLI-based first-admin provisioning.
- Mobile-responsive shared design system, accessible forms, confirmation dialog, CSP/security headers, safe upload extension/signature checks, and no external JavaScript/CDN requirements.

## Run locally

Python 3.11 or later is required. The runtime dependencies are pinned in `requirements.txt` and `requirements.lock`.

```bash
python -m venv .venv
# Linux/macOS
. .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.lock
cp .env.example .env
python manage.py migrate
python wsgi.py
```

Open <http://127.0.0.1:8000>. On Windows, copy `.env.example` to `.env` instead of using `cp`.

The app reads `.env` in the project root for local development. Values set in the process environment take precedence over `.env` values.

## Tests and checks

```bash
python -m unittest discover -s tests -t . -v
python -m compileall -q pyh tests manage.py wsgi.py
node --check static/js/app.js
node --check static/js/plan-editor.js
node --check static/js/estimate.js
```

The test suite uses a temporary SQLite database and isolated file storage. Tests do not contact Razorpay or send email. Browser-based Playwright testing is optional; a Chromium browser binary is required if you add/run browser E2E tests.

## Create the first administrator

First create an account via the website, then run:

```bash
python manage.py create-admin existing-account@example.com
```

Enter and confirm the administrator password only if you are creating a new account. If the email already exists, the CLI promotes that user and leaves their password unchanged. Do not provision an admin through request parameters.

## Production deployment outline

1. For a single-instance Linux host, provide persistent private disk storage; for Vercel/serverless, create a shared Turso/libSQL database and configure `TURSO_DATABASE_URL` plus `TURSO_AUTH_TOKEN`.
2. Install dependencies from `requirements.lock` and set the environment variables listed in `docs/ENVIRONMENT.md`.
3. Set `APP_ENV=production`, a unique `SECRET_KEY`, an HTTPS `APP_URL`, and `COOKIE_SECURE=true`. Use `DATABASE_PATH` only with persistent local disk; Vercel production requires Turso.
4. Run `python manage.py migrate` against the configured database and provision the first administrator.
5. Configure persistent/shared private object storage for uploads, restrict storage access, maintain backups, and set a real SMTP sender if password resets/notifications are needed.
6. Configure Razorpay using test credentials first, create plan IDs in the merchant account, set the matching display prices, register the webhook endpoint `/webhooks/razorpay`, and use the **webhook secret** (not the API secret) in `RAZORPAY_WEBHOOK_SECRET`.
7. Verify registration, reset email, trial expiry, payment lifecycle webhooks, backups/restores, access-control behavior, and all legal/price information before exposing the site to real users.

The included `Procfile` uses Gunicorn. `python wsgi.py` uses Python's standard WSGI server and is intended for local development, not public production traffic. Local SQLite is suitable for a modest single-instance installation with persistent disk. Vercel deployments can use the optional Turso-compatible shared database backend for account/session/project persistence; uploaded documents still need shared private object storage because Vercel `/tmp` is ephemeral.

## What is not represented as complete

This release is a production-oriented starter, **not a certified or fully audited production service**. It does not include multi-tenant team invitations/roles, a complete legal/compliance review, external penetration testing, an operational backup automation job, or live verified Razorpay transactions (no merchant credentials were supplied). The following product differentiators are also future work rather than fake UI: supplier quote comparison, formal change orders/decision approvals, immutable revision history for documents/estimates, inspiration mood boards, home handover/warranty/maintenance management, row-level CSV import preview, expanded translations, and optional AI features. Current PDF/CSV exports contain only the data listed on the relevant report/export route.

Review [Implementation status](IMPLEMENTATION_STATUS.md), [Security notes](docs/SECURITY.md), [Deployment](docs/DEPLOYMENT.md), and [Architecture decisions](docs/DECISIONS.md) before deploying.

## Environment variable checklist (complete)

Copy `.env.example` to `.env` for local development. For production, set these values in your host's secret/environment configuration rather than committing a `.env` file.

| Variable | Required / purpose |
|---|---|
| `APP_ENV` | Required; use `development` locally and `production` when deployed. |
| `APP_NAME` | Optional site/product label. |
| `APP_URL` | Required in production; public HTTPS base URL. |
| `SECRET_KEY` | Required in production; unique random secret of at least 32 characters. |
| `DATABASE_PATH` | Required persistent SQLite file path on single-instance hosts; absolute path in production. Not a durable Vercel database. |
| `TURSO_DATABASE_URL` | Required on Vercel production; shared Turso/libSQL database URL. |
| `TURSO_AUTH_TOKEN` | Required with `TURSO_DATABASE_URL`; server-side database token. |
| `STORAGE_DIR` | Required private writable document-storage directory. |
| `PUBLIC_DEMO_ENABLED` | Optional; controls public demo, default `true`. |
| `DEMO_SESSION_HOURS` | Optional demo lifetime, default `8`. |
| `SESSION_DAYS` | Optional authenticated session lifetime, default `14`. |
| `TRIAL_DAYS` | Optional default trial duration, default `7`. |
| `MAX_UPLOAD_BYTES` | Optional per-upload limit in bytes, default `8388608` (8 MiB). |
| `COOKIE_SECURE` | Must be `true` in production to require Secure cookies. |
| `TRUST_PROXY` | Optional; enable only behind a trusted proxy that sanitizes forwarded headers. |
| `SUPPORT_EMAIL` | Optional support/contact address. |
| `SMTP_HOST` | Optional; set to enable outgoing email. |
| `SMTP_PORT` | Optional SMTP port, default `587`. |
| `SMTP_USERNAME` | Optional SMTP authentication username. |
| `SMTP_PASSWORD` | Optional SMTP authentication password; keep secret. |
| `SMTP_STARTTLS` | Optional STARTTLS toggle, default `true`. |
| `EMAIL_FROM` | Required with SMTP; configured sender address. |
| `RAZORPAY_KEY_ID` | Required if Razorpay subscriptions are enabled. |
| `RAZORPAY_KEY_SECRET` | Required server-only Razorpay API secret. |
| `RAZORPAY_WEBHOOK_SECRET` | Required server-only webhook signing secret, distinct from API secret. |
| `RAZORPAY_PLAN_STARTER` | Optional provider plan ID for Starter. |
| `RAZORPAY_PLAN_PRO` | Optional provider plan ID for Pro. |
| `RAZORPAY_PLAN_TEAM` | Optional provider plan ID for Team. |
| `PRICE_STARTER_DISPLAY` | Optional display price matching the Starter merchant plan and terms. |
| `PRICE_PRO_DISPLAY` | Optional display price matching the Pro merchant plan and terms. |
| `PRICE_TEAM_DISPLAY` | Optional display price matching the Team merchant plan and terms. |
| `HOST` | Optional bind host for `python wsgi.py`, default `127.0.0.1`. |
| `PORT` | Optional bind port, default `8000`. |

Do not enable any Razorpay plan in production until test credentials, plan IDs, exact displayed prices, the `/webhooks/razorpay` HTTPS endpoint, and webhook-secret verification have been checked. `HOST` and `PORT` are local WSGI-runner settings; hosted Gunicorn deployments usually control the bind address through their platform command.
