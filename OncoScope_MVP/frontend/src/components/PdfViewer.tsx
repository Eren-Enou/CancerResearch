import { useEffect, useRef, useState } from 'react';
import * as pdfjs from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { mapQuote, parseRegionBox, type Mapping, type TextRun } from '../pdfMapping';
import type { TimelineEntry } from '../types';
import { RegionSelector, type Overlay } from './RegionSelector';

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

type Props = {
  url: string; expectedSha256: string; observations: TimelineEntry[]; active: string | null;
  selection: { page: number | null; region: string | null } | null; onRegion: (page: number, region: string | null) => void;
};
type PageInfo = { num: number; width: number; height: number; runs: TextRun[] };
const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3];

async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', data);
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('');
}

/** How each citation on this document relates to the rendered pages; computed per observation. */
export function citationMappings(observations: TimelineEntry[], pages: PageInfo[]) {
  return observations.filter(o => o.anchor_page !== null && (o.anchor_quote !== null || o.anchor_region !== null)).map(o => {
    const page = pages[(o.anchor_page ?? 1) - 1];
    const key = `${o.entity}-${o.id}`;
    if (o.anchor_region) return { key, o, kind: 'drawn' as const, mapping: null };
    const mapping: Mapping = page ? mapQuote(page.runs, o.anchor_quote!) : { status: 'not_found' };
    return { key, o, kind: 'quote' as const, mapping };
  });
}

