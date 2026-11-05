import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

// A pack bundle is an ESM library, not an app: one entry, ES output, and `react`
// / `react/jsx-runtime` aliased to shims that re-export the host's single React
// instance (bundling a second copy throws "Invalid hook call"). Everything else
// inlines, so dist/pack.js is self-contained apart from the window.CcPresent host.
const fromHere = (p: string): string => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  esbuild: { jsx: 'automatic' },
  resolve: {
    alias: {
      // Order matters: the specific specifier must precede `react`, which also
      // matches `react/jsx-runtime` as a prefix.
      'react/jsx-runtime': fromHere('./src/host/jsx-runtime.ts'),
      react: fromHere('./src/host/react.ts'),
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    lib: {
      entry: fromHere('./src/pack.tsx'),
      formats: ['es'],
      fileName: () => 'pack.js',
    },
  },
});
