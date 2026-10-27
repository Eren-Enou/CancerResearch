/**
 * M3.1-C acceptance demonstration: an existing plaintext OncoScope installation (fictional case, M3.1-B vault) is
 * backed up, migrated to SQLCipher with the command-line tool, shown to be unreadable by ordinary SQLite, and then used
 * in the browser: unlock, the same case, passphrase change, recovery-key access, a restarted server, and an interrupted
 * migration on a disposable copy. The CLI and fingerprint runs cross-check what the UI shows. Fictional data only.
 */
import { expect, test, type Page } from '@playwright/test';
import { execFileSync, spawn } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const BACKEND = join(ROOT, 'backend');
const LEGACY = join(ROOT, 'fixtures', 'legacy_installation.py');
const PYTHON = process.env.ONCOSCOPE_PYTHON ?? join(BACKEND, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const DB = process.env.ONCOSCOPE_E2E_DB_M31C!;
const FIRST = 'fictional migration passphrase one';
const SECOND = 'fictional migration passphrase two';
const THIRD = 'fictional migration passphrase three';
const CASE = 'Fictional Encrypted Case SENTINEL-CASE';
const RECORD_TABLES = ['patients', 'sources', 'events', 'labs', 'treatments', 'studies', 'measurements', 'extractions', 'document_text'];

type Fingerprint = { kind: string; tables: Record<string, { rows: number; sha256: string }>; audit_chain: { valid: boolean };
  audit_head: { entries: number }; user_version: number; patients: string[] };

function py(args: string[], { db = DB, input, env = {} }: { db?: string; input?: string; env?: Record<string, string> } = {}) {
  return execFileSync(PYTHON, args, { cwd: BACKEND, input, encoding: 'utf-8',
    env: { ...process.env, ONCOSCOPE_MODE: 'protected', ONCOSCOPE_DB_PATH: db, ...env } });
}
const fingerprint = (db = DB, passphrase?: string): Fingerprint =>
  JSON.parse(py([LEGACY, 'inspect'], { db, input: passphrase ? passphrase + '\n' : '' }));
const migrateResult = (out: string) => JSON.parse(out.slice(0, out.indexOf('\n}') + 2));
const records = (f: Fingerprint) => Object.fromEntries(RECORD_TABLES.map(t => [t, f.tables[t]]));

async function nav(page: Page, view: string) {
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: view, exact: true }).click();
}

async function unlock(page: Page, passphrase: string) {
  await page.getByLabel('Passphrase').fill(passphrase);
  await page.getByRole('button', { name: 'Unlock' }).click();
}

async function lock(page: Page) {
  await page.getByRole('button', { name: 'Lock now' }).click();
  await expect(page.getByRole('heading', { name: 'OncoScope is locked' })).toBeVisible();
}

