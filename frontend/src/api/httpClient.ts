import {
  composeInitialSearch,
  composeQuerySearch,
  mapActivation,
  mapApplicationReceipt,
  mapDecision,
  mapFounderStatus,
  mapOpportunityDetail,
  mapProblem,
  mapThesisRevision,
  serializeExecutablePlan,
} from './contractAdapter'
import type {
  ActivationReceipt,
  ApiProblem,
  ApplicationInput,
  ApplicationReceipt,
  DecisionInput,
  DecisionReceipt,
  ExecutableQueryPlan,
  FounderLookupClient,
  FounderStatusView,
  OpportunityDetail,
  SearchInput,
  SearchResponse,
  StableId,
  WorkspaceFixture,
} from './types'
import type {
  WireApplicationReceipt,
  WireCandidateCollection,
  WireFounderStatusView,
  WireHumanDecision,
  WireInvestmentThesisRevision,
  WireOpportunityCollection,
  WireOpportunityDetail,
  WireOutboundCandidate,
  WirePipelineRun,
  WireProblemDetails,
  WireQueryResult,
} from './wireTypes'

type CredentialProvider = () => string | undefined | Promise<string | undefined>

export interface HttpClientOptions {
  /** Full versioned base, for example `http://localhost:8000/api/v1`. */
  baseUrl: string
  getInvestorCredential?: CredentialProvider
  fetchImplementation?: typeof fetch
  createId?: () => string
  now?: () => Date
}

export class FounderLookupApiError extends Error {
  readonly problem: ApiProblem

  constructor(problem: ApiProblem) {
    super(problem.detail ?? problem.title)
    this.name = 'FounderLookupApiError'
    this.problem = problem
  }
}

export class QueryPlanUnavailableError extends Error {
  constructor() {
    super('A validated typed Query Plan needs at least one criterion or retrieval request.')
    this.name = 'QueryPlanUnavailableError'
  }
}

/**
 * Browser-facing adapter for the implemented FastAPI `/api/v1` contract.
 *
 * The UI model stays camelCase and task-oriented. Every method below explicitly translates a
 * real snake_case command/read model; it never guesses a generic casing conversion.
 */
export class HttpFounderLookupClient implements FounderLookupClient {
  readonly runtime = 'http' as const
  readonly #baseUrl: string
  readonly #getInvestorCredential?: CredentialProvider
  readonly #fetch: typeof fetch
  readonly #createId: () => string
  readonly #now: () => Date
  readonly #companyNames = new Map<string, string>()

  constructor(options: HttpClientOptions) {
    this.#baseUrl = options.baseUrl.replace(/\/$/, '')
    this.#getInvestorCredential = options.getInvestorCredential
    this.#fetch = options.fetchImplementation ?? globalThis.fetch.bind(globalThis)
    this.#createId = options.createId ?? (() => globalThis.crypto.randomUUID())
    this.#now = options.now ?? (() => new Date())
  }

  async getWorkspace(): Promise<WorkspaceFixture> {
    const [thesisWire, candidatesWire, opportunitiesWire] = await Promise.all([
      this.#request<WireInvestmentThesisRevision>('/theses/active'),
      this.#request<WireCandidateCollection>('/outbound-candidates?limit=50'),
      this.#request<WireOpportunityCollection>('/opportunities?limit=50'),
    ])
    this.#rememberCompanyNames(candidatesWire)

    const firstOpportunity = opportunitiesWire.items[0]
    const opportunity = firstOpportunity
      ? await this.#getOpportunityDetail(firstOpportunity.opportunity_id)
      : null

    return {
      thesis: mapThesisRevision(thesisWire),
      search: composeInitialSearch(thesisWire, candidatesWire, opportunitiesWire),
      opportunity,
    }
  }

