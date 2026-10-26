import { useState, type FormEvent } from 'react';
import { patch, post, request } from '../api';
import { changedFields, editableFields, entityLabel, toDraft } from '../records';
import type { AnyRecord, AuditEntry, Entity, Source, Study, VerificationStatus } from '../types';

type Props = { entity: Entity; record: AnyRecord; sources: Source[]; studies: Study[]; onSaved: () => void };

/** Plain-language names for the review states (stored values stay entered / reviewed / verified). */
export const statusLabel: Record<VerificationStatus, string> = { entered: 'not yet checked', reviewed: 'checked', verified: 'verified' };

export function StatusBadge({ record }: { record: { revision: number; verification_status: VerificationStatus } }) {
  return <span className="badges">
    <span className={`badge status-${record.verification_status}`} title="Checking confirms the value matches its document, not a medical conclusion">{statusLabel[record.verification_status]}</span>
    {record.revision > 1 && <span className="badge corrected" title="Corrected; originals are in the audit history">rev {record.revision}</span>}
  </span>;
}

function show(value: unknown) {
  return value === null || value === undefined || value === '' ? '∅' : String(value);
}

/** Renders one audit entry as field-level before → after lines. */
export function AuditDetail({ entry }: { entry: AuditEntry }) {
  const detail = JSON.parse(entry.detail_json) as Record<string, unknown>;
  if (entry.action === 'create') {
    const shown = Object.entries(detail).filter(([k]) => !['uid', 'patient_id', 'entered_at', 'updated_at', 'revision', 'verification_status', 'id'].includes(k) && !k.startsWith('document_'));
    return <div className="audit-detail">Original values: {shown.map(([k, v]) => <span key={k} className="kv"><b>{k}</b> {show(v)}</span>)}</div>;
  }
  const before = (detail.before ?? {}) as Record<string, unknown>;
  const after = (detail.after ?? {}) as Record<string, unknown>;
  const fields = (detail.changed_fields ?? []) as string[];
  return <div className="audit-detail">
    {fields.map(f => <div key={f} className="change-line" data-testid="audit-change"><b>{f}</b>: <del>{show(before[f])}</del> → <ins>{show(after[f])}</ins></div>)}
    {entry.reason && <div className="muted tiny">Reason: {entry.reason}</div>}
  </div>;
}

export function RecordTools({ entity, record, sources, studies, onSaved }: Props) {
  const [mode, setMode] = useState<'closed' | 'correct' | 'history'>('closed');
  const [draft, setDraft] = useState<Record<string, string>>(() => toDraft(entity, record));
  const [reason, setReason] = useState('');
  const [status, setStatus] = useState<VerificationStatus>(record.verification_status);
  const [history, setHistory] = useState<AuditEntry[]>([]);
  const [error, setError] = useState('');
  const label = `${entityLabel[entity]} #${record.id}`;

  function open(next: typeof mode) {
    setError('');
    if (mode === next) { setMode('closed'); return; }
    setMode(next);
    if (next === 'correct') { setDraft(toDraft(entity, record)); setReason(''); setStatus(record.verification_status); }
    if (next === 'history') request<AuditEntry[]>(`/records/${entity}/${record.id}/history`).then(setHistory).catch(e => setError(e.message));
  }

  async function submit(e: FormEvent) {
    e.preventDefault(); setError('');
    const changes = changedFields(entity, record, draft);
    try {
      if (Object.keys(changes).length) await patch(`/records/${entity}/${record.id}`, { changes, reason });
      else if (status !== record.verification_status) await post(`/records/${entity}/${record.id}/status`, { status, reason });
      else { setError('Nothing changed.'); return; }
      setMode('closed'); onSaved();
    } catch (err) { setError((err as Error).message); }
  }

  return <div className="record-tools">
    <button className="text-button" type="button" aria-expanded={mode === 'correct'} aria-label={`Correct ${label}`} onClick={() => open('correct')}>{mode === 'correct' ? 'Cancel' : 'Correct'}</button>
    <button className="text-button" type="button" aria-expanded={mode === 'history'} aria-label={`History of ${label}`} onClick={() => open('history')}>{mode === 'history' ? 'Hide history' : 'History'}</button>
    {mode === 'correct' && <form className="correction-form" onSubmit={submit} aria-label={`Correct ${label}`}>
      {editableFields[entity].map(f => {
        const value = draft[f.name] ?? '';
        const set = (v: string) => setDraft(d => ({ ...d, [f.name]: v }));
        let input;
        if (f.type === 'textarea') input = <textarea value={value} onChange={e => set(e.target.value)} />;
        else if (f.type === 'select') input = <select value={value} onChange={e => set(e.target.value)}>{f.options!.map(o => <option key={o}>{o}</option>)}</select>;
        else if (f.type === 'source') input = <select value={value} onChange={e => set(e.target.value)}>{sources.map(s => <option key={s.id} value={s.id}>#{s.id} · {s.label}</option>)}</select>;
        else if (f.type === 'study') input = <select value={value} onChange={e => set(e.target.value)}>{studies.map(s => <option key={s.id} value={s.id}>#{s.id} · {s.date} · {s.study_label}</option>)}</select>;
        else input = <input type={f.type ?? 'text'} step={f.type === 'number' ? 'any' : undefined} required={!f.optional} value={value} onChange={e => set(e.target.value)} />;
        return <label key={f.name}>{f.label}{input}</label>;
      })}
      <label>Checked against the document?<select value={status} onChange={e => setStatus(e.target.value as VerificationStatus)}>{(['entered', 'reviewed', 'verified'] as VerificationStatus[]).map(s => <option key={s} value={s}>{statusLabel[s]}</option>)}</select></label>
      <label>Reason for change (required)<input required minLength={3} value={reason} onChange={e => setReason(e.target.value)} /></label>
      <p className="caption">Saved as version {record.revision + 1}. The earlier values stay in the change history; a corrected value goes back to “not yet checked”.</p>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="primary">Save correction</button>
    </form>}
    {mode === 'history' && <div className="history" aria-label={`History of ${label}`}>
      {error && <p className="error" role="alert">{error}</p>}
      {history.map(h => <div key={h.id} className="history-entry"><span className="date-label">{h.recorded_at} · {h.action}</span><AuditDetail entry={h} /></div>)}
    </div>}
  </div>;
}
