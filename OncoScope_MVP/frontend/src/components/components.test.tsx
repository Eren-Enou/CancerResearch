import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { LabChart } from './LabChart';
import { RecordTools } from './RecordTools';
import type { Lab, LabSeries, Source } from '../types';

const meta = { uid: 'u', revision: 1, verification_status: 'entered' as const, entered_at: '2026-09-01T00:00:00Z', updated_at: null };
const lab = (id: number, date: string, value: number, unit = 'U/mL'): Lab => ({ id, date, test: 'CA-125', value, unit, source_id: 1, ...meta });
const sources: Source[] = [{ id: 1, label: 'Fictional printout', kind: 'lab', source_date: null, note: '', origin: '', document_sha256: null, document_filename: null, document_media_type: null, document_size: null, ...meta }];

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('LabChart', () => {
  const series: LabSeries = { test: 'CA-125', unit: 'U/mL', available_units: ['U/mL', 'kU/L'], note: '',
    points: [lab(2, '2026-09-20', 70), lab(1, '2026-09-01', 87.5)], excluded: [lab(3, '2026-09-10', 80, 'kU/L')] };

  it('plots exactly the series points, in date order, with their stored values', () => {
    const { container } = render(<LabChart series={series} />);
    const points = [...container.querySelectorAll('[data-testid="chart-point"]')];
    expect(points.map(p => [p.getAttribute('data-date'), p.getAttribute('data-value'), p.getAttribute('data-unit')]))
      .toEqual([['2026-09-01', '87.5', 'U/mL'], ['2026-09-20', '70', 'U/mL']]);
  });

  it('spaces points by calendar date, not by index', () => {
    const three = { ...series, excluded: [], points: [lab(1, '2026-09-01', 10), lab(2, '2026-09-02', 11), lab(3, '2026-09-30', 12)] };
    const { container } = render(<LabChart series={three} />);
    const xs = [...container.querySelectorAll('circle')].map(c => Number(c.getAttribute('cx')));
    expect(xs[1] - xs[0]).toBeLessThan((xs[2] - xs[1]) / 10);
  });

  it('names excluded mixed-unit results instead of hiding them', () => {
    render(<LabChart series={series} />);
    expect(screen.getByRole('note').textContent).toContain('2026-09-10 80 kU/L (lab #3)');
  });
});

describe('RecordTools', () => {
  it('sends only the changed field with the reason, then shows original and corrected values in history', async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
      calls.push({ url: String(url), init });
      const body = String(url).endsWith('/history')
        ? [{ id: 2, entity_type: 'labs', entity_id: 1, patient_id: 1, action: 'correct', reason: 'Digits swapped', recorded_at: '2026-10-01T00:00:00Z', entry_hash: 'h',
             detail_json: JSON.stringify({ before: { value: 87.5 }, after: { value: 78.5 }, changed_fields: ['value'] }) }]
        : { ...lab(1, '2026-09-01', 78.5), revision: 2 };
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    const onSaved = vi.fn();
    const record = lab(1, '2026-09-01', 87.5);
    render(<RecordTools entity="labs" record={record} sources={sources} studies={[]} onSaved={onSaved} />);
    fireEvent.click(screen.getByRole('button', { name: 'Correct lab #1' }));
    fireEvent.change(screen.getByLabelText('Value'), { target: { value: '78.5' } });
    fireEvent.change(screen.getByLabelText('Reason for change (required)'), { target: { value: 'Digits swapped' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save correction' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(calls[0].url).toBe('/api/records/labs/1');
    expect(calls[0].init?.method).toBe('PATCH');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ changes: { value: 78.5 }, reason: 'Digits swapped' });

    fireEvent.click(screen.getByRole('button', { name: 'History of lab #1' }));
    const change = await screen.findByTestId('audit-change');
    expect(change.querySelector('del')?.textContent).toBe('87.5');
    expect(change.querySelector('ins')?.textContent).toBe('78.5');
  });

  it('shows the server rejection and does not report success', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: [{ loc: ['body'], msg: "Value error, Unit 'u/ml' is not accepted for CA-125" }] }), { status: 422 }));
    const onSaved = vi.fn();
    render(<RecordTools entity="labs" record={lab(1, '2026-09-01', 87.5)} sources={sources} studies={[]} onSaved={onSaved} />);
    fireEvent.click(screen.getByRole('button', { name: 'Correct lab #1' }));
    fireEvent.change(screen.getByLabelText('Unit (as written in source)'), { target: { value: 'u/ml' } });
    fireEvent.change(screen.getByLabelText('Reason for change (required)'), { target: { value: 'Fictional' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save correction' }));
    expect((await screen.findByRole('alert')).textContent).toContain("Unit 'u/ml' is not accepted");
    expect(onSaved).not.toHaveBeenCalled();
  });
});
