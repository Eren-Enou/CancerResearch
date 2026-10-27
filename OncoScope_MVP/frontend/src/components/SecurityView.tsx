import { useEffect, useState, type FormEvent } from 'react';
import { ApiError, post, request } from '../api';

type VaultStatus = {
  vault_id: string; key_id: string; version: number; created_at: string; updated_at: string; recovery_confirmed: boolean;
  passphrase_kdf: { kdf: string; time_cost: number; memory_cost: number; parallelism: number };
  derived_key_fingerprints: Record<string, string>;
  database: { state: string; cipher: string; sqlcipher_version: string; key_fingerprint: string; migrated_from_plaintext_at: string | null };
  encrypted_at_rest: { database: boolean; documents: boolean; backups: string };
};

const MIN_PASSPHRASE = 12;

/** Key vault facts (identifiers only, never keys) and passphrase change. */
export function SecurityView() {
  const [info, setInfo] = useState<VaultStatus | null>(null);
  const [error, setError] = useState('');
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [repeat, setRepeat] = useState('');
  const [message, setMessage] = useState('');
  const [formError, setFormError] = useState('');
  const load = () => request<VaultStatus>('/vault/status').then(setInfo).catch(e => setError(e.message));
  useEffect(() => { load(); }, []);

  async function change(e: FormEvent) {
    e.preventDefault();
    setFormError(''); setMessage('');
    if (next.length < MIN_PASSPHRASE) { setFormError(`Choose a new passphrase of at least ${MIN_PASSPHRASE} characters.`); return; }
    if (next !== repeat) { setFormError('The two new passphrases do not match.'); return; }
    try {
      const r = await post<{ key_id: string }>('/vault/passphrase', { current_passphrase: current, new_passphrase: next });
      setMessage(`Passphrase changed. The data key is the same (key ID ${r.key_id}); your recovery key still works.`);
      load();
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      setFormError(status === 403 ? 'The current passphrase is incorrect.' : (err as Error).message);
    } finally { setCurrent(''); setNext(''); setRepeat(''); }
  }

  return <>
    <section className="panel"><div className="section-heading"><div><h2>Key vault</h2>
      <p className="muted">The vault holds OncoScope's data key, protected by your passphrase and, separately, by your recovery key. Only identifiers are shown here, never keys.</p></div></div>
      <div className="unit-warning"><strong>Documents are not encrypted yet.</strong> The database (records, evidence links, audit history) is encrypted on disk. Original documents, backup manifests and any plaintext pre-migration backup are still readable files.</div>
      {error && <p className="error" role="alert">{error}</p>}
      {info && <dl className="vault-facts" data-testid="vault-status">
        <dt>Database</dt><dd data-testid="database-encryption">{info.encrypted_at_rest.database ? 'Encrypted' : 'Not encrypted'} · {info.database.cipher} · SQLCipher {info.database.sqlcipher_version}
          {info.database.migrated_from_plaintext_at && <> · migrated from plaintext {info.database.migrated_from_plaintext_at}</>}</dd>
        <dt>Database key fingerprint</dt><dd className="hash-inline" data-testid="database-key">{info.database.key_fingerprint}</dd>
        <dt>Key ID</dt><dd data-testid="key-id" className="hash-inline">{info.key_id}</dd>
        <dt>Vault</dt><dd className="hash-inline">{info.vault_id} · format version {info.version}</dd>
        <dt>Created / last changed</dt><dd>{info.created_at} / {info.updated_at}</dd>
        <dt>Recovery key</dt><dd>{info.recovery_confirmed ? 'saved (confirmed by you)' : 'not yet confirmed'}</dd>
        <dt>Passphrase protection</dt><dd>{info.passphrase_kdf.kdf}, {info.passphrase_kdf.time_cost} passes, {Math.round(info.passphrase_kdf.memory_cost / 1024)} MiB, {info.passphrase_kdf.parallelism} lanes</dd>
        <dt>Derived keys (database in use; the others for later versions)</dt><dd><ul className="tiny" data-testid="derived-keys">{Object.entries(info.derived_key_fingerprints).map(([purpose, fp]) =>
          <li key={purpose}><b>{purpose}</b> fingerprint <span className="hash-inline" data-purpose={purpose}>{fp}</span></li>)}</ul></dd>
      </dl>}
    </section>
    <section className="panel form-panel"><h2>Change passphrase</h2>
      <p className="muted">Re-protects the same data key, so the database stays encrypted with the same key and nothing is re-encrypted. Your recovery key keeps working.</p>
      <form onSubmit={change} aria-label="Change passphrase" noValidate>
        <label>Current passphrase<input type="password" value={current} onChange={e => setCurrent(e.target.value)} autoComplete="current-password" /></label>
        <label>New passphrase<input type="password" value={next} onChange={e => setNext(e.target.value)} autoComplete="new-password" /></label>
        <label>Repeat new passphrase<input type="password" value={repeat} onChange={e => setRepeat(e.target.value)} autoComplete="new-password" /></label>
        {formError && <p className="error" role="alert">{formError}</p>}
        {message && <p className="success" role="status">{message}</p>}
        <button type="submit" className="primary">Change passphrase</button>
      </form>
    </section>
  </>;
}
