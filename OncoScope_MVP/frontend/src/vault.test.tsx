import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { setSession } from './api';
import { RecoveryKeyScreen } from './components/RecoveryKey';
import { SecurityView } from './components/SecurityView';
import Root from './Root';

const KEY = 'ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567-ABCD-EFGH-IJKL-MNOP-QRST-WXYZ';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const session = (extra = {}) => ({ token: 'fictional-token', actor: { id: 'owner:1', label: 'Fictional Owner' }, idle_seconds: 900,
  max_seconds: 28800, vault: { key_id: 'k'.repeat(32), recovery_confirmed: false }, recovery_key: null, ...extra });

afterEach(() => { cleanup(); vi.restoreAllMocks(); setSession(null); localStorage.clear(); sessionStorage.clear(); });

function route(handlers: Record<string, (init?: RequestInit) => Response>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const key = Object.keys(handlers).find(k => url === `/api${k}` || url.startsWith(`/api${k}?`));
    return key ? handlers[key](init) : json([]);
  });
}

describe('RecoveryKeyScreen', () => {
  it('shows the key once, copies it, and requires saving to be confirmed', async () => {
    const fetchMock = route({ '/vault/recovery-key/confirm': () => json({ recovery_confirmed: true }) });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const done = vi.fn();
    render(<RecoveryKeyScreen recoveryKey={KEY} onConfirmed={done} onLock={() => {}} />);
    expect(screen.getByTestId('recovery-key').textContent).toBe(KEY);
    expect(document.body.textContent).toContain('cannot be recovered by anyone');
    fireEvent.click(screen.getByRole('button', { name: 'Copy recovery key' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(KEY));
    fireEvent.submit(screen.getByRole('button', { name: 'Continue to OncoScope' }));
    expect(screen.getByRole('alert').textContent).toContain('Tick the box');
    fireEvent.click(screen.getByLabelText('I have saved this recovery key somewhere safe'));
    fireEvent.change(screen.getByLabelText(/Type the last group/), { target: { value: 'ABCD' } });
    fireEvent.submit(screen.getByRole('button', { name: 'Continue to OncoScope' }));
    expect(screen.getByRole('alert').textContent).toContain('does not match');
    fireEvent.change(screen.getByLabelText(/Type the last group/), { target: { value: 'wxyz' } });
    fireEvent.submit(screen.getByRole('button', { name: 'Continue to OncoScope' }));
    await waitFor(() => expect(done).toHaveBeenCalled());
    expect(fetchMock.mock.calls.some(c => String(c[0]) === '/api/vault/recovery-key/confirm')).toBe(true);
    expect(fetchMock.mock.calls.every(c => !String(c[0]).includes(KEY.slice(0, 9)))).toBe(true);   // never in a URL
    expect(JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage })).not.toContain('ABCD');
  });
});

