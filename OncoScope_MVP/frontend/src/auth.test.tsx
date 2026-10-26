import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { downloadFile, fetchBlob, hasSession, request, setSession, setSessionEndedHandler } from './api';
import { LockScreen } from './components/LockScreen';
import Root from './Root';

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } });
const session = { token: 'fictional-token', actor: { id: 'owner:1', label: 'Fictional Owner' }, idle_seconds: 900, max_seconds: 28800 };

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); setSession(null); setSessionEndedHandler(null); localStorage.clear(); sessionStorage.clear(); });

describe('session token handling', () => {
  it('sends the token only in the Authorization header, never in the URL or storage', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json([]));
    setSession('fictional-token');
    await request('/patients');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/patients');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer fictional-token');
    expect(init.credentials).toBe('omit');
    expect(localStorage.length + sessionStorage.length).toBe(0);
    expect(document.cookie).toBe('');
  });

  it('forgets the token and reports the end of the session on a 401', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ detail: 'OncoScope is locked. Unlock it to continue.' }, 401));
    const ended = vi.fn();
    setSessionEndedHandler(ended);
    setSession('fictional-token');
    await expect(request('/patients')).rejects.toThrow('locked');
    expect(hasSession()).toBe(false);
    expect(ended).toHaveBeenCalledTimes(1);
  });

  it('downloads authorized files through blob URLs that carry no token', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('# Fictional report', { headers: { 'Content-Type': 'text/markdown' } }));
    const created: string[] = [];
    URL.createObjectURL = vi.fn(() => { created.push('blob:local/1'); return 'blob:local/1'; });
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    setSession('fictional-token');
    await downloadFile('/patients/1/report.md', 'report.md');
    expect((fetchMock.mock.calls[0][1] as RequestInit).headers).toMatchObject({ Authorization: 'Bearer fictional-token' });
    expect(click).toHaveBeenCalled();
    expect(created.every(u => !u.includes('fictional-token'))).toBe(true);
  });

  it('never renders unexpected media types in the app origin', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('<script>', { headers: { 'Content-Type': 'text/html' } }));
    expect((await fetchBlob('/sources/1/document')).type).toBe('application/octet-stream');
  });
});

