# Environment variables

`.env.example` contains the complete set of supported variables. Required values depend on whether optional email or Razorpay billing is enabled.

## Core

- `APP_ENV`: `development` locally; `production` for a public deployment.
- `APP_NAME`: product/site name.
- `APP_URL`: externally visible base URL; HTTPS required in production.
- `SECRET_KEY`: unique server-only secret, at least 32 characters in production.
- `DATABASE_PATH`: local SQLite file path; must be absolute on a persistent-disk deployment. On Vercel, configure the Turso variables below instead.
- `TURSO_DATABASE_URL`: optional shared Turso/libSQL URL (for example, `libsql://...`); required on Vercel when `APP_ENV=production`.
- `TURSO_AUTH_TOKEN`: database access token paired with `TURSO_DATABASE_URL`; keep secret.
- `STORAGE_DIR`: private, writable directory for uploaded documents.
- `PUBLIC_DEMO_ENABLED`: enable/disable public demo, default `true`.
- `DEMO_SESSION_HOURS`: demo cookie/session lifetime, default `8`.
- `SESSION_DAYS`: authenticated session lifetime, default `14`.
- `TRIAL_DAYS`: new account trial duration, default `7` (admin settings can override it between 1 and 60 days).
- `MAX_UPLOAD_BYTES`: max document upload bytes, default `8388608` (8 MiB).
- `COOKIE_SECURE`: enable `Secure` cookies; production requires `true`.
- `TRUST_PROXY`: only enable if deployed behind a trusted proxy whose headers are sanitized.
- `SUPPORT_EMAIL`: optional support address used for configured outbound contact handling.

For Vercel/serverless deployments, the shared Turso database keeps accounts, sessions, and projects consistent between function instances. Do not use SQLite under `/tmp` for real users; it is temporary. Document uploads still require persistent/shared object storage on Vercel.

## Optional SMTP delivery

- `SMTP_HOST`, `SMTP_PORT` (default 587), `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_STARTTLS` (default `true`), `EMAIL_FROM`.

Set `SMTP_HOST` and `EMAIL_FROM` to enable sending. Configure authentication where your provider requires it. Without SMTP, password-reset links are only shown in development; production responses do not expose token URLs.

## Optional Razorpay billing

- `RAZORPAY_KEY_ID`
- `RAZORPAY_KEY_SECRET`
- `RAZORPAY_WEBHOOK_SECRET` — the separate webhook signing secret.
- `RAZORPAY_PLAN_STARTER`, `RAZORPAY_PLAN_PRO`, `RAZORPAY_PLAN_TEAM`
- `PRICE_STARTER_DISPLAY`, `PRICE_PRO_DISPLAY`, `PRICE_TEAM_DISPLAY`

Keep secrets server-side. Display prices should match the merchant plans and commercial terms exactly. A plan is rendered as configured only when both its provider plan ID and display text are present. Production validation requires complete Razorpay credentials when any part of the integration is enabled. A live merchant plan/webhook has not been configured in this source package.
