import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, post } from '../api';

export type SessionInfo = { token: string; actor: { id: string; label: string }; idle_seconds: number; max_seconds: number };

type Props = { setupRequired: boolean; notice: string; onUnlocked: (session: SessionInfo) => void };

const MIN_PASSPHRASE = 12;

/** First-launch owner setup, or unlocking. Shows no case information; nothing is loaded until a session exists. */
export function LockScreen({ setupRequired, notice, onUnlocked }: Props) {
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
      <h1 id="lock-title">{setupRequired ? 'Set up this installation' : 'OncoScope is locked'}</h1>
      {notice && <p className="lock-notice" role="status">{notice}</p>}
      <p className="muted">{setupRequired
        ? 'Create the local owner. Every change you make will be recorded under this name. There is no account recovery yet: if you forget the passphrase, the case data cannot be opened through OncoScope.'
        : 'Enter the owner passphrase to open your cases.'}</p>
      <form onSubmit={submit} className="lock-form" noValidate>
        {setupRequired && <label>Owner name<input ref={first} value={name} onChange={e => setName(e.target.value)} autoComplete="username" required maxLength={80} /></label>}
        <label>Passphrase<input ref={setupRequired ? undefined : first} type="password" value={passphrase} onChange={e => setPassphrase(e.target.value)}
          autoComplete={setupRequired ? 'new-password' : 'current-password'} required aria-describedby={setupRequired ? 'passphrase-help' : undefined} /></label>
        {setupRequired && <><p id="passphrase-help" className="tiny muted">At least {MIN_PASSPHRASE} characters. A few unrelated words work well.</p>
          <label>Repeat passphrase<input type="password" value={confirm} onChange={e => setConfirm(e.target.value)} autoComplete="new-password" required /></label></>}
        {error && <p className="error" role="alert">{error}</p>}
        <button type="submit" className="primary" disabled={busy}>{busy ? 'Checking…' : setupRequired ? 'Create owner and unlock' : 'Unlock'}</button>
      </form>
      <div className="integrity-note"><strong>What this protects</strong>
        <p>The lock stops OncoScope's own server from showing or changing case data without your passphrase. It does <b>not</b> yet encrypt the database or documents on disk: anyone who can read those files can still read them. Use fictional data only.</p></div>
    </section>
  </main>;
}
