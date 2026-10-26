import { describe, expect, it } from 'vitest';
import { formatErrorDetail } from './api';
import { changedFields, formatDifference, formatNumber, toDraft } from './records';
import type { Lab, Measurement } from './types';

const meta = { uid: 'u', revision: 1, verification_status: 'entered' as const, entered_at: '2026-09-01T00:00:00Z', updated_at: null };
const evidence = { provenance: 'transcribed' as const, anchor_page: null, anchor_quote: null, anchor_start: null, anchor_end: null, anchor_region: null };
const lab: Lab = { id: 1, date: '2026-09-01', test: 'CA-125', value: 87.5, unit: 'U/mL', source_id: 2, ...meta, ...evidence };
const measurement: Measurement = { id: 4, study_id: 1, lesion_key: 'demo-L1', site: 'Fictional', long_axis_mm: 41.3, short_axis_mm: 20, notes: '', source_id: 3, ...meta, ...evidence };

describe('changedFields', () => {
  it('sends nothing when the draft is unchanged', () => {
    expect(changedFields('labs', lab, toDraft('labs', lab))).toEqual({});
  });
  it('sends only edited fields, typed for the API', () => {
    const draft = { ...toDraft('labs', lab), value: '78.5', source_id: '5' };
    expect(changedFields('labs', lab, draft)).toEqual({ value: 78.5, source_id: 5 });
  });
  it('clears nullable fields with null instead of 0 or empty string', () => {
    const draft = { ...toDraft('measurements', measurement), short_axis_mm: '' };
    expect(changedFields('measurements', measurement, draft)).toEqual({ short_axis_mm: null });
  });
  it('treats whitespace-only edits as no change', () => {
    expect(changedFields('labs', lab, { ...toDraft('labs', lab), unit: ' U/mL ' })).toEqual({});
  });
});

describe('number display', () => {
  it('never uses exponent notation', () => {
    expect(formatNumber(1234567)).toBe('1234567');
    expect(formatNumber(0.0000012)).toBe('0.0000012');
    expect(formatNumber(38)).toBe('38');
    expect(formatNumber(null)).toBe('—');
  });
  it('signs differences explicitly', () => {
    expect(formatDifference(-6.2)).toBe('-6.2');
    expect(formatDifference(3)).toBe('+3');
    expect(formatDifference(0)).toBe('0');
    expect(formatDifference(null)).toBe('—');
  });
});

describe('formatErrorDetail', () => {
  it('flattens FastAPI validation lists into readable text', () => {
    expect(formatErrorDetail([{ loc: ['body', 'long_axis_mm'], msg: 'Input should be greater than 0' }]))
      .toBe('long_axis_mm: Input should be greater than 0');
    expect(formatErrorDetail([{ loc: ['body'], msg: 'Value error, Unit is not accepted' }])).toBe('Unit is not accepted');
    expect(formatErrorDetail('Source #9 must exist')).toBe('Source #9 must exist');
  });
});
