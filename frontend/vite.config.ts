import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { VitePWA } from 'vite-plugin-pwa';

export default defineConfig(({ mode }) => {
  const isTauriBuild = mode === 'tauri';

  return {
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  plugins: [
    react(),
    tailwindcss(),
    !isTauriBuild && VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: 'OpenJarvis',
        short_name: 'Jarvis',
        description: 'On-device AI assistant',
        theme_color: '#161618',
        background_color: '#161618',
        display: 'standalone',
        icons: [
          { src: 'pwa-192x192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png' },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg}'],
        navigateFallbackDenylist: [/^\/v1\//, /^\/health/, /^\/dashboard/],
      },
    }),
  ].filter(Boolean),
  build: {
    outDir: '../src/openjarvis/server/static',
    emptyOutDir: true,
    minify: 'esbuild',
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom'],
          markdown: ['react-markdown', 'rehype-highlight', 'remark-gfm'],
          charts: ['recharts'],
          router: ['react-router'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      // The live FastAPI backend serves /v1 and /health on 7710 (same as the
      // Jarvis feeds below). The old 8000 default was a dead port — dev-preview
      // chat silently 500'd. Override with VITE_API_URL if running the API elsewhere.
      '/v1': process.env.VITE_API_URL || 'http://localhost:7710',
      '/health': process.env.VITE_API_URL || 'http://localhost:7710',
      // Jarvis backend feeds (NOT '/memory' itself - that's an SPA route)
      '/memory/activity': process.env.VITE_JARVIS_URL || 'http://localhost:7710',
      '/capability': process.env.VITE_JARVIS_URL || 'http://localhost:7710',
      '/whatsapp': process.env.VITE_JARVIS_URL || 'http://localhost:7710',
      // Studio API (state/runs/projects/etc.) — without this the Studio page is
      // dead in the dev preview. '/studio' is ALSO the SPA route, so bypass the
      // proxy for browser navigation (Accept: text/html) and only proxy the
      // fetch/XHR API calls.
      '/studio': {
        target: process.env.VITE_JARVIS_URL || 'http://localhost:7710',
        changeOrigin: true,
        bypass: (req: { headers: Record<string, string | undefined>; url?: string }) =>
          (req.headers.accept || '').includes('text/html') ? (req.url || '/') : undefined,
      },
    },
  },
  };
});
