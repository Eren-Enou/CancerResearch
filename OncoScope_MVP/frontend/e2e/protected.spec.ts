import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// M3.1-A: protected mode from an empty database — setup, unlock, protected access, lock, wrong passphrase, and
// rejection once the session's lifetime (ONCOSCOPE_SESSION_MAX_SECONDS, set short for this project) has passed.
// Fictional data only. This checks access control; it does not show that anything is encrypted (nothing is yet).
const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'fixtures', 'fictional');
const PASSPHRASE = 'fictional e2e passphrase';
const CASE = 'Fictional Case M31A E2E';
const SESSION_SECONDS = Number(process.env.ONCOSCOPE_E2E_SESSION_SECONDS ?? 45);

async function nav(page: Page, view: string) {
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: view, exact: true }).click();
}

async function unlock(page: Page, passphrase = PASSPHRASE) {
  await page.getByLabel('Passphrase').fill(passphrase);
  await page.getByRole('button', { name: 'Unlock' }).click();
}

test('M3.1-A: setup → unlock → protected access → lock → expiry rejection', async ({ page, request }) => {
  test.setTimeout(SESSION_SECONDS * 1000 + 120_000);
  const lockScreen = page.getByRole('heading', { name: 'OncoScope is locked' });
  // The test watches the session header the page sends, so it can replay a token after lock and expiry.
  let lastAuthorization = '';
  page.on('request', r => { const h = r.headers().authorization; if (h) lastAuthorization = h; });
  const replay = (path: string) => request.get(path, { headers: { Authorization: lastAuthorization } });

  // 1. First protected launch: nothing is reachable before setup, and the screen says nothing is encrypted yet.
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Set up this installation' })).toBeVisible();
  await expect(page.getByText('does not yet encrypt the database or documents on disk', { exact: false })).toBeVisible();
  expect((await request.get('/api/patients')).status()).toBe(401);
  await page.getByLabel('Owner name').fill('Fictional E2E Owner');
  await page.getByLabel('Passphrase', { exact: true }).fill(PASSPHRASE);
  await page.getByLabel('Repeat passphrase').fill(PASSPHRASE);
  await page.getByRole('button', { name: 'Create owner and unlock' }).click();
  await expect(page.getByTestId('session-box')).toContainText('Fictional E2E Owner');

  // 2. Protected work through authorized requests: case, document import, rendered PDF, review, export, audit.
  await page.getByLabel('Fictional case label').fill(CASE);
  await page.getByRole('button', { name: 'Create fictional case' }).click();
  await expect(page.getByText('SYNTHETIC DATA — NOT A REAL PATIENT')).toBeVisible();
  await nav(page, 'Documents');
  const importForm = page.getByRole('form', { name: 'Import a fictional document' });
  await importForm.getByLabel('File (PDF, TXT, PNG, JPEG)').setInputFiles(join(FIXTURES, 'lab_2026-09-18_imaginary_valley.pdf'));
  await importForm.getByLabel('Document type').selectOption('lab');
  await importForm.getByLabel('Document date (optional)').fill('2026-09-19');
  await importForm.getByRole('button', { name: 'Import document' }).click();
  await expect(page.getByTestId('pdf-integrity')).toContainText('SHA-256 re-verified');   // bytes fetched with the session
  await expect(page.getByTestId('pdf-canvas-1')).toBeVisible();
  await page.getByRole('tab', { name: 'Extracted text' }).click();
  await page.getByRole('button', { name: 'Propose CA-125 values' }).click();
  await expect(page.getByRole('status')).toContainText('new proposal(s) added to the review queue');

  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export JSON' }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^oncoscope-synthetic-\d+-export\.json$/);
  const exported = JSON.parse(readFileSync((await file.path())!, 'utf-8'));
  expect(exported.patient.label).toBe(CASE);
  expect(file.url()).toMatch(/^blob:/);                                   // no API URL, so no token in any URL

  await nav(page, 'Audit');
  await expect(page.getByTestId('audit-actor').first()).toHaveText('by Fictional E2E Owner');
  const actors = await page.getByTestId('audit-actor').allTextContents();
  expect(new Set(actors)).toEqual(new Set(['by Fictional E2E Owner']));

  // 3. The same data is refused to anyone without the session, including another web origin.
  const pid = exported.patient.id;
  const sourceId = exported.sources[0].id;
  for (const path of ['/api/patients', `/api/patients/${pid}/timeline`, `/api/sources/${sourceId}/document`,
    `/api/patients/${pid}/report.md`, `/api/patients/${pid}/export/labs.csv`, `/api/patients/${pid}/audit`]) {
    expect((await request.get(path)).status(), path).toBe(401);
  }
  expect((await request.get('/api/patients', { headers: { Origin: 'https://attacker.example' } })).status()).toBe(403);

  expect((await replay('/api/patients')).status()).toBe(200);           // the page's own session is valid

  // 4. Locking clears the workspace and revokes the session at once.
  const firstSession = lastAuthorization;
  await page.getByRole('button', { name: 'Lock now' }).click();
  await expect(lockScreen).toBeVisible();
  await expect(page.locator('body')).not.toContainText(CASE);
  await expect(page.getByRole('status')).toHaveText('Locked.');
  expect((await request.get('/api/patients', { headers: { Authorization: firstSession } })).status()).toBe(401);

  // 5. A wrong passphrase is refused with a plain message.
  await unlock(page, 'fictional wrong passphrase');
  await expect(page.getByRole('alert')).toHaveText('Incorrect passphrase.');
  await expect(page.getByLabel('Passphrase')).toHaveValue('');

  // 6. Unlocking reloads the selected case and view.
  await unlock(page);
  await expect(page.getByText(CASE).first()).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Audit', exact: true })).toBeVisible();
  const unlockedAt = Date.now();

  await nav(page, 'Timeline');
  expect((await replay('/api/patients')).status()).toBe(200);

  // 7. When the session's lifetime ends, the screen clears itself and the server rejects the token.
  await page.waitForTimeout(Math.max(0, unlockedAt + SESSION_SECONDS * 1000 + 1500 - Date.now()));
  await expect(lockScreen).toBeVisible();
  expect((await replay('/api/patients')).status()).toBe(401);
  await expect(page.getByRole('status')).toHaveText('Your session ended. Unlock again to continue.');
  await expect(page.locator('body')).not.toContainText(CASE);

  // 8. Reloading does not restore anything: the token was only ever in memory.
  await page.reload();
  await expect(lockScreen).toBeVisible();
  await unlock(page);
  await expect(page.getByText(CASE).first()).toBeVisible();
});
