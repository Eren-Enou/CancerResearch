import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { clampBox, mapQuote, nudge, parseRegionBox, regionString, type TextRun } from './pdfMapping';
import { RegionSelector } from './components/RegionSelector';

afterEach(() => cleanup());
const run = (str: string, y: number, x = 10): TextRun => ({ str, x, y, w: 20, h: 1.4 });

describe('mapQuote: text-layer citation to rendered page', () => {
  it('maps a unique match to the boxes of the items it covers', () => {
    const runs = [run('CA-125', 50), run('202', 50, 35), run('U/mL', 50, 50), run('Report date: 2026-09-19', 40)];
    const m = mapQuote(runs, 'CA-125   202   U/mL');          // pypdf spacing differs from pdf.js items
    expect(m).toEqual({ status: 'mapped', method: 'exact', boxes: [runs[0], runs[1], runs[2]].map(({ x, y, w, h }) => ({ x, y, w, h })) });
  });
  it('refuses to highlight ambiguous or absent passages', () => {
    const runs = [run('CA-125 202 U/mL', 50), run('CA-125 202 U/mL', 60)];
    expect(mapQuote(runs, 'CA-125 202 U/mL')).toEqual({ status: 'ambiguous', count: 2 });
    expect(mapQuote(runs, 'CA-125 999 U/mL')).toEqual({ status: 'not_found' });
    expect(mapQuote([], 'anything')).toEqual({ status: 'no_text' });
  });
  it('matches glued text only when the whitespace-free match is unique', () => {
    const runs = [run('U/mL', 50, 60), run('88', 50, 45), run('CA-125', 50, 10)];
    const m = mapQuote(runs, 'U/mL88CA-125');
    expect(m.status).toBe('mapped');
    expect(m.status === 'mapped' && m.method).toBe('whitespace-insensitive');
  });
});

describe('regions', () => {
  it('round-trips and clamps', () => {
    expect(regionString({ x: 10.04, y: 20, w: 30.26, h: 5 })).toBe('10,20,30.3,5');
    expect(parseRegionBox('1,2,3')).toBeNull();
    expect(clampBox({ x: 95, y: -3, w: 20, h: 0 })).toEqual({ x: 80, y: 0, w: 20, h: 0.5 });
  });
  it('moves with arrows and resizes with Shift+arrows', () => {
    const b = { x: 10, y: 10, w: 30, h: 8 };
    expect(nudge(b, 'ArrowRight', false)).toEqual({ x: 11, y: 10, w: 30, h: 8 });
    expect(nudge(b, 'ArrowDown', true)).toEqual({ x: 10, y: 10, w: 30, h: 9 });
    expect(nudge(b, 'Enter', false)).toBe(b);
  });
  it('can be operated entirely by keyboard', () => {
    let region: string | null = null;
    const onChange = vi.fn((r: string | null) => { region = r; });
    const { rerender } = render(<RegionSelector label="page 1" region={region} onChange={onChange} overlays={[]}><div style={{ width: 100, height: 100 }} /></RegionSelector>);
    fireEvent.click(screen.getByRole('button', { name: 'Mark a region with the keyboard' }));
    expect(region).toBe('10,10,30,8');
    rerender(<RegionSelector label="page 1" region={region} onChange={onChange} overlays={[]}><div /></RegionSelector>);
    fireEvent.keyDown(screen.getByTestId('selected-region'), { key: 'ArrowRight' });
    expect(region).toBe('11,10,30,8');
    rerender(<RegionSelector label="page 1" region={region} onChange={onChange} overlays={[]}><div /></RegionSelector>);
    fireEvent.keyDown(screen.getByTestId('selected-region'), { key: 'ArrowRight', shiftKey: true });
    expect(region).toBe('11,10,31,8');
    rerender(<RegionSelector label="page 1" region={region} onChange={onChange} overlays={[]}><div /></RegionSelector>);
    fireEvent.change(screen.getByLabelText('Region h percent on page 1'), { target: { value: '12' } });
    expect(region).toBe('11,10,31,12');
  });
});
