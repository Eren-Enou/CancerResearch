import { useEffect, useState } from 'react';
import { request } from '../api';
import { formatNumber, provenanceLabel } from '../records';
import type { Extraction, Finding, LabSeries, Source, TimelineEntry, Treatment } from '../types';
import { locationText } from './EvidenceView';

type Props = { patientId: number; refresh: number; timeline: TimelineEntry[]; series: LabSeries | null; treatments: Treatment[];
  sources: Source[]; onOpenDocument: (sourceId: number, recordKey?: string) => void; onReview: () => void };

/** Dates on which something is documented, with what kind of attention each date needs. */
export function dateSummaries(timeline: TimelineEntry[], pending: Extraction[], findings: Finding[]) {
  const dates = new Map<string, { date: string; records: number; unreviewed: number; pending: number; conflict: boolean }>();
  const touch = (d: string) => dates.get(d) ?? dates.set(d, { date: d, records: 0, unreviewed: 0, pending: 0, conflict: false }).get(d)!;
  for (const t of timeline) { const s = touch(t.date); s.records++; if (t.verification_status === 'entered') s.unreviewed++; }
  for (const e of pending) if (e.proposed.date) touch(e.proposed.date).pending++;
  for (const f of findings) if (f.kind === 'conflicting_values' || f.kind === 'different_units_same_date')
    for (const d of dates.keys()) if (f.message.includes(` on ${d} `)) touch(d).conflict = true;
  return [...dates.values()].sort((a, b) => a.date.localeCompare(b.date));
}

const DAY = 86_400_000;

export function HistoryView({ patientId, refresh, timeline, series, treatments, sources, onOpenDocument, onReview }: Props) {
  const [findings, setFindings] = useState<Finding[]>([]);
  const [pending, setPending] = useState<Extraction[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    request<Finding[]>(`/patients/${patientId}/evidence`).then(setFindings).catch(() => setFindings([]));
    request<Extraction[]>(`/patients/${patientId}/extractions?status=pending`).then(setPending).catch(() => setPending([]));
  }, [patientId, refresh]);
  const days = dateSummaries(timeline, pending, findings);
  const day = selected ?? days[days.length - 1]?.date ?? null;
  const onDay = timeline.filter(t => t.date === day);
  const pendingOnDay = pending.filter(e => e.proposed.date === day);
  const conflictsOnDay = findings.filter(f => day && f.message.includes(` on ${day} `) && f.severity !== 'info');
  const docsOnDay = [...new Set(onDay.map(t => t.source_id))].map(id => sources.find(s => s.id === id)).filter(Boolean) as Source[];
  const unreviewed = timeline.filter(t => t.verification_status === 'entered');
  const sourceName = (id: number) => { const s = sources.find(x => x.id === id); return s ? `${s.label}${s.document_filename ? ` (${s.document_filename})` : ''}` : `source #${id}`; };
  const evidence = (t: TimelineEntry) => sources.find(s => s.id === t.source_id)?.document_sha256 && t.provenance !== 'user_observation'
    ? <button type="button" className="text-button" aria-label={`Show evidence for ${t.entity} #${t.id}`} onClick={() => onOpenDocument(t.source_id, `${t.entity}-${t.id}`)}>📄 {locationText(t)}</button>
    : <span className="tiny muted">{t.provenance === 'user_observation' ? 'user observation (no document)' : 'no document on file'}</span>;

  return <>
    <section className="panel"><div className="section-heading"><div><h2>Documented history</h2>
      <p className="muted">What the documents record, in date order. The application shows what was written and when; it does not explain why values changed or judge treatment.</p></div></div>
      <HistoryChart series={series} treatments={treatments} timeline={timeline} selected={day} conflictDates={days.filter(d => d.conflict).map(d => d.date)}
        onSelect={setSelected} onOpen={(t) => onOpenDocument(t.source_id, `${t.entity}-${t.id}`)} />
      <div className="date-strip" role="listbox" aria-label="Documented dates">
        {days.map(d => <button key={d.date} type="button" role="option" aria-selected={d.date === day} data-testid="date-chip" data-date={d.date}
          className={`date-chip ${d.date === day ? 'active' : ''}`} onClick={() => setSelected(d.date)}>
          <span>{d.date}</span><span className="tiny">{d.records} record(s)</span>
          {d.conflict && <span className="chip-flag conflict" title="Documents disagree">⚠ conflict</span>}
          {d.unreviewed + d.pending > 0 && <span className="chip-flag unreviewed" title="Not yet reviewed">{d.unreviewed + d.pending} unreviewed</span>}
        </button>)}
      </div>
    </section>

    {day && <section className="panel" data-testid="day-panel" aria-label={`Recorded on ${day}`}><h2>Recorded on {day}</h2>
      {conflictsOnDay.map(f => <div key={f.id} className="unit-warning" role="note" data-testid="day-conflict"><strong>Documents disagree.</strong> {f.message}</div>)}
      {onDay.length ? <ul className="day-list">{onDay.map(t => <li key={`${t.entity}-${t.id}`} data-testid="day-record">
        <strong>{t.label}</strong> <span className="tiny muted">· {t.kind} #{t.id} · {provenanceLabel[t.provenance]} · {t.verification_status === 'entered' ? 'not yet reviewed' : t.verification_status}</span>
        <div className="tiny">Source: {sourceName(t.source_id)} {evidence(t)}</div></li>)}</ul> : <p className="muted">No accepted records on this date.</p>}
      {pendingOnDay.length > 0 && <div className="integrity-note"><strong>{pendingOnDay.length} proposal(s) for this date await review</strong>
        <p>{pendingOnDay.map(e => `“${e.original_text}” (${sourceName(e.source_id)})`).join('; ')}</p>
        <button type="button" className="text-button" onClick={onReview}>Open review queue →</button></div>}
      <h3>Supporting documents</h3>
      <ul className="tiny">{docsOnDay.map(s => <li key={s.id}>#{s.id} · {s.label} {s.document_filename ? `· ${s.document_filename}` : '· no file attached'}
        {s.document_sha256 && <button type="button" className="text-button" onClick={() => onOpenDocument(s.id)}>Open</button>}</li>)}</ul>
    </section>}

    <section className="panel"><h2>Not yet reviewed</h2>
      <p className="muted">Records still in state “entered” and extraction proposals nobody has decided on. Reviewing confirms transcription only.</p>
      <p data-testid="unreviewed-summary">{unreviewed.length} record(s) not reviewed · {pending.length} proposal(s) pending
        {pending.length > 0 && <button type="button" className="text-button" onClick={onReview}>Review proposals →</button>}</p>
      <ul className="tiny">{unreviewed.slice(0, 20).map(t => <li key={`${t.entity}-${t.id}`}>{t.date} · {t.label} · {sourceName(t.source_id)} {evidence(t)}</li>)}</ul>
    </section>
  </>;
}

