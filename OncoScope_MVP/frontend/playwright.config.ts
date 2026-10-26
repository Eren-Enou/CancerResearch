import { defineConfig } from '@playwright/test';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// Every run starts from a brand-new, empty database in the OS temp directory.
const dbPath = join(tmpdir(), `oncoscope-e2e-${Date.now()}.sqlite3`);
const python = process.env.ONCOSCOPE_PYTHON
  ?? (process.platform === 'win32' ? '.venv\\Scripts\\python.exe' : '.venv/bin/python');

export default defineConfig({
  testDir: 'e2e',
  timeout: 60_000,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5180',
    // Uses an installed browser (Edge by default on Windows) so no browser download is needed.
    channel: process.env.PW_CHANNEL ?? 'msedge',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: `${python} -m uvicorn app.main:app --host 127.0.0.1 --port 8010`,
      cwd: '../backend',
      url: 'http://127.0.0.1:8010/api/health',
      env: { ONCOSCOPE_DB_PATH: dbPath, ONCOSCOPE_DOCUMENT_DIR: `${dbPath}-documents` },
      reuseExistingServer: false,
    },
    {
      command: 'npx vite --port 5180 --strictPort',
      url: 'http://127.0.0.1:5180',
      env: { ONCOSCOPE_API_PORT: '8010' },
      reuseExistingServer: false,
    },
  ],
});