  async searchOpportunities(input: SearchInput): Promise<SearchResponse> {
    const plan = this.#preparePlan(input)
    const result = await this.#request<WireQueryResult>('/queries', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan: serializeExecutablePlan(plan) }),
    })
    const details = await Promise.all(
      result.results.map((item) =>
        this.#request<WireOpportunityDetail>(
          `/opportunities/${encodeURIComponent(item.opportunity_id)}?expand=claims%2Cevidence`,
        ),
      ),
    )
    return composeQuerySearch(result, details, input.filters, this.#companyNames)
  }

  getOpportunity(opportunityId: StableId): Promise<OpportunityDetail> {
    return this.#getOpportunityDetail(opportunityId)
  }

  async submitApplication(input: ApplicationInput): Promise<ApplicationReceipt> {
    const form = new FormData()
    form.set('company_name', input.companyName)
    form.set('deck', input.deck)

    const wire = await this.#request<WireApplicationReceipt>(
      '/applications',
      {
        method: 'POST',
        headers: { 'Idempotency-Key': input.idempotencyKey },
        body: form,
      },
      false,
    )
    return mapApplicationReceipt(wire)
  }

  async getFounderStatus(capability: string): Promise<FounderStatusView> {
    const wire = await this.#request<WireFounderStatusView>(
      '/founder-status',
      { headers: { 'X-Founder-Status-Capability': capability } },
      false,
    )
    return mapFounderStatus(wire)
  }

  async activateCandidate(
    candidateId: StableId,
    outreachDraft: string,
  ): Promise<ActivationReceipt> {
    const wire = await this.#request<WireOutboundCandidate>(
      `/outbound-candidates/${encodeURIComponent(candidateId)}/activate`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          outreach_draft: outreachDraft.trim().length === 0 ? null : outreachDraft,
        }),
      },
    )
    this.#companyNames.set(wire.company_id, wire.company_name)
    return mapActivation(wire)
  }

  async recordDecision(input: DecisionInput): Promise<DecisionReceipt> {
    const wire = await this.#request<WireHumanDecision>(
      `/opportunities/${encodeURIComponent(input.opportunityId)}/decisions`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          assessment_id: input.assessmentId,
          memo_id: input.memoId,
          recommendation_id: input.recommendationId,
          disposition: input.disposition,
          rationale: input.rationale,
        }),
      },
    )
    return mapDecision(wire)
  }

  async #getOpportunityDetail(opportunityId: StableId): Promise<OpportunityDetail> {
    const detail = await this.#request<WireOpportunityDetail>(
      `/opportunities/${encodeURIComponent(opportunityId)}?expand=claims%2Cevidence`,
    )
    const runs = await Promise.all(
      detail.related_run_ids.map((runId) =>
        this.#request<WirePipelineRun>(`/runs/${encodeURIComponent(runId)}`),
      ),
    )
    return mapOpportunityDetail(detail, runs, this.#companyNames.get(detail.company_id))
  }

  #preparePlan(input: SearchInput): ExecutableQueryPlan {
    const removed = new Set(input.removedCriterionIds ?? [])
    const rawQuery = input.query.trim()
    const criteria = input.plan.criteria.filter((criterion) => !removed.has(criterion.criterionId))

    if (input.filters.origin !== 'all') {
      criteria.push({
        criterionId: `origin-${this.#createId()}`,
        field: 'origin',
        operator: 'equals',
        operands: [input.filters.origin],
        strength: 'hard_constraint',
        unknownPolicy: 'manual_review',
        sourceText: `Origin: ${input.filters.origin}`,
      })
    }

    if (criteria.length === 0 && input.plan.retrievalRequests.length === 0) {
      throw new QueryPlanUnavailableError()
    }

    const unresolvedPhrases = input.plan.unresolvedPhrases.flatMap((phrase) => {
      const startOffset = rawQuery.indexOf(phrase.text)
      return startOffset < 0
        ? []
        : [{ ...phrase, startOffset, endOffset: startOffset + phrase.text.length }]
    })

    return {
      ...input.plan,
      queryPlanVersionId: `query-version-${this.#createId()}`,
      supersedesQueryPlanVersionId: input.plan.queryPlanVersionId,
      rawQuery,
      state: 'validated',
      criteria,
      retrievalRequests: input.plan.retrievalRequests.map((request) => ({
        ...request,
        query: rawQuery,
      })),
      unresolvedPhrases,
      semanticRerank: input.plan.semanticRerank
        ? { ...input.plan.semanticRerank, query: rawQuery }
        : undefined,
      createdAt: this.#now().toISOString(),
    }
  }

  #rememberCompanyNames(collection: WireCandidateCollection) {
    for (const candidate of collection.items) {
      this.#companyNames.set(candidate.company_id, candidate.company_name)
    }
  }

  async #request<T>(path: string, init: RequestInit = {}, investorOnly = true): Promise<T> {
    const headers = new Headers(init.headers)
    headers.set('Accept', 'application/json')

    if (investorOnly) {
      const credential = await this.#getInvestorCredential?.()
      if (credential) headers.set('Authorization', `Bearer ${credential}`)
    }

    const response = await this.#fetch(`${this.#baseUrl}${path}`, { ...init, headers })
    if (!response.ok) {
      const fallback: ApiProblem = {
        type: 'about:blank',
        title: 'Request failed',
        status: response.status,
        code: 'http_error',
        requestId: response.headers.get('x-request-id') ?? undefined,
      }
      const body = (await response.json().catch(() => null)) as WireProblemDetails | null
      const problem = body?.request_id ? mapProblem(body, response.status) : fallback
      throw new FounderLookupApiError(problem)
    }

    return (await response.json()) as T
  }
}
