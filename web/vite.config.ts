/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import { dotlineTokens } from './build/dotline-tokens.ts';

export default defineConfig({
  plugins: [react(), dotlineTokens('./src/ds/tokens.json')],
  build: {
    // Written into the vantage package, which serves it at / and carries it
    // in its wheel; git ignores it.
    outDir: '../packages/vantage/src/vantage/service/client',
    emptyOutDir: true,
    // No data: URIs, which the page policy refuses; every font is a file.
    assetsInlineLimit: 0,
    // The polyfill would be an inline script.
    modulePreload: { polyfill: false },
    sourcemap: false,
  },
  server: {
    // The browser's own Sec-Fetch-Site: same-origin reaches vantage, so
    // the Host header is left as the browser sent it: localhost:5173, a
    // name vantage answers on loopback. A dev server reached by any other
    // name needs vantage started with --allowed-host for it.
    proxy: { '/api': { target: 'http://127.0.0.1:8765' } },
  },
  test: {
    environment: 'jsdom',
    css: true,
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}', 'build/**/*.test.ts'],
  },
});
