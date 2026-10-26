/**
 * Mapping between the two kinds of document location. They are NOT interchangeable:
 * - a text-layer citation is page + character offsets into the server's pypdf text, verified by the backend;
 * - a rendered-page region is page + x,y,w,h percent of the page as pdf.js draws it.
 * A citation is drawn on the rendered page only when its quote matches exactly one run of pdf.js text items.
 */

/** A pdf.js text item converted to page-relative percentages (top-left origin). */
export type TextRun = { str: string; x: number; y: number; w: number; h: number };
export type Box = { x: number; y: number; w: number; h: number };
export type Mapping =
  | { status: 'mapped'; method: 'exact' | 'whitespace-insensitive'; boxes: Box[] }
  | { status: 'not_found' }
  | { status: 'ambiguous'; count: number }
  | { status: 'no_text' };

const collapse = (s: string) => s.replace(/\s+/g, ' ').trim();

function occurrences(haystack: string, needle: string): number[] {
  const out: number[] = [];
  if (!needle) return out;
  for (let i = haystack.indexOf(needle); i !== -1; i = haystack.indexOf(needle, i + 1)) out.push(i);
  return out;
}

/** Locates a quoted passage among rendered text runs; returns boxes only for a unique match. */
export function mapQuote(runs: TextRun[], quote: string): Mapping {
  if (!runs.some(r => r.str.trim())) return { status: 'no_text' };
  for (const method of ['exact', 'whitespace-insensitive'] as const) {
    const sep = method === 'exact' ? ' ' : '';
    let stream = '';
    const owner: number[] = [];
    runs.forEach((r, i) => {
      const piece = method === 'exact' ? collapse(r.str) : r.str.replace(/\s+/g, '');
      if (!piece) return;
      if (stream && sep) { stream += sep; owner.push(-1); }
      for (const ch of piece) { stream += ch; owner.push(i); }
    });
    const needle = method === 'exact' ? collapse(quote) : quote.replace(/\s+/g, '');
    const hits = occurrences(stream, needle);
    if (hits.length > 1) return { status: 'ambiguous', count: hits.length };
    if (hits.length === 1) {
      const idx = [...new Set(owner.slice(hits[0], hits[0] + needle.length).filter(o => o >= 0))];
      return { status: 'mapped', method, boxes: idx.map(i => ({ x: runs[i].x, y: runs[i].y, w: runs[i].w, h: runs[i].h })) };
    }
  }
  return { status: 'not_found' };
}

export function regionString(r: Box): string {
  const f = (n: number) => String(Math.round(n * 10) / 10);
  return [r.x, r.y, r.w, r.h].map(f).join(',');
}

export function parseRegionBox(region: string | null | undefined): Box | null {
  const parts = (region ?? '').split(',').map(Number);
  return parts.length === 4 && parts.every(Number.isFinite) ? { x: parts[0], y: parts[1], w: parts[2], h: parts[3] } : null;
}

/** Keeps a region inside the page with a positive size. */
export function clampBox(b: Box): Box {
  const w = Math.min(100, Math.max(0.5, b.w)), h = Math.min(100, Math.max(0.5, b.h));
  return { x: Math.min(100 - w, Math.max(0, b.x)), y: Math.min(100 - h, Math.max(0, b.y)), w, h };
}

/** Keyboard editing: arrows move the region, Shift+arrows resize it; step in percent of the page. */
export function nudge(b: Box, key: string, resize: boolean, step = 1): Box {
  const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[key];
  if (!d) return b;
  return clampBox(resize ? { ...b, w: b.w + d[0], h: b.h + d[1] } : { ...b, x: b.x + d[0], y: b.y + d[1] });
}
