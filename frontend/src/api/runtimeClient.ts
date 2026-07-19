import { fixtureClient } from './fixtureClient'
import { HttpFounderLookupClient } from './httpClient'
import type { FounderLookupClient } from './types'

export interface BrowserRuntimeEnvironment {
  readonly VITE_DATA_SOURCE?: string
  readonly VITE_API_BASE_URL?: string
  readonly [name: string]: unknown
}

const DEFAULT_API_BASE_URL = '/api/v1'
const normalizeApiBaseUrl = (candidate: string | undefined) => {
  const baseUrl = candidate?.trim() || DEFAULT_API_BASE_URL
  if (/^http:\/\//i.test(baseUrl)) {
    const url = new URL(baseUrl)
    if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) {
      throw new Error('A remote VITE_API_BASE_URL must use HTTPS.')
    }
  }
  return baseUrl
}

/**
 * Select the standalone browser runtime. Hosts can bypass this selector and inject any
 * FounderLookupClient through App's `client` prop.
 */
export const createBrowserClient = (
  environment: BrowserRuntimeEnvironment,
): FounderLookupClient => {
  const dataSource = environment.VITE_DATA_SOURCE?.trim().toLowerCase() || 'fixture'

  if (dataSource === 'fixture') return fixtureClient
  if (dataSource !== 'http') {
    throw new Error('VITE_DATA_SOURCE must be either "fixture" or "http".')
  }

  return new HttpFounderLookupClient({
    baseUrl: normalizeApiBaseUrl(environment.VITE_API_BASE_URL),
  })
}
