# Security notes

## Implemented controls

- Passwords are hashed using scrypt with a fresh random salt; no plaintext password is persisted.
- Session bearer tokens are high entropy, stored as SHA-256 digests, rotated after sign-in/sign-up, and transmitted in HttpOnly, SameSite=Lax cookies. In production, Secure cookies are enforced by config validation.
- CSRF checks protect state-changing routes. Provider webhooks are exempt from CSRF only because they use the raw request body and HMAC signature verification.
- Templates use Jinja2 HTML autoescaping. CSP disallows inline JavaScript/styles and third-party scripts by default.
- Project records are fetched with owner scope on each route. Private document access is nested under an owner-scoped project route; files are not served from the public static directory.
- Uploaded filenames are sanitized and replaced by server-generated storage keys. Type/size checks are applied before saving.
- Query data is passed with SQLite bound parameters. Unexpected server errors return a generic message plus a request reference rather than a traceback.
- Admin access is checked server-side, and the admin CLI is the supported first-admin provisioning route.
- Trial writes are enforced server-side; opening checkout does not grant a paid entitlement.

## Production checklist

1. Set a unique `SECRET_KEY` of at least 32 characters, `APP_ENV=production`, `APP_URL=https://...`, an absolute persistent `DATABASE_PATH`, `COOKIE_SECURE=true`, and a private `STORAGE_DIR`.
2. Serve only over HTTPS; configure trusted reverse proxy behavior intentionally. Leave `TRUST_PROXY=false` unless proxy headers are configured and sanitized by a trusted platform.
3. Restrict database/upload filesystem permissions and back up both together. Test restore on an isolated environment.
4. Configure SMTP for password resets. In production, reset links aren't rendered back into the web response when email is not configured.
5. Configure Razorpay webhook endpoint/secret independently of API credentials, verify the exact registered events, and test duplicates, retries, out-of-order delivery, failed payments and cancellations before turning on billing.
6. Keep all secrets out of source control, logs, issue trackers, and browser/client-side code. Never use real user data in demo content or tests.
7. Add TLS/host-level request size limits, rate limiting/WAF at the edge, monitoring/alerting, backups, vulnerability scanning and an external application security assessment before production use.
8. Treat document signature checks as basic hardening, not malware detection. If uploads are a material risk, implement quarantine + antivirus scanning and private object storage.
9. Run the automated suite after all deployment-specific changes and perform a manual role/tenant/CSRF/payment lifecycle review.

These controls do not make the application certified or penetration-tested. SQLite and local filesystem storage target a single application instance with persistent storage; use PostgreSQL and shared private storage before scaling to multiple workers/instances where the storage semantics require it.