function HistoryChart({ series, treatments, timeline, selected, conflictDates, onSelect, onOpen }: {
  series: LabSeries | null; treatments: Treatment[]; timeline: TimelineEntry[]; selected: string | null; conflictDates: string[];
  onSelect: (d: string) => void; onOpen: (t: TimelineEntry) => void;
}) {
  const points = series?.points ?? [];
  const dates = [...points.map(p => p.date), ...treatments.map(t => t.date)];
  if (!dates.length) return <p className="muted">No CA-125 values or treatments documented yet.</p>;
  const W = 860, H = 260, padX = 56, top = 30, bottom = 46;
  const ts = dates.map(d => Date.parse(d + 'T00:00:00Z'));
  const t0 = Math.min(...ts) - 3 * DAY, t1 = Math.max(...ts) + 3 * DAY;
  const values = points.map(p => p.value);
  const vmin = values.length ? Math.min(...values) : 0, vmax = values.length ? Math.max(...values) : 1;
  const span = Math.max(vmax - vmin, Math.abs(vmax) * 0.1, 1), lo = vmin - span * 0.15, hi = vmax + span * 0.2;
  const x = (d: string) => padX + (Date.parse(d + 'T00:00:00Z') - t0) / (t1 - t0) * (W - 2 * padX);
  const y = (v: number) => top + (hi - v) / (hi - lo) * (H - top - bottom);
  const entryFor = (labId: number) => timeline.find(t => t.entity === 'labs' && t.id === labId);
  // Several documents can report the same value for the same date; the label says how many records the point stands for.
  const same = (p: { date: string; value: number }) => points.filter(q => q.date === p.date && q.value === p.value);
  return <div className="chart-wrap">
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`CA-125 in ${series?.unit} with treatment dates; values listed in the table below`} data-testid="history-chart">
      {selected && <rect x={x(selected) - 6} y={top - 8} width={12} height={H - top - bottom + 8} fill="#67d0bf" opacity={0.12} />}
      {treatments.map(t => <g key={`tx-${t.id}`} data-testid="treatment-marker" data-date={t.date}>
        <line x1={x(t.date)} x2={x(t.date)} y1={top - 6} y2={H - bottom} stroke="#b28cf0" strokeDasharray="5 5" />
        <text x={x(t.date) + 4} y={top + 4} fill="#cdb6f5" fontSize="11">cycle {t.cycle}</text></g>)}
      {conflictDates.map(d => <text key={`c-${d}`} x={x(d)} y={H - bottom + 30} textAnchor="middle" fill="#ffb4ac" fontSize="13">⚠</text>)}
      {points.map(p => { const e = entryFor(p.id); const reviewed = p.verification_status !== 'entered';
        return <g key={p.id} data-testid="history-point" data-lab-id={p.id} data-date={p.date} data-value={p.value} style={{ cursor: 'pointer' }}
          onClick={() => { onSelect(p.date); if (e) onOpen(e); }}>
          <circle cx={x(p.date)} cy={y(p.value)} r={6} fill={reviewed ? '#94efe0' : '#121f31'} stroke="#94efe0" strokeWidth={2}>
            <title>{p.date}: {formatNumber(p.value)} {p.unit} · lab #{p.id} · {reviewed ? p.verification_status : 'not yet reviewed'} · click for evidence</title></circle>
          {same(p)[0].id === p.id && <text x={x(p.date) + 9} y={y(p.value) - 8} fill="#cfe9e4" fontSize="11">{formatNumber(p.value)}{same(p).length > 1 ? ` ×${same(p).length}` : ''}</text>}</g>; })}
      {[...new Set(dates)].map(d => <text key={`d-${d}`} x={x(d)} y={H - bottom + 16} textAnchor="middle" fill="#8999ae" fontSize="10">{d.slice(5)}</text>)}
    </svg>
    <p className="caption">Filled points are reviewed transcriptions; hollow points are not yet reviewed. Dashed lines mark documented treatment dates. ⚠ marks dates where documents disagree. Click a point to see its evidence.</p>
    <details><summary>Charted values ({points.length}) with evidence links</summary><table className="sr-table"><thead><tr><th>Date</th><th>Value</th><th>Review state</th><th>Evidence</th></tr></thead>
      <tbody>{points.map(p => { const e = entryFor(p.id); return <tr key={p.id}><td>{p.date}</td><td>{formatNumber(p.value)} {p.unit}</td><td>{p.verification_status}</td>
        <td>{e && <button type="button" className="text-button" onClick={() => onOpen(e)}>lab #{p.id}</button>}</td></tr>; })}</tbody></table></details>
  </div>;
}
