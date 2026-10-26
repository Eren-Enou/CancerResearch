import { useState, type FormEvent } from 'react';
import { post } from '../api';
import { eventTypes, knownLabUnits, modalities, sourceTypes } from '../records';
import type { Source, Study } from '../types';

const today = () => new Date().toLocaleDateString('en-CA'); // local calendar date, YYYY-MM-DD
const defaultForm = () => ({ date: today(), source_id: '', kind: 'other', title: '', detail: '', test: 'CA-125', value: '', unit: 'U/mL', regimen: '', cycle: '1', notes: '', modality: 'CT', study_label: '', report_text: '', study_id: '', lesion_key: '', site: '', long_axis_mm: '', short_axis_mm: '', label: '', source_kind: 'manual', source_date: '', origin: '', note: '' });
type FormState = ReturnType<typeof defaultForm>;
type FormKind = 'Source' | 'Event' | 'Lab' | 'Treatment' | 'Imaging study' | 'Lesion measurement';
const kinds: FormKind[] = ['Source', 'Event', 'Lab', 'Treatment', 'Imaging study', 'Lesion measurement'];
// After a save these fields stay filled so several observations from one source can be entered quickly.
const sticky: (keyof FormState)[] = ['source_id', 'date', 'test', 'unit', 'regimen', 'modality', 'study_id', 'site'];

