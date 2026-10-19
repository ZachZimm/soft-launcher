import { defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { builtinModules } from 'node:module';
import { resolve } from 'node:path';

// The webview bundle must stay browser safe: soft-if/node and Node builtins are
// rejected at resolve time so an accidental import fails the build loudly.
const nodeOnly = new Set([...builtinModules, ...builtinModules.map((m) => `node:${m}`)]);

export default defineConfig({
  plugins: [
    svelte(),
    {
      name: 'soft-launcher:browser-only',
      enforce: 'pre',
      resolveId(id, importer) {
        if (!importer || importer.includes('node_modules/vitest') || process.env.VITEST) return null;
        if (id === 'soft-if/node' || nodeOnly.has(id)) {
          throw new Error(`Node-only module "${id}" imported from ${importer}; the launcher webview must stay browser safe`);
        }
        return null;
      },
    },
  ],
  clearScreen: false,
  server: { host: '127.0.0.1', port: 5173, strictPort: true, watch: { ignored: ['**/src-tauri/**'] } },
  envPrefix: ['VITE_', 'TAURI_ENV_'],
  build: {
    target: 'es2022',
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      input: { main: resolve(import.meta.dirname, 'index.html'), replay: resolve(import.meta.dirname, 'replay.html') },
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
