import { useEffect, useRef, useState } from 'react';
import { post, request } from '../api';
import { provenanceLabel } from '../records';
import type { AnchorDraft, DocFocus, DocumentText, Source, TimelineEntry } from '../types';
import { parseRegionBox } from '../pdfMapping';
import { PdfViewer } from './PdfViewer';
import { RegionSelector, type Overlay } from './RegionSelector';

type Props = {
  source: Source; observations: TimelineEntry[]; focus: DocFocus | null;
  onTranscribe: (draft: AnchorDraft) => void; onExtracted: (created: number) => void;
};
type Selection = { page: number | null; start: number | null; end: number | null; quote: string | null; region: string | null };
const transcribeKinds = ['Lab', 'Imaging study', 'Lesion measurement', 'Treatment', 'Event'];

function offsetIn(container: Node, node: Node, offset: number): number {
  const range = document.createRange();
  range.selectNodeContents(container);
  range.setEnd(node, offset);
  return range.toString().length;
}

export function parseRegion(region: string | null | undefined) {
  const parts = (region ?? '').split(',').map(Number);
  return parts.length === 4 && parts.every(n => Number.isFinite(n)) ? { x: parts[0], y: parts[1], w: parts[2], h: parts[3] } : null;
}

/** Splits page text into plain and highlighted segments for the observations anchored on it (first wins on overlap). */
export function segmentPage(text: string, anchors: { start: number; end: number; key: string }[]) {
  const sorted = [...anchors].filter(a => a.start >= 0 && a.end <= text.length && a.end > a.start).sort((a, b) => a.start - b.start);
  const out: { text: string; keys: string[] }[] = [];
  let cursor = 0;
  for (const a of sorted) {
    if (a.start < cursor) { out[out.length - 1]?.keys.push(a.key); continue; }
    if (a.start > cursor) out.push({ text: text.slice(cursor, a.start), keys: [] });
    out.push({ text: text.slice(a.start, a.end), keys: [a.key] });
    cursor = a.end;
  }
  if (cursor < text.length) out.push({ text: text.slice(cursor), keys: [] });
  return out;
}

