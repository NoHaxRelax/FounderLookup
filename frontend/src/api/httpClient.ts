import {
  composeInitialSearch,
  composeQuerySearch,
  mapActivation,
  mapApplicationReceipt,
  mapDecision,
  mapFounderStatus,
  mapOutreach,
  mapOpportunityDetail,
  mapPipelineRun,
  mapProblem,
  mapThesisRevision,
  mapWireExecutablePlan,
  serializeExecutablePlan,
} from './contractAdapter'
import type {
  ActivationReceipt,
  ApiProblem,
  ApplicationInput,
  ApplicationReceipt,
  DecisionInput,
  DecisionReceipt,
  DiscoveryInput,
  ExecutableQueryPlan,
  FounderLookupClient,
  FounderStatusView,
  InvestorAccessController,
  OpportunityDetail,
  OpportunityCommandResult,
  OutreachInput,
  OutreachReceipt,
  SearchInput,
  SearchResponse,
  StableId,
  ThesisCriterion,
  ThesisCriterionKey,
  ThesisView,
  WorkspaceCommandResult,
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
  WireOutreachRecord,
  WireOutboundCandidate,
  WirePipelineRun,
  WireProblemDetails,
  WireQueryResult,
  WireQueryPlan,
  WireRunAccepted,
} from './wireTypes'

type CredentialProvider = () => string | undefined | Promise<string | undefined>

export interface HttpClientOptions {
  /** Full versioned base, for example `http://localhost:8000/api/v1`. */
  baseUrl: string
  getInvestorCredential?: CredentialProvider
  investorAccess?: InvestorAccessController
  fetchImplementation?: typeof fetch
  createId?: () => string
  now?: () => Date
  pollIntervalMs?: number
  maxPollAttempts?: number
  wait?: (milliseconds: number) => Promise<void>
}

const TERMINAL_RUN_STATUSES = new Set(['succeeded', 'partially_succeeded', 'failed'])
const THESIS_KEYS: ThesisCriterionKey[] = [
  'sector',
  'stage',
  'geography',
  'check_size',
  'ownership_target',
  'risk_appetite',
]

