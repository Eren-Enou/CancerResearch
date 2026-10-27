# M3.1-A — Protected local access and audit identity (synthetic data only)

**What this milestone does:** in protected mode, OncoScope's HTTP API shows or changes case data only during a session
opened with the local owner's passphrase. Every new audit entry names the authenticated owner.

**What it does not do:** it does **not encrypt anything at rest**. The database, the document store, backups and exports
are plaintext files. Any process or person who can read those files can read every case without going through the lock.
Encryption is M3.1-B (key vault) and M3.1-C/D (database and documents). OncoScope is not approved for clinical use, and
no HIPAA or other compliance is claimed.

## Modes

| | Protected (default) | Demonstration (`ONCOSCOPE_MODE=demo`) |
|---|---|---|
| Who can use the API | Only a valid session (bearer token) | Anyone on this computer (no sign-in) |
| Default database | `backend/data/oncoscope.sqlite3` | `backend/data/demo/oncoscope-demo.sqlite3` (its own document store) |
| Owner setup / unlock | Yes | Refused (409) |
| Audit actor | `owner:<uid>` + owner name | `demo` ("Synthetic demonstration mode (no sign-in)") |
| Opens a database that has a local owner | Yes | **Refused at start-up** |

Any other `ONCOSCOPE_MODE` value stops the server. Tests, the benchmark, the seed script and the M2–M2.6 Playwright
demonstrations set demonstration mode explicitly; `python -m app.seed` refuses to run in protected mode.

## Owner and passphrase

- First protected launch: `POST /api/auth/setup` with a display name (1–80 characters) and a passphrase (12–1024
  characters, taken exactly as typed). Allowed once; later calls get 409.
- Only an **Argon2id verifier** is stored (`local_owner.password_verifier`). It is produced by `argon2-cffi`'s
  `PasswordHasher` with its RFC 9106 low-memory profile (t=3, 64 MiB, p=4, random 16-byte salt per hash). No
  cryptography is implemented in OncoScope itself. The table allows exactly one row, and SQLite refuses to delete it.
- Wrong passphrase: 401 "Incorrect passphrase" (no other detail). After 5 consecutive failures, unlocking is refused with
  429 and `Retry-After` for 30 s. The delay doubles with each further failure, up to 15 minutes; this applies even to
  the correct passphrase. A success resets the count.

## Sessions

- `POST /api/auth/unlock` (or setup) returns a 256-bit token from `secrets.token_urlsafe(32)` in a `no-store` response
  body, with the idle and lifetime limits.
- The server keeps sessions **in memory only**, keyed by the token's SHA-256. Restarting the server ends every session.
  Unlocking again ends the previous session, so there is one active session per installation.
- A session ends after **15 minutes without a request** (`ONCOSCOPE_SESSION_IDLE_SECONDS`) or **8 hours after unlocking**
  (`ONCOSCOPE_SESSION_MAX_SECONDS`), whichever comes first. `POST /api/auth/lock` revokes it immediately.
- Tokens travel only in the `Authorization: Bearer` header. Tokens in the query string, a cookie or another header are
  ignored, so those requests get 401. Missing, malformed, unknown, expired and revoked tokens all get the same 401
  "OncoScope is locked." response.

## What is reachable without a session (protected mode)

Exactly four operations: `GET /api/health`, `GET /api/auth/status` (mode, whether setup is needed, and
`encrypted_at_rest: false`), `POST /api/auth/setup` and `POST /api/auth/unlock`. A test enumerates every other route
and checks that each returns 401. FastAPI's `/docs` and `/openapi.json` (schema only, no data) stay reachable from
loopback.

## Requests refused in every mode

- **Non-loopback peers:** 403, even if the server is bound to another interface by mistake.
- **Hosts other than `127.0.0.1` or `localhost`:** 400 (DNS-rebinding protection, M2).
- **Foreign web origins:** 403. This covers any `Origin` other than the request's own `http(s)://<Host>`, including
  `null` and other local ports, and any `Sec-Fetch-Site: cross-site` or `same-site`. The UI is same-origin through the
  Vite proxy. No CORS headers are ever sent.

## Audit identity (schema v4)

- Migration 4 adds `audit_log.actor_id` and `actor_label`. Existing entries keep `NULL`, shown as "unknown (recorded
  before sign-in existed)". No reviewer is invented.
- The actor is included in the entry hash **only when present**, so every earlier hash is unchanged, and editing a new
  entry's actor breaks the chain (tested).
