import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, post } from '../api';
import type { SessionInfo } from './LockScreen';

const MIN_PASSPHRASE = 12;

/**
 * Shown once, right after the server issues a recovery key. The key lives only in this component's state: it is never
 * written to storage, the URL or a file, and it is dropped as soon as the owner confirms or the session ends.
 */
export function RecoveryKeyScreen({ recoveryKey, onConfirmed, onLock }: { recoveryKey: string; onConfirmed: () => void; onLock: () => void }) {
  const [saved, setSaved] = useState(false);
  const [lastGroup, setLastGroup] = useState('');
  const [copied, setCopied] = useState('');
  const [error, setError] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, []);
  const expected = recoveryKey.slice(-4);

  async function copy() {
    try {
      await navigator.clipboard.writeText(recoveryKey);
      setCopied('Copied. Paste it into your password manager or write it down, then clear the clipboard.');
    } catch {
      setCopied('Copying is not available here. Select the key and copy it, or write it down.');
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError('');
    if (!saved) { setError('Tick the box once the recovery key is saved somewhere safe.'); return; }
    if (lastGroup.trim().toUpperCase() !== expected) { setError('The last group does not match. Check the key you saved.'); return; }
    try { await post('/vault/recovery-key/confirm', {}); onConfirmed(); } catch (err) { setError((err as Error).message); }
  }

  return <main className="lock-shell">
    <section className="panel lock-panel recovery-panel" aria-labelledby="recovery-title">
      <h1 id="recovery-title" ref={heading} tabIndex={-1}>Save your recovery key</h1>
      <p>This key is the only other way into OncoScope if you forget your passphrase. <strong>It is shown only now</strong> and is not stored anywhere in a readable form.</p>
      <code className="recovery-key" data-testid="recovery-key" aria-label="Recovery key">{recoveryKey}</code>
      <div className="transcribe-row"><button type="button" className="lock-button" onClick={copy}>Copy recovery key</button></div>
      {copied && <p className="tiny" role="status">{copied}</p>}
      <div className="unit-warning"><strong>Keep it outside this computer</strong>: in a password manager or on paper in a safe place.
        If you lose both the passphrase and this recovery key, the encrypted database cannot be opened by anyone, including you. There is no reset.</div>
      <form onSubmit={submit} className="lock-form" noValidate>
        <label className="check-label"><input type="checkbox" checked={saved} onChange={e => setSaved(e.target.checked)} /> I have saved this recovery key somewhere safe</label>
        <label>Type the last group of the key ({expected.length} characters) to confirm<input value={lastGroup} onChange={e => setLastGroup(e.target.value)} autoComplete="off" spellCheck={false} maxLength={4} /></label>
        {error && <p className="error" role="alert">{error}</p>}
        <button type="submit" className="primary">Continue to OncoScope</button>
        <button type="button" className="text-button" onClick={onLock}>Lock instead (a new recovery key will be shown next time)</button>
      </form>
    </section>
  </main>;
}

/** Recovery-key unlock: proves the recovery key, sets a new passphrase, opens a session. */
export function RecoverForm({ onRecovered, onCancel }: { onRecovered: (s: SessionInfo) => void; onCancel: () => void }) {
  const [key, setKey] = useState('');
  const [passphrase, setPassphrase] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const first = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { first.current?.focus(); }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError('');
    if (passphrase.length < MIN_PASSPHRASE) { setError(`Choose a new passphrase of at least ${MIN_PASSPHRASE} characters.`); return; }
    if (passphrase !== confirm) { setError('The two new passphrases do not match.'); return; }
    setBusy(true);
    try {
      const session = await post<SessionInfo>('/auth/recover', { recovery_key: key, new_passphrase: passphrase });
      setKey(''); setPassphrase(''); setConfirm('');
      onRecovered(session);
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      setError(status === 401 ? 'That recovery key does not open this installation.'
        : status === 429 ? `Too many incorrect attempts. Wait ${(err as ApiError).retryAfter ?? 'a few'} seconds, then try again.`
        : status === 0 ? 'Could not reach the local OncoScope server.' : (err as Error).message);
      setPassphrase(''); setConfirm('');
    } finally { setBusy(false); }
  }

  return <form onSubmit={submit} className="lock-form" aria-label="Recover with recovery key" noValidate>
    <label>Recovery key<textarea ref={first} value={key} onChange={e => setKey(e.target.value)} rows={2} autoComplete="off" spellCheck={false}
      placeholder="XXXX-XXXX-…" /></label>
    <label>New passphrase<input type="password" value={passphrase} onChange={e => setPassphrase(e.target.value)} autoComplete="new-password" /></label>
    <label>Repeat new passphrase<input type="password" value={confirm} onChange={e => setConfirm(e.target.value)} autoComplete="new-password" /></label>
    {error && <p className="error" role="alert">{error}</p>}
    <button type="submit" className="primary" disabled={busy}>{busy ? 'Checking…' : 'Recover and set new passphrase'}</button>
    <button type="button" className="text-button" onClick={onCancel}>Back to passphrase unlock</button>
  </form>;
}
