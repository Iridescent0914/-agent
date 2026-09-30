import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          const moduleId = id.replace(/\\/g, '/')
          if (moduleId.indexOf('/node_modules/mathlive/') >= 0) return 'vendor-mathlive'
          if (moduleId.indexOf('/node_modules/zrender/') >= 0) return 'vendor-zrender'
          if (moduleId.indexOf('/node_modules/echarts/') >= 0) return 'vendor-echarts'
          if (moduleId.indexOf('/node_modules/react/') >= 0 || moduleId.indexOf('/node_modules/react-dom/') >= 0 || moduleId.indexOf('/node_modules/react-router') >= 0) return 'vendor-react'
          return undefined
        },
      },
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
  },
})
