import { describe, expect, it, vi } from 'vitest'
import { searchFixture } from '../fixtures/vcBrainFixture'
import { mapOutboundCandidate, serializeExecutablePlan } from './contractAdapter'
import {
  FounderLookupApiError,
  HttpFounderLookupClient,
  type HttpClientOptions,
} from './httpClient'
import type { WireInvestmentThesisRevision, WireOutboundCandidate } from './wireTypes'

const API_BASE = 'http://api.test/api/v1'
const NOW = '2026-07-19T12:00:00.000Z'

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json' },
  })

const requestHeaders = (init?: RequestInit) => new Headers(init?.headers)

const thesisCriterion = (
  mode: 'hard_constraint' | 'scored_preference' | 'no_preference',
  values: Array<string | number | boolean> = [],
) => ({
  mode,
  operator: mode === 'no_preference' ? null : ('equals' as const),
  values,
  unknown_policy: 'preserve_as_unknown' as const,
})

const thesisWire: WireInvestmentThesisRevision = {
  thesis_id: 'thesis-1',
  thesis_version_id: 'thesis-version-1',
  revision_number: 1,
  created_at: NOW,
  created_by: 'investor-1',
  sector: thesisCriterion('scored_preference', ['AI infrastructure']),
  stage: thesisCriterion('hard_constraint', ['seed']),
  geography: thesisCriterion('no_preference'),
  check_size: thesisCriterion('no_preference'),
  ownership_target: thesisCriterion('no_preference'),
  risk_appetite: thesisCriterion('no_preference'),
}

const outboundCandidateWire: WireOutboundCandidate = {
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
  outreach_draft: 'Edited human-reviewed draft.',
  updated_at: NOW,
  public_contact_routes: [
    {
      route_id: 'route-public-website',
      kind: 'contact_url',
      label: 'Company contact page',
      value: 'https://jade.example',
      href: 'https://jade.example/contact',
      classification: 'public',
      source_artifact_id: 'artifact-public-1',
      source_name: 'Public launch page',
      source_locator: 'Contact link',
      collected_at: NOW,
    },
    {
      route_id: 'route-private-email',
      kind: 'public_email',
      label: 'Deck email',
      value: 'private@jade.example',
      href: 'mailto:private@jade.example',
      classification: 'founder_private',
      source_artifact_id: 'artifact-private-1',
      source_name: 'Founder deck',
      source_locator: 'Page 1',
    },
  ],
  sourcing_audit: {
    status: 'stopped',
    rounds_completed: 2,
    round_limit: 3,
    stop_reason: 'Authoritative sources satisfied the bounded plan.',
    run_id: 'run-sourcing-1',
  },
}

const makeClient = (
  fetchImplementation: typeof fetch,
  overrides: Partial<HttpClientOptions> = {},
) =>
  new HttpFounderLookupClient({
    baseUrl: API_BASE,
    fetchImplementation,
    getInvestorCredential: () => 'investor-token',
    createId: () => 'fixed-id',
    now: () => new Date(NOW),
    ...overrides,
  })