describe('LockScreen', () => {
  it('checks the new passphrase before setup and explains that nothing is encrypted yet', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    render(<LockScreen setupRequired notice="" onUnlocked={() => {}} />);
    expect(screen.getByRole('heading', { name: 'Set up this installation' })).toBeTruthy();
    expect(document.body.textContent).toContain('does not yet encrypt the database or documents on disk');
    fireEvent.change(screen.getByLabelText('Owner name'), { target: { value: 'Fictional Owner' } });
    fireEvent.change(screen.getByLabelText('Passphrase'), { target: { value: 'short' } });
    fireEvent.submit(screen.getByRole('button', { name: 'Create owner and unlock' }));
    expect(screen.getByRole('alert').textContent).toContain('at least 12');
    fireEvent.change(screen.getByLabelText('Passphrase'), { target: { value: 'fictional passphrase' } });
    fireEvent.change(screen.getByLabelText('Repeat passphrase'), { target: { value: 'different passphrase' } });
    fireEvent.submit(screen.getByRole('button', { name: 'Create owner and unlock' }));
    expect(screen.getByRole('alert').textContent).toContain('do not match');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('shows a plain message for a wrong passphrase and for rate limiting, and clears the field', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ detail: 'Incorrect passphrase' }, 401))
      .mockResolvedValueOnce(json({ detail: 'Too many' }, 429, { 'Retry-After': '30' }));
    const unlocked = vi.fn();
    render(<LockScreen setupRequired={false} notice="Your session ended. Unlock again to continue." onUnlocked={unlocked} />);
    expect(screen.getByRole('status').textContent).toContain('session ended');
    const field = screen.getByLabelText('Passphrase') as HTMLInputElement;
    fireEvent.change(field, { target: { value: 'fictional wrong passphrase' } });
    fireEvent.submit(screen.getByRole('button', { name: 'Unlock' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('Incorrect passphrase.'));
    expect(field.value).toBe('');
    fireEvent.change(field, { target: { value: 'fictional wrong passphrase' } });
    fireEvent.submit(screen.getByRole('button', { name: 'Unlock' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Wait 30 seconds'));
    expect(unlocked).not.toHaveBeenCalled();
  });
});

describe('Root', () => {
  const caseData: Record<string, () => Response> = {
    '/patients/1/dashboard': () => json({ counts: { sources: 0, events: 0, labs: 0, treatments: 0, studies: 0, measurements: 0 } }),
    '/patients/1/labs/series': () => json({ test: 'CA-125', unit: 'U/mL', available_units: [], points: [], excluded: [], note: '' }),
    '/audit/verify': () => json({ valid: true, entries_checked: 0, first_invalid_id: null }),
  };
  function route(own: Record<string, () => Response>) {
    const handlers = { ...caseData, ...own };
    return vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
      const url = String(input);
      const key = Object.keys(handlers).find(k => url === `/api${k}` || url.startsWith(`/api${k}?`));
      return key ? handlers[key]() : json([]);
    });
  }

  it('shows only the lock screen until unlocked, then loads cases; locking discards them', async () => {
    const fetchMock = route({ '/auth/status': () => json({ mode: 'protected', setup_required: false }), '/auth/unlock': () => json(session),
      '/patients': () => json([{ id: 1, uid: 'u', label: 'Fictional Case Alpha', is_synthetic: 1 }]), '/auth/lock': () => json({ locked: true }) });
    render(<Root />);
    await screen.findByRole('heading', { name: 'OncoScope is locked' });
    expect(fetchMock.mock.calls.map(c => String(c[0]))).toEqual(['/api/auth/status']);
    fireEvent.change(screen.getByLabelText('Passphrase'), { target: { value: 'fictional passphrase' } });
    fireEvent.submit(screen.getByRole('button', { name: 'Unlock' }));
    await screen.findByText('Fictional Case Alpha');
    expect(screen.getByTestId('session-box').textContent).toContain('Fictional Owner');
    fireEvent.click(screen.getByRole('button', { name: 'Lock now' }));
    await screen.findByRole('heading', { name: 'OncoScope is locked' });
    expect(screen.queryByText('Fictional Case Alpha')).toBeNull();
    expect(hasSession()).toBe(false);
    const lockCall = fetchMock.mock.calls.find(c => String(c[0]) === '/api/auth/lock')!;
    expect((lockCall[1] as RequestInit).headers).toMatchObject({ Authorization: 'Bearer fictional-token' });
  });

  it('returns to the lock screen when the server ends the session', async () => {
    let expired = false;
    route({ '/auth/status': () => json({ mode: 'protected', setup_required: false }), '/auth/unlock': () => json(session),
      '/patients': () => expired ? json({ detail: 'locked' }, 401) : json([{ id: 1, uid: 'u', label: 'Fictional Case Alpha', is_synthetic: 1 }]) });
    render(<Root />);
    fireEvent.change(await screen.findByLabelText('Passphrase'), { target: { value: 'fictional passphrase' } });
    fireEvent.submit(screen.getByRole('button', { name: 'Unlock' }));
    await screen.findByText('Fictional Case Alpha');
    expired = true;
    fireEvent.click(screen.getByRole('button', { name: /Audit/ }));   // any action that reaches the server
    await act(async () => { await request('/patients').catch(() => {}); });
    await screen.findByText('Your session ended. Unlock again to continue.');
    expect(screen.queryByText('Fictional Case Alpha')).toBeNull();
  });

  it('locks itself after the idle limit without activity', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    route({ '/auth/status': () => json({ mode: 'protected', setup_required: false }), '/auth/unlock': () => json({ ...session, idle_seconds: 60 }),
      '/patients': () => json([{ id: 1, uid: 'u', label: 'Fictional Case Alpha', is_synthetic: 1 }]), '/auth/lock': () => json({ locked: true }) });
    render(<Root />);
    fireEvent.change(await screen.findByLabelText('Passphrase'), { target: { value: 'fictional passphrase' } });
    fireEvent.submit(screen.getByRole('button', { name: 'Unlock' }));
    await screen.findByText('Fictional Case Alpha');
    await act(async () => { vi.advanceTimersByTime(59_000); });
    fireEvent.keyDown(window, { key: 'a' });                          // activity restarts the countdown
    await act(async () => { vi.advanceTimersByTime(59_000); });
    expect(screen.queryByText('Fictional Case Alpha')).not.toBeNull();
    await act(async () => { vi.advanceTimersByTime(2_000); });
    await screen.findByText('Locked after 1 minute(s) without activity.');
    expect(screen.queryByText('Fictional Case Alpha')).toBeNull();
  });

  it('runs demonstration mode without a lock screen and says so', async () => {
    route({ '/auth/status': () => json({ mode: 'demo', setup_required: false }) });
    render(<Root />);
    expect((await screen.findByTestId('demo-banner')).textContent).toContain('Demonstration mode');
    expect(screen.queryByRole('button', { name: 'Lock now' })).toBeNull();
  });
});