- The actor comes from the session, set by the access middleware for the duration of the request, and never from request
  content. In protected mode, `db.audit()` refuses to write any entry without an actor.
- Attributed actions:
  - record creation
  - correction
  - verification-status change
  - document attachment
  - extraction acceptance and rejection
  - owner setup, unlock and lock (`entity_type = 'local_owner'`, no case)

## Frontend

- `Root` loads `/api/auth/status`, then shows setup, unlock or (in demonstration mode) the workspace with a banner. The
  workspace is mounted only while a session exists. Locking unmounts it, discarding all case data in component state,
  and revokes blob URLs.
- The UI locks itself in four situations:
  - manually ("Lock now")
  - after the idle limit without keyboard, pointer, wheel or touch input
  - when the session lifetime ends
  - whenever the server answers 401 to a request that carried the token
- The token is held in a module variable in `api.ts`. It is never written to the URL, `localStorage`,
  `sessionStorage` or a cookie, so reloading the page requires unlocking again.
- Documents, rendered PDF pages, images, the Markdown report and JSON/CSV exports are fetched with the session header.
  The browser receives them as short-lived `blob:` URLs, which contain no token. Only PDF, plain text, PNG and JPEG are
  rendered; any other type is saved as a download, never displayed in the app's origin.
- Audit and record history show who made each entry.

## Verification

- **Backend:** `backend/tests/test_protected.py`, plus the former strict xfail `test_unauthenticated_requests_are_rejected`
  in `test_security.py`, which now passes in protected mode.
- **Frontend:** `frontend/src/auth.test.tsx` covers token handling, downloads, the lock screen, lock, expiry and idle lock.
- **End to end:** `frontend/e2e/protected.spec.ts` (Playwright project `m3.1a`, API 8013 / UI 5183, 45-second session
  lifetime) walks through setup, a PDF import through authorized requests, an export download, attributed audit
  entries, 401/403 from outside the session, lock (with the old token replayed → 401), a wrong passphrase, unlock
  restoring the selected case, and expiry (screen cleared, replayed token → 401).

The two remaining strict xfails (plaintext at rest; audit-tail truncation without an external head) are unchanged and
belong to later increments.

## Deviations from the M3.1 plan

- Criterion 7 of the plan ("refuse to bind a non-loopback host") is met only in effect: requests from non-loopback peers
  are refused with 403, but uvicorn can still open the port. A launcher that refuses to bind remains in M3.1-F.
- Foreign origins are refused explicitly (`Origin` / `Sec-Fetch-Site`). The plan relied only on CORS preflights failing.

## Remaining risks (not fixed by this milestone)

1. **Plaintext at rest** (database: **fixed in M3.1-C**; documents remain plaintext until M3.1-D). Before M3.1-C,
   anything that could read `backend/data/` bypassed the lock entirely, including the CLI tools
   (`app.backup`, `app.restore`), other programs, other OS accounts with access, and a lost device. Restores and backups
   made with the CLI are not attributed to anyone in the audit log.
2. **Setup race.** Until the owner is set up, any local process that reaches the API first can become the owner. Set up
   immediately after installing. A one-time setup code shown on the server console would close this.
3. **Offline guessing.** The rate limit lives in server memory, so restarting the server resets it. Anyone with the
   database file can also try passphrases against the verifier offline at Argon2id speed. The 12-character minimum is a
   policy, not a strength check.
4. ~~No passphrase change or recovery~~ — **added in M3.1-B** (`M3_1B_KEY_VAULT.md`). Originally: A forgotten passphrase locks the owner out of the app, although
   the files stay readable on disk.
5. **Loopback traffic is unencrypted HTTP.** An administrator able to capture loopback traffic can see the passphrase
   at unlock and the token on every request.
6. **Same-user malware** (keyloggers, debuggers, malicious browser extensions) can capture the passphrase or read the
   token and case data from browser memory.
7. **Identity is self-declared.** The actor is a local label chosen at setup, not a verified person. One owner is
   supported; there are no roles or other users.
8. **Unlock and lock entries** are in the audit chain but not shown in the UI. They have no case, so per-case audit
   views and exports omit them.
9. **Demonstration mode is unauthenticated by design.** It refuses databases that have an owner, but someone with file
   access could copy a protected database and remove its owner row.

## Next increment: M3.1-B — key vault and recovery key

A passphrase-derived key (Argon2id) wraps a random data key, with a second wrap by a one-time recovery key. It adds
passphrase change, never makes a SQLCipher call with an unverified key, and has tests that the vault holds no
plaintext key material.
