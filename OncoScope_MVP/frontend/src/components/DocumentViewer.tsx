import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { post, request } from '../api';
import { provenanceLabel } from '../records';
import type { AnchorDraft, DocFocus, DocumentText, Source, TimelineEntry } from '../types';

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
  const [drag, setDrag] = useState<{ x: number; y: number; x2: number; y2: number } | null>(null);
  const focusRef = useRef<HTMLElement | null>(null);
  const isImage = source.document_media_type?.startsWith('image/') ?? false;
  const url = `/api/sources/${source.id}/document`;

  useEffect(() => {
    setDoc(null); setError(''); setSelection(null); setMessage('');
    if (!source.document_sha256 || isImage) return;
    request<DocumentText>(`/sources/${source.id}/document/text`).then(setDoc).catch(e => setError(e.message));
  }, [source.id, source.document_sha256, isImage]);

  const keyOf = (o: { entity: string; id: number }) => `${o.entity}-${o.id}`;
  useEffect(() => { setActive(focus?.recordKey ?? null); }, [focus]);
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

  function pct(e: MouseEvent<HTMLDivElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    return { x: Math.min(100, Math.max(0, (e.clientX - box.left) / box.width * 100)), y: Math.min(100, Math.max(0, (e.clientY - box.top) / box.height * 100)) };
  }
  function finishDrag() {
    if (!drag) return;
    const x = Math.min(drag.x, drag.x2), y = Math.min(drag.y, drag.y2), w = Math.abs(drag.x2 - drag.x), h = Math.abs(drag.y2 - drag.y);
    setDrag(null);
    if (w < 1 || h < 1) return;
    const r = (n: number) => Math.round(n * 10) / 10;
    const [rx, ry] = [r(x), r(y)];
    const region = `${rx},${ry},${Math.min(r(w), r(100 - rx))},${Math.min(r(h), r(100 - ry))}`;
    setSelection({ page: 1, start: null, end: null, quote: null, region });
  }

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
    {doc && <p className="caption">Showing the deterministic text layer ({doc.extractor}). The original file is unchanged; open it to compare layout. Highlighted passages are cited by records.</p>}
    {isImage && <p className="caption">Drag on the image to mark a region, then transcribe what it shows. Images have no text layer and are never parsed.</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {message && <p className="success" role="status">{message}</p>}
    <div className="doc-body">
      <div className="doc-pages">
        {isImage ? <div className="image-wrap" data-testid="image-canvas" onMouseDown={e => { e.preventDefault(); const p = pct(e); setDrag({ x: p.x, y: p.y, x2: p.x, y2: p.y }); }}
            onMouseMove={e => { if (drag) { const p = pct(e); setDrag(d => d && { ...d, x2: p.x, y2: p.y }); } }} onMouseUp={finishDrag} onMouseLeave={finishDrag}>
            <img src={url} alt={`Original fictional document ${source.document_filename}`} draggable={false} />
            {observations.filter(o => parseRegion(o.anchor_region)).map(o => { const r = parseRegion(o.anchor_region)!; const key = keyOf(o);
              return <div key={key} ref={active === key ? el => { focusRef.current = el; } : undefined} className={`region ${active === key ? 'focus' : ''}`} style={{ left: `${r.x}%`, top: `${r.y}%`, width: `${r.w}%`, height: `${r.h}%` }} title={`${o.kind} #${o.id}: ${o.label}`} />; })}
            {(drag || selection?.region) && (() => { const r = drag ? { x: Math.min(drag.x, drag.x2), y: Math.min(drag.y, drag.y2), w: Math.abs(drag.x2 - drag.x), h: Math.abs(drag.y2 - drag.y) } : parseRegion(selection!.region)!;
              return <div className="region pending" style={{ left: `${r.x}%`, top: `${r.y}%`, width: `${r.w}%`, height: `${r.h}%` }} />; })()}
          </div>
          : doc && (doc.has_text_layer ? doc.pages.map(p => <div key={p.page} className="doc-page"><div className="page-label">Page {p.page}</div>
            <pre data-testid={`doc-page-${p.page}`} onMouseUp={e => captureText(p.page, e.currentTarget)}>{segmentPage(p.text, anchoredOn(p.page)).map((seg, i) => seg.keys.length
              ? <mark key={i} ref={seg.keys.includes(active ?? '') ? el => { focusRef.current = el; } : undefined} className={seg.keys.includes(active ?? '') ? 'focus' : ''} title={seg.keys.map(k => { const o = labelFor(k); return o ? `${o.kind} #${o.id}: ${o.label}` : k; }).join('\n')} data-anchor={seg.keys.join(' ')}>{seg.text}</mark>
              : <span key={i}>{seg.text}</span>)}</pre></div>)
            : <p className="muted">This PDF has no text layer (for example a scan). Open the original and transcribe manually; anchor by page.</p>)}
      </div>
      <aside className="doc-side">
        <h3>Transcribe from this document</h3>
        {selection ? <div className="selection-box" data-testid="selection">
          <div className="tiny muted">{selection.quote !== null ? `Page ${selection.page}, characters ${selection.start}–${selection.end}` : `Region ${selection.region} (x,y,w,h %)`}</div>
          {selection.quote !== null && <blockquote>{selection.quote}</blockquote>}
          <div className="transcribe-row">{transcribeKinds.map(k => <button key={k} type="button" className="text-button" onClick={() => transcribe(k)}>As {k.toLowerCase()}</button>)}</div>
          <button type="button" className="text-button" onClick={() => setSelection(null)}>Clear selection</button>
        </div> : <p className="muted tiny">{isImage ? 'Drag a region on the image.' : 'Select the exact passage in the text with the mouse.'} The record will cite that location.</p>}
        <h3>Observations citing this source ({observations.length})</h3>
        <ul className="obs-list">{observations.map(o => { const key = keyOf(o); return <li key={key} className={active === key ? 'focus' : ''}>
          <button type="button" className="text-button" onClick={() => setActive(key)} disabled={o.anchor_start === null && !o.anchor_region}>{o.kind} #{o.id}</button> {o.label}
          <div className="tiny muted">{provenanceLabel[o.provenance]} · {o.anchor_start !== null ? `p.${o.anchor_page} chars ${o.anchor_start}–${o.anchor_end}` : o.anchor_region ? `region ${o.anchor_region}` : 'no location recorded'}</div></li>; })}</ul>
      </aside>
    </div>
  </section>;
}
