import { useEffect, useState, type FormEvent } from 'react';
import { post, request } from '../api';
import { formatNumber } from '../records';
import type { DocumentText, ExtractedFields, Extraction, Source } from '../types';

type Props = { patientId: number; sources: Source[]; refresh: number; onDecided: () => void; onOpenDocument: (sourceId: number, recordKey?: string) => void };
const FIELDS = ['date', 'test', 'value', 'unit'] as const;
type Field = typeof FIELDS[number];
// Which field each parser issue concerns, so the reviewer sees uncertainty next to the value.
const ISSUE_FIELD: Record<string, Field> = {
  value_not_found: 'value', value_qualifier: 'value', value_separator: 'value', line_qualifier: 'value',
  unit_missing: 'unit', unit_not_accepted: 'unit', date_missing: 'date', date_ambiguous: 'date', date_unrecognised: 'date',
  date_after_report: 'date', date_after_source: 'date', date_in_future: 'date',
};
const show = (v: unknown) => v === null || v === undefined || v === '' ? '—' : typeof v === 'number' ? formatNumber(v) : String(v);

/** The page text around a proposal: its own line plus one line either side, with the extracted passage marked. */
export function excerpt(text: string, start: number, end: number) {
  const lineStart = text.lastIndexOf('\n', Math.max(0, text.lastIndexOf('\n', start - 1) - 1)) + 1;
  let lineEnd = text.indexOf('\n', text.indexOf('\n', end) + 1);
  if (lineEnd === -1) lineEnd = text.length;
  return { before: text.slice(lineStart, start), match: text.slice(start, end), after: text.slice(end, lineEnd) };
}

export function ReviewQueue({ patientId, sources, refresh, onDecided, onOpenDocument }: Props) {
  const [items, setItems] = useState<Extraction[] | null>(null);
  const [texts, setTexts] = useState<Record<number, DocumentText>>({});
  const [filter, setFilter] = useState<'pending' | 'decided' | 'all'>('pending');
  useEffect(() => {
    request<Extraction[]>(`/patients/${patientId}/extractions`).then(async all => {
      setItems(all);
      const ids = [...new Set(all.map(e => e.source_id))];
      const loaded = await Promise.all(ids.map(id => request<DocumentText>(`/sources/${id}/document/text`).catch(() => null)));
      setTexts(Object.fromEntries(loaded.filter(Boolean).map(t => [t!.source_id, t!])));
    }).catch(() => setItems([]));
  }, [patientId, refresh]);
  if (items === null) return <section className="panel"><h2>Values found in documents — check before saving</h2><p className="muted">Loading proposals…</p></section>;
  const shown = items.filter(e => filter === 'all' || (filter === 'pending' ? e.status === 'pending' : e.status !== 'pending'));
  const pending = items.filter(e => e.status === 'pending').length;
  return <section className="panel">
    <div className="section-heading"><div><h2>Values found in documents — check before saving</h2>
      <p className="muted">Rule-based proposals from fictional lab documents ({items[0] ? `${items[0].parser} v${items[0].parser_version}` : 'ca125-lab'}). Compare each with its original text, then accept, correct, or reject it.</p></div>
      <label className="inline-filter">Show<select value={filter} onChange={e => setFilter(e.target.value as typeof filter)}><option value="pending">pending ({pending})</option><option value="decided">decided</option><option value="all">all</option></select></label></div>
    <div className="integrity-note" role="note"><strong>What accepting means</strong><p>Accepting confirms that the recorded value matches the document text. It does not verify a diagnosis, a clinical conclusion, or that the original document is itself correct. Accepted values are marked “reviewed”; “verified” remains a separate, explicit step.</p></div>
    {!shown.length && <p className="muted">{filter === 'pending' ? 'No proposals awaiting review. Open a lab document in Documents and choose “Propose CA-125 values”.' : 'Nothing here yet.'}</p>}
    {shown.map(e => <ExtractionCard key={`${e.id}-${e.status}`} item={e} text={texts[e.source_id]} source={sources.find(s => s.id === e.source_id)} onDecided={onDecided} onOpenDocument={onOpenDocument} />)}
  </section>;
}

