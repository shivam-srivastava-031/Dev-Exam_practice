import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In development the FastAPI backend runs on :8000; Vite proxies /api to it.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
});
