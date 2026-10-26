# Vercel sign-in/sign-up fix

This patch fixes the underlying session-storage mismatch: CSRF tokens, sessions, user accounts and projects were being stored in SQLite under Vercel's temporary `/tmp` directory. Serverless function instances do not share that temporary database, so a form could be rendered with one session and submitted to another instance where that session did not exist.

## Files in this patch

Replace the files in this ZIP at the same paths in the root of the existing GitHub repository. Keep your other project files unchanged. The patch adds a Turso-compatible shared database backend while keeping local development on standard SQLite.

## Required Vercel settings

Before redeploying, create a Turso/libSQL database and generate an authentication token in the provider dashboard. Add these environment variables in **Vercel → Project → Settings → Environment Variables** for Production (and Preview if you test Preview deployments):

```env
APP_ENV=production
APP_URL=https://planyourhomes.vercel.app
SECRET_KEY=<unique random secret of at least 32 characters>
COOKIE_SECURE=true
TURSO_DATABASE_URL=libsql://<your-database-host>
TURSO_AUTH_TOKEN=<your database auth token>
```

Generate `SECRET_KEY` on your own computer with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Do not commit real secrets to GitHub or paste them into support chats. The database URL and token must both be set; the app will reject a production Vercel startup without them rather than silently use temporary local storage. The schema is created automatically when the app first starts against the shared database.

`DATABASE_PATH` is still used for local SQLite development and for non-Vercel deployments with persistent disks. Vercel's temporary `/tmp` database is no longer used when the Turso settings are present.

## After redeployment

1. Open `https://planyourhomes.vercel.app/healthz`; expect JSON with `"status":"ok"` and `"database":"ok"`.
2. Open `/signup` in a fresh private/incognito browser window, create a test account, and verify the onboarding/dashboard loads.
3. Sign out, sign in again, and confirm a project remains after reload.
4. Check the Vercel function logs and provider database logs if a step fails.

The local automated suite is run with `python -m unittest discover -s tests -t . -v`. This ZIP's tests exercise local SQLite and the database adapter shape; live Turso credentials are not included, so a real cloud smoke test is still required after you configure the database.

## Remaining launch limitation

This fixes shared persistence for login sessions, accounts, and project data, but the app's uploaded project documents still use a local storage directory. On Vercel that directory is also under `/tmp`, which is temporary and not shared across function instances. Before accepting customer document uploads, configure shared private object storage (or deploy to a host with persistent shared storage). No code patch can create the cloud database or provide credentials for your account, and this ZIP cannot certify a zero-bug worldwide launch by itself.
