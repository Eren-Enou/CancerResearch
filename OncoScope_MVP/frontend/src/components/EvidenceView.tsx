import { useEffect, useState } from 'react';
import { request } from '../api';
import { formatDifference, formatNumber, provenanceLabel } from '../records';
import type { Finding, LabSeries, Source, TimelineEntry } from '../types';

type Props = { patientId: number; refresh: number; timeline: TimelineEntry[]; sources: Source[]; series: LabSeries | null;
  onOpenDocument: (sourceId: number, recordKey?: string) => void };

export function locationText(t: Pick<TimelineEntry, 'anchor_page' | 'anchor_start' | 'anchor_end' | 'anchor_region' | 'provenance'>) {
  if (t.provenance === 'user_observation') return 'user observation (no document)';
  if (t.anchor_start !== null) return `p.${t.anchor_page} chars ${t.anchor_start}–${t.anchor_end}`;
  if (t.anchor_region) return `region ${t.anchor_region}`;
  return 'location not recorded';
}

export function EvidenceView({ patientId, refresh, timeline, sources, series, onOpenDocument }: Props) {
  const [findings, setFindings] = useState<Finding[]>([]);
  useEffect(() => { request<Finding[]>(`/patients/${patientId}/evidence`).then(setFindings).catch(() => setFindings([])); }, [patientId, refresh]);
  const sourceById = (id: number) => sources.find(s => s.id === id);
  const byDate = new Map<string, TimelineEntry[]>();
  for (const t of [...timeline].sort((a, b) => a.date.localeCompare(b.date))) byDate.set(t.date, [...(byDate.get(t.date) ?? []), t]);
  const open = (sourceId: number | null, key?: string) => sourceId !== null && sourceById(sourceId)?.document_sha256 && onOpenDocument(sourceId, key);

  return <>
    <section className="panel"><div className="section-heading"><div><h2>Documentation findings</h2>
      <p className="muted">Conflicts, repeated reports, date mismatches and missing support found in the recorded evidence. The application lists them; it never decides which document is right.</p></div></div>
      {!findings.length && <p className="muted">No findings.</p>}
      <ul className="findings">{findings.map(f => <li key={f.id} data-testid="finding" data-kind={f.kind} className={`finding sev-${f.severity}`}>
        <span className={`badge sev-${f.severity}`}>{f.severity}</span> <span className="tiny muted">{f.kind.replace(/_/g, ' ')}</span>
        <div>{f.message}</div>
        <div className="tiny">{f.records.map(r => <button key={`${r.entity}-${r.id}`} type="button" className="text-button" onClick={() => open(r.source_id, `${r.entity}-${r.id}`)}
          disabled={!r.source_id || !sourceById(r.source_id)?.document_sha256}>{r.entity} #{r.id}{r.source_id ? ` (source #${r.source_id})` : ''}</button>)}</div>
      </li>)}</ul>
    </section>

    <section className="panel"><h2>Changes between documented values</h2>
      <p className="muted">Arithmetic differences between consecutive {series?.test ?? 'CA-125'} values in {series?.unit ?? 'U/mL'}. No trend, response or effectiveness is inferred.</p>
      {series?.changes?.length ? <div className="table-scroll"><table><thead><tr><th>From</th><th>To</th><th>Difference</th><th>Records</th></tr></thead><tbody>
        {series.changes.map(c => { const a = series.points.find(p => p.id === c.from_id)!; const b = series.points.find(p => p.id === c.to_id)!;
          return <tr key={`${c.from_id}-${c.to_id}`} data-testid="lab-change"><td>{c.from_date}: {formatNumber(a.value)}</td><td>{c.to_date}: {formatNumber(b.value)}</td>
            <td className="numeric">{formatDifference(c.difference)} {c.unit}</td><td className="tiny">lab #{c.from_id} (src #{a.source_id}) → lab #{c.to_id} (src #{b.source_id})</td></tr>; })}
      </tbody></table></div> : <p className="muted">Fewer than two values in one unit.</p>}
      <p className="caption">Lesion measurement differences are under Imaging.</p>
    </section>

    <section className="panel"><h2>What was recorded on each date</h2>
      {[...byDate.entries()].map(([day, entries]) => <div key={day} className="date-group" data-testid="date-group" data-date={day}>
        <div className="date-label">{day}</div>
        <ul>{entries.map(t => { const src = sourceById(t.source_id); return <li key={`${t.entity}-${t.id}`}>
          <strong>{t.kind} #{t.id}</strong> {t.label} <span className="tiny muted">· source #{t.source_id}{src?.document_filename ? ` (${src.document_filename})` : ' (no file)'} · {locationText(t)}</span>
          {src?.document_sha256 && <button type="button" className="text-button" onClick={() => open(t.source_id, `${t.entity}-${t.id}`)}>Show evidence</button>}</li>; })}</ul>
      </div>)}
    </section>

    <section className="panel"><h2>Observations by document</h2><div className="table-scroll"><table>
      <thead><tr><th>Source</th><th>File</th><th>Records</th><th>By provenance</th><th>Without location</th><th></th></tr></thead>
      <tbody>{sources.map(s => { const obs = timeline.filter(t => t.source_id === s.id);
        const counts = Object.entries(obs.reduce<Record<string, number>>((acc, t) => ({ ...acc, [t.provenance]: (acc[t.provenance] ?? 0) + 1 }), {}));
        const unlocated = obs.filter(t => t.provenance !== 'user_observation' && t.anchor_start === null && !t.anchor_region).length;
        return <tr key={s.id} data-testid="doc-row" data-source={s.id}><td>#{s.id} · {s.label}</td><td className="tiny">{s.document_filename ?? 'no file'}</td><td className="numeric">{obs.length}</td>
          <td className="tiny">{counts.map(([p, n]) => `${n} ${provenanceLabel[p]}`).join(', ') || '—'}</td><td className="numeric">{unlocated}</td>
          <td>{s.document_sha256 && <button type="button" className="text-button" onClick={() => onOpenDocument(s.id)}>Open</button>}</td></tr>; })}</tbody></table></div>
    </section>
  </>;
}
