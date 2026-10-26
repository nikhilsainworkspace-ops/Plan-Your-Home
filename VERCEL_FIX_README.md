# Vercel startup fix — apply this patch to the existing GitHub repository

This patch contains only the changed/new files:

- `wsgi.py` — exports a Vercel-compatible WSGI object and redirects SQLite/uploads to `/tmp` in Vercel Functions.
- `pyproject.toml` — declares the exact Vercel entrypoint (`wsgi:app`).

## Apply it

Upload/replace these files in the **root of the existing GitHub repository**, keeping their paths exactly as shown. Commit/push to `main`; Vercel should create a new deployment automatically. Do not upload the ZIP as a nested folder.

## Environment variables in Vercel

In **Project → Settings → Environment Variables**, set these for Production (and Preview if you test Preview deployments):

```env
APP_ENV=production
APP_URL=https://planyourhomes.vercel.app
SECRET_KEY=<a unique random value of at least 32 characters>
COOKIE_SECURE=true
```

Generate the secret locally with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Redeploy after adding/changing environment variables. Do not commit the real secret to GitHub.

The app now chooses `/tmp/plan-your-home/plan-your-home.sqlite3` and `/tmp/plan-your-home/uploads` automatically when `VERCEL=1`; any old `DATABASE_PATH=/var/data/...` or `STORAGE_DIR=/var/data/...` values are intentionally ignored on Vercel because Vercel Functions do not provide that persistent disk by default.

## Verify after redeployment

Open these URLs:

- `https://planyourhomes.vercel.app/`
- `https://planyourhomes.vercel.app/healthz`

The health endpoint should return JSON with `"status":"ok"` and `"database":"ok"`.

## Important database limitation

This patch fixes Vercel entrypoint/startup handling. It does **not** convert the app's SQL queries and migrations to MongoDB. The current code still uses SQLite, and Vercel's `/tmp` storage is temporary and can be different across function instances/cold starts. It is only enough to get the current app serving pages; do not treat it as persistent production storage. MongoDB Atlas will not be used until the application data layer is explicitly migrated to MongoDB.
