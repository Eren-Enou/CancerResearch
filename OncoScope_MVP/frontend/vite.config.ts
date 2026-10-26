/// <reference types="vitest/config" />
import { defineConfig } from 'vite';

// The API port is overridable so end-to-end tests can run against a separate, throwaway database.
const apiPort = process.env.ONCOSCOPE_API_PORT ?? '8000';

export default defineConfig({
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: { '/api': { target: `http://127.0.0.1:${apiPort}`, changeOrigin: false } },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
