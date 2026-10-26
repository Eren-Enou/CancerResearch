import { useState, type FormEvent } from 'react';
import { post, readBytes, uploadDocument } from '../api';
import { sourceTypes } from '../records';
import type { Source } from '../types';

const ALLOWED = ['.pdf', '.txt', '.png', '.jpg', '.jpeg'];

async function sha256Hex(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await readBytes(file));
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('');
}

/** One step instead of two: registers the source and attaches its fictional file, refusing duplicates first. */
export function ImportDocument({ patientId, sources, onImported }: { patientId: number; sources: Source[]; onImported: (sourceId: number) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [label, setLabel] = useState('');
  const [kind, setKind] = useState('lab');
  const [date, setDate] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault(); setError('');
    if (!file) return;
    const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
    if (!ALLOWED.includes(ext)) { setError(`Only ${ALLOWED.join(', ')} files can be imported.`); return; }
    setBusy(true);
    try {
      const sha = await sha256Hex(file);
      const twin = sources.find(s => s.document_sha256 === sha);
      if (twin) { setError(`Duplicate import: this exact file is already attached to source #${twin.id} (${twin.label}).`); return; }
      const source = await post<Source>('/sources', { patient_id: patientId, label: label || file.name.replace(/\.[^.]+$/, ''), kind, source_date: date || null });
      try {
        await uploadDocument(source.id, file);
      } catch (err) {
        setError(`Source #${source.id} was created, but the file was refused: ${(err as Error).message}. Attach a supported file to it in Sources.`);
        onImported(source.id);
        return;
      }
      setFile(null); setLabel(''); setDate('');
      (e.target as HTMLFormElement).reset();
      onImported(source.id);
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }

  return <form className="import-form" onSubmit={submit} aria-label="Import a fictional document">
    <h3>Import a fictional document</h3>
    <label>File (PDF, TXT, PNG, JPEG)<input type="file" accept={ALLOWED.join(',')} required onChange={e => { const f = e.target.files?.[0] ?? null; setFile(f); if (f && !label) setLabel(f.name.replace(/\.[^.]+$/, '')); }} /></label>
    <label>Label<input value={label} onChange={e => setLabel(e.target.value)} placeholder="defaults to the file name" /></label>
    <label>Document type<select value={kind} onChange={e => setKind(e.target.value)}>{sourceTypes.map(t => <option key={t}>{t}</option>)}</select></label>
    <label>Document date (optional)<input type="date" value={date} onChange={e => setDate(e.target.value)} /></label>
    {error && <p className="error" role="alert">{error}</p>}
    <button className="primary" type="submit" disabled={busy || !file}>{busy ? 'Importing…' : 'Import document'}</button>
  </form>;
}
