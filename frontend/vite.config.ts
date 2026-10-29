import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // 绝对 base 与 fnOS 统一网关前缀一致（后端中间件会剥离该前缀，直接访问同样可用）
  base: '/app/mteam-manager/',
  server: {
    port: 5180,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8686', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1200,
  },
})
