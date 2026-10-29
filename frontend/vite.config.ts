import { resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig, type Plugin } from 'vite';

// KaTeX отдаёт каждый шрифт в трёх форматах; встроенный Chromium читает woff2, остальные только раздувают сборку.
const katexWoff2Only: Plugin = {
  name: 'katex-woff2-only',
  generateBundle: {
    order: 'post',
    handler(_options, bundle) {
      const legacy = /KaTeX_[^/]*\.(?:woff|ttf)$/;
      for (const name of Object.keys(bundle)) if (legacy.test(name)) delete bundle[name];
      for (const file of Object.values(bundle)) {
        if (file.type !== 'asset' || !file.fileName.endsWith('.css')) continue;
        file.source = String(file.source).replace(/,\s*url\([^)]*KaTeX_[^)]*\.(?:woff|ttf)\)\s*format\("(?:woff|truetype)"\)/g, '');
      }
    },
  },
};

// Собранный интерфейс используется приложением из каталога web-next.
export default defineConfig({
  base: '/ui/',
  plugins: [react(), katexWoff2Only],
  build: {
    outDir: resolve(import.meta.dirname, '../backend/koddi/desktop/web-next'),
    emptyOutDir: true,
    target: 'es2022',
    sourcemap: false,
    cssCodeSplit: false,
    rollupOptions: {
      output: { entryFileNames: 'app.js', chunkFileNames: 'chunk-[name].js', assetFileNames: (info) => (info.names?.some((n) => n.endsWith('.css')) ? 'app.css' : 'assets/[name][extname]') },
    },
  },
  server: { proxy: { '/api': 'http://127.0.0.1:8765' } },
});
