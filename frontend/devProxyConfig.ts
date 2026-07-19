export type FrontendDataSource = 'fixture' | 'http'

export interface DevProxyEnvironment {
  readonly VITE_DATA_SOURCE?: string
  readonly FOUNDERLOOKUP_API_PROXY_TARGET?: string
}

export interface HttpDevProxySettings {
  readonly target: string
}

const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]'])

export const resolveDataSource = (value: string | undefined): FrontendDataSource => {
  const normalized = value?.trim().toLowerCase() || 'fixture'
  if (normalized === 'fixture' || normalized === 'http') return normalized
  throw new Error('VITE_DATA_SOURCE must be either "fixture" or "http".')
}

export const resolveHttpDevProxySettings = (
  environment: DevProxyEnvironment,
): HttpDevProxySettings => {
  const rawTarget = environment.FOUNDERLOOKUP_API_PROXY_TARGET?.trim()
  if (!rawTarget) {
    throw new Error(
      'HTTP dev mode requires FOUNDERLOOKUP_API_PROXY_TARGET (for example http://127.0.0.1:8000).',
    )
  }

  let target: URL
  try {
    target = new URL(rawTarget)
  } catch {
    throw new Error('FOUNDERLOOKUP_API_PROXY_TARGET must be a valid absolute URL.')
  }

  if (
    !['http:', 'https:'].includes(target.protocol) ||
    !LOOPBACK_HOSTS.has(target.hostname) ||
    target.username ||
    target.password ||
    target.pathname !== '/' ||
    target.search ||
    target.hash
  ) {
    throw new Error(
      'FOUNDERLOOKUP_API_PROXY_TARGET must be an HTTP(S) loopback origin without credentials or a path.',
    )
  }

  return {
    target: target.origin,
  }
}