test('M3.1-C: plaintext installation → backup → SQLCipher migration → same case, passphrase change, recovery, restart, interrupted copy', async ({ page, request }) => {
  test.setTimeout(240_000);
  const urls: string[] = [];
  page.on('request', r => urls.push(r.url()));
  const work = join(dirname(dirname(DB)), 'm31c-work');
  mkdirSync(work, { recursive: true });
  const recoveryKeyFile = join(work, 'fictional-recovery-key.txt');

  // 1. An existing plaintext installation, as M3.1-B left it (the server is already running on this data folder).
  py([LEGACY, 'plaintext', recoveryKeyFile], { input: FIRST + '\n' });
  const recoveryKey = readFileSync(recoveryKeyFile, 'utf-8').trim();
  expect(recoveryKey).toMatch(/^([A-Z2-7]{4}-){13}[A-Z2-7]{4}$/);

  // 2. Its contents and audit history verify; ordinary SQLite can read it (that is the problem being fixed).
  const before = fingerprint();
  expect(before.kind).toBe('plaintext');
  expect(before.audit_chain.valid).toBe(true);
  expect(before.patients).toEqual([CASE]);
  expect(JSON.parse(py([LEGACY, 'sqlite3-read'])).readable).toBe(true);

  // The browser refuses to offer an unlock for a plaintext protected database and explains the migration.
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Encrypt the database first' })).toBeVisible();
  await expect(page.getByText('python -m app.encrypt_migrate', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Passphrase')).toHaveCount(0);

  // 3. Back it up (a plaintext backup, verified), then 4. migrate it with the command-line tool.
  const backupOut = py(['-m', 'app.backup', join(work, 'backups')]);
  expect(backupOut).toContain('Backup written and verified');
  const migrated = py(['-m', 'app.encrypt_migrate', '--yes'], { input: FIRST + '\n' });
  const result = migrateResult(migrated);
  expect(result.status).toBe('encrypted');
  expect(result.plaintext_values_checked).toBeGreaterThan(50);
  expect(migrated).toContain('WARNING: the pre-migration backup is NOT encrypted');
  expect(migrated).not.toContain(FIRST);
  expect(existsSync(result.plaintext_backup)).toBe(true);

  // 5. Ordinary SQLite cannot read the database any more, and its bytes hold none of the case's text.
  const plainRead = JSON.parse(py([LEGACY, 'sqlite3-read']));
  expect(plainRead).toEqual({ readable: false, error: 'file is not a database' });
  const raw = readFileSync(DB);
  for (const text of [CASE, 'SENTINEL-SOURCE', 'SENTINEL-EVENT', 'Fictional typo', 'CA-125          310']) expect(raw.includes(text)).toBe(false);
  const after = fingerprint(DB, FIRST);
  expect(after.kind).toBe('encrypted');
  expect(records(after)).toEqual(records(before));                       // every record table: same rows, same hashes
  expect(after.audit_chain.valid).toBe(true);
  expect(after.audit_head.entries).toBe(before.audit_head.entries + 3);  // vault upgrade, migration start, migration done

  // 6. Launch (the running server now sees an encrypted database) and unlock.
  await page.getByRole('button', { name: 'Check again' }).click();
  await expect(page.getByRole('heading', { name: 'OncoScope is locked' })).toBeVisible();
  await expect(page.getByText('the database on disk is encrypted', { exact: false })).toBeVisible();
  await unlock(page, FIRST);
  await expect(page.getByTestId('session-box')).toContainText('Fictional Owner');

  // 7. The fictional case looks and behaves as before: records, corrections, evidence, documents, audit.
  await expect(page.getByText(CASE).first()).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export JSON' }).click();
  const exported = JSON.parse(readFileSync((await (await download).path())!, 'utf-8'));
  const lab = exported.labs.find((l: { test: string }) => l.test === 'CA-125' && l.anchor_quote);
  expect([lab.value, lab.revision, lab.verification_status, lab.provenance]).toEqual([310, 4, 'verified', 'transcribed']);   // created, 2 corrections, verified
  expect(exported.labs.some((l: { provenance: string }) => l.provenance === 'extracted')).toBe(true);
  await nav(page, 'Timeline');
  await expect(page.getByText('Fictional SENTINEL-EVENT').first()).toBeVisible();
  await nav(page, 'Documents');
  await expect(page.getByText('Fictional lab SENTINEL-SOURCE').first()).toBeVisible();
  await nav(page, 'Audit');
  await expect(page.getByText('Fictional typo').first()).toBeVisible();
  await nav(page, 'Security');
  await expect(page.getByTestId('database-encryption')).toContainText('Encrypted');
  await expect(page.getByTestId('database-encryption')).toContainText('migrated from plaintext');
  const databaseKey = await page.getByTestId('database-key').textContent();

  // 8. Change the passphrase and unlock again: the same database key, no re-encryption.
  const form = page.getByRole('form', { name: 'Change passphrase' });
  await form.getByLabel('Current passphrase').fill(FIRST);
  await form.getByLabel('New passphrase', { exact: true }).fill(SECOND);
  await form.getByLabel('Repeat new passphrase').fill(SECOND);
  await form.getByRole('button', { name: 'Change passphrase' }).click();
  await expect(form.getByRole('status')).toContainText('Passphrase changed');
  await lock(page);
  await unlock(page, FIRST);
  await expect(page.getByRole('alert')).toHaveText('Incorrect passphrase.');
  await unlock(page, SECOND);
  await nav(page, 'Security');
  expect(await page.getByTestId('database-key').textContent()).toBe(databaseKey);

  // 9. Recover with the recovery key issued under M3.1-B (its v1 wrap was carried into the v2 vault).
  await lock(page);
  await page.getByRole('button', { name: 'Forgot the passphrase? Use your recovery key' }).click();
  const recover = page.getByRole('form', { name: 'Recover with recovery key' });
  await recover.getByLabel('Recovery key').fill(recoveryKey);
  await recover.getByLabel('New passphrase', { exact: true }).fill(THIRD);
  await recover.getByLabel('Repeat new passphrase').fill(THIRD);
  await recover.getByRole('button', { name: 'Recover and set new passphrase' }).click();
  await expect(page.getByText(CASE).first()).toBeVisible();
  await nav(page, 'Security');
  expect(await page.getByTestId('database-key').textContent()).toBe(databaseKey);
  await lock(page);

  // 10. A restarted application (a new server process on the same data folder) opens the database again.
  const port = 8025;
  const server = spawn(PYTHON, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)],
    { cwd: BACKEND, env: { ...process.env, ONCOSCOPE_MODE: 'protected', ONCOSCOPE_DB_PATH: DB }, stdio: 'ignore' });
  try {
    const api = `http://127.0.0.1:${port}/api`;
    await expect.poll(async () => (await request.get(`${api}/health`).catch(() => null))?.status() ?? 0, { timeout: 30_000 }).toBe(200);
    expect((await (await request.get(`${api}/auth/status`)).json()).storage).toBe('locked');
    expect((await request.get(`${api}/patients`)).status()).toBe(401);
    const session = await (await request.post(`${api}/auth/unlock`, { data: { passphrase: THIRD } })).json();
    const patients = await (await request.get(`${api}/patients`, { headers: { Authorization: `Bearer ${session.token}` } })).json();
    expect(patients.map((p: { label: string }) => p.label)).toEqual([CASE]);
  } finally {
    server.kill();
  }

  // 11. An intentionally interrupted migration on a disposable copy, then 12. recovery with unchanged record hashes.
  const copy = join(tmpdir(), `oncoscope-e2e-m31c-copy-${Date.now()}`, 'oncoscope.sqlite3');
  py([LEGACY, 'plaintext', join(dirname(copy), '..', `${Date.now()}-key.txt`)], { db: copy, input: FIRST + '\n' });
  const copyBefore = fingerprint(copy);
  let exitCode = 0;
  try {
    py([LEGACY, 'interrupt', 'vault_marked'], { db: copy, input: FIRST + '\n' });
  } catch (e) {
    exitCode = (e as { status: number }).status;
  }
  expect(exitCode).toBe(3);                                              // stopped like a crash, between two steps
  expect(existsSync(join(dirname(copy), 'encrypt-migration.json'))).toBe(true);
  expect(fingerprint(copy).kind).toBe('plaintext');                      // the original is still in place and readable
  const resumed = migrateResult(py(['-m', 'app.encrypt_migrate', '--yes'], { db: copy, input: FIRST + '\n' }));
  expect(resumed.status).toBe('encrypted');
  expect(existsSync(join(dirname(copy), 'encrypt-migration.json'))).toBe(false);
  const copyAfter = fingerprint(copy, FIRST);
  expect(copyAfter.kind).toBe('encrypted');
  expect(records(copyAfter)).toEqual(records(copyBefore));

  // Secrets never travelled in a URL and nothing was kept in browser storage.
  for (const url of urls) for (const secret of [FIRST, SECOND, THIRD, recoveryKey]) expect(url).not.toContain(encodeURIComponent(secret));
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});
