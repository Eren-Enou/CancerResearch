import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { setSession } from './api';
import Root from './Root';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const status = (storage: string, message: string) => ({ mode: 'protected', setup_required: false, recovery_available: false,
  storage, storage_message: message, database_encrypted: false, documents_encrypted: false });

afterEach(() => { cleanup(); vi.restoreAllMocks(); setSession(null); });

describe('Root when the files on disk cannot be opened safely (M3.1-C)', () => {
  it('asks for the migration and offers no unlock form for a plaintext database', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      json(status('migration_required', "This installation's database is not encrypted yet.")));
    render(<Root />);
    expect(await screen.findByRole('heading', { name: 'Encrypt the database first' })).toBeTruthy();
    expect(screen.getByTestId('storage-message').textContent).toContain('not encrypted yet');
    expect(document.body.textContent).toContain('python -m app.encrypt_migrate');
    expect(screen.queryByLabelText('Passphrase')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Unlock' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls.every(c => String(c[0]) === '/api/auth/status')).toBe(true);   // nothing else is requested
  });

  it('explains an interrupted migration, and shows refusals without a way around them', async () => {
    let reply = status('migration_interrupted', 'A database encryption migration did not finish.');
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json(reply));
    render(<Root />);
    expect(await screen.findByRole('heading', { name: 'The encryption migration did not finish' })).toBeTruthy();
    reply = status('refused', 'The key vault file is missing, so the encrypted database cannot be opened.');
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    expect(await screen.findByRole('heading', { name: 'OncoScope will not open this data' })).toBeTruthy();
    expect(screen.getByTestId('storage-message').textContent).toContain('key vault file is missing');
    expect(document.body.textContent).not.toContain('encrypt_migrate');
    expect(screen.queryByLabelText('Passphrase')).toBeNull();
  });

  it('shows the normal lock screen once the database is encrypted', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json({ ...status('locked', ''), recovery_available: true, database_encrypted: true }));
    render(<Root />);
    expect(await screen.findByRole('heading', { name: 'OncoScope is locked' })).toBeTruthy();
    expect(screen.getByLabelText('Passphrase')).toBeTruthy();
  });
});
