/**
 * M2.6 demonstration: rendered PDF pages, honest citation mapping, keyboard region transcription of a document without a
 * text layer, and the case-history view — on a brand-new empty database with benchmark documents (all fictional).
 * Screenshots are written to docs/screenshots as browser-test evidence.
 */
import { expect, test, type Page } from '@playwright/test';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const DOCS = join(ROOT, 'fixtures', 'benchmark', 'documents');
const SHOTS = join(ROOT, 'docs', 'screenshots');

async function nav(page: Page, view: string) {
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: view, exact: true }).click();
}

async function importDoc(page: Page, file: string, kind: string, date?: string) {
  await nav(page, 'Documents');
  const details = page.locator('details.import-details');
  if (!(await details.getAttribute('open') !== null)) await details.locator('summary').click();
  const form = page.getByRole('form', { name: 'Import a fictional document' });
  await form.getByLabel('File (PDF, TXT, PNG, JPEG)').setInputFiles(join(DOCS, file));
  await form.getByLabel('Document type').selectOption(kind);
  if (date) await form.getByLabel('Document date (optional)').fill(date);
  await form.getByRole('button', { name: 'Import document' }).click();
  const viewer = page.getByRole('region', { name: /Document viewer for source #\d+/ });
  await expect(viewer).toContainText(file);
  return Number((await viewer.getAttribute('aria-label'))!.match(/#(\d+)/)![1]);
}

async function canvasHasInk(page: Page, testId: string) {
  return page.getByTestId(testId).evaluate((c: HTMLCanvasElement) => {
    const data = c.getContext('2d')!.getImageData(0, 0, c.width, c.height).data;
    let dark = 0;
    for (let i = 0; i < data.length; i += 4) if (data[i] < 128 && data[i + 3] > 0) dark++;
    return dark;
  });
}

test('M2.6: rendered pages, mapped citations, keyboard regions and the case history', async ({ page, request }) => {
  const api = async (path: string) => (await request.get(`/api${path}`)).json();
  await page.goto('/');
  await page.getByLabel('Fictional case label').fill('Fictional Case M26 E2E');
  await page.getByRole('button', { name: 'Create fictional case' }).click();
  await expect(page.getByText('SYNTHETIC DATA — NOT A REAL PATIENT')).toBeVisible();
  const [{ id: pid }] = await api('/patients');

  // One-step import of six benchmark documents; a re-import is refused before anything is created.
  const b03 = await importDoc(page, 'B03_lab_flag_column.txt', 'lab', '2026-09-19');
  const b04 = await importDoc(page, 'B04_lab_cumulative.pdf', 'lab', '2026-10-10');
  const b16 = await importDoc(page, 'B16_portal_conflicting.txt', 'lab', '2026-09-20');
  const b07 = await importDoc(page, 'B07_lab_ambiguous_date.txt', 'lab');
  const b20 = await importDoc(page, 'B20_lab_scanned_no_text.pdf', 'lab');
  const r02 = await importDoc(page, 'R02_ct_followup.pdf', 'radiology', '2026-10-12');
  const before = (await api(`/patients/${pid}/sources`)).length;
  if ((await page.locator('details.import-details').getAttribute('open')) === null) await page.locator('details.import-details summary').click();
  const form = page.getByRole('form', { name: 'Import a fictional document' });
  await form.getByLabel('File (PDF, TXT, PNG, JPEG)').setInputFiles(join(DOCS, 'B03_lab_flag_column.txt'));
  await form.getByRole('button', { name: 'Import document' }).click();
  await expect(form.getByRole('alert')).toContainText(`already attached to source #${b03}`);
  expect((await api(`/patients/${pid}/sources`)).length).toBe(before);

  // Rendered PDF pages: real ink on the canvas, zoom and page navigation; integrity re-checked in the browser.
  await page.getByRole('navigation', { name: 'Documents' }).getByRole('button', { name: new RegExp(`^#${r02} ·`) }).click();
  await expect(page.getByTestId('pdf-integrity')).toContainText('SHA-256 re-verified');
  await expect.poll(() => canvasHasInk(page, 'pdf-canvas-1')).toBeGreaterThan(200);
  const width1 = await page.getByTestId('pdf-canvas-1').evaluate(c => c.getBoundingClientRect().width);
  await page.getByRole('button', { name: 'Zoom in' }).click();
  await expect(page.getByTestId('zoom-level')).toHaveText('125%');
  expect(await page.getByTestId('pdf-canvas-1').evaluate(c => c.getBoundingClientRect().width)).toBeGreaterThan(width1 * 1.2);
  await page.getByRole('button', { name: 'Next page' }).click();
  await expect(page.getByLabel('Page number')).toHaveValue('2');
  await page.screenshot({ path: join(SHOTS, 'm2_6-rendered-pdf.png') });

  // Extraction from the lab documents; only issue-free proposals are accepted, the ambiguous one stays pending.
  for (const id of [b03, b04, b16, b07]) {
    await page.getByRole('navigation', { name: 'Documents' }).getByRole('button', { name: new RegExp(`^#${id} ·`) }).click();
    await page.getByRole('button', { name: 'Propose CA-125 values' }).click();
    await expect(page.getByRole('status')).toContainText('new proposal(s) added to the review queue');
  }
  // A document without a text layer offers no extraction; the API refuses it explicitly.
  await page.getByRole('navigation', { name: 'Documents' }).getByRole('button', { name: new RegExp(`^#${b20} ·`) }).click();
  await expect(page.getByRole('button', { name: 'Propose CA-125 values' })).toHaveCount(0);
  expect((await request.post(`/api/sources/${b20}/extract`)).status()).toBe(422);

  await nav(page, 'Review');
  await page.getByLabel('Show').selectOption('all');
  const cards = page.getByTestId('extraction-card');
  await expect(cards).toHaveCount(7);   // B03 1, B04 4, B16 1, B07 1
  const ambiguous = cards.filter({ hasText: 'B07_lab_ambiguous_date.txt' });
  await expect(ambiguous).toContainText('several possible readings');
  await expect(ambiguous.getByRole('button', { name: 'Accept' })).toBeDisabled();
  for (let i = 0; i < 7; i++) {
    const clean = cards.filter({ hasNotText: 'B07_lab_ambiguous_date.txt' }).and(page.locator('[data-status="pending"]')).first();
    if (!(await clean.count())) break;
    const id = await clean.getAttribute('data-id');
    await clean.getByRole('button', { name: 'Accept' }).click();
    await expect(cards.and(page.locator(`[data-id="${id}"]`))).toHaveAttribute('data-status', 'accepted');
  }
  const labs = await api(`/patients/${pid}/labs`);
  expect(labs.map((l: { value: number }) => l.value).sort((a: number, b: number) => a - b)).toEqual([174, 202, 202, 220, 288, 310]);

  // Citations from B04 are mapped onto the rendered page only because each matches once.
  const lab202 = labs.find((l: { value: number; source_id: number }) => l.value === 202 && l.source_id === b04);
  await nav(page, 'Documents');
  await page.getByRole('navigation', { name: 'Documents' }).getByRole('button', { name: new RegExp(`^#${b04} ·`) }).click();
  const mapping = page.getByTestId('citation-mapping').and(page.locator(`[data-key="labs-${lab202.id}"]`));
  await expect(mapping).toHaveAttribute('data-status', 'mapped');
  await expect(page.getByTestId('overlay-mapped')).toHaveCount(4 * 4);  // 4 citations × 4 cells (date, test, value, unit)
  await expect(page.getByTestId('mapping-summary')).toContainText('4 shown on the pages, 0 could not be placed reliably');
  await page.getByTestId('pdf-page-1').scrollIntoViewIfNeeded();
  await page.screenshot({ path: join(SHOTS, 'm2_6-mapped-citations.png') });

  // Keyboard-only region transcription on the text-free PDF.
  await page.getByRole('navigation', { name: 'Documents' }).getByRole('button', { name: new RegExp(`^#${b20} ·`) }).click();
  await expect.poll(() => canvasHasInk(page, 'pdf-canvas-1')).toBeGreaterThan(200);
  await page.getByRole('button', { name: 'Mark a region with the keyboard' }).click();
  const box = page.getByTestId('selected-region');
  await expect(box).toBeFocused();
  await page.keyboard.press('ArrowDown');
  for (let i = 0; i < 9; i++) await page.keyboard.press('ArrowDown');
  for (let i = 0; i < 40; i++) await page.keyboard.press('Shift+ArrowRight');
  await expect(page.getByTestId('selection')).toContainText('Rendered page 1: region 10,20,70,8');
  await page.getByRole('button', { name: 'As lab' }).click();
  const add = page.getByRole('form', { name: 'Add record' });
  await expect(page.getByTestId('anchor-preview')).toContainText('Cites region 10,20,70,8');
  await add.getByLabel('Date', { exact: true }).fill('2026-08-21');
  await add.getByLabel('Test name').fill('CA-125');
  await add.getByLabel('Measured value').fill('260');
  await add.getByLabel('Unit exactly as written').fill('U/mL');
  await add.getByRole('button', { name: 'Save record' }).click();
  await expect(add.getByRole('status')).toContainText('Lab #');
  const manual = (await api(`/patients/${pid}/labs`)).find((l: { value: number }) => l.value === 260);
  expect([manual.anchor_page, manual.anchor_region, manual.provenance, manual.verification_status]).toEqual([1, '10,20,70,8', 'transcribed', 'entered']);

  // A documented treatment so the history shows it beside the lab values.
  await add.getByLabel('Record type').selectOption('Treatment');
  await add.getByLabel('Original source (required)').selectOption(String(b03));
  await add.getByLabel('Provenance').selectOption('user_observation');
  await add.getByLabel('Date', { exact: true }).fill('2026-09-04');
  await add.getByLabel('Documented regimen').fill('Fictional regimen X');
  await add.getByRole('button', { name: 'Save record' }).click();
  await expect(add.getByRole('status')).toContainText('Treatment #');

  // Case history: date selection, conflict without resolution, unreviewed items, evidence navigation.
  await nav(page, 'History');
  await expect(page.getByTestId('treatment-marker')).toHaveCount(1);
  await expect(page.getByTestId('history-point')).toHaveCount(7);
  await page.getByTestId('date-chip').and(page.locator('[data-date="2026-09-18"]')).click();
  const day = page.getByTestId('day-panel');
  await expect(day.getByTestId('day-conflict')).toContainText('does not choose');
  await expect(day.getByTestId('day-record')).toHaveCount(3);
  await expect(day).toContainText('B16_portal_conflicting.txt');
  await expect(day).toContainText('B04_lab_cumulative.pdf');
  await expect(page.getByTestId('unreviewed-summary')).toContainText('1 proposal(s) pending');
  await expect(page.getByTestId('unreviewed-summary')).toContainText('2 record(s) not reviewed');   // manual lab + treatment
  await page.screenshot({ path: join(SHOTS, 'm2_6-case-history.png'), fullPage: true });
  await day.getByRole('button', { name: new RegExp(`Show evidence for labs #`) }).first().click();
  await expect(page.getByRole('region', { name: /Document viewer for source/ })).toBeVisible();
  await expect(page.locator('mark.focus')).toContainText(/22|20/);

  // Reload keeps the case and view.
  await nav(page, 'History');
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Case history' })).toBeVisible();
});
