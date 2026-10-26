import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readBytes } from '../api';
import { dateSummaries } from './HistoryView';
import { ImportDocument } from './ImportDocument';
import type { Extraction, Finding, Source, TimelineEntry } from '../types';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const entry = (id: number, date: string, status: 'entered' | 'reviewed' = 'reviewed'): TimelineEntry => ({
  id, uid: 'u', entity: 'labs', kind: 'lab', date, label: `lab ${id}`, source_id: 1, revision: 1, verification_status: status,
  provenance: 'extracted', anchor_page: 1, anchor_quote: 'q', anchor_start: 0, anchor_end: 1, anchor_region: null });

describe('dateSummaries', () => {
  it('counts records, unreviewed items, pending proposals and conflicts per date', () => {
    const pending = [{ proposed: { date: '2026-09-18' } }, { proposed: { date: null } }] as unknown as Extraction[];
    const findings = [{ kind: 'conflicting_values', message: 'CA-125 on 2026-09-18 has conflicting recorded values: …' }] as Finding[];
    const days = dateSummaries([entry(1, '2026-08-07'), entry(2, '2026-09-18', 'entered'), entry(3, '2026-09-18')], pending, findings);
    expect(days).toEqual([
      { date: '2026-08-07', records: 1, unreviewed: 0, pending: 0, conflict: false },
      { date: '2026-09-18', records: 2, unreviewed: 1, pending: 1, conflict: true },
    ]);
  });
});

describe('ImportDocument', () => {
  const existing = { id: 4, label: 'Northwind lab', document_sha256: '' } as Source;
  it('refuses a duplicate file before creating anything', async () => {
    const file = new File(['FICTIONAL'], 'lab.txt', { type: 'text/plain' });
    const digest = await crypto.subtle.digest('SHA-256', await readBytes(file));
    existing.document_sha256 = [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('');
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    render(<ImportDocument patientId={1} sources={[existing]} onImported={() => undefined} />);
    fireEvent.change(screen.getByLabelText('File (PDF, TXT, PNG, JPEG)'), { target: { files: [file] } });
    fireEvent.submit(screen.getByRole('form', { name: 'Import a fictional document' }));  // jsdom cannot see test-assigned files for 'required'
    expect((await screen.findByRole('alert')).textContent).toContain('already attached to source #4');
    expect(fetchSpy).not.toHaveBeenCalled();
  });
  it('creates the source and attaches the file in one step', async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
      calls.push(`${init?.method} ${url}`);
      return new Response(JSON.stringify({ id: 9 }), { status: 201 });
    });
    const onImported = vi.fn();
    render(<ImportDocument patientId={1} sources={[]} onImported={onImported} />);
    fireEvent.change(screen.getByLabelText('File (PDF, TXT, PNG, JPEG)'), { target: { files: [new File(['X'], 'B01_lab.txt')] } });
    fireEvent.submit(screen.getByRole('form', { name: 'Import a fictional document' }));  // jsdom cannot see test-assigned files for 'required'
    await waitFor(() => expect(onImported).toHaveBeenCalledWith(9));
    expect(calls).toEqual(['POST /api/sources', 'PUT /api/sources/9/document?filename=B01_lab.txt']);
  });
});
