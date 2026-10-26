import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { parseRegion, segmentPage } from './DocumentViewer';
import { locationText } from './EvidenceView';
import { ReviewQueue, excerpt } from './ReviewQueue';
import type { Extraction } from '../types';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('segmentPage', () => {
  const text = 'Collection date: 2026-09-18\nCA-125   202   U/mL\n';
  it('reassembles to the exact page text with anchored passages marked', () => {
    const segs = segmentPage(text, [{ start: 28, end: 47, key: 'labs-1' }]);
    expect(segs.map(s => s.text).join('')).toBe(text);
    expect(segs.find(s => s.keys.length)).toEqual({ text: 'CA-125   202   U/mL', keys: ['labs-1'] });
  });
  it('keeps the first of overlapping anchors and ignores out-of-range ones', () => {
    const segs = segmentPage(text, [{ start: 28, end: 47, key: 'a' }, { start: 30, end: 40, key: 'b' }, { start: 5, end: 999, key: 'c' }]);
    expect(segs.map(s => s.text).join('')).toBe(text);
    expect(segs.filter(s => s.keys.length).map(s => s.keys)).toEqual([['a', 'b']]);
  });
});

describe('helpers', () => {
  it('parses regions strictly', () => {
    expect(parseRegion('10,37.5,80,15')).toEqual({ x: 10, y: 37.5, w: 80, h: 15 });
    expect(parseRegion('10,20,30')).toBeNull();
    expect(parseRegion(null)).toBeNull();
  });
  it('shows one line of context around an extracted passage', () => {
    const page = 'A\nB\nCA-125 310 U/mL\nC\nD';
    const start = page.indexOf('CA-125');
    expect(excerpt(page, start, start + 15)).toEqual({ before: 'B\n', match: 'CA-125 310 U/mL', after: '\nC' });
  });
  it('describes evidence locations', () => {
    const base = { anchor_page: 2, anchor_start: 10, anchor_end: 29, anchor_region: null, provenance: 'transcribed' as const };
    expect(locationText(base)).toBe('p.2 chars 10–29');
    expect(locationText({ ...base, anchor_start: null, anchor_end: null, anchor_region: '1,2,3,4' })).toBe('region 1,2,3,4');
    expect(locationText({ ...base, provenance: 'user_observation' })).toBe('user observation (no document)');
  });
});

describe('ReviewQueue', () => {
  const proposal: Extraction = {
    id: 3, uid: 'u', patient_id: 1, source_id: 6, document_sha256: 'a'.repeat(64), parser: 'ca125-lab', parser_version: '1',
    page: 1, start: 0, end: 18, original_text: 'CA-125   188   U/mL', proposed: { test: 'CA-125', value: 188, unit: 'U/mL', date: null },
    issues: [{ code: 'date_ambiguous', message: "Collection date '09/10/2026' has several possible readings (2026-09-10, 2026-10-09)" }],
    status: 'pending', accepted: null, decision_reason: null, decided_at: null, record_entity: null, record_id: null, created_at: '',
  };
  it('shows missing/uncertain fields, blocks acceptance until supplied, and sends only the correction', async () => {
    const calls: { url: string; body?: string }[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
      calls.push({ url: String(url), body: init?.body as string | undefined });
      const body = String(url).includes('/extractions?') || String(url).endsWith('/extractions') ? [proposal] : String(url).endsWith('/text') ? null : {};
      return new Response(JSON.stringify(body), { status: String(url).endsWith('/text') ? 404 : 200 });
    });
    const onDecided = vi.fn();
    render(<ReviewQueue patientId={1} sources={[]} refresh={0} onDecided={onDecided} onOpenDocument={() => undefined} />);
    expect(await screen.findByText(/Missing — must be supplied/)).toBeTruthy();
    expect(screen.getByText(/several possible readings/)).toBeTruthy();
    const accept = screen.getByRole('button', { name: 'Accept' }) as HTMLButtonElement;
    expect(accept.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('date for proposal #3'), { target: { value: '2026-09-10' } });
    fireEvent.change(screen.getByLabelText('Reason for proposal #3'), { target: { value: 'Sender confirmed day-first (fictional)' } });
    fireEvent.click(screen.getByRole('button', { name: 'Accept with corrections' }));
    await waitFor(() => expect(onDecided).toHaveBeenCalled());
    const sent = calls.find(c => c.url.endsWith('/extractions/3/accept'))!;
    expect(JSON.parse(sent.body!)).toEqual({ changes: { date: '2026-09-10' }, reason: 'Sender confirmed day-first (fictional)' });
  });
});
