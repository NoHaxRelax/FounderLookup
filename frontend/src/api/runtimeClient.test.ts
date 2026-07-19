import { afterEach, describe, expect, it, vi } from 'vitest'
import { fixtureClient } from './fixtureClient'
import { HttpFounderLookupClient } from './httpClient'
import { createBrowserClient } from './runtimeClient'

const NOW = '2026-07-19T12:00:00.000Z'

afterEach(() => {
  vi.unstubAllGlobals()
  globalThis.sessionStorage.clear()
})

describe('standalone browser client selection', () => {
  it('uses deterministic fixtures unless HTTP is explicitly selected', () => {
    expect(createBrowserClient({})).toBe(fixtureClient)
    expect(createBrowserClient({ VITE_DATA_SOURCE: 'fixture' })).toBe(fixtureClient)
  })

  it('selects the HTTP adapter with the same-origin API base and no browser credential', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void input
      void init
      return new Response(
        JSON.stringify({
          outbound_candidate_id: 'candidate-1',
          company_id: 'company-1',
          company_name: 'Jade Systems',
          founder_id: {
            schema_version: 'knowledge-value.v0',
            state: 'unknown',
            value: null,
            reason: 'Not verified.',
            evidence_ids: [],
            alternatives: [],
          },
          status: 'activated',
          discovered_at: NOW,
          source_artifact_ids: [],
          preliminary_assessment: null,
          outreach_draft: 'Reviewed draft.',
          updated_at: NOW,
        }),
        { headers: { 'Content-Type': 'application/json' } },
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    const client = createBrowserClient({ VITE_DATA_SOURCE: 'http' })
    expect(client).toBeInstanceOf(HttpFounderLookupClient)

    await client.activateCandidate('candidate-1', 'Reviewed draft.')

    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/v1/outbound-candidates/candidate-1/activate')
    expect(new Headers(init?.headers).get('Authorization')).toBeNull()
  })

  it('keeps the single investor credential session-scoped and off public founder calls', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void init
      if (String(input).endsWith('/founder-status')) {
        return new Response(
          JSON.stringify({
            application_id: 'application-1',
            received_at: NOW,
            stage: 'under_review',
            last_updated_at: NOW,
            target_state: 'on_track',
            information_requests: [],
            outcome: null,
            next_action: 'Review continues.',
            outcome_at: null,
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response(
        JSON.stringify({
          outbound_candidate_id: 'candidate-1',
          company_id: 'company-1',
          company_name: 'Jade Systems',
          founder_id: {
            schema_version: 'knowledge-value.v0',
            state: 'unknown',
            value: null,
            reason: 'Not verified.',
            evidence_ids: [],
            alternatives: [],
          },
          status: 'activated',
          discovered_at: NOW,
          source_artifact_ids: [],
          preliminary_assessment: null,
          outreach_draft: null,
          updated_at: NOW,
        }),
        { headers: { 'Content-Type': 'application/json' } },
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    const client = createBrowserClient({
      VITE_DATA_SOURCE: 'http',
      VITE_INVESTOR_AUTH_MODE: 'session',
      VITE_API_BASE_URL: '/api/v1',
    })
    expect(client.investorAccess?.hasCredential()).toBe(false)
    client.investorAccess?.setCredential(' session-only-key ')

    await client.activateCandidate('candidate-1', '')
    await client.getFounderStatus('founder-capability')

    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get('Authorization')).toBe(
      'Bearer session-only-key',
    )
    expect(new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get('Authorization')).toBeNull()
    expect(sessionStorage.getItem('founderlookup.investor-access-key')).toBe('session-only-key')
    client.investorAccess?.clearCredential()
    expect(client.investorAccess?.hasCredential()).toBe(false)
    expect(sessionStorage.getItem('founderlookup.investor-access-key')).toBeNull()
  })

  it('rejects cleartext remote API origins before creating an HTTP client', () => {
    expect(() =>
      createBrowserClient({
        VITE_DATA_SOURCE: 'http',
        VITE_INVESTOR_AUTH_MODE: 'session',
        VITE_API_BASE_URL: 'http://api.example.com/api/v1',
      }),
    ).toThrow(/must use HTTPS/)
  })

  it('rejects an unsupported runtime instead of silently falling back', () => {
    expect(() => createBrowserClient({ VITE_DATA_SOURCE: 'live' })).toThrow(
      /VITE_DATA_SOURCE/,
    )
  })
})