export function AddRecord({ patientId, sources, studies, onSaved }: { patientId: number; sources: Source[]; studies: Study[]; onSaved: () => void }) {
  const [kind, setKind] = useState<FormKind>('Source');
  const [form, setForm] = useState<FormState>(defaultForm);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const edit = (name: keyof FormState, value: string) => setForm(p => ({ ...p, [name]: value }));
  const field = (name: keyof FormState, label: string, type = 'text', required = true, extra: Record<string, unknown> = {}) =>
    <label>{label}<input name={name} value={form[name]} type={type} step={type === 'number' ? 'any' : undefined} required={required} onChange={e => edit(name, e.target.value)} {...extra} /></label>;
  const sourcePicker = <label>Original source (required)<select name="source_id" value={form.source_id} required onChange={e => edit('source_id', e.target.value)}><option value="">Choose a source</option>{sources.map(s => <option value={s.id} key={s.id}>#{s.id} · {s.label}</option>)}</select></label>;
  const knownUnits = Object.entries(knownLabUnits).find(([t]) => t.toLowerCase().replace(/[\s_-]/g, '') === form.test.toLowerCase().replace(/[\s_-]/g, ''))?.[1];

  function chooseStudy(id: string) {
    const study = studies.find(s => String(s.id) === id);
    // Measurements usually come from the study's own report: preselect its source, but let the user change it.
    setForm(p => ({ ...p, study_id: id, source_id: p.source_id || (study ? String(study.source_id) : '') }));
  }

  async function submit(e: FormEvent) {
    e.preventDefault(); setError(''); setMessage('');
    try {
      const common = { patient_id: patientId, source_id: Number(form.source_id) };
      const payloads: Record<FormKind, [string, Record<string, unknown>]> = {
        'Source': ['/sources', { patient_id: patientId, label: form.label, kind: form.source_kind, source_date: form.source_date || null, origin: form.origin, note: form.note }],
        'Event': ['/events', { ...common, date: form.date, kind: form.kind, title: form.title, detail: form.detail }],
        'Lab': ['/labs', { ...common, date: form.date, test: form.test, value: Number(form.value), unit: form.unit }],
        'Treatment': ['/treatments', { ...common, date: form.date, regimen: form.regimen, cycle: Number(form.cycle), notes: form.notes }],
        'Imaging study': ['/studies', { ...common, date: form.date, modality: form.modality, study_label: form.study_label, report_text: form.report_text }],
        'Lesion measurement': ['/measurements', { ...common, study_id: Number(form.study_id), lesion_key: form.lesion_key, site: form.site, long_axis_mm: Number(form.long_axis_mm), short_axis_mm: form.short_axis_mm ? Number(form.short_axis_mm) : null, notes: form.notes }],
      };
      const [route, data] = payloads[kind];
      const saved = await post<{ id: number }>(route, data);
      setMessage(`${kind} #${saved.id} saved with source provenance and an audit entry.`);
      setForm(p => {
        const next = defaultForm();
        for (const key of sticky) next[key] = p[key];
        if (kind === 'Source') next.source_id = String(saved.id);
        return next;
      });
      onSaved();
    } catch (err) { setError((err as Error).message); }
  }

  return <section className="panel form-panel">
    <div className="section-heading"><div><h2>Add a sourced record</h2><p className="muted">Create its source first, then link observations to that source. Enter values exactly as written in the fictional document; never enter inferred measurements.</p></div></div>
    <form onSubmit={submit} aria-label="Add record">
      <label>Record type<select name="record_type" value={kind} onChange={e => { setKind(e.target.value as FormKind); setError(''); setMessage(''); }}>{kinds.map(k => <option key={k}>{k}</option>)}</select></label>
      {kind === 'Source' ? <>
        {field('label', 'Source label')}
        <label>Document type<select name="source_kind" value={form.source_kind} onChange={e => edit('source_kind', e.target.value)}>{sourceTypes.map(v => <option key={v}>{v}</option>)}</select></label>
        {field('source_date', 'Document date', 'date', false)}
        {field('origin', 'Origin (fictional issuer, optional)', 'text', false)}
        <label>Source notes<textarea name="note" value={form.note} onChange={e => edit('note', e.target.value)} /></label>
        <p className="caption">A synthetic PDF, text or image file can be attached afterwards in Sources; its SHA-256 is recorded.</p>
      </> : <>
        {sourcePicker}
        {kind === 'Lesion measurement' && <label>Associated study<select name="study_id" value={form.study_id} required onChange={e => chooseStudy(e.target.value)}><option value="">Select study</option>{studies.map(s => <option value={s.id} key={s.id}>#{s.id} · {s.date} · {s.study_label}</option>)}</select></label>}
        {kind !== 'Lesion measurement' && field('date', 'Date', 'date')}
        {kind === 'Event' && <>
          <label>Type<select name="kind" value={form.kind} onChange={e => edit('kind', e.target.value)}>{eventTypes.map(v => <option key={v}>{v}</option>)}</select></label>
          {field('title', 'Event title')}
          <label>Details<textarea name="detail" value={form.detail} onChange={e => edit('detail', e.target.value)} /></label>
        </>}
        {kind === 'Lab' && <>
          {field('test', 'Test name')}
          {field('value', 'Measured value', 'number')}
          {field('unit', 'Unit exactly as written', 'text', true, { list: 'unit-options' })}
          <datalist id="unit-options">{(knownUnits ?? []).map(u => <option key={u} value={u} />)}</datalist>
          <p className="caption">{knownUnits ? `Accepted spellings for ${form.test}: ${knownUnits.join(', ')}. ` : ''}Values are never converted; each unit is charted separately.</p>
        </>}
        {kind === 'Treatment' && <>{field('regimen', 'Documented regimen')}{field('cycle', 'Cycle', 'number', true, { min: 1, step: 1 })}<label>Notes<textarea name="notes" value={form.notes} onChange={e => edit('notes', e.target.value)} /></label></>}
        {kind === 'Imaging study' && <>
          <label>Modality<select name="modality" value={form.modality} onChange={e => edit('modality', e.target.value)}>{modalities.map(v => <option key={v}>{v}</option>)}</select></label>
          {field('study_label', 'Study label')}
          <label>Report text (verbatim)<textarea name="report_text" rows={5} value={form.report_text} onChange={e => edit('report_text', e.target.value)} /></label>
        </>}
        {kind === 'Lesion measurement' && <>
          {field('lesion_key', 'Consistent lesion key (e.g. reference-A)', 'text', true, { pattern: '[A-Za-z0-9][A-Za-z0-9._\\-]*', title: 'Letters, digits, dot, underscore or hyphen; no spaces' })}
          {field('site', 'Documented location')}
          {field('long_axis_mm', 'Long axis (mm)', 'number', true, { min: 0.1, max: 1000 })}
          {field('short_axis_mm', 'Short axis (mm; optional)', 'number', false, { min: 0.1, max: 1000 })}
          <label>Notes<textarea name="notes" value={form.notes} onChange={e => edit('notes', e.target.value)} /></label>
        </>}
      </>}
      {error && <p role="alert" className="error">{error}</p>}
      {message && <p role="status" className="success">{message}</p>}
      <button className="primary" type="submit">Save record</button>
    </form>
  </section>;
}

export function CreateCase({ onCreated }: { onCreated: (id: number) => void }) {
  const [label, setLabel] = useState('');
  const [error, setError] = useState('');
  async function submit(e: FormEvent) {
    e.preventDefault(); setError('');
    try { onCreated((await post<{ id: number }>('/patients', { label })).id); } catch (err) { setError((err as Error).message); }
  }
  return <form className="create-case" onSubmit={submit} aria-label="Create fictional case">
    <label>Fictional case label<input name="case_label" required maxLength={100} value={label} placeholder="e.g. Fictional Case B" onChange={e => setLabel(e.target.value)} /></label>
    <p className="caption">Every case created here is flagged synthetic. Do not enter real names or patient data.</p>
    {error && <p role="alert" className="error">{error}</p>}
    <button className="primary" type="submit">Create fictional case</button>
  </form>;
}
