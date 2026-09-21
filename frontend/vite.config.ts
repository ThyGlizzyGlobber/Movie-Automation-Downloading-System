import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      // The same mapping nginx.conf does in front of the built app: /img/
      // is TMDB's image CDN, cached there. Without this the dev server
      // has no such route, so every poster, backdrop and logo falls
      // through to the SPA fallback and comes back as index.html with a
      // 200 — images that silently render as nothing, in dev only.
      '/img': {
        target: 'https://image.tmdb.org',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/img/, '/t/p'),
      },
    },
  },
  build: {
    // Explicit, not just relying on the (currently also `false`) default
    // — dist/ is served straight to the internet once remote access is
    // enabled (Part G), so this is a deliberate choice to never ship
    // source maps there, not an implicit one that could quietly flip if
    // Vite's own default ever changed.
    sourcemap: false,
  },
})
