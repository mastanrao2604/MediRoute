import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.ico', 'icons/*.png'],
      // Manifest is in public/manifest.json — plugin reads it automatically.
      // Duplicate here so the SW knows which assets to precache.
      manifest: {
        name: 'MediRoute — Real-Time Healthcare Staffing',
        short_name: 'MediRoute',
        description: 'Real-Time Healthcare Staffing — connecting medical professionals with opportunities.',
        start_url: '/',
        display: 'standalone',
        orientation: 'portrait',
        background_color: '#ffffff',
        theme_color: '#2563EB',
        categories: ['medical', 'business'],
        icons: [
          { src: '/icons/icon-48.png',           sizes: '48x48',   type: 'image/png' },
          { src: '/icons/icon-72.png',           sizes: '72x72',   type: 'image/png' },
          { src: '/icons/icon-96.png',           sizes: '96x96',   type: 'image/png' },
          { src: '/icons/icon-144.png',          sizes: '144x144', type: 'image/png' },
          { src: '/icons/icon-192.png',          sizes: '192x192', type: 'image/png' },
          { src: '/icons/icon-512.png',          sizes: '512x512', type: 'image/png' },
          { src: '/icons/icon-512-maskable.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // Precache all built assets
        globPatterns: ['**/*.{js,css,html,ico,png,svg,woff,woff2}'],
        // SPA: serve index.html for navigation requests
        navigateFallback: 'index.html',
        // Don't intercept API calls or direct file requests
        navigateFallbackDenylist: [/^\/api\//, /^\/uploads\//],
        runtimeCaching: [
          {
            // API: network-first, fall back to cache.
            // Matches both the production Render backend (onrender.com) and local dev (port 8000).
            urlPattern: ({ url }) => url.hostname.includes('onrender.com') || url.port === '8000',
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api-cache',
              networkTimeoutSeconds: 10,
              cacheableResponse: { statuses: [0, 200] },
            },
          },
        ],
      },
      // Only enable SW in production build
      devOptions: { enabled: false },
    }),
  ],
})