const DEFAULT_DISCOVERY_SOURCES = [
  'company_update',
  'product_launch',
  'developer_activity',
  'research',
  'accelerator_cohort',
]

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
  readonly investorAccess?: InvestorAccessController
  readonly #baseUrl: string
  readonly #getInvestorCredential?: CredentialProvider
  readonly #fetch: typeof fetch
  readonly #createId: () => string
  readonly #now: () => Date
  readonly #pollIntervalMs: number
  readonly #maxPollAttempts: number
  readonly #wait: (milliseconds: number) => Promise<void>
  readonly #companyNames = new Map<string, string>()
  readonly #outboundCandidates = new Map<string, WireOutboundCandidate>()

  constructor(options: HttpClientOptions) {
    this.#baseUrl = options.baseUrl.replace(/\/$/, '')
    this.investorAccess = options.investorAccess
    this.#getInvestorCredential =
      options.getInvestorCredential ?? (() => options.investorAccess?.getCredential())
    this.#fetch = options.fetchImplementation ?? globalThis.fetch.bind(globalThis)
    this.#createId = options.createId ?? (() => globalThis.crypto.randomUUID())
    this.#now = options.now ?? (() => new Date())
    this.#pollIntervalMs = options.pollIntervalMs ?? 500
    this.#maxPollAttempts = options.maxPollAttempts ?? 24
    this.#wait =
      options.wait ??
      ((milliseconds) => new Promise((resolve) => globalThis.setTimeout(resolve, milliseconds)))
  }

  async getWorkspace(): Promise<WorkspaceFixture> {
    const [thesisWire, candidatesWire, opportunitiesWire] = await Promise.all([
      this.#request<WireInvestmentThesisRevision>('/theses/active'),
      this.#request<WireCandidateCollection>('/outbound-candidates?limit=50'),
      this.#request<WireOpportunityCollection>('/opportunities?limit=50'),
    ])
    this.#rememberCompanyNames(candidatesWire)
    const firstOpportunityId = opportunitiesWire.items[0]?.opportunity_id
    const opportunity = firstOpportunityId
      ? await this.#getOpportunityDetail(firstOpportunityId)
      : null

    return {
      thesis: mapThesisRevision(thesisWire),
      search: composeInitialSearch(thesisWire, candidatesWire, opportunitiesWire),
      opportunity,
    }
  }

  async searchOpportunities(input: SearchInput): Promise<SearchResponse> {
    const freshWirePlan = await this.#request<WireQueryPlan>('/query-plans', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        raw_query: input.query.trim(),
        max_results: 50,
        retrieval_max_results: 10,
        retrieval_max_pages: 2,
        retrieval_timeout_seconds: 10,
      }),
    })
    const plan = this.#preparePlan(mapWireExecutablePlan(freshWirePlan), input)
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

  async saveThesis(criteria: ThesisCriterion[]): Promise<ThesisView> {
    const byKey = new Map(criteria.map((criterion) => [criterion.key, criterion]))
    const body = Object.fromEntries(
      THESIS_KEYS.map((key) => {
        const criterion = byKey.get(key)
        if (!criterion) throw new Error(`The thesis is missing the required ${key} criterion.`)
        if (criterion.mode !== 'no_preference' && criterion.operator === null) {
          throw new Error(`${criterion.label} requires an operator.`)
        }
        return [
          key,
          {
            mode: criterion.mode,
            operator: criterion.mode === 'no_preference' ? null : criterion.operator,
            values: criterion.mode === 'no_preference' ? [] : criterion.values,
            unknown_policy: criterion.unknownPolicy,
          },
        ]
      }),
    )
    const wire = await this.#request<WireInvestmentThesisRevision>('/theses', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    return mapThesisRevision(wire)
  }

  async discoverCandidates(input: DiscoveryInput): Promise<WorkspaceCommandResult> {
    return this.#workspaceRun('/sourcing-runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: input.query.trim(),
        source_categories: input.sourceCategories ?? DEFAULT_DISCOVERY_SOURCES,
        allowed_domains: input.allowedDomains ?? [],
        excluded_domains: input.excludedDomains ?? [],
        max_results: 10,
        max_pages: 3,
        max_bytes: 500_000,
        timeout_seconds: 20,
      }),
    })
  }

  async preliminaryAssessCandidate(candidateId: StableId): Promise<WorkspaceCommandResult> {
    return this.#workspaceRun(
      `/outbound-candidates/${encodeURIComponent(candidateId)}/preliminary-assessment`,
      { method: 'POST' },
    )
  }

  getOpportunity(opportunityId: StableId): Promise<OpportunityDetail> {
    return this.#getOpportunityDetail(opportunityId)
  }

  async screenOpportunity(opportunityId: StableId): Promise<OpportunityCommandResult> {
    return this.#opportunityRun(
      opportunityId,
      `/opportunities/${encodeURIComponent(opportunityId)}/screen`,
    )
  }

  async retryOpportunityRun(
    opportunityId: StableId,
    runId: StableId,
  ): Promise<OpportunityCommandResult> {
    return this.#opportunityRun(
      opportunityId,
      `/runs/${encodeURIComponent(runId)}/retry`,
    )
  }

  async submitApplication(input: ApplicationInput): Promise<ApplicationReceipt> {
    const form = new FormData()
    form.set('company_name', input.companyName)
    form.set('deck', input.deck)
    if (input.outboundCandidateId) form.set('outbound_candidate_id', input.outboundCandidateId)

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
    this.#outboundCandidates.set(wire.outbound_candidate_id, wire)
    return mapActivation(wire)
  }

  async recordOutreach(candidateId: StableId, input: OutreachInput): Promise<OutreachReceipt> {
    const wire = await this.#request<WireOutreachRecord>(
      `/outbound-candidates/${encodeURIComponent(candidateId)}/outreach`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ method: input.method, status: input.status.trim() }),
      },
    )
    return mapOutreach(wire)
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
    return mapOpportunityDetail(
      detail,
      runs,
      this.#companyNames.get(detail.company_id),
      detail.outbound_candidate_id
        ? this.#outboundCandidates.get(detail.outbound_candidate_id)
        : undefined,
    )
  }

  #preparePlan(freshPlan: ExecutableQueryPlan, input: SearchInput): ExecutableQueryPlan {
    const removedIds = new Set(input.removedCriterionIds ?? [])
    const removedFields = new Set(input.removedCriterionFields ?? [])
    for (const criterion of input.plan.criteria) {
      if (removedIds.has(criterion.criterionId)) removedFields.add(criterion.field)
    }
    const rawQuery = input.query.trim()
    const criteria = freshPlan.criteria.filter((criterion) => !removedFields.has(criterion.field))

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

    if (criteria.length === 0 && freshPlan.retrievalRequests.length === 0) {
      throw new QueryPlanUnavailableError()
    }

    return {
      ...freshPlan,
      queryPlanVersionId: `query-version-ui-${this.#createId()}`,
      supersedesQueryPlanVersionId: freshPlan.queryPlanVersionId,
      rawQuery,
      state: 'validated',
      criteria,
      retrievalRequests: freshPlan.retrievalRequests.map((request) => ({
        ...request,
        query: rawQuery,
      })),
      semanticRerank: freshPlan.semanticRerank
        ? { ...freshPlan.semanticRerank, query: rawQuery }
        : undefined,
      createdAt: this.#now().toISOString(),
    }
  }

  async #workspaceRun(path: string, init: RequestInit): Promise<WorkspaceCommandResult> {
    const accepted = await this.#request<WireRunAccepted>(path, init)
    const settled = await this.#pollRun(accepted.run)
    return {
      run: mapPipelineRun(settled.run),
      workspace: await this.getWorkspace(),
      timedOut: settled.timedOut,
    }
  }

  async #opportunityRun(
    opportunityId: StableId,
    path: string,
  ): Promise<OpportunityCommandResult> {
    const accepted = await this.#request<WireRunAccepted>(path, { method: 'POST' })
    const settled = await this.#pollRun(accepted.run)
    return {
      run: mapPipelineRun(settled.run),
      opportunity: await this.#getOpportunityDetail(opportunityId),
      timedOut: settled.timedOut,
    }
  }

  async #pollRun(initial: WirePipelineRun): Promise<{ run: WirePipelineRun; timedOut: boolean }> {
    let run = initial
    for (let attempt = 0; attempt < this.#maxPollAttempts; attempt += 1) {
      if (TERMINAL_RUN_STATUSES.has(run.status)) return { run, timedOut: false }
      if (attempt === this.#maxPollAttempts - 1) break
      await this.#wait(this.#pollIntervalMs)
      run = await this.#request<WirePipelineRun>(`/runs/${encodeURIComponent(run.run_id)}`)
    }
    return { run, timedOut: true }
  }

  #rememberCompanyNames(collection: WireCandidateCollection) {
    for (const candidate of collection.items) {
      this.#companyNames.set(candidate.company_id, candidate.company_name)
      this.#outboundCandidates.set(candidate.outbound_candidate_id, candidate)
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
