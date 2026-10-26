import { useEffect, useState } from 'react';
import { request, uploadDocument } from '../api';
import type { AnyRecord, AuditEntry, AuditVerification, Entity, Source, Study } from '../types';
import { AuditDetail, RecordTools, StatusBadge } from './RecordTools';

export function SourceReference({ id, sources }: { id: number | null; sources: Source[] }) {
  if (id === null) return <span className="muted">—</span>;
  const source = sources.find(s => s.id === id);
  return <span className="source-ref" title={source?.note ?? ''}>Source #{id}{source ? ` · ${source.label}` : ' · missing'}{source?.document_sha256 ? ' · 📄' : ''}</span>;
}

type Shared = { sources: Source[]; studies: Study[]; onSaved: () => void };

export function SourcesView({ sources, studies, onSaved, citations }: Shared & { citations: Record<number, { entity: Entity; record: AnyRecord }[]> }) {
  return <section className="panel"><div className="section-heading"><div><h2>Source register</h2><p className="muted">Every observation links to a registered source. Attach the synthetic original (PDF, TXT, PNG, JPEG ≤ 10 MB); its SHA-256 is recorded and checked whenever it is opened.</p></div></div>
    <div className="source-grid">{sources.map(s => <SourceCard key={s.id} source={s} cited={citations[s.id] ?? []} sources={sources} studies={studies} onSaved={onSaved} />)}</div>
  </section>;
}

function SourceCard({ source: s, cited, sources, studies, onSaved }: Shared & { source: Source; cited: { entity: Entity; record: AnyRecord }[] }) {
  const [error, setError] = useState('');
  const [check, setCheck] = useState('');
  async function attach(file: File | undefined) {
    if (!file) return;
    setError('');
    try { await uploadDocument(s.id, file); onSaved(); } catch (e) { setError((e as Error).message); }
  }
  async function verify() {
    setCheck('');
    try {
      const v = await request<{ valid: boolean; present: boolean }>(`/sources/${s.id}/document/verify`);
      setCheck(v.valid ? 'Hash verified: stored file matches the recorded SHA-256.' : v.present ? 'MISMATCH: stored file was altered.' : 'MISSING: stored file not found.');
    } catch (e) { setCheck((e as Error).message); }
  }
  return <div className="source-card" data-testid={`source-${s.id}`}>
    <span className="small-pill">#{s.id} · {s.kind}</span> <StatusBadge record={s} />
    <h3>{s.label}</h3>
    <p className="muted">Document date: {s.source_date || 'Not recorded'}{s.origin ? ` · Origin: ${s.origin}` : ''}</p>
    {s.note && <p className="caption">{s.note}</p>}
    {s.document_sha256 ? <div className="doc-line">
      <a href={`/api/sources/${s.id}/document`} target="_blank" rel="noreferrer">Open {s.document_filename}</a> · {s.document_size} bytes
      <div className="hash" title={s.document_sha256}>SHA-256 {s.document_sha256}</div>
      <button className="text-button" type="button" onClick={verify}>Verify hash</button>{check && <span className="tiny" role="status"> {check}</span>}
    </div> : <label className="attach">Attach synthetic document<input type="file" accept=".pdf,.txt,.png,.jpg,.jpeg" onChange={e => attach(e.target.files?.[0])} /></label>}
    {error && <p className="error" role="alert">{error}</p>}
    <details><summary>Cited by {cited.length} record(s)</summary><ul className="tiny">{cited.map(c => <li key={`${c.entity}-${c.record.id}`}>{c.entity} #{c.record.id}</li>)}</ul></details>
    <RecordTools entity="sources" record={s} sources={sources} studies={studies} onSaved={onSaved} />
  </div>;
}

export function AuditView({ patientId, refresh }: { patientId: number; refresh: number }) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [chain, setChain] = useState<AuditVerification | null>(null);
  const [filter, setFilter] = useState('all');
  useEffect(() => {
    request<AuditEntry[]>(`/patients/${patientId}/audit`).then(setEntries).catch(() => setEntries([]));
    request<AuditVerification>('/audit/verify').then(setChain).catch(() => setChain(null));
  }, [patientId, refresh]);
  const shown = entries.filter(e => filter === 'all' || e.action === filter);
  return <section className="panel"><div className="section-heading"><div><h2>Audit history</h2>
    <p className="muted">Append-only and hash-chained. Each correction lists the original and corrected value with its reason.</p></div>
    <label className="inline-filter">Show<select value={filter} onChange={e => setFilter(e.target.value)}>{['all', 'create', 'correct', 'status', 'attach_document'].map(a => <option key={a}>{a}</option>)}</select></label></div>
    {chain && <p className={chain.valid ? 'success' : 'error'} role="status">{chain.valid ? `Hash chain verified (${chain.entries_checked} entries).` : `Hash chain FAILED at entry #${chain.first_invalid_id}.`}</p>}
    <div className="audit-list">{shown.map(e => <div key={e.id} className="history-entry" data-testid="audit-entry" data-action={e.action}>
      <span className="date-label">#{e.id} · {e.recorded_at} · {e.action.toUpperCase()} · {e.entity_type} #{e.entity_id}</span>
      <AuditDetail entry={e} />
    </div>)}</div>
  </section>;
}