export function DocumentViewer({ source, observations, focus, onTranscribe, onExtracted }: Props) {
  const [doc, setDoc] = useState<DocumentText | null>(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [selection, setSelection] = useState<Selection | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const focusRef = useRef<HTMLElement | null>(null);
  const isImage = source.document_media_type?.startsWith('image/') ?? false;
  const isPdf = source.document_media_type === 'application/pdf';
  // PDFs open on the rendered pages; following a text-layer citation opens the exact text-layer view instead.
  const [mode, setMode] = useState<'rendered' | 'text'>('rendered');
  const url = `/api/sources/${source.id}/document`;

  useEffect(() => {
    setDoc(null); setError(''); setSelection(null); setMessage('');
    if (!source.document_sha256 || isImage) return;
    request<DocumentText>(`/sources/${source.id}/document/text`).then(setDoc).catch(e => setError(e.message));
  }, [source.id, source.document_sha256, isImage]);

  const keyOf = (o: { entity: string; id: number }) => `${o.entity}-${o.id}`;
  useEffect(() => {
    setActive(focus?.recordKey ?? null);
    const target = observations.find(o => keyOf(o) === focus?.recordKey);
    setMode(target && target.anchor_quote !== null ? 'text' : 'rendered');
  }, [focus]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { focusRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' }); }, [active, doc]);

  function captureText(page: number, container: HTMLElement) {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !container.contains(sel.anchorNode) || !container.contains(sel.focusNode)) return;
    const range = sel.getRangeAt(0);
    let start = offsetIn(container, range.startContainer, range.startOffset);
    let end = offsetIn(container, range.endContainer, range.endOffset);
    const raw = container.textContent!.slice(start, end);
    start += raw.length - raw.trimStart().length;
    end -= raw.length - raw.trimEnd().length;
    if (end > start) setSelection({ page, start, end, quote: container.textContent!.slice(start, end), region: null });
  }

  const setRegion = (page: number, region: string | null) =>
    setSelection(region ? { page, start: null, end: null, quote: null, region } : null);
  const imageOverlays: Overlay[] = observations.flatMap(o => {
    const box = parseRegionBox(o.anchor_region);
    return box ? [{ key: keyOf(o), box, kind: 'drawn' as const, focus: active === keyOf(o), title: `${o.kind} #${o.id}: ${o.label}` }] : [];
  });

  async function extract() {
    setError(''); setMessage('');
    try {
      const result = await post<{ created: unknown[]; existing: unknown[] }>(`/sources/${source.id}/extract`, {});
      setMessage(`${result.created.length} new proposal(s) added to the review queue${result.existing.length ? `; ${result.existing.length} already proposed` : ''}. Nothing is recorded until you accept it.`);
      onExtracted(result.created.length);
    } catch (e) { setError((e as Error).message); }
  }

  const transcribe = (kind: string) => selection && onTranscribe({ kind, source_id: source.id, anchor_page: selection.page,
    anchor_quote: selection.quote, anchor_start: selection.start, anchor_end: selection.end, anchor_region: selection.region });

  if (!source.document_sha256) return <section className="panel"><h2>{source.label}</h2><p className="muted">No document attached to source #{source.id}. Attach the fictional original in Sources to view and anchor it.</p></section>;
  const anchoredOn = (page: number) => observations.filter(o => o.anchor_page === page && o.anchor_start !== null && o.anchor_end !== null)
    .map(o => ({ start: o.anchor_start!, end: o.anchor_end!, key: keyOf(o) }));
  const labelFor = (key: string) => observations.find(o => keyOf(o) === key);

  return <section className="panel doc-viewer" aria-label={`Document viewer for source #${source.id}`}>
    <div className="section-heading"><div><div className="eyebrow">SOURCE #{source.id} · {source.kind}</div><h2>{source.label}</h2>
      <p className="muted tiny">{source.document_filename} · {source.document_media_type} · SHA-256 <span className="hash-inline">{source.document_sha256}</span></p></div>
      <div className="doc-actions"><a className="export-link" href={url} target="_blank" rel="noreferrer">Open original file ↗</a>
        {doc?.has_text_layer && <button className="export-link" type="button" onClick={extract}>Propose CA-125 values</button>}</div></div>
    {isPdf && <div className="view-tabs" role="tablist" aria-label="Document view">
      <button type="button" role="tab" aria-selected={mode === 'rendered'} className={mode === 'rendered' ? 'active' : ''} onClick={() => setMode('rendered')}>Rendered pages</button>
      <button type="button" role="tab" aria-selected={mode === 'text'} className={mode === 'text' ? 'active' : ''} onClick={() => setMode('text')}>Text layer</button></div>}
    {doc && (!isPdf || mode === 'text') && <p className="caption">Showing the deterministic text layer ({doc.extractor}). Character positions here are what citations and the report refer to; they are not page coordinates. Highlighted passages are cited by records.</p>}
    {isPdf && mode === 'rendered' && <p className="caption">Showing the original pages rendered by pdf.js. Drag (or use the keyboard) to mark an evidence region on a page. Text-layer citations are highlighted here only where they match exactly one place.</p>}
    {isImage && <p className="caption">Drag on the image, or use the keyboard, to mark a region, then transcribe what it shows. Images have no text layer and are never parsed.</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {message && <p className="success" role="status">{message}</p>}
    <div className="doc-body">
      <div className="doc-pages">
        {isImage ? <div className="image-wrap" data-testid="image-canvas">
            <RegionSelector label="image" region={selection?.region ?? null} onChange={r => setRegion(1, r)} overlays={imageOverlays}>
              <img src={url} alt={`Original fictional document ${source.document_filename}`} draggable={false} />
            </RegionSelector></div>
          : isPdf && mode === 'rendered' ? <PdfViewer url={url} expectedSha256={source.document_sha256} observations={observations} active={active}
              selection={selection?.region ? { page: selection.page, region: selection.region } : null} onRegion={setRegion} />
          : doc && (doc.has_text_layer ? doc.pages.map(p => <div key={p.page} className="doc-page"><div className="page-label">Page {p.page}</div>
            <pre data-testid={`doc-page-${p.page}`} onMouseUp={e => captureText(p.page, e.currentTarget)}>{segmentPage(p.text, anchoredOn(p.page)).map((seg, i) => seg.keys.length
              ? <mark key={i} ref={seg.keys.includes(active ?? '') ? el => { focusRef.current = el; } : undefined} className={seg.keys.includes(active ?? '') ? 'focus' : ''} title={seg.keys.map(k => { const o = labelFor(k); return o ? `${o.kind} #${o.id}: ${o.label}` : k; }).join('\n')} data-anchor={seg.keys.join(' ')}>{seg.text}</mark>
              : <span key={i}>{seg.text}</span>)}</pre></div>)
            : <p className="muted">This PDF has no text layer (for example a scan). Open the original and transcribe manually; anchor by page.</p>)}
      </div>
      <aside className="doc-side">
        <h3>Transcribe from this document</h3>
        {selection ? <div className="selection-box" data-testid="selection">
          <div className="tiny muted">{selection.quote !== null ? `Text layer: page ${selection.page}, characters ${selection.start}–${selection.end}` : `Rendered page ${selection.page}: region ${selection.region} (x,y,w,h %)`}</div>
          {selection.quote !== null && <blockquote>{selection.quote}</blockquote>}
          <div className="transcribe-row">{transcribeKinds.map(k => <button key={k} type="button" className="text-button" onClick={() => transcribe(k)}>As {k.toLowerCase()}</button>)}</div>
          <button type="button" className="text-button" onClick={() => setSelection(null)}>Clear selection</button>
        </div> : <p className="muted tiny">{isImage || (isPdf && mode === 'rendered') ? 'Drag a region on the page, or press “Mark a region with the keyboard”.' : 'Select the exact passage in the text with the mouse.'} The record will cite that location.</p>}
        <h3>Observations citing this source ({observations.length})</h3>
        <ul className="obs-list">{observations.map(o => { const key = keyOf(o); return <li key={key} className={active === key ? 'focus' : ''}>
          <button type="button" className="text-button" onClick={() => setActive(key)} disabled={o.anchor_start === null && !o.anchor_region}>{o.kind} #{o.id}</button> {o.label}
          <div className="tiny muted">{provenanceLabel[o.provenance]} · {o.anchor_start !== null ? `p.${o.anchor_page} chars ${o.anchor_start}–${o.anchor_end}` : o.anchor_region ? `region ${o.anchor_region}` : 'no location recorded'}</div></li>; })}</ul>
      </aside>
    </div>
  </section>;
}
