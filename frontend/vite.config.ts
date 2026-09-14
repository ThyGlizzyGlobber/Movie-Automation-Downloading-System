import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
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