function ExtractionCard({ item, text, source, onDecided, onOpenDocument }: { item: Extraction; text?: DocumentText; source?: Source; onDecided: () => void; onOpenDocument: Props['onOpenDocument'] }) {
  const [values, setValues] = useState<Record<Field, string>>(() => Object.fromEntries(FIELDS.map(f => [f, item.proposed[f] === null || item.proposed[f] === undefined ? '' : String(item.proposed[f])])) as Record<Field, string>);
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const page = text?.pages[item.page - 1]?.text;
  const ctx = page && page.slice(item.start, item.end) === item.original_text ? excerpt(page, item.start, item.end) : null;
  const issuesFor = (f: Field) => item.issues.filter(i => ISSUE_FIELD[i.code] === f);
  const typed = (f: Field, v: string) => f === 'value' ? (v.trim() === '' ? null : Number(v)) : (v.trim() === '' ? null : v.trim());
  const changes = Object.fromEntries(FIELDS.filter(f => typed(f, values[f]) !== (item.proposed[f] ?? null)).map(f => [f, typed(f, values[f])]));
  const needsReason = Object.keys(changes).length > 0 || item.issues.length > 0;
  const missing = FIELDS.filter(f => typed(f, values[f]) === null);

  async function accept(e: FormEvent) {
    e.preventDefault(); setError('');
    try { await post(`/extractions/${item.id}/accept`, { changes, ...(reason ? { reason } : {}) }); onDecided(); } catch (err) { setError((err as Error).message); }
  }
  async function reject() {
    setError('');
    try { await post(`/extractions/${item.id}/reject`, { reason }); onDecided(); } catch (err) { setError((err as Error).message); }
  }
  const decided = item.status !== 'pending';
  return <article className={`extraction status-${item.status}`} data-testid="extraction-card" data-id={item.id} data-status={item.status} aria-label={`Proposal #${item.id}`}>
    <div className="study-top"><strong>Proposal #{item.id} · {item.status.toUpperCase()}{item.issues.length > 0 && !decided ? ` · ${item.issues.length} issue(s)` : ''}</strong>
      <span className="date-label">source #{item.source_id} {source ? `· ${source.document_filename}` : ''} · page {item.page}, chars {item.start}–{item.end}</span></div>
    <div className="original-text" data-testid="original-text">{ctx ? <pre>{ctx.before}<mark>{ctx.match}</mark>{ctx.after}</pre> : <pre><mark>{item.original_text}</mark></pre>}</div>
    <button type="button" className="text-button" onClick={() => onOpenDocument(item.source_id, item.record_id ? `labs-${item.record_id}` : undefined)}>Open in document viewer</button>
    {decided ? <div className="decision">
      <table><thead><tr><th>Field</th><th>Extracted</th><th>Recorded</th></tr></thead><tbody>
        {FIELDS.map(f => { const a = item.accepted?.[f as keyof ExtractedFields]; const p = item.proposed[f]; const differs = item.accepted && a !== (p ?? null);
          return <tr key={f} data-field={f}><td>{f}</td><td>{show(p)}</td><td>{item.accepted ? (differs ? <span className="change-line"><del>{show(p)}</del> → <ins>{show(a)}</ins></span> : show(a)) : '—'}</td></tr>; })}
      </tbody></table>
      <p className="tiny">{item.status === 'accepted' ? <>Recorded as <strong>lab #{item.record_id}</strong> (provenance: extracted, status: reviewed).</> : 'Rejected; no record was created.'} {item.decision_reason && <>Reason: {item.decision_reason}</>}</p>
    </div> : <form onSubmit={accept} className="review-form" aria-label={`Review proposal #${item.id}`}>
      <table><thead><tr><th>Field</th><th>Extracted</th><th>Value to record</th><th>Uncertainty</th></tr></thead><tbody>
        {FIELDS.map(f => <tr key={f} data-field={f} className={issuesFor(f).length || typed(f, values[f]) === null ? 'uncertain' : ''}>
          <td>{f}</td><td>{show(item.proposed[f])}{f === 'unit' && item.proposed.unit_as_written ? ` (written “${item.proposed.unit_as_written}”)` : ''}</td>
          <td><input aria-label={`${f} for proposal #${item.id}`} type={f === 'date' ? 'date' : f === 'value' ? 'number' : 'text'} step={f === 'value' ? 'any' : undefined} value={values[f]} onChange={e => setValues(v => ({ ...v, [f]: e.target.value }))} /></td>
          <td className="tiny">{typed(f, values[f]) === null && <div className="error">Missing — must be supplied</div>}{issuesFor(f).map(i => <div key={i.code}>{i.message}</div>)}{f === 'date' && item.proposed.date_basis && <div className="muted">Basis: {item.proposed.date_basis}</div>}</td>
        </tr>)}</tbody></table>
      {item.issues.filter(i => !ISSUE_FIELD[i.code]).map(i => <p key={i.code} className="tiny">{i.message}</p>)}
      <label>Reason {needsReason ? '(required: corrected or has issues)' : '(optional)'}<input value={reason} onChange={e => setReason(e.target.value)} required={needsReason} minLength={3} aria-label={`Reason for proposal #${item.id}`} /></label>
      {Object.keys(changes).length > 0 && <p className="tiny">Will be recorded with your correction(s) to: {Object.keys(changes).join(', ')}. The extracted values stay in the audit log.</p>}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="transcribe-row"><button className="primary" type="submit" disabled={missing.length > 0}>Accept{Object.keys(changes).length ? ' with corrections' : ''}</button>
        <button className="secondary" type="button" onClick={reject}>Reject</button></div>
    </form>}
  </article>;
}