describe('Root with the key vault', () => {
  it('after setup shows the recovery key before any case data, then the workspace', async () => {
    route({ '/auth/status': () => json({ mode: 'protected', setup_required: true, recovery_available: false }),
      '/auth/setup': () => json(session({ recovery_key: KEY })), '/vault/recovery-key/confirm': () => json({ recovery_confirmed: true }),
      '/patients': () => json([{ id: 1, uid: 'u', label: 'Fictional Case Alpha', is_synthetic: 1 }]),
      '/patients/1/dashboard': () => json({ counts: { sources: 0, events: 0, labs: 0, treatments: 0, studies: 0, measurements: 0 } }),
      '/patients/1/labs/series': () => json({ test: 'CA-125', unit: 'U/mL', available_units: [], points: [], excluded: [], note: '' }) });
    render(<Root />);
    fireEvent.change(await screen.findByLabelText('Owner name'), { target: { value: 'Fictional Owner' } });
    fireEvent.change(screen.getByLabelText('Passphrase'), { target: { value: 'fictional passphrase' } });
    fireEvent.change(screen.getByLabelText('Repeat passphrase'), { target: { value: 'fictional passphrase' } });
    fireEvent.submit(screen.getByRole('button', { name: 'Create owner and unlock' }));
    await screen.findByRole('heading', { name: 'Save your recovery key' });
    expect(screen.queryByText('Fictional Case Alpha')).toBeNull();
    fireEvent.click(screen.getByLabelText('I have saved this recovery key somewhere safe'));
    fireEvent.change(screen.getByLabelText(/Type the last group/), { target: { value: 'WXYZ' } });
    fireEvent.submit(screen.getByRole('button', { name: 'Continue to OncoScope' }));
    await screen.findByText('Fictional Case Alpha');
    expect(screen.queryByTestId('recovery-key')).toBeNull();
  });

  it('offers recovery on the lock screen and sends the key only in the request body', async () => {
    const fetchMock = route({ '/auth/status': () => json({ mode: 'protected', setup_required: false, recovery_available: true }),
      '/auth/recover': () => json(session({ vault: { key_id: 'k'.repeat(32), recovery_confirmed: true } })), '/patients': () => json([]) });
    render(<Root />);
    fireEvent.click(await screen.findByRole('button', { name: 'Forgot the passphrase? Use your recovery key' }));
    expect(screen.getByRole('heading', { name: 'Recover access' })).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Recovery key'), { target: { value: KEY } });
    fireEvent.change(screen.getByLabelText('New passphrase'), { target: { value: 'new fictional passphrase' } });
    fireEvent.change(screen.getByLabelText('Repeat new passphrase'), { target: { value: 'new fictional passphrase' } });
    fireEvent.submit(screen.getByRole('button', { name: 'Recover and set new passphrase' }));
    await screen.findByText('No case yet');
    const call = fetchMock.mock.calls.find(c => String(c[0]) === '/api/auth/recover')!;
    expect(JSON.parse(String((call[1] as RequestInit).body))).toEqual({ recovery_key: KEY, new_passphrase: 'new fictional passphrase' });
  });

  it('explains a wrong recovery key without detail', async () => {
    route({ '/auth/status': () => json({ mode: 'protected', setup_required: false, recovery_available: true }),
      '/auth/recover': () => json({ detail: 'Incorrect recovery key' }, 401) });
    render(<Root />);
    fireEvent.click(await screen.findByRole('button', { name: 'Forgot the passphrase? Use your recovery key' }));
    fireEvent.change(screen.getByLabelText('Recovery key'), { target: { value: KEY } });
    fireEvent.change(screen.getByLabelText('New passphrase'), { target: { value: 'new fictional passphrase' } });
    fireEvent.change(screen.getByLabelText('Repeat new passphrase'), { target: { value: 'new fictional passphrase' } });
    fireEvent.submit(screen.getByRole('button', { name: 'Recover and set new passphrase' }));
    expect((await screen.findByRole('alert')).textContent).toBe('That recovery key does not open this installation.');
  });
});

describe('SecurityView', () => {
  it('shows identifiers only and changes the passphrase', async () => {
    const status = { vault_id: 'v-1', key_id: 'a'.repeat(32), version: 1, created_at: '2026-10-27T00:00:00Z', updated_at: '2026-10-27T00:00:00Z',
      recovery_confirmed: true, passphrase_kdf: { kdf: 'argon2id', time_cost: 3, memory_cost: 65536, parallelism: 4 },
      derived_key_fingerprints: { database: '0123456789abcdef' }, encrypted_at_rest: false };
    const fetchMock = route({ '/vault/status': () => json(status), '/vault/passphrase': () => json({ changed: true, key_id: status.key_id }) });
    render(<SecurityView />);
    expect((await screen.findByTestId('key-id')).textContent).toBe(status.key_id);
    expect(document.body.textContent).toContain('Not encrypted yet');
    fireEvent.change(screen.getByLabelText('Current passphrase'), { target: { value: 'fictional passphrase' } });
    fireEvent.change(screen.getByLabelText('New passphrase'), { target: { value: 'new fictional passphrase' } });
    fireEvent.change(screen.getByLabelText('Repeat new passphrase'), { target: { value: 'different passphrase!' } });
    fireEvent.submit(screen.getByRole('button', { name: 'Change passphrase' }));
    expect(screen.getByRole('alert').textContent).toContain('do not match');
    fireEvent.change(screen.getByLabelText('Current passphrase'), { target: { value: 'fictional passphrase' } });
    fireEvent.change(screen.getByLabelText('New passphrase'), { target: { value: 'new fictional passphrase' } });
    fireEvent.change(screen.getByLabelText('Repeat new passphrase'), { target: { value: 'new fictional passphrase' } });
    fireEvent.submit(screen.getByRole('button', { name: 'Change passphrase' }));
    expect((await screen.findByRole('status')).textContent).toContain(`key ID ${status.key_id}`);
    expect(fetchMock.mock.calls.some(c => String(c[0]) === '/api/vault/passphrase')).toBe(true);
    expect((screen.getByLabelText('Current passphrase') as HTMLInputElement).value).toBe('');
  });
});
