# Deployment guide

## Local setup

Use Python 3.11+, install `requirements.lock`, copy `.env.example` to `.env`, run `python manage.py migrate`, and start `python wsgi.py`. This starts Python's built-in WSGI server on `127.0.0.1:8000` by default.

## Linux/container host

Use a persistent volume for the SQLite database and the private upload directory. Set `HOST=0.0.0.0` only behind a trusted host firewall/reverse proxy, or use the provided Gunicorn `Procfile` with the configured platform port. Install dependencies from `requirements.lock`. Do not expose the built-in development server directly to the public internet.

Required production settings are:

- `APP_ENV=production`
- `APP_URL=https://your-public-hostname`
- `SECRET_KEY` set to a unique random value of 32+ characters
- `DATABASE_PATH` an absolute path on persistent storage
- `STORAGE_DIR` a private writable path
- `COOKIE_SECURE=true`

Then apply schema migration and make the administrator with `python manage.py create-admin ...`. First create a normal account if you want to promote it; the CLI will ask for a new password only if the account does not exist.

## Email

Provide `SMTP_HOST`, `SMTP_PORT`, `EMAIL_FROM` and optional `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_STARTTLS=true` for password-reset delivery. Password reset should be tested end to end on the deployed hostname before launch. Contact requests are saved in the database even if outbound email isn't enabled. No notification is claimed delivered when email isn't configured.

## Razorpay

Create the plan IDs and display-price strings in the Razorpay merchant account and set the relevant `RAZORPAY_PLAN_*` and `PRICE_*_DISPLAY` variables. Provide `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, and a distinct `RAZORPAY_WEBHOOK_SECRET`. Register `https://your-public-hostname/webhooks/razorpay` in the provider dashboard and subscribe to the subscription lifecycle events supported in `pyh/app.py`. Test mode must be verified on a reachable HTTPS deployment; no payment credentials are included with this ZIP and no live transaction is claimed.

## Database and backups

`python manage.py migrate` applies the included SQLite schema. Keep SQLite and file storage private. Back up the database and uploads directory in a consistent snapshot and test restoration regularly. SQLite is for a single-instance deployment. For concurrent multi-instance deployments, plan a migration to PostgreSQL and private shared object storage.

## Operational smoke check

After boot, request `/healthz` and verify the JSON contains both `status: ok` and `database: ok`. Then verify account creation/sign-in, a project/room save, PDF/CSV downloads, private document access from a different account, expired trial behavior, and signed webhook handling with provider test credentials.
