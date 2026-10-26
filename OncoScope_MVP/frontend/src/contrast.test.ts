import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/** WCAG 2.x contrast; every text colour must reach AA (4.5:1) on every background it can sit on. */
const channel = (v: number) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
function luminance(hex: string) {
  const h = hex.replace('#', '');
  const full = h.length === 3 ? [...h].map(c => c + c).join('') : h;
  const [r, g, b] = [0, 2, 4].map(i => parseInt(full.slice(i, i + 2), 16) / 255);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}
export function contrast(a: string, b: string) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const DARK_BACKGROUNDS = ['#0b1323', '#121f31', '#16253a', '#1b2a3d', '#173339', '#0c1525', '#223149', '#2a2316', '#2a1717', '#4a2020', '#3a3115', '#1c383c'];
const LIGHT_BACKGROUNDS = ['#67d0bf', '#f4f1e8', '#ffe08a', '#7fe3d2', '#ffffff'];

function files(dir: string, ext: string): string[] {
  return readdirSync(dir).flatMap(name => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path, ext) : path.endsWith(ext) ? [path] : [];
  });
}

describe('colour contrast', () => {
  it('computes known WCAG values', () => {
    expect(contrast('#000000', '#ffffff')).toBeCloseTo(21, 1);
    expect(contrast('#767676', '#ffffff')).toBeGreaterThanOrEqual(4.5);
  });
  it('every text colour in the stylesheet and SVG labels meets AA on its possible backgrounds', () => {
    const css = readFileSync('src/styles.css', 'utf-8');
    const tsx = files('src', '.tsx').map(f => readFileSync(f, 'utf-8')).join('\n');
    const colours = new Set([...css.matchAll(/(?<!-)color:(#[0-9a-fA-F]{3,6})/g)].map(m => m[1]));
    for (const m of tsx.matchAll(/<text[^>]*fill="(#[0-9a-fA-F]{6})"/g)) colours.add(m[1]);
    const failures: string[] = [];
    for (const c of colours) {
      const backgrounds = luminance(c) < 0.2 ? LIGHT_BACKGROUNDS : DARK_BACKGROUNDS;
      for (const bg of backgrounds) if (contrast(c, bg) < 4.5) failures.push(`${c} on ${bg}: ${contrast(c, bg).toFixed(2)}`);
    }
    expect(failures).toEqual([]);
  });
});
