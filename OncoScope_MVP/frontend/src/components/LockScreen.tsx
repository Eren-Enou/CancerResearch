import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, post } from '../api';
import { RecoverForm } from './RecoveryKey';

export type SessionInfo = { token: string; actor: { id: string; label: string }; idle_seconds: number; max_seconds: number;
  vault?: { key_id: string; recovery_confirmed: boolean }; recovery_key?: string | null };

type Props = { setupRequired: boolean; recoveryAvailable?: boolean; notice: string; onUnlocked: (session: SessionInfo) => void };

const MIN_PASSPHRASE = 12;

const STORAGE_TITLES: Record<string, string> = {
  migration_required: 'Encrypt the database first',
  migration_interrupted: 'The encryption migration did not finish',
  refused: 'OncoScope will not open this data',
};

/** Shown instead of any unlock form when the files on disk cannot be opened safely (decided by the server without
 * opening the database). Nothing here offers a way around the refusal. */
export function StorageNotice({ storage, message, onRetry }: { storage: string; message: string; onRetry: () => void }) {
  return <main className="lock-shell">
    <section className="panel lock-panel" aria-labelledby="storage-title">
      <div className="brand"><div className="brand-mark" aria-hidden="true">◎</div><div><strong>ONCOSCOPE</strong><span>PROTECTED LOCAL WORKSPACE</span></div></div>
      <h1 id="storage-title">{STORAGE_TITLES[storage] ?? 'OncoScope cannot open this data'}</h1>
      <p className="lock-notice" role="alert" data-testid="storage-message">{message}</p>
      {storage !== 'refused' && <p className="muted">Stop the OncoScope server, then run this in the <code>backend</code> folder. It checks your passphrase,
        makes a verified backup, encrypts a copy, verifies it, and only then replaces the database:</p>}
      {storage !== 'refused' && <pre className="command">python -m app.encrypt_migrate</pre>}
      <button type="button" className="primary" onClick={onRetry}>Check again</button>
    </section>
  </main>;
}

/** First-launch owner setup, or unlocking. Shows no case information; nothing is loaded until a session exists. */
export function LockScreen({ setupRequired, recoveryAvailable = false, notice, onUnlocked }: Props) {
  const [recovering, setRecovering] = useState(false);
  const [name, setName] = useState('');
  const [passphrase, setPassphrase] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const first = useRef<HTMLInputElement>(null);
  useEffect(() => { first.current?.focus(); }, [setupRequired]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError('');
    if (setupRequired) {
      if (passphrase.length < MIN_PASSPHRASE) { setError(`Choose a passphrase of at least ${MIN_PASSPHRASE} characters.`); return; }
      if (passphrase !== confirm) { setError('The two passphrases do not match.'); return; }
    }
    setBusy(true);
    try {
      const session = setupRequired
        ? await post<SessionInfo>('/auth/setup', { display_name: name, passphrase })
        : await post<SessionInfo>('/auth/unlock', { passphrase });
      setPassphrase(''); setConfirm('');
      onUnlocked(session);
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      // Only these messages are shown; nothing about the stored owner or verifier is revealed.
      setError(status === 401 ? 'Incorrect passphrase.'
        : status === 429 ? `Too many incorrect attempts. Wait ${(err as ApiError).retryAfter ?? 'a few'} seconds, then try again.`
        : status === 0 ? 'Could not reach the local OncoScope server.' : (err as Error).message);
      setPassphrase(''); setConfirm('');
      first.current?.focus();
    } finally { setBusy(false); }
  }

  return <main className="lock-shell">
    <section className="panel lock-panel" aria-labelledby="lock-title">
      <div className="brand"><div className="brand-mark" aria-hidden="true">◎</div><div><strong>ONCOSCOPE</strong><span>PROTECTED LOCAL WORKSPACE</span></div></div>
      <h1 id="lock-title">{setupRequired ? 'Set up this installation' : recovering ? 'Recover access' : 'OncoScope is locked'}</h1>
      {notice && <p className="lock-notice" role="status">{notice}</p>}
      <p className="muted">{setupRequired
        ? 'Create the local owner. Every change you make will be recorded under this name. Next you will get a recovery key: the only other way in if you forget the passphrase.'
        : recovering ? 'Enter the recovery key you saved and choose a new passphrase. Your data key stays the same.' : 'Enter the owner passphrase to open your cases.'}</p>
      {recovering ? <RecoverForm onRecovered={onUnlocked} onCancel={() => setRecovering(false)} /> : <form onSubmit={submit} className="lock-form" noValidate>
        {setupRequired && <label>Owner name<input ref={first} value={name} onChange={e => setName(e.target.value)} autoComplete="username" required maxLength={80} /></label>}
        <label>Passphrase<input ref={setupRequired ? undefined : first} type="password" value={passphrase} onChange={e => setPassphrase(e.target.value)}
          autoComplete={setupRequired ? 'new-password' : 'current-password'} required aria-describedby={setupRequired ? 'passphrase-help' : undefined} /></label>
        {setupRequired && <><p id="passphrase-help" className="tiny muted">At least {MIN_PASSPHRASE} characters. A few unrelated words work well.</p>
          <label>Repeat passphrase<input type="password" value={confirm} onChange={e => setConfirm(e.target.value)} autoComplete="new-password" required /></label></>}
        {error && <p className="error" role="alert">{error}</p>}
        <button type="submit" className="primary" disabled={busy}>{busy ? 'Checking…' : setupRequired ? 'Create owner and unlock' : 'Unlock'}</button>
        {!setupRequired && recoveryAvailable && <button type="button" className="text-button" onClick={() => setRecovering(true)}>Forgot the passphrase? Use your recovery key</button>}
      </form>}
      <div className="integrity-note"><strong>What this protects</strong>
        <p>The lock stops OncoScope's own server from showing or changing case data without your passphrase, and the database on disk is encrypted with a key that only your passphrase or recovery key can unlock. Original documents are <b>not</b> encrypted yet: anyone who can read the documents folder can still read them. Use fictional data only.</p></div>
    </section>
  </main>;
}
