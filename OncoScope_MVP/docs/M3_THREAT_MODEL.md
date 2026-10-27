# M3 threat model (synthetic-data stage)

Scope: OncoScope as built on `feat/m3.0-secure-foundations` — a single-user, local-first app (FastAPI + SQLite on
`127.0.0.1:8000`, React/Vite on `127.0.0.1:5173`, document store on disk). Data locations are numbered in
`M3_DATA_INVENTORY.md` (#1–#26). This model does **not** claim the application is safe for real records.

## Assets
A1 record content (#1–#5) · A2 original documents (#6) · A3 backups and exports (#8–#12) · A4 audit history and its
integrity (#2) · A5 availability of the user's own records · A6 future encryption keys and credentials.

## Actors examined
| Actor | Capability assumed |
|---|---|
| Other process under the same OS account (malware, a compromised dev tool, a browser extension) | Reads/writes the user's files; calls `127.0.0.1` ports |
| Other OS account on the same machine | Depends on file-system permissions; administrators can read everything |
| Malicious web origin open in the user's browser | Sends requests to `127.0.0.1` from the page; attempts DNS rebinding; cannot read cross-origin responses without CORS |
| Compromised or malicious document | Crafted PDF/image processed by pypdf (API process) and pdf.js (browser) |
| Thief / finder of a lost laptop | Full disk access unless the disk is encrypted |
| Thief of a backup (USB drive, cloud folder) | Full access to the backup folder |
| Developer mistake | Commits data or secrets; pastes logs; runs tests on real data |
| Public observers | Read the public GitHub repository, CI logs and committed screenshots |
| Dependency supply chain | Malicious npm/PyPI release |

## Risks (prioritised)

Likelihood (L) and impact (I) are rated 1–3 for a future real-data deployment; priority = L × I.

| ID | Threat | L | I | P | Existing controls | Gap → planned mitigation |
|---|---|---|---|---|---|---|
| T1 | Any same-user process reads or changes all cases via the API or the files | 3 | 3 | **9** | Loopback bind; Host check; **M3.1-A: API requires an owner-passphrase session in protected mode** | Files are still plaintext, so the lock is bypassed by reading them directly → passphrase-derived key and encryption (M3.1 B, C) |
| T4 | Lost/stolen device exposes database, documents, caches, exports | 2 | 3 | **6** | none in-app (OS disk encryption is the user's); **M3.1-B: recoverable key vault exists, not yet used** | Encryption at rest of DB + documents with vault-derived keys (M3.1 C, D); require OS disk encryption for real data |
| T5 | Backup theft (USB/cloud copy) | 2 | 3 | **6** | Manifest verification | Encrypted backups with independent restore test (M3.1 D) |
| T12 | Changes cannot be attributed to a person | 3 | 2 | **6** | Audit log records what/when/why; **M3.1-A: authenticated local owner on every new entry** (older entries: unknown) | Identity is self-declared and single-user; CLI backup/restore are unattributed |
| T13 | Data cannot be deleted (append-only audit keeps old values; no deletion endpoint) | 3 | 2 | **6** | — (by design so far) | Crypto-shredding design: per-case keys; delete key + purge caches (design in M3_SECURITY_ARCHITECTURE §7; M3.2) |
| T7 | Malicious document exploits pypdf (API process) or pdf.js (browser) | 2 | 3 | **6** | Type/magic checks, 10 MB cap, decompression limit, XFA off, hash check before render | Parse in a sandboxed subprocess with resource limits; keep pdf.js/pypdf patched (M3.2) |
| T6 | Local attacker rewrites or truncates audit history | 2 | 2 | 4 | Triggers; hash chain; head in backup manifests and `/api/audit/verify` | Keyed chain (HMAC with key from the vault) and head recorded outside the DB (M3.1 E) |
| T2 | Other OS account reads data files | 1 | 3 | 3 | Default user-profile ACLs if data stays under the profile | Create data dir owner-only (0700 / owner ACL) and verify at start-up (M3.1 F) |
| T8 | Leakage via logs, caches, browser history, downloads, CI artifacts | 2 | 2 | 4 | **Fixed D1, D2, D3**; no CI artifacts; no input echo; log regression test | Export audit entries; encrypted export option; log allow-list (M3.2) |
| T3 | Malicious website drives the local API (CSRF, DNS rebinding) | 1 | 3 | 3 | Host allow-list; JSON-only mutations; no CORS grant; **M3.1-A: bearer token in a header (not a cookie) and foreign `Origin`/`Sec-Fetch-Site` refused** | — (residual: a website can still detect that a local service answers) |
| T9 | Developer commits real data or secrets | 1 | 3 | 3 | `.gitignore`; CI hygiene job; synthetic-only rule | Pre-commit hook option; keep fixtures generated |
| T10 | Supply-chain compromise | 1 | 3 | 3 | `package-lock.json`; `npm audit` clean at time of writing | Python lock file with hashes; dependency review in CI |
| T15 | Restoring an untrusted backup injects files or a crafted DB | 1 | 2 | 2 | **Fixed D4** (store entries must be hash-named and hash-matching); manifest checks | Open restored DB with SQLite defensive settings; signed manifests once keys exist |
| T11 | API/UI accidentally exposed on the network | 1 | 3 | 3 | Bind addresses tested; no `0.0.0.0` in configs (test) | Refuse to start on non-loopback interfaces (M3.1 F) |
| T16 | Public repository/CI/screenshot exposure | 3 | 1 | 3 | Fictional data only | Remove the local user path from `.claude/launch.json`; never add real data or secrets to CI |

## Findings from the M3.0 review

Confirmed defects (reproduced, fixed, regression-tested in `backend/tests/test_security.py`):

| ID | Defect | Fix |
|---|---|---|
| D1 | Attached document **filenames appeared in uvicorn access logs and browser history** (sent as `?filename=`) | Filename moved to the `X-Document-Filename` header; test runs the real server and searches its log for sentinels |
| D2 | API responses (documents, reports, exports) had **no cache headers** — browsers could keep case content on disk | `Cache-Control: no-store`, `Pragma: no-cache`, `nosniff`, `no-referrer` on all `/api` responses |
| D3 | A tampered document reference (e.g. `../../...` in `document_sha256`) caused a **500 with a traceback** in the log | Refused with 409; never touches the path |
| D4 | **Restore copied arbitrary files** (e.g. `notes.html`, `payload.exe`) from a backup's `documents/` into the store and still reported "consistent" | Verification and restore refuse anything that is not `<2 hex>/<64 hex>` with matching bytes, symlinks and stray folders |
| D5 | The benchmark left a fictional database in the OS temp folder after every run | Deleted in `finally` |

Confirmed limitations (kept as strict `xfail` tests that will fail once fixed):

| ID | Limitation | Test |
|---|---|---|
| ~~L1 (T1)~~ | ~~No authentication~~ — **fixed in M3.1-A** for protected mode; the test now passes and is no longer an xfail | `test_unauthenticated_requests_are_rejected` |
| L2 (T6) | Removing the newest audit entries leaves a valid-looking chain unless a head was recorded elsewhere | `test_tail_truncation_is_detected_without_external_head` |
| L3 (T4) | Records are plaintext at rest | `test_database_file_does_not_contain_plaintext_records` |

Hypothetical concerns (not demonstrated; listed for review): undiscovered pypdf/pdf.js parser vulnerabilities (T7);
SQLite parsing of a deliberately crafted database during restore (T15); malicious browser extensions reading page
memory (T1, out of the app's control); timing/port-scan detection of a running local service by websites (low impact).

## Out of scope at this stage
Multi-user/server deployment, network sync, mobile, DICOM, legal/regulatory determination (needs independent
assessment), and physical attacks on a running, unlocked machine.
