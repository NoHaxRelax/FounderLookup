import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'
import { resolveDataSource, resolveHttpDevProxySettings } from './devProxyConfig'

export default defineConfig(({ command, isPreview, mode }) => {
  const environment = loadEnv(mode, '.', '')
  const dataSource = resolveDataSource(environment.VITE_DATA_SOURCE)
  const httpDevProxy =
    command === 'serve' && !isPreview && dataSource === 'http'
      ? resolveHttpDevProxySettings(environment)
      : undefined

  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: httpDevProxy
        ? {
            '/api': {
              target: httpDevProxy.target,
              headers: { Authorization: httpDevProxy.authorization },
            },
          }
        : undefined,
    },
    preview: {
      port: 4173,
      strictPort: true,
    },
    test: {
      environment: 'jsdom',
      setupFiles: './src/test/setup.ts',
      css: true,
      restoreMocks: true,
    },
  }
})
