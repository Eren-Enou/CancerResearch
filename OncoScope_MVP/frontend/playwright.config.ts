import { defineConfig } from '@playwright/test';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// Each demonstration starts from its own brand-new, empty database in the OS temp directory. The paths are fixed once
// in the runner process and inherited by workers, so specs can back up / restore the same database the API uses.
const stamp = Date.now();
process.env.ONCOSCOPE_E2E_DB_M2 ??= join(tmpdir(), `oncoscope-e2e-m2-${stamp}`, 'oncoscope.sqlite3');
process.env.ONCOSCOPE_E2E_DB_M25 ??= join(tmpdir(), `oncoscope-e2e-m25-${stamp}`, 'oncoscope.sqlite3');
process.env.ONCOSCOPE_E2E_DB_M26 ??= join(tmpdir(), `oncoscope-e2e-m26-${stamp}`, 'oncoscope.sqlite3');
process.env.ONCOSCOPE_E2E_DB_M31A ??= join(tmpdir(), `oncoscope-e2e-m31a-${stamp}`, 'oncoscope.sqlite3');
process.env.ONCOSCOPE_E2E_SESSION_SECONDS ??= '45';
process.env.ONCOSCOPE_E2E_DB_M31B ??= join(tmpdir(), `oncoscope-e2e-m31b-${stamp}`, 'oncoscope.sqlite3');
process.env.ONCOSCOPE_E2E_DB_M31C ??= join(tmpdir(), `oncoscope-e2e-m31c-${stamp}`, 'data', 'oncoscope.sqlite3');
const python = process.env.ONCOSCOPE_PYTHON
  ?? (process.platform === 'win32' ? '.venv\\Scripts\\python.exe' : '.venv/bin/python');

// The M2–M2.6 demonstrations run in explicitly isolated demonstration mode; M3.1-A/B/C run the protected mode.
const stack = (dbPath: string, apiPort: number, uiPort: number, env: Record<string, string> = { ONCOSCOPE_MODE: 'demo' }) => [
  {
    command: `${python} -m uvicorn app.main:app --host 127.0.0.1 --port ${apiPort}`,
    cwd: '../backend',
    url: `http://127.0.0.1:${apiPort}/api/health`,
    env: { ONCOSCOPE_DB_PATH: dbPath, ...env },
    reuseExistingServer: false,
  },
  {
    command: `npx vite --port ${uiPort} --strictPort`,
    url: `http://127.0.0.1:${uiPort}`,
    env: { ONCOSCOPE_API_PORT: String(apiPort) },
    reuseExistingServer: false,
  },
];

export default defineConfig({
  testDir: 'e2e',
  timeout: 120_000,
  workers: 1,
  reporter: 'list',
  use: {
    // Locally: an installed browser (Edge by default) so no download is needed. CI sets PW_CHANNEL=bundled to use
    // Playwright's own Chromium (installed in the workflow).
    channel: process.env.PW_CHANNEL === 'bundled' ? undefined : (process.env.PW_CHANNEL ?? 'msedge'),
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'm2', testMatch: /demonstration\.spec\.ts/, use: { baseURL: 'http://127.0.0.1:5180' } },
    { name: 'm2.5', testMatch: /reconstruction\.spec\.ts/, use: { baseURL: 'http://127.0.0.1:5181' } },
    { name: 'm2.6', testMatch: /fidelity\.spec\.ts/, use: { baseURL: 'http://127.0.0.1:5182', viewport: { width: 1440, height: 900 } } },
    { name: 'm3.1a', testMatch: /protected\.spec\.ts/, use: { baseURL: 'http://127.0.0.1:5183', acceptDownloads: true } },
    { name: 'm3.1b', testMatch: /vault\.spec\.ts/, use: { baseURL: 'http://127.0.0.1:5184' } },
    { name: 'm3.1c', testMatch: /encryption\.spec\.ts/, use: { baseURL: 'http://127.0.0.1:5185', acceptDownloads: true } },
  ],
  webServer: [
    ...stack(process.env.ONCOSCOPE_E2E_DB_M2, 8010, 5180),
    ...stack(process.env.ONCOSCOPE_E2E_DB_M25, 8011, 5181),
    ...stack(process.env.ONCOSCOPE_E2E_DB_M26, 8012, 5182),
    ...stack(process.env.ONCOSCOPE_E2E_DB_M31A, 8013, 5183,
      { ONCOSCOPE_MODE: 'protected', ONCOSCOPE_SESSION_MAX_SECONDS: process.env.ONCOSCOPE_E2E_SESSION_SECONDS! }),
    ...stack(process.env.ONCOSCOPE_E2E_DB_M31B, 8014, 5184, { ONCOSCOPE_MODE: 'protected' }),
    ...stack(process.env.ONCOSCOPE_E2E_DB_M31C, 8015, 5185, { ONCOSCOPE_MODE: 'protected' }),
  ],
});
