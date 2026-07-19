import { fixtureClient } from './fixtureClient'
import { HttpFounderLookupClient } from './httpClient'
import type { FounderLookupClient, InvestorAccessController } from './types'

export interface BrowserRuntimeEnvironment {
  readonly VITE_DATA_SOURCE?: string
  readonly VITE_API_BASE_URL?: string
  readonly VITE_INVESTOR_AUTH_MODE?: string
  readonly [name: string]: unknown
}

const DEFAULT_API_BASE_URL = '/api/v1'
const INVESTOR_CREDENTIAL_SESSION_KEY = 'founderlookup.investor-access-key'

class SessionInvestorAccess implements InvestorAccessController {
  #memoryCredential = ''

  #storage(): Storage | undefined {
    try {
      return globalThis.sessionStorage
    } catch {
      return undefined
    }
  }

  getCredential(): string | undefined {
    try {
      return this.#storage()?.getItem(INVESTOR_CREDENTIAL_SESSION_KEY)?.trim() ||
        this.#memoryCredential ||
        undefined
    } catch {
      return this.#memoryCredential || undefined
    }
  }

  hasCredential(): boolean {
    return this.getCredential() !== undefined
  }

  setCredential(credential: string): void {
    const normalized = credential.trim()
    if (!normalized) throw new Error('Enter the investor access key.')
    this.#memoryCredential = normalized
    try {
      this.#storage()?.setItem(INVESTOR_CREDENTIAL_SESSION_KEY, normalized)
    } catch {
      // Memory remains the safe fallback when sessionStorage is unavailable.
    }
  }

  clearCredential(): void {
    this.#memoryCredential = ''
    try {
      this.#storage()?.removeItem(INVESTOR_CREDENTIAL_SESSION_KEY)
    } catch {
      // The in-memory credential has still been cleared.
    }
  }
}

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

  const authMode = environment.VITE_INVESTOR_AUTH_MODE?.trim().toLowerCase() || 'proxy'
  if (authMode !== 'proxy' && authMode !== 'session') {
    throw new Error('VITE_INVESTOR_AUTH_MODE must be either "proxy" or "session".')
  }
  const investorAccess = authMode === 'session' ? new SessionInvestorAccess() : undefined

  return new HttpFounderLookupClient({
    baseUrl: normalizeApiBaseUrl(environment.VITE_API_BASE_URL),
    investorAccess,
  })
}
