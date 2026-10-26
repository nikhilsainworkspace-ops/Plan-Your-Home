# Implementation status

This checklist records verified implementation scope for the supplied ZIP. “Done” means present in source and exercised by automated tests in this runtime; it does not mean a production security certification or an externally hosted deployment.

## P0 — Foundation and security

- [x] Source repository structure, WSGI entry point, environment example, SQL schema/migration, data access helpers.
- [x] Account creation/sign-in, scrypt password hashing, persistent sessions, CSRF tokens, reset tokens, IP login throttling.
- [x] Server-side project ownership checks, admin authorization checks, trial access enforcement, guarded object downloads.
- [x] Production configuration validation, request payload cap, safe filenames/extensions, CSP and common response security headers.
- [x] CLI first-admin provisioning and health check endpoint.
- [x] Automated test suite for status codes, CSRF, access boundaries, and calculation/security helpers.

## P1 — Core product

- [x] Public website: marketing and legal/information pages.
- [x] User dashboard, project create/edit/archive/restore/duplicate/delete, account preferences.
- [x] Room/space records and persisted SVG floor-plan room positions/dimensions.
- [x] Estimate assumptions, contingency, category allocation, materials, expenses, tasks.
- [x] Private document upload/download with ownership check.
- [x] Project CSV and PDF reports.
- [x] Responsive CSS, accessible labels/keyboard affordances, shared layout and confirmation dialog.

## P2 — Demo and conversion

- [x] Public demo before signup, labelled synthetic data, visitor-specific demo session, reset, room/plan/estimate edits, sample CSV export.
- [x] Separate seven-day trial, account onboarding, and server-side write blocking after expiry.
- [x] Pricing/billing pages and meaningful “not configured” state.
- [x] Tests for demo isolation, trial enforcement, signup, and core workflows.

## P3 — Subscription and operations

- [x] Razorpay subscription request implementation with server-side credentials.
- [x] Raw-body HMAC-SHA256 webhook verification and event idempotency.
- [x] Subscription state updates are only driven by verified server-side webhook events.
- [x] Protected admin dashboard, contact queue, app settings, and audit entries.
- [x] Optional SMTP adapter; no success claimed when email is not configured.
- [ ] Live Razorpay sandbox transaction and lifecycle verification. Requires merchant credentials, provider plan IDs and a reachable HTTPS webhook URL; not run in this container.
- [ ] External security assessment, automated backup/restore drills and production monitoring. Not performed in this container.

## P4 — Differentiators (not included in this release)

- [ ] Multi-member team invitations and permission levels.
- [ ] Supplier/contractor quote comparison and preferred quote records.
- [ ] Formal change orders and approval history.
- [ ] Versioned document/estimate revisions and approvals.
- [ ] Inspiration board and image library.
- [ ] Home handover, warranty, maintenance, and service history.
- [ ] CSV import preview/row-level validation, richer scenario comparison, expanded localization, AI assistant.

## Validation record

- Python unit/integration suite: **30 tests passed** in the final local verification run (account flows, ownership boundaries, demo isolation, trial expiry, uploads, PDF/CSV safety, and signed webhooks).
- Python compilation: passed in the final release verification.
- JavaScript syntax checks: passed in the final release verification.
- WSGI health and route smoke checks: passed in the final release verification.
- Playwright Chromium screenshots: not run because this environment has the Playwright Python package but no Chromium executable installed.
