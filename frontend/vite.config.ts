/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

/**
 * Where the browser sends `/v1` in development.
 *
 * The app only ever asks for same-origin relative paths, so something has to
 * carry `/v1` to the backend. Doing it here rather than by putting a host in
 * the client means the browser makes no cross-origin request, so the backend
 * needs no CORS policy. Loosening the server to suit the dev setup would be a
 * real change to what the server accepts in exchange for a convenience here.
 *
 * nginx does the same job in the container. The two are deliberately the same
 * shape, so the app cannot work in one and not the other.
 */
export const API_PROXY_TARGET = 'http://127.0.0.1:8000';
export const API_PROXY_PREFIX = '/v1';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      [API_PROXY_PREFIX]: {
        target: API_PROXY_TARGET,
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/setupTests.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      // main.tsx only mounts the app into the page, and the type declaration and
      // test setup files hold no runtime logic worth covering.
      exclude: [
        'src/main.tsx',
        'src/setupTests.ts',
        'src/vite-env.d.ts',
        // Fixtures are data copied from real API responses, not logic.
        'src/test/fixtures.ts',
      ],
      thresholds: {
        lines: 90,
        functions: 90,
        branches: 90,
        statements: 90,
      },
    },
  },
});
