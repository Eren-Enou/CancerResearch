/**
 * M2.5 required demonstration: reconstruct a fictional case from original fictional documents, through the UI,
 * on a brand-new empty database. API calls and CLI runs are used only to cross-check what the UI produced.
 */
import { expect, test, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const FIXTURES = join(ROOT, 'fixtures', 'fictional');
const BACKEND = join(ROOT, 'backend');
const PYTHON = process.env.ONCOSCOPE_PYTHON ?? join(BACKEND, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const DB = process.env.ONCOSCOPE_E2E_DB_M25!;

function py(args: string[], env: Record<string, string> = {}) {
  return execFileSync(PYTHON, args, { cwd: BACKEND, env: { ...process.env, ONCOSCOPE_DB_PATH: DB, ...env }, encoding: 'utf-8' });
}

async function nav(page: Page, view: string) {
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: view, exact: true }).click();
}

async function savedId(page: Page, type: string) {
  const status = page.getByRole('form', { name: 'Add record' }).getByRole('status');
  await expect(status).toContainText(`${type} #`);
  return Number((await status.textContent())!.match(/#(\d+)/)![1]);
}

async function addSource(page: Page, label: string, kind: string, date: string, file: string) {
  await nav(page, 'Add record');
  const form = page.getByRole('form', { name: 'Add record' });
  await form.getByLabel('Record type').selectOption('Source');
  await expect(form.getByRole('status')).toHaveCount(0);
  await form.getByLabel('Source label', { exact: true }).fill(label);
  await form.getByLabel('Document type').selectOption(kind);
  await form.getByLabel('Document date', { exact: true }).fill(date);
  await form.getByRole('button', { name: 'Save record' }).click();
  const id = await savedId(page, 'Source');
  await nav(page, 'Sources');
  const card = page.getByTestId(`source-${id}`);
  await card.getByLabel('Attach synthetic document').setInputFiles(join(FIXTURES, file));
  await expect(card.getByText(/SHA-256 [0-9a-f]{64}/)).toBeVisible();
  return id;
}

async function openDocument(page: Page, sourceId: number) {
  await nav(page, 'Documents');
  await page.getByRole('navigation', { name: 'Documents' }).getByRole('button', { name: new RegExp(`^#${sourceId} ·`) }).click();
  await expect(page.getByRole('region', { name: `Document viewer for source #${sourceId}` })).toBeVisible();
}

/** Selects an exact passage in the rendered text layer, as a user would with the mouse. */
async function selectPassage(page: Page, pageNo: number, passage: string) {
  const textTab = page.getByRole('tab', { name: 'Extracted text' });
  if (await textTab.count()) await textTab.click();   // PDFs open on rendered pages; citations are made in the text layer
  const pre = page.getByTestId(`doc-page-${pageNo}`);
  await expect(pre).toContainText(passage);
  await pre.evaluate((el, text) => {
    const full = el.textContent!;
    const start = full.indexOf(text);
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let pos = 0, sNode: Text | null = null, sOff = 0, eNode: Text | null = null, eOff = 0;
    while (walker.nextNode()) {
      const n = walker.currentNode as Text;
      if (!sNode && start < pos + n.data.length) { sNode = n; sOff = start - pos; }
      if (!eNode && start + text.length <= pos + n.data.length) { eNode = n; eOff = start + text.length - pos; }
      pos += n.data.length;
    }
    const range = document.createRange();
    range.setStart(sNode!, sOff); range.setEnd(eNode!, eOff);
    window.getSelection()!.removeAllRanges(); window.getSelection()!.addRange(range);
    el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
  }, passage);
  await expect(page.getByTestId('selection')).toContainText(passage);
}

test('M2.5: fictional documents → review queue → anchored timeline → correction → traceable report → backup/restore', async ({ page, request }) => {
  const api = async (path: string) => (await request.get(`/api${path}`)).json();

  // 1. New synthetic case on an empty database.
  await page.goto('/');
  await page.getByLabel('Fictional case label').fill('Fictional Case M25 E2E');
  await page.getByRole('button', { name: 'Create fictional case' }).click();
  await expect(page.getByText('SYNTHETIC DATA — NOT A REAL PATIENT')).toBeVisible();
  const [{ id: pid }] = await api('/patients');

  // 2. Import two lab reports (plus an independent portal copy) and two imaging reports.
  const lab1 = await addSource(page, 'Imaginary Valley lab 2026-08-07', 'lab', '2026-08-08', 'lab_2026-08-07_imaginary_valley.txt');
  const lab2 = await addSource(page, 'Imaginary Valley lab 2026-09-18', 'lab', '2026-09-19', 'lab_2026-09-18_imaginary_valley.pdf');
  const portal = await addSource(page, 'Portal copy 2026-09-18', 'lab', '2026-09-20', 'lab_2026-09-18_portal_copy.txt');
  const ct1 = await addSource(page, 'Baseline CT report', 'radiology', '2026-08-10', 'ct_2026-08-10_baseline.txt');
  const ct2 = await addSource(page, 'Follow-up CT report', 'radiology', '2026-10-12', 'ct_2026-10-12_followup.pdf');
  // Importing the same file again under another source is refused as a duplicate.
  await nav(page, 'Add record');
  const form = page.getByRole('form', { name: 'Add record' });
  await form.getByLabel('Record type').selectOption('Source');
  await form.getByLabel('Source label', { exact: true }).fill('Accidental re-import');
  await form.getByRole('button', { name: 'Save record' }).click();
  const dupSource = await savedId(page, 'Source');
  await nav(page, 'Sources');
  await page.getByTestId(`source-${dupSource}`).getByLabel('Attach synthetic document').setInputFiles(join(FIXTURES, 'lab_2026-08-07_imaginary_valley.txt'));
  await expect(page.getByTestId(`source-${dupSource}`).getByRole('alert')).toContainText(`Duplicate import: this exact file is already attached to source #${lab1}`);

  // 3. Extract supported lab observations into the review queue; nothing becomes a record yet.
  for (const id of [lab1, lab2, portal]) {
    await openDocument(page, id);
    await page.getByRole('button', { name: 'Propose CA-125 values' }).click();
    await expect(page.getByRole('status')).toContainText('1 new proposal(s) added to the review queue');
  }
  expect(await api(`/patients/${pid}/labs`)).toEqual([]);

  // 4. Manually transcribe source-anchored imaging observations from text selections.
  await openDocument(page, ct1);
  await selectPassage(page, 1, 'Study date: 2026-08-10');
  await page.getByRole('button', { name: 'As imaging study' }).click();
  await expect(page.getByTestId('anchor-preview')).toContainText('Study date: 2026-08-10');
  await form.getByLabel('Date', { exact: true }).fill('2026-08-10');
  await form.getByLabel('Study label').fill('Baseline abdominal CT (fictional)');
  await form.getByRole('button', { name: 'Save record' }).click();
  const study1 = await savedId(page, 'Imaging study');

  await openDocument(page, ct1);
  await selectPassage(page, 1, 'measures 38 x 22 mm');
  await page.getByRole('button', { name: 'As lesion measurement' }).click();
  await form.getByLabel('Associated study').selectOption(String(study1));
  await form.getByLabel('Consistent lesion key (e.g. reference-A)').fill('reference-A');
  await form.getByLabel('Documented location').fill('Fictional soft-tissue lesion');
  await form.getByLabel('Long axis (mm)').fill('38');
  await form.getByLabel('Short axis (mm; optional)').fill('22');
  await form.getByRole('button', { name: 'Save record' }).click();
  const m1 = await savedId(page, 'Lesion measurement');

  await openDocument(page, ct2);
  await selectPassage(page, 1, 'Study date: 2026-10-12');
  await page.getByRole('button', { name: 'As imaging study' }).click();
  await form.getByLabel('Date', { exact: true }).fill('2026-10-12');
  await form.getByLabel('Study label').fill('Follow-up abdominal CT (fictional)');
  await form.getByRole('button', { name: 'Save record' }).click();
  const study2 = await savedId(page, 'Imaging study');

  // Page 2 of the PDF; the long axis is deliberately mistyped as 34 (the report says 31) to be corrected later.
  await openDocument(page, ct2);
  await selectPassage(page, 2, 'measures 31 x 18 mm');
  await page.getByRole('button', { name: 'As lesion measurement' }).click();
  await expect(page.getByTestId('anchor-preview')).toContainText('Cites page 2');
  await form.getByLabel('Associated study').selectOption(String(study2));
  await form.getByLabel('Consistent lesion key (e.g. reference-A)').fill('reference-A');
  await form.getByLabel('Documented location').fill('Fictional soft-tissue lesion');
  await form.getByLabel('Long axis (mm)').fill('34');
  await form.getByLabel('Short axis (mm; optional)').fill('18');
  await form.getByRole('button', { name: 'Save record' }).click();
  const m2 = await savedId(page, 'Lesion measurement');
  const m2Record = await api(`/records/measurements/${m2}`);
  expect([m2Record.anchor_page, m2Record.anchor_quote, m2Record.provenance]).toEqual([2, 'measures 31 x 18 mm', 'transcribed']);

  // 5. Review: compare each proposal with its original text and accept it.
  await nav(page, 'Review');
  await page.getByLabel('Show').selectOption('all');  // keep decided cards visible
  const cards = page.getByTestId('extraction-card');
  await expect(cards).toHaveCount(3);
  for (const file of ['lab_2026-08-07_imaginary_valley.txt', 'lab_2026-09-18_imaginary_valley.pdf', 'lab_2026-09-18_portal_copy.txt']) {
    const card = cards.filter({ hasText: file });
    await expect(card.getByTestId('original-text').locator('mark')).toContainText('CA-125');
    await card.getByRole('button', { name: 'Accept' }).click();
    await expect(card).toHaveAttribute('data-status', 'accepted');
  }
  const labs = await api(`/patients/${pid}/labs`);
  expect(labs.map((l: { value: number }) => l.value).sort()).toEqual([202, 220, 310]);
  expect(labs.every((l: { provenance: string; verification_status: string }) => l.provenance === 'extracted' && l.verification_status === 'reviewed')).toBe(true);

  // 6. Timeline and CA-125 chart match the database; every row links to its evidence.
  await nav(page, 'Overview');
  const points = page.getByTestId('chart-point');
  await expect(points).toHaveCount(3);
  const plotted = await points.evaluateAll(ps => ps.map(p => [p.getAttribute('data-date'), Number(p.getAttribute('data-value'))]));
  expect(plotted.sort()).toEqual(labs.map((l: { date: string; value: number }) => [l.date, l.value]).sort());
  await nav(page, 'Timeline');
  const timeline = await api(`/patients/${pid}/timeline`);
  await expect(page.getByTestId('timeline-row')).toHaveCount(timeline.length);
  const lab310 = labs.find((l: { value: number }) => l.value === 310);
  await page.getByRole('button', { name: `Show evidence for labs #${lab310.id}` }).click();
  await expect(page.locator('mark.focus')).toHaveText('CA-125          310      U/mL');

  // The evidence view surfaces the conflicting 2026-09-18 values without choosing one.
  await nav(page, 'Evidence');
  const conflict = page.getByTestId('finding').and(page.locator('[data-kind="conflicting_values"]'));
  await expect(conflict).toContainText('202 U/mL');
  await expect(conflict).toContainText('220 U/mL');
  await expect(page.getByTestId('date-group').and(page.locator('[data-date="2026-09-18"]'))).toContainText('lab_2026-09-18_portal_copy.txt');

  // 7. Compare lesion measurements (still showing the mistyped value).
  await nav(page, 'Imaging');
  await expect(page.getByTestId('comparison-row').getByTestId('difference')).toHaveText('-4');

  // 8. Correct the intentionally incorrect observation against its cited passage.
  await page.getByRole('button', { name: `Show evidence for measurements #${m2}` }).click();
  await expect(page.locator('mark.focus')).toHaveText('measures 31 x 18 mm');
  await nav(page, 'Imaging');
  await page.getByRole('button', { name: `Correct measurement #${m2}` }).click();
  const correction = page.getByRole('form', { name: `Correct measurement #${m2}` });
  await correction.getByLabel('Long axis (mm)').fill('31');
  await correction.getByLabel('Reason for change (required)').fill('Mistyped; cited passage on page 2 reads 31 x 18 mm');
  await correction.getByRole('button', { name: 'Save correction' }).click();
  await expect(page.getByTestId('comparison-row').getByTestId('difference')).toHaveText('-7');

  // 9. The audit log keeps the original value and the reason.
  await nav(page, 'Audit');
  const entry = page.getByTestId('audit-entry').and(page.locator('[data-action="correct"]'));
  await expect(entry.locator('del')).toHaveText('34');
  await expect(entry.locator('ins')).toHaveText('31');
  await expect(entry).toContainText('Mistyped; cited passage on page 2 reads 31 x 18 mm');
  await expect(page.getByText(/Hash chain verified/)).toBeVisible();

  // 10. Report: each displayed observation cites its exact document passage; an independent script re-checks them.
  const report = await (await request.get(`/api/patients/${pid}/report.md`)).text();
  const reportFile = join(mkdtempSync(join(tmpdir(), 'oncoscope-report-')), 'report.md');
  writeFileSync(reportFile, report, 'utf-8');
  const auditOut = execFileSync(PYTHON, [join(ROOT, 'fixtures', 'audit_report.py'), reportFile, FIXTURES], { encoding: 'utf-8' });
  expect(auditOut).toContain('7/7 citations verified against original files');
  expect(report).toContain(`[measurement #${m2}; corrected, revision 2; entered]`);

  // 11. Back up and restore the whole fictional case into a new database; verify records, audit and hashes.
  const backupRoot = mkdtempSync(join(tmpdir(), 'oncoscope-backup-'));
  const backupDir = py(['-m', 'app.backup', backupRoot]).trim().split('verified: ').pop()!;
  const restoredDb = join(mkdtempSync(join(tmpdir(), 'oncoscope-restored-')), 'oncoscope.sqlite3');
  const restored = JSON.parse(py(['-m', 'app.restore', backupDir, '--target', restoredDb]));
  expect(restored.consistent).toBe(true);
  expect(restored.differences).toEqual([]);
  const live = await api(`/patients/${pid}/export.json`);
  const fromRestore = JSON.parse(py(['-c', [
    'import json; from fastapi.testclient import TestClient; from app.main import app',
    `c = TestClient(app).__enter__(); print(json.dumps(c.get("/api/patients/${pid}/export.json").json()))`,
  ].join('\n')], { ONCOSCOPE_DB_PATH: restoredDb }));
  delete live.generated_at; delete fromRestore.generated_at;
  expect(fromRestore).toEqual(live);
});