export function PdfViewer({ url, expectedSha256, observations, active, selection, onRegion }: Props) {
  const [doc, setDoc] = useState<pdfjs.PDFDocumentProxy | null>(null);
  const [pages, setPages] = useState<PageInfo[]>([]);
  const [zoom, setZoom] = useState(1);
  const [current, setCurrent] = useState(1);
  const [status, setStatus] = useState<'loading' | 'verified' | 'mismatch' | 'error'>('loading');
  const [error, setError] = useState('');
  const pageRefs = useRef<Record<number, HTMLDivElement | null>>({});

  useEffect(() => {
    let cancelled = false;
    let task: pdfjs.PDFDocumentLoadingTask | null = null;
    setStatus('loading'); setPages([]); setDoc(null);
    (async () => {
      try {
        const bytes = await (await fetch(url)).arrayBuffer();
        // The browser re-hashes the exact bytes it is about to render; a mismatch stops rendering.
        if ((await sha256Hex(bytes)) !== expectedSha256) { if (!cancelled) setStatus('mismatch'); return; }
        task = pdfjs.getDocument({ data: new Uint8Array(bytes), enableXfa: false });
        const loaded = await task.promise;
        const infos: PageInfo[] = [];
        for (let n = 1; n <= loaded.numPages; n++) {
          const page = await loaded.getPage(n);
          const vp = page.getViewport({ scale: 1 });
          const content = await page.getTextContent();
          const runs: TextRun[] = [];
          for (const item of content.items) {
            if (!('str' in item) || !item.str) continue;
            const [, , , , e, f] = item.transform as number[];
            const [x1, y1] = vp.convertToViewportPoint(e, f);
            const [x2, y2] = vp.convertToViewportPoint(e + item.width, f + (item.height || 10));
            runs.push({ str: item.str, x: Math.min(x1, x2) / vp.width * 100, y: Math.min(y1, y2) / vp.height * 100,
              w: Math.abs(x2 - x1) / vp.width * 100, h: Math.abs(y2 - y1) / vp.height * 100 });
          }
          infos.push({ num: n, width: vp.width, height: vp.height, runs });
        }
        if (!cancelled) { setDoc(loaded); setPages(infos); setStatus('verified'); }
      } catch (e) { if (!cancelled) { setStatus('error'); setError((e as Error).message); } }
    })();
    return () => { cancelled = true; task?.destroy(); };
  }, [url, expectedSha256]);

  const mappings = citationMappings(observations, pages);
  useEffect(() => {
    const target = mappings.find(m => m.key === active);
    if (target?.o.anchor_page) goTo(target.o.anchor_page);
  }, [active, pages.length]); // eslint-disable-line react-hooks/exhaustive-deps

  function goTo(n: number) {
    const page = Math.min(Math.max(1, n), pages.length || 1);
    setCurrent(page);
    pageRefs.current[page]?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }

  if (status === 'mismatch') return <p className="error" role="alert">The downloaded file does not match its recorded SHA-256; it is not displayed.</p>;
  if (status === 'error') return <p className="error" role="alert">This PDF could not be rendered: {error}</p>;
  if (status === 'loading') return <p className="muted">Loading and verifying the original PDF…</p>;

  return <div className="pdf-viewer">
    <div className="pdf-toolbar" role="toolbar" aria-label="PDF controls">
      <button type="button" className="secondary" onClick={() => goTo(current - 1)} disabled={current <= 1} aria-label="Previous page">‹</button>
      <label className="page-input">Page <input type="number" min={1} max={pages.length} value={current} aria-label="Page number"
        onChange={e => goTo(Number(e.target.value))} /> / {pages.length}</label>
      <button type="button" className="secondary" onClick={() => goTo(current + 1)} disabled={current >= pages.length} aria-label="Next page">›</button>
      <span className="toolbar-gap" />
      <button type="button" className="secondary" onClick={() => setZoom(z => ZOOMS[Math.max(0, ZOOMS.indexOf(z) - 1)])} aria-label="Zoom out">−</button>
      <span aria-live="polite" data-testid="zoom-level">{Math.round(zoom * 100)}%</span>
      <button type="button" className="secondary" onClick={() => setZoom(z => ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(z) + 1)])} aria-label="Zoom in">+</button>
      <span className="tiny muted" data-testid="pdf-integrity">Rendered with pdf.js · SHA-256 re-verified in this browser</span>
    </div>
    <ul className="mapping-list" aria-label="Citation locations on the rendered pages">
      {mappings.map(m => <li key={m.key} data-testid="citation-mapping" data-key={m.key} data-status={m.kind === 'drawn' ? 'drawn' : m.mapping!.status}
        className={active === m.key ? 'focus' : ''}>
        <strong>{m.o.kind} #{m.o.id}</strong> p.{m.o.anchor_page}: {m.kind === 'drawn' ? 'region drawn on the rendered page (exact)'
          : m.mapping!.status === 'mapped' ? `text-layer citation matched once on the rendered page (${m.mapping!.method}; highlight covers whole text items)`
          : m.mapping!.status === 'ambiguous' ? `text-layer citation matches ${m.mapping!.count} places on the rendered page — not highlighted`
          : m.mapping!.status === 'no_text' ? 'the rendered page has no text items — not highlighted'
          : 'text-layer citation not found on the rendered page — not highlighted; see the text layer view'}
      </li>)}
    </ul>
    <div className="pdf-pages" onScroll={e => {
      // Keep the page indicator in step with scrolling: the last page whose top has passed the viewport's top.
      const top = e.currentTarget.getBoundingClientRect().top + 40;
      const visible = pages.filter(p => (pageRefs.current[p.num]?.getBoundingClientRect().top ?? Infinity) <= top).pop();
      setCurrent(visible?.num ?? 1);
    }}>
      {doc && pages.map(p => {
        const overlays: Overlay[] = [];
        for (const m of mappings.filter(m => m.o.anchor_page === p.num)) {
          if (m.kind === 'drawn') {
            const box = parseRegionBox(m.o.anchor_region);
            if (box) overlays.push({ key: m.key, box, kind: 'drawn', focus: m.key === active, title: `${m.o.kind} #${m.o.id}: drawn region` });
          } else if (m.mapping?.status === 'mapped') {
            m.mapping.boxes.forEach((box, i) => overlays.push({ key: `${m.key}-${i}`, box, kind: 'mapped', focus: m.key === active,
              title: `${m.o.kind} #${m.o.id}: mapped from the text-layer citation` }));
          }
        }
        return <div key={p.num} ref={el => { pageRefs.current[p.num] = el; }} className="pdf-page" data-testid={`pdf-page-${p.num}`}>
          <div className="page-label">Page {p.num}</div>
          <RegionSelector label={`page ${p.num}`} region={selection?.page === p.num ? selection.region : null}
            onChange={r => onRegion(p.num, r)} overlays={overlays}>
            <PdfCanvas doc={doc} num={p.num} zoom={zoom} width={p.width} height={p.height} />
          </RegionSelector>
        </div>;
      })}
    </div>
  </div>;
}

function PdfCanvas({ doc, num, zoom, width, height }: { doc: pdfjs.PDFDocumentProxy; num: number; zoom: number; width: number; height: number }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    let task: pdfjs.RenderTask | null = null;
    let cancelled = false;
    doc.getPage(num).then(page => {
      if (cancelled || !ref.current) return;
      const ratio = window.devicePixelRatio || 1;
      const viewport = page.getViewport({ scale: zoom * ratio });
      ref.current.width = viewport.width;
      ref.current.height = viewport.height;
      task = page.render({ canvas: ref.current, viewport });
      task.promise.catch(() => undefined);
    });
    return () => { cancelled = true; task?.cancel(); };
  }, [doc, num, zoom]);
  return <canvas ref={ref} style={{ width: width * zoom, height: height * zoom }} aria-label={`Rendered page ${num}`} role="img" data-testid={`pdf-canvas-${num}`} />;
}
