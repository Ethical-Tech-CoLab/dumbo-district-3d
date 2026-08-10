import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

/**
 * The shared kernel and contracts are consumed straight from the sibling repository rather than
 * through a published package. Aliasing the source keeps cross-repo development immediate: edit the
 * kernel, see it here, with no build or link step. `server.fs.allow` has to be widened because the
 * sources live outside this project root.
 */
const contractsRoot = fileURLToPath(new URL('../../digital-3d-shared-contracts', import.meta.url));

export default defineConfig(({ command }) => ({
  // Relative asset URLs so one build works at a domain root and under a project subpath like
  // /dumbo-district-3d/ on GitHub Pages; every data fetch in the app is already relative for the
  // same reason. Only at build time: the dev server needs an absolute base to resolve /src/main.tsx,
  // and a relative one makes it answer 400.
  base: command === 'build' ? './' : '/',
  plugins: [react()],
  resolve: {
    alias: {
      '@d3d/contracts': `${contractsRoot}/packages/contracts/src/index.ts`,
      '@d3d/viewer-kernel': `${contractsRoot}/packages/viewer-kernel/src/index.ts`,
    },
  },
  server: {
    port: 5178,
    fs: { allow: ['..', contractsRoot] },
  },
  build: { outDir: 'dist', sourcemap: true },
}));
