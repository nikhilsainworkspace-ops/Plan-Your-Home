# Architecture decisions

## 1. Runtime

The supplied build transcript describes a different local checkout, but only its text log—not the source repository—was uploaded. This deliverable is a separate implementation built from the product brief and transcript's architecture decisions. A web-framework/package install was not assumed, so this package uses WSGI from Python's standard library with Jinja2 templates, SQLite, and ReportLab PDF rendering. It has no Node build step and no required runtime assets from a third-party CDN.

The HTTP adapter and route dispatcher are deliberately small so the artifact can boot with minimal dependencies. Pure calculations, password/token helpers, environment configuration, persistence, SMTP and Razorpay adapters live in separate modules. The main WSGI dispatcher still owns many routes; future work should split it into smaller route modules before adding significant new business areas.

## 2. Database and persistence

SQLite with WAL, foreign keys, busy timeout and schema migration is selected for a single-instance starter with persistent storage. It keeps local setup and backup simple. It is not a good multi-instance coordination layer; move to PostgreSQL plus shared private object storage before horizontal scaling. All database access uses bound parameters for values. The schema keeps user-owned projects and demo-owned projects mutually exclusive via a database CHECK constraint.

## 3. Authentication and security

Passwords use stdlib `hashlib.scrypt` with random salts and constant-time verification. Session bearer tokens are random and only token digests are stored. Sessions rotate on signup/sign-in. Mutating routes require a session CSRF token in a hidden field or `X-CSRF-Token` header, except the provider webhook endpoint which uses HMAC verification. Templates autoescape HTML. The app applies restrictive baseline CSP and response headers.

Uploaded files use generated server-side stored names and remain outside the public static directory. Download requires account/project authorization. Extension allowlists, payload caps and a small executable-signature blocklist are safeguards, not antivirus scanning; production uploads should additionally be quarantined/scanned and stored in a private object service if required by threat model.

## 4. Demo and trial

The demo visitor gets a random cookie whose digest maps to a short-lived demo-session row. Its synthetic sample project is separate from authenticated account data, and its expiry is enforced server side. New accounts receive a configurable trial period (default seven days). Trial write gating happens in route logic, not only in the UI. Demo figures are explicitly labelled as examples, not local market research or contractor quotes.

## 5. Estimating and measurement

Geometry is stored in metres and square metres; user settings include metric/imperial preferences. Internal estimates use the explicit cost-per-square-metre rate and contingency entered by the account owner. Defaults are planning assumptions, not verified local rates. Currency is saved per project and isn't silently converted. Locale-aware translations and currency conversion with dated FX rates are not yet implemented.

## 6. Billing

Razorpay is an optional integration. Checkout creation requires API credentials and a plan ID. The webhook must have a valid HMAC signature computed against the exact raw body using the separate webhook secret. Events are keyed by the provider event ID (with a deterministic raw-body digest fallback) and processed idempotently. A return URL or browser-side event cannot grant paid access. Actual provider transactions are not verifiable without merchant credentials and a reachable webhook endpoint.

## 7. Explicitly deferred scope

Formal quote comparisons, change orders, versioned revisions, inspiration boards, handover/warranty tracking, data import preview, team invites, AI assistance, and full localization are deferred as described in `IMPLEMENTATION_STATUS.md`. They are not presented as implemented features.
