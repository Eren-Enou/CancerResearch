import { expect, test, type Page } from '@playwright/test';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readFileSync } from 'node:fs';

// M3.1-B: key vault in the browser — setup, saving the recovery key, lock/unlock, passphrase change, recovery with the
// recovery key, and the same data key throughout. Fictional data only. Nothing is encrypted with the key yet.
const FIRST = 'fictional vault passphrase one';
const SECOND = 'fictional vault passphrase two';
const THIRD = 'fictional vault passphrase three';
const CASE = 'Fictional Case M31B E2E';
const DB = process.env.ONCOSCOPE_E2E_DB_M31B!;

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

async function keyId(page: Page) {
  await nav(page, 'Security');
  return (await page.getByTestId('key-id').textContent())!;
}

test('M3.1-B: setup → save recovery key → lock/unlock → change passphrase → recover → same data key', async ({ page, request }) => {
  test.setTimeout(180_000);
  const urls: string[] = [];
  page.on('request', r => urls.push(r.url()));

  // 1. Setup issues a recovery key, shown once in a readable form, before any workspace.
  await page.goto('/');
  await page.getByLabel('Owner name').fill('Fictional Vault Owner');
  await page.getByLabel('Passphrase', { exact: true }).fill(FIRST);
  await page.getByLabel('Repeat passphrase').fill(FIRST);
  await page.getByRole('button', { name: 'Create owner and unlock' }).click();
  await expect(page.getByRole('heading', { name: 'Save your recovery key' })).toBeVisible();
  const recoveryKey = (await page.getByTestId('recovery-key').textContent())!;
  expect(recoveryKey).toMatch(/^([A-Z2-7]{4}-){13}[A-Z2-7]{4}$/);
  await expect(page.getByText('cannot be recovered by anyone', { exact: false })).toBeVisible();
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {});
  await page.getByRole('button', { name: 'Copy recovery key' }).click();
  await expect(page.getByRole('status')).toContainText(/Copied|not available/);
  // Saving must be confirmed: the box and the last group.
  await page.getByRole('button', { name: 'Continue to OncoScope' }).click();
  await expect(page.getByRole('alert')).toContainText('Tick the box');
  await page.getByLabel('I have saved this recovery key somewhere safe').check();
  await page.getByLabel(/Type the last group/).fill(recoveryKey.slice(-4));
  await page.getByRole('button', { name: 'Continue to OncoScope' }).click();
  await page.getByLabel('Fictional case label').fill(CASE);
  await page.getByRole('button', { name: 'Create fictional case' }).click();
  await expect(page.getByText('SYNTHETIC DATA — NOT A REAL PATIENT')).toBeVisible();

  // The vault file holds wrapped keys only: neither the recovery key nor the passphrase is in it.
  const vaultText = readFileSync(join(dirname(DB), 'oncoscope-vault.json'), 'utf-8');
  for (const secret of [recoveryKey, recoveryKey.replace(/-/g, ''), FIRST]) expect(vaultText).not.toContain(secret);

  // 2. The Security view shows identifiers only and says nothing is encrypted yet.
  const originalKeyId = await keyId(page);
  expect(originalKeyId).toMatch(/^[0-9a-f]{32}$/);
  await expect(page.getByText('Not encrypted yet.', { exact: false })).toBeVisible();
  const fingerprints = await page.getByTestId('derived-keys').textContent();

  // 3. Lock and unlock: no recovery key is shown again once confirmed.
  await lock(page);
  await unlock(page, FIRST);
  await expect(page.getByTestId('session-box')).toBeVisible();
  await expect(page.getByTestId('recovery-key')).toHaveCount(0);

  // 4. Change the passphrase; the data key stays the same.
  await nav(page, 'Security');
  const form = page.getByRole('form', { name: 'Change passphrase' });
  await form.getByLabel('Current passphrase').fill('fictional wrong passphrase');
  await form.getByLabel('New passphrase', { exact: true }).fill(SECOND);
  await form.getByLabel('Repeat new passphrase').fill(SECOND);
  await form.getByRole('button', { name: 'Change passphrase' }).click();
  await expect(form.getByRole('alert')).toHaveText('The current passphrase is incorrect.');
  await form.getByLabel('Current passphrase').fill(FIRST);
  await form.getByLabel('New passphrase', { exact: true }).fill(SECOND);
  await form.getByLabel('Repeat new passphrase').fill(SECOND);
  await form.getByRole('button', { name: 'Change passphrase' }).click();
  await expect(form.getByRole('status')).toContainText(`key ID ${originalKeyId}`);
  await lock(page);
  await unlock(page, FIRST);
  await expect(page.getByRole('alert')).toHaveText('Incorrect passphrase.');
  await unlock(page, SECOND);
  expect(await keyId(page)).toBe(originalKeyId);

  // 5. "Forgot" the passphrase: recover with the recovery key and choose a new one.
  await lock(page);
  await page.getByRole('button', { name: 'Forgot the passphrase? Use your recovery key' }).click();
  const recover = page.getByRole('form', { name: 'Recover with recovery key' });
  const wrongKey = recoveryKey.slice(0, -4) + (recoveryKey.endsWith('AAAA') ? 'BBBB' : 'AAAA');
  await recover.getByLabel('Recovery key').fill(wrongKey);
  await recover.getByLabel('New passphrase', { exact: true }).fill(THIRD);
  await recover.getByLabel('Repeat new passphrase').fill(THIRD);
  await recover.getByRole('button', { name: 'Recover and set new passphrase' }).click();
  await expect(recover.getByRole('alert')).toContainText('typing mistake');          // checksum catches the typo
  await recover.getByLabel('Recovery key').fill(recoveryKey.toLowerCase().replace(/-/g, ' '));   // forgiving input
  await recover.getByLabel('New passphrase', { exact: true }).fill(THIRD);
  await recover.getByLabel('Repeat new passphrase').fill(THIRD);
  await recover.getByRole('button', { name: 'Recover and set new passphrase' }).click();
  await expect(page.getByText(CASE).first()).toBeVisible();
  expect(await keyId(page)).toBe(originalKeyId);
  expect(await page.getByTestId('derived-keys').textContent()).toBe(fingerprints);   // derived keys reproduced

  // 6. Only the new passphrase works now; the audit history records each step under the owner.
  await lock(page);
  await unlock(page, SECOND);
  await expect(page.getByRole('alert')).toHaveText('Incorrect passphrase.');
  await unlock(page, THIRD);
  await expect(page.getByTestId('session-box')).toContainText('Fictional Vault Owner');

  // Secrets never travelled in a URL and nothing was kept in browser storage.
  for (const url of urls) {
    for (const secret of [recoveryKey, recoveryKey.replace(/-/g, ''), FIRST, SECOND, THIRD]) expect(url).not.toContain(encodeURIComponent(secret));
  }
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
  expect((await request.get('/api/vault/status')).status()).toBe(401);
});
