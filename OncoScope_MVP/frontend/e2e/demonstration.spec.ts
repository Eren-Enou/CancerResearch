/**
 * M2 required demonstration, driven through the real UI against a brand-new empty database.
 * All data is fictional. API calls below are used only to cross-check what the UI shows.
 */
import { expect, test, type Page } from '@playwright/test';

test.describe.configure({ mode: 'serial' });

async function nav(page: Page, view: string) {
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: view, exact: true }).click();
}

async function addRecord(page: Page, type: string, fields: Record<string, string>, selects: Record<string, string> = {}) {
  await nav(page, 'Add record');
  const form = page.getByRole('form', { name: 'Add record' });
  await form.getByLabel('Record type').selectOption(type);
  await expect(form.getByRole('status')).toHaveCount(0); // previous "saved" message cleared
  for (const [label, value] of Object.entries(selects)) await form.getByLabel(label).selectOption({ label: value });
  for (const [label, value] of Object.entries(fields)) await form.getByLabel(label, { exact: true }).fill(value);
  await form.getByRole('button', { name: 'Save record' }).click();
}

async function expectSaved(page: Page, type: string) {
  const status = page.getByRole('status');
  await expect(status).toContainText(`${type} #`);
  const id = Number((await status.textContent())!.match(/#(\d+)/)![1]);
  return id;
}

test('blank database → sourced records → verified views → audited correction → edge cases', async ({ page, request }) => {
  const api = async (path: string) => (await request.get(`/api${path}`)).json();

  // 1. Empty database: the UI offers case creation.
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'No case yet' })).toBeVisible();
  expect(await api('/patients')).toEqual([]);
  await page.getByLabel('Fictional case label').fill('Fictional Demo Case E2E');
  await page.getByRole('button', { name: 'Create fictional case' }).click();
  await expect(page.getByText('SYNTHETIC DATA — NOT A REAL PATIENT')).toBeVisible();
  const [patient] = await api('/patients');
  expect(patient.is_synthetic).toBe(1);
  const pid = patient.id;

  // 2. Sources.
  await addRecord(page, 'Source', { 'Source label': 'Fictional lab printout E2E', 'Document date': '2026-09-01', 'Origin (fictional issuer, optional)': 'Imaginary Clinic Lab' }, { 'Document type': 'lab' });
  const labSrc = await expectSaved(page, 'Source');
  await addRecord(page, 'Source', { 'Source label': 'Fictional CT report A', 'Document date': '2026-08-02' }, { 'Document type': 'radiology' });
  const ctSrcA = await expectSaved(page, 'Source');
  await addRecord(page, 'Source', { 'Source label': 'Fictional CT report B', 'Document date': '2026-10-02' }, { 'Document type': 'radiology' });
  const ctSrcB = await expectSaved(page, 'Source');

  // 3. Missing-source attempt: the form refuses to submit without a source; nothing is stored.
  await nav(page, 'Add record');
  const form = page.getByRole('form', { name: 'Add record' });
  await form.getByLabel('Record type').selectOption('Lab');
  await form.getByLabel('Original source (required)').selectOption('');
  await form.getByLabel('Measured value').fill('87.5');
  await form.getByRole('button', { name: 'Save record' }).click();
  expect(await form.getByLabel('Original source (required)').evaluate((el: HTMLSelectElement) => el.validity.valueMissing)).toBe(true);
  expect(await api(`/patients/${pid}/labs`)).toEqual([]);
  // ...and the API refuses it directly too.
  expect((await request.post('/api/labs', { data: { patient_id: pid, date: '2026-09-01', test: 'CA-125', value: 87.5, unit: 'U/mL' } })).status()).toBe(422);
  expect((await request.post('/api/labs', { data: { patient_id: pid, source_id: 999, date: '2026-09-01', test: 'CA-125', value: 87.5, unit: 'U/mL' } })).status()).toBe(422);

  // 4. Dated CA-125 value, treatment, two studies, matched measurements.
  const srcOption = (id: number, label: string) => `#${id} · ${label}`;
  await addRecord(page, 'Lab', { 'Date': '2026-09-01', 'Test name': 'CA-125', 'Measured value': '87.5', 'Unit exactly as written': 'U/mL' },
    { 'Original source (required)': srcOption(labSrc, 'Fictional lab printout E2E') });
  const labId = await expectSaved(page, 'Lab');
  await addRecord(page, 'Treatment', { 'Date': '2026-09-03', 'Documented regimen': 'Fictional regimen X', 'Cycle': '1' },
    { 'Original source (required)': srcOption(labSrc, 'Fictional lab printout E2E') });
  await expectSaved(page, 'Treatment');
  await addRecord(page, 'Imaging study', { 'Date': '2026-08-02', 'Study label': 'Fictional CT A', 'Report text (verbatim)': 'Fictional report A. Not diagnostic.' },
    { 'Original source (required)': srcOption(ctSrcA, 'Fictional CT report A') });
  const studyA = await expectSaved(page, 'Imaging study');
  await addRecord(page, 'Imaging study', { 'Date': '2026-10-02', 'Study label': 'Fictional CT B' },
    { 'Original source (required)': srcOption(ctSrcB, 'Fictional CT report B') });
  const studyB = await expectSaved(page, 'Imaging study');

  // Choosing the study preselects that study's source.
  await nav(page, 'Add record');
  await form.getByLabel('Record type').selectOption('Lesion measurement');
  await form.getByLabel('Original source (required)').selectOption('');
  await form.getByLabel('Associated study').selectOption({ label: `#${studyA} · 2026-08-02 · Fictional CT A` });
  await expect(form.getByLabel('Original source (required)')).toHaveValue(String(ctSrcA));
  await form.getByLabel('Consistent lesion key (e.g. reference-A)').fill('demo-L1');
  await form.getByLabel('Documented location').fill('Fictional site');
  await form.getByLabel('Long axis (mm)').fill('41.3');
  await form.getByLabel('Short axis (mm; optional)').fill('20');
  await form.getByRole('button', { name: 'Save record' }).click();
  const m1 = await expectSaved(page, 'Lesion measurement');

  // Zero / negative measurement: blocked in the browser, and refused by the API.
  await form.getByLabel('Original source (required)').selectOption('');
  await form.getByLabel('Associated study').selectOption({ label: `#${studyB} · 2026-10-02 · Fictional CT B` });
  await form.getByLabel('Consistent lesion key (e.g. reference-A)').fill('demo-L1');
  await form.getByLabel('Long axis (mm)').fill('0');
  await form.getByRole('button', { name: 'Save record' }).click();
  expect(await form.getByLabel('Long axis (mm)').evaluate((el: HTMLInputElement) => el.validity.rangeUnderflow)).toBe(true);
  for (const bad of [0, -5]) {
    const r = await request.post('/api/measurements', { data: { patient_id: pid, source_id: ctSrcB, study_id: studyB, lesion_key: 'demo-L1', site: 'Fictional site', long_axis_mm: bad } });
    expect(r.status()).toBe(422);
  }
  await form.getByLabel('Long axis (mm)').fill('35.1');
  await form.getByLabel('Short axis (mm; optional)').fill('');
  await form.getByRole('button', { name: 'Save record' }).click();
  const m2 = await expectSaved(page, 'Lesion measurement');
  expect((await api(`/patients/${pid}/measurements`)).length).toBe(2);

  // 5. Mixed units: a case-variant spelling is refused with an explanation; a genuinely different unit is stored
  //    but kept out of the U/mL chart and named under it.
  await addRecord(page, 'Lab', { 'Date': '2026-09-10', 'Test name': 'CA-125', 'Measured value': '80', 'Unit exactly as written': 'u/ml' },
    { 'Original source (required)': srcOption(labSrc, 'Fictional lab printout E2E') });
  await expect(page.getByRole('alert')).toContainText("Unit 'u/ml' is not accepted for CA-125");
  await page.getByRole('form', { name: 'Add record' }).getByLabel('Unit exactly as written').fill('kU/L');
  await page.getByRole('form', { name: 'Add record' }).getByRole('button', { name: 'Save record' }).click();
  const kuLab = await expectSaved(page, 'Lab');

  // 6. Chart and timeline agree with the database.
  await nav(page, 'Overview');
  const dbLabs = await api(`/patients/${pid}/labs`);
  const points = page.getByTestId('chart-point');
  await expect(points).toHaveCount(1);
  const point = points.first();
  const stored = dbLabs.find((l: { id: number }) => l.id === labId);
  expect([await point.getAttribute('data-date'), Number(await point.getAttribute('data-value')), await point.getAttribute('data-unit')])
    .toEqual([stored.date, stored.value, stored.unit]);
  await expect(page.getByRole('note').first()).toContainText(`2026-09-10 80 kU/L (lab #${kuLab})`);

  await nav(page, 'Timeline');
  const timeline = await api(`/patients/${pid}/timeline`);
  const rows = page.getByTestId('timeline-row');
  await expect(rows).toHaveCount(timeline.length);
  for (const [i, t] of timeline.entries()) {
    const row = rows.nth(i);
    await expect(row).toHaveAttribute('data-entity', t.entity);
    await expect(row).toContainText(t.label);
    await expect(row).toContainText(`${t.date} · ${t.kind.toUpperCase()} #${t.id}`);
    await expect(row).toContainText(`Source #${t.source_id}`);
  }
  await expect(page.getByTestId('timeline-row').filter({ hasText: 'CA-125: 87.5 U/mL' })).toHaveCount(1);
  await expect(page.getByTestId('timeline-row').filter({ hasText: 'demo-L1 (Fictional site): long 41.3 mm, short 20 mm' })).toHaveCount(1);

  // 7. Comparison shows only the matched difference.
  await nav(page, 'Imaging');
  const cmpRows = page.getByTestId('comparison-row');
  await expect(cmpRows).toHaveCount(1);
  await expect(cmpRows.first().getByTestId('difference')).toHaveText('-6.2');
  await expect(cmpRows.first()).toContainText(`#${m1} (src #${ctSrcA})`);
  await expect(cmpRows.first()).toContainText(`#${m2} (src #${ctSrcB})`);
  // Reversed selection is refused rather than silently flipping the sign.
  await page.getByLabel('Earlier study').selectOption(String(studyB));
  await page.getByLabel('Later study').selectOption(String(studyA));
  await expect(page.getByText('swap the selection')).toBeVisible();
  await expect(cmpRows).toHaveCount(0);

  // 8. Markdown report cites each source and record.
  const report = await (await request.get(`/api/patients/${pid}/report.md`)).text();
  for (const [id, label] of [[labSrc, 'Fictional lab printout E2E'], [ctSrcA, 'Fictional CT report A'], [ctSrcB, 'Fictional CT report B']] as const) {
    expect(report).toContain(`source #${id}: ${label}`);
  }
  expect(report).toContain(`- 2026-09-01 — CA-125: 87.5 U/mL [lab #${labId}; entered] [source #${labSrc}: Fictional lab printout E2E]`);
  expect(report).toContain(`long axis 41.3 mm, short axis 20 mm [measurement #${m1}; entered] [source #${ctSrcA}: Fictional CT report A]`);

  // 9. Correct the lab value in the UI; original and corrected values appear in history and the audit view.
  await nav(page, 'Laboratory');
  await page.getByRole('button', { name: `Correct lab #${labId}` }).click();
  const correction = page.getByRole('form', { name: `Correct lab #${labId}` });
  await correction.getByLabel('Value', { exact: true }).fill('78.5');
  await correction.getByLabel('Reason for change (required)').fill('Transcription error: digits swapped (fictional)');
  await correction.getByRole('button', { name: 'Save correction' }).click();
  const labRow = page.getByTestId('lab-row').and(page.locator(`[data-id="${labId}"]`));
  await expect(labRow).toContainText('78.5');
  await expect(labRow).toContainText('rev 2');
  await page.getByRole('button', { name: `History of lab #${labId}` }).click();
  const change = page.getByTestId('audit-change').first();
  await expect(change.locator('del')).toHaveText('87.5');
  await expect(change.locator('ins')).toHaveText('78.5');
  await expect(page.getByTestId('chart-point').first()).toHaveAttribute('data-value', '78.5');

  await nav(page, 'Audit');
  await expect(page.getByText(/Hash chain verified/)).toBeVisible();
  const auditCorrection = page.getByTestId('audit-entry').and(page.locator('[data-action="correct"]'));
  await expect(auditCorrection).toHaveCount(1);
  await expect(auditCorrection.locator('del')).toHaveText('87.5');
  await expect(auditCorrection.locator('ins')).toHaveText('78.5');
  await expect(auditCorrection).toContainText('Reason: Transcription error: digits swapped (fictional)');
  const history = await api(`/records/labs/${labId}/history`);
  expect(history.map((h: { action: string }) => h.action)).toEqual(['create', 'correct']);
  expect(JSON.parse(history[0].detail_json).value).toBe(87.5);

  const corrected = await (await request.get(`/api/patients/${pid}/report.md`)).text();
  expect(corrected).toContain(`CA-125: 78.5 U/mL [lab #${labId}; corrected, revision 2; entered]`);

  // 10. Invalid IDs.
  expect((await request.get('/api/records/labs/99999')).status()).toBe(404);
  expect((await request.get('/api/patients/99999/timeline')).status()).toBe(404);
  expect((await request.get(`/api/patients/${pid}/compare?baseline_id=${studyA}&followup_id=99999`)).status()).toBe(422);

  // 11. Attach a synthetic document to a source and verify its hash in the UI.
  await nav(page, 'Sources');
  const card = page.getByTestId(`source-${labSrc}`);
  await card.getByLabel('Attach synthetic document').setInputFiles({ name: 'fictional-printout.txt', mimeType: 'text/plain', buffer: Buffer.from('FICTIONAL LAB PRINTOUT\nCA-125 78.5 U/mL\n') });
  await expect(card.getByText(/SHA-256 [0-9a-f]{64}/)).toBeVisible();
  await card.getByRole('button', { name: 'Verify hash' }).click();
  await expect(card.getByText('Hash verified')).toBeVisible();
});
