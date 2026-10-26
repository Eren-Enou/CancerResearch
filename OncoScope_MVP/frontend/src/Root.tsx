import { useCallback, useEffect, useState } from 'react';
import App from './App';
import { post, request, setSession, setSessionEndedHandler } from './api';
import { LockScreen, type SessionInfo } from './components/LockScreen';

type Status = { mode: 'protected' | 'demo'; setup_required: boolean };
export type Access = { mode: 'demo' } | { mode: 'protected'; actor: SessionInfo['actor']; onLock: () => void };

const ACTIVITY = ['pointerdown', 'keydown', 'wheel', 'touchstart'] as const;

/**
 * Decides what may be shown. In protected mode the workspace (and every request for case data) exists only while a
 * session does; locking unmounts it, which discards all case data held in component state.
 */
export default function Root() {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState('');
  const [session, setSessionState] = useState<SessionInfo | null>(null);
  const [notice, setNotice] = useState('');
  const [generation, setGeneration] = useState(0);

  const endSession = useCallback((message: string) => {
    setSession(null);
    setSessionState(null);
    setNotice(message);
    setStatus(s => s && { ...s, setup_required: false });
  }, []);

  useEffect(() => {
    request<Status>('/auth/status').then(setStatus).catch(() => setError('Could not reach the local OncoScope server at 127.0.0.1.'));
  }, []);

  useEffect(() => {
    setSessionEndedHandler(() => endSession('Your session ended. Unlock again to continue.'));
    return () => setSessionEndedHandler(null);
  }, [endSession]);

  const lock = useCallback((message = 'Locked.') => {
    post('/auth/lock', {}).catch(() => { /* the session is forgotten locally either way */ });
    endSession(message);
  }, [endSession]);

  // The server ends every session after max_seconds; clear the screen then too, instead of waiting for the next request.
  useEffect(() => {
    if (!session) return;
    const timer = window.setTimeout(() => endSession('Your session ended. Unlock again to continue.'), session.max_seconds * 1000);
    return () => window.clearTimeout(timer);
  }, [session, endSession]);

  // Inactivity lock on the client, matching the server's idle limit (which also ends the session on its side).
  useEffect(() => {
    if (!session) return;
    let timer = 0;
    const minutes = Math.max(1, Math.round(session.idle_seconds / 60));
    const reset = () => { window.clearTimeout(timer); timer = window.setTimeout(() => lock(`Locked after ${minutes} minute(s) without activity.`), session.idle_seconds * 1000); };
    reset();
    ACTIVITY.forEach(e => window.addEventListener(e, reset, { passive: true }));
    return () => { window.clearTimeout(timer); ACTIVITY.forEach(e => window.removeEventListener(e, reset)); };
  }, [session, lock]);

  if (error) return <main className="lock-shell"><p className="error-banner" role="alert">{error}</p></main>;
  if (!status) return <main className="lock-shell"><p className="muted" role="status">Connecting to the local server…</p></main>;
  if (status.mode === 'demo') return <App access={{ mode: 'demo' }} />;
  if (!session) return <LockScreen setupRequired={status.setup_required} notice={notice}
    onUnlocked={s => { setSession(s.token); setNotice(''); setGeneration(g => g + 1); setSessionState(s); }} />;
  return <App key={generation} access={{ mode: 'protected', actor: session.actor, onLock: () => lock() }} />;
}