describe('HttpFounderLookupClient /api/v1 boundary', () => {
  it('maps only supplied public contact routes and preserves sourcing-loop provenance', () => {
    const candidate = mapOutboundCandidate(outboundCandidateWire)

    expect(candidate.publicContactRoutes).toEqual([
      expect.objectContaining({
        id: 'route-public-website',
        kind: 'contact_page',
        href: 'https://jade.example/contact',
        sourceArtifactId: 'artifact-public-1',
        sourceLocator: 'Contact link',
      }),
    ])
    expect(candidate.publicContactRoutes).not.toEqual(
      expect.arrayContaining([expect.objectContaining({ id: 'route-private-email' })]),
    )
    expect(candidate.sourcingLoopAudit).toEqual({
      status: 'stopped',
      roundsCompleted: 2,
      roundLimit: 3,
      stopReason: 'Authoritative sources satisfied the bounded plan.',
      runId: 'run-sourcing-1',
    })
  })

  it('keeps the founder capability out of the URL and sends it only in the status header', async () => {
    const capability = 'founder-secret/capability'
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void input
      void init
      return jsonResponse({
        application_id: 'application-1',
        received_at: NOW,
        stage: 'needs_information',
        last_updated_at: NOW,
        target_state: 'approaching',
        information_requests: ['Upload a signed pilot letter.'],
        outcome: null,
        next_action: 'Reply through the secure channel.',
        outcome_at: null,
      })
    })
    const client = makeClient(fetchMock as typeof fetch)

    const status = await client.getFounderStatus(capability)

    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, init] = fetchMock.mock.calls[0]!
    const headers = requestHeaders(init)
    expect(String(url)).toBe(`${API_BASE}/founder-status`)
    expect(String(url)).not.toContain(capability)
    expect(headers.get('X-Founder-Status-Capability')).toBe(capability)
    expect(headers.get('Authorization')).toBeNull()
    expect(status).toMatchObject({
      applicationId: 'application-1',
      stage: 'needs information',
      targetState: 'approaching',
      focusedRequest: 'Upload a signed pilot letter.',
    })
  })

  it('submits only the backend-supported company name and deck fields', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void input
      void init
      return jsonResponse(
        {
          application_id: 'application-1',
          company_id: 'company-1',
          run_id: 'run-1',
          source_artifact_id: 'artifact-1',
          status: 'received',
          received_at: NOW,
          founder_status_capability: 'secret/with slash',
          replayed: false,
        },
        202,
      )
    })
    const client = makeClient(fetchMock as typeof fetch)
    const deck = new File(['%PDF fixture'], 'deck.pdf', { type: 'application/pdf' })

    const receipt = await client.submitApplication({
      companyName: 'Jade Systems',
      deck,
      idempotencyKey: 'attempt-1',
    })

    const [url, init] = fetchMock.mock.calls[0]!
    const headers = requestHeaders(init)
    expect(String(url)).toBe(`${API_BASE}/applications`)
    expect(init?.method).toBe('POST')
    expect(headers.get('Idempotency-Key')).toBe('attempt-1')
    expect(headers.get('Authorization')).toBeNull()
    expect(headers.get('Content-Type')).toBeNull()
    expect(init?.body).toBeInstanceOf(FormData)
    const form = init?.body as FormData
    expect(form.get('company_name')).toBe('Jade Systems')
    expect(form.get('deck')).toBe(deck)
    expect(form.has('contact_email')).toBe(false)
    expect([...form.keys()].sort()).toEqual(['company_name', 'deck'])
    expect(receipt).toMatchObject({
      applicationId: 'application-1',
      companyId: 'company-1',
      sourceArtifactId: 'artifact-1',
      founderStatusUrl: '#/apply/status/secret%2Fwith%20slash',
      replayed: false,
    })

    await client.submitApplication({
      companyName: 'Jade Systems',
      deck,
      idempotencyKey: 'attempt-2',
      website: 'https://jade.example',
      oneLinePitch: 'A concise pitch.',
      location: 'Zurich',
      stage: 'pre-seed',
      contactEmail: 'hello@jade.example',
      founders: [{
        fullName: 'Ada Rivera',
        roleTitle: 'CEO',
        email: 'ada@jade.example',
        linkedinUrl: 'https://www.linkedin.com/in/ada-rivera',
        githubUrl: 'https://github.com/adarivera',
        previousCompanies: ['Acme', 'Northstar'],
        background: 'Built production infrastructure.',
      }],
    })

    const optionalForm = fetchMock.mock.calls[1]?.[1]?.body as FormData
    expect(optionalForm.get('website')).toBe('https://jade.example')
    expect(optionalForm.get('one_line_pitch')).toBe('A concise pitch.')
    expect(optionalForm.get('location')).toBe('Zurich')
    expect(optionalForm.get('stage')).toBe('pre-seed')
    expect(optionalForm.get('contact_email')).toBe('hello@jade.example')
    expect(JSON.parse(String(optionalForm.get('founders')))).toEqual([{
      full_name: 'Ada Rivera',
      role_title: 'CEO',
      email: 'ada@jade.example',
      linkedin_url: 'https://www.linkedin.com/in/ada-rivera',
      github_url: 'https://github.com/adarivera',
      previous_companies: ['Acme', 'Northstar'],
      background: 'Built production infrastructure.',
    }])
  })

  it('activates the real outbound-candidate resource with only the edited draft', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void input
      void init
      return jsonResponse(outboundCandidateWire)
    })
    const client = makeClient(fetchMock as typeof fetch)

    const receipt = await client.activateCandidate('candidate-1', 'Edited human-reviewed draft.')

    const [url, init] = fetchMock.mock.calls[0]!
    const headers = requestHeaders(init)
    expect(String(url)).toBe(`${API_BASE}/outbound-candidates/candidate-1/activate`)
    expect(headers.get('Authorization')).toBe('Bearer investor-token')
    expect(headers.get('Idempotency-Key')).toBeNull()
    expect(JSON.parse(String(init?.body))).toEqual({
      outreach_draft: 'Edited human-reviewed draft.',
    })
    expect(receipt).toEqual({
      candidateId: 'candidate-1',
      companyId: 'company-1',
      state: 'activated',
      activatedAt: NOW,
      outreachDraft: 'Edited human-reviewed draft.',
    })
  })

  it('records the decision command with exact snake_case fields', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void input
      void init
      return jsonResponse({
        decision_id: 'decision-1',
        screening_case_id: 'case-1',
        opportunity_id: 'opportunity-1',
        assessment_id: 'assessment-1',
        memo_id: 'memo-1',
        reviewed_recommendation_id: 'recommendation-1',
        disposition: 'request_more_information',
        actor_id: 'investor-1',
        rationale: 'Verify the pilot evidence.',
        decided_at: NOW,
      })
    })
    const client = makeClient(fetchMock as typeof fetch)

    const receipt = await client.recordDecision({
      opportunityId: 'opportunity-1',
      assessmentId: 'assessment-1',
      memoId: 'memo-1',
      recommendationId: 'recommendation-1',
      disposition: 'request_more_information',
      rationale: 'Verify the pilot evidence.',
    })

    const [url, init] = fetchMock.mock.calls[0]!
    const headers = requestHeaders(init)
    expect(String(url)).toBe(`${API_BASE}/opportunities/opportunity-1/decisions`)
    expect(headers.get('Authorization')).toBe('Bearer investor-token')
    expect(headers.get('Idempotency-Key')).toBeNull()
    expect(JSON.parse(String(init?.body))).toEqual({
      assessment_id: 'assessment-1',
      memo_id: 'memo-1',
      recommendation_id: 'recommendation-1',
      disposition: 'request_more_information',
      rationale: 'Verify the pilot evidence.',
    })
    expect(receipt).toMatchObject({
      decisionId: 'decision-1',
      actorId: 'investor-1',
      actorLabel: 'Investor · investor-1',
    })
  })

  it('executes a validated typed Query Plan at /queries instead of a fictional search route', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/query-plans')) {
        return jsonResponse(
          serializeExecutablePlan({
            ...searchFixture.plan.execution,
            rawQuery: 'fresh server interpretation',
          }),
        )
      }
      const request = JSON.parse(String(init?.body)) as { plan: Record<string, unknown> }
      return jsonResponse({
        plan: request.plan,
        results: [],
        eligible_count: 0,
        truncated: false,
        ordering: 'matched_preferences_desc,opportunity_id_asc',
        sourcing_run_id: null,
      })
    })
    const client = makeClient(fetchMock as typeof fetch)
    const inputQuery = 'technical founder in Berlin'

    const response = await client.searchOpportunities({
      query: inputQuery,
      filters: { origin: 'all', knowledgeHandling: 'include_unknown' },
      plan: searchFixture.plan.execution,
    })

    expect(fetchMock).toHaveBeenCalledTimes(2)
    const [planUrl, planInit] = fetchMock.mock.calls[0]!
    expect(String(planUrl)).toBe(`${API_BASE}/query-plans`)
    expect(JSON.parse(String(planInit?.body))).toMatchObject({ raw_query: inputQuery })
    const [url, init] = fetchMock.mock.calls[1]!
    expect(String(url)).toBe(`${API_BASE}/queries`)
    expect(String(url)).not.toContain('searches')
    const body = JSON.parse(String(init?.body)) as { plan: Record<string, unknown> }
    expect(body).toHaveProperty('plan.query_plan_id')
    expect(body).toHaveProperty('plan.query_plan_version_id', 'query-version-ui-fixed-id')
    expect(body).toHaveProperty(
      'plan.supersedes_query_plan_version_id',
      searchFixture.plan.execution.queryPlanVersionId,
    )
    expect(body).toHaveProperty('plan.raw_query', inputQuery)
    expect(body).toHaveProperty('plan.state', 'validated')
    expect(body.plan).not.toHaveProperty('queryPlanId')
    expect(response.plan.rawQuery).toBe(inputQuery)
    expect(response.results).toEqual([])
  })

  it('composes the workspace from the implemented resources and tolerates no opportunities', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void init
      const url = String(input)
      if (url.endsWith('/theses/active')) return jsonResponse(thesisWire)
      if (url.endsWith('/outbound-candidates?limit=50')) {
        return jsonResponse({
          items: [],
          limit: 50,
          truncated: false,
          applied_filters: [],
          ordering: 'updated_at_desc,outbound_candidate_id_asc',
        })
      }
      if (url.endsWith('/opportunities?limit=50')) {
        return jsonResponse({
          items: [],
          limit: 50,
          truncated: false,
          applied_filters: [],
          ordering: 'updated_at_desc,opportunity_id_asc',
        })
      }
      return jsonResponse({ title: 'Unexpected test route' }, 500)
    })
    const client = makeClient(fetchMock as typeof fetch)

    const workspace = await client.getWorkspace()

    const urls = fetchMock.mock.calls.map(([input]) => String(input)).sort()
    expect(urls).toEqual(
      [
        `${API_BASE}/outbound-candidates?limit=50`,
        `${API_BASE}/opportunities?limit=50`,
        `${API_BASE}/theses/active`,
      ].sort(),
    )
    expect(urls.every((url) => !url.endsWith('/workspace'))).toBe(true)
    for (const [, init] of fetchMock.mock.calls) {
      expect(requestHeaders(init).get('Authorization')).toBe('Bearer investor-token')
    }
    expect(workspace.opportunity).toBeNull()
    expect(workspace.search.results).toEqual([])
  })

  it('maps RFC problem details without losing the backend request id', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void input
      void init
      return jsonResponse(
        {
          type: 'https://founderlookup.test/problems/validation',
          title: 'Invalid command',
          status: 422,
          code: 'validation_error',
          request_id: 'request-123',
          detail: 'The command is invalid.',
          fields: [{ field: 'plan', code: 'required', message: 'A plan is required.' }],
        },
        422,
      )
    })
    const client = makeClient(fetchMock as typeof fetch)

    const rejection = await client.getFounderStatus('private-capability').catch((error) => error)

    expect(rejection).toBeInstanceOf(FounderLookupApiError)
    expect((rejection as FounderLookupApiError).problem).toMatchObject({
      status: 422,
      code: 'validation_error',
      requestId: 'request-123',
      fields: [{ field: 'plan', code: 'required', message: 'A plan is required.' }],
    })
  })
})
