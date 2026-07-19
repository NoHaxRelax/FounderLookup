export type StableId = string

export type KnowledgeState =
  | 'known'
  | 'unknown'
  | 'not_disclosed'
  | 'not_applicable'
  | 'conflicted'

export interface KnownValue<T> {
  state: 'known'
  value: T
  evidenceIds: StableId[]
}

export interface MissingValue {
  state: 'unknown' | 'not_disclosed' | 'not_applicable'
  reason: string
  evidenceIds: StableId[]
}

export interface ConflictedAlternative<T> {
  value: T
  evidenceIds: StableId[]
}

export interface ConflictedValue<T> {
  state: 'conflicted'
  reason: string
  alternatives: ConflictedAlternative<T>[]
}

export type KnowledgeValue<T> = KnownValue<T> | MissingValue | ConflictedValue<T>

export type Origin = 'inbound' | 'outbound'
export type MatchOutcome = 'match' | 'mismatch' | 'unknown' | 'not_evaluated'
export type CriterionMode = 'hard_constraint' | 'scored_preference' | 'no_preference'
export type UnknownPolicy = 'preserve_as_unknown' | 'needs_information' | 'manual_review'
export type ViewState = 'ready' | 'loading' | 'empty' | 'error' | 'blocked'
export type ScalarValue = string | number | boolean
export type QueryPlanningMode = 'deterministic' | 'model_assisted' | 'agent_assisted'
export type QueryPlanState = 'draft' | 'validated' | 'rejected'
export type CriterionStrength = Exclude<CriterionMode, 'no_preference'>
export type QueryCriterionField =
  | 'technical_founder'
  | 'geography'
  | 'sector'
  | 'stage'
  | 'check_size'
  | 'ownership_target'
  | 'risk_appetite'
  | 'enterprise_traction'
  | 'prior_vc_backing'
  | 'accelerator'
  | 'source_category'
  | 'origin'
  | 'workflow_state'
  | 'recommendation'
  | 'founder_axis'
  | 'market_axis'
  | 'idea_vs_market_axis'
  | 'trend'
  | 'contradiction_state'
  | 'evidence_coverage'
  | 'knowledge_state'
export type QueryOperator =
  | 'equals'
  | 'not_equals'
  | 'any_of'
  | 'all_of'
  | 'contains'
  | 'greater_than_or_equal'
  | 'less_than_or_equal'
  | 'between'
  | 'is_known'
  | 'is_unknown'

export interface TypedQueryCriterion {
  criterionId: StableId
  field: QueryCriterionField
  operator: QueryOperator
  operands: ScalarValue[]
  strength: CriterionStrength
  unknownPolicy: UnknownPolicy
  sourceText: string
}

export interface BoundedRetrievalRequest {
  retrievalRequestId: StableId
  query: string
  sourceCategories: string[]
  allowedDomains: string[]
  excludedDomains: string[]
  publishedAfter?: string
  publishedBefore?: string
  maxResults: number
  maxPages: number
  timeoutSeconds: number
}

export interface TypedUnresolvedPhrase {
  text: string
  startOffset: number
  endOffset: number
  reason: string
}

export interface SemanticRerankPlan {
  query: string
  methodVersion: string
  maxResults: number
}

export interface ExecutableQueryPlan {
  schemaVersion: 'opportunity-query-plan.v0'
  queryPlanId: StableId
  queryPlanVersionId: StableId
  supersedesQueryPlanVersionId?: StableId
  rawQuery: string
  planningMode: QueryPlanningMode
  plannerVersion: string
  state: QueryPlanState
  criteria: TypedQueryCriterion[]
  retrievalRequests: BoundedRetrievalRequest[]
  unresolvedPhrases: TypedUnresolvedPhrase[]
  semanticRerank?: SemanticRerankPlan
  maxResults: number
  createdAt: string
}

export interface InterpretedCriterion {
  id: StableId
  label: string
  sourceText: string
  mode: CriterionMode
  outcome: MatchOutcome
  knowledgeState: KnowledgeState
  valueLabel: string
  unknownPolicy: UnknownPolicy
}

export interface QueryPlan {
  id: StableId
  version: string
  planningMode: QueryPlanningMode
  rawQuery: string
  criteria: InterpretedCriterion[]
  sourceCategories: string[]
  unresolvedPhrases: Array<{
    text: string
    reason: string
  }>
  maxResults: number
  execution: ExecutableQueryPlan
}

export type FounderAxisRating = 'strong' | 'mixed' | 'weak' | 'unknown'
export type MarketAxisRating = 'bullish' | 'neutral' | 'bear' | 'unknown'
export type IdeaAxisRating = 'viable' | 'pivotable' | 'weak' | 'unknown'
export type Trend = 'improving' | 'stable' | 'declining' | 'unknown'

export type OutboundCandidateStatus =
  | 'discovered'
  | 'preliminary_assessment'
  | 'ready_for_activation'
  | 'activated'
  | 'contacted'
  | 'applied'
  | 'closed'

export type PublicContactKind = 'website' | 'contact_page' | 'public_email' | 'public_profile' | 'other'

export interface PublicContactRoute {
  id: StableId
  kind: PublicContactKind
  label: string
  displayValue: string
  /** Present only for a validated HTTPS URL or a supplied public-email mailto route. */
  href?: string
  sourceArtifactId: StableId
  sourceName: string
  sourceLocator: string
  collectedAt?: string
}

export interface SourcingLoopAudit {
  status: 'running' | 'stopped' | 'completed' | 'failed'
  roundsCompleted: number
  roundLimit?: number
  stopReason: string
  runId?: StableId
}

export interface AxisSummary {
  key: 'founder' | 'market' | 'idea_vs_market'
  label: string
  rating: FounderAxisRating | MarketAxisRating | IdeaAxisRating
  trend: Trend
  trendLabel: string
  confidence: KnowledgeValue<number>
  coverageLabel: string
  supportingClaimIds: StableId[]
  counterClaimIds: StableId[]
  openQuestions: string[]
}

export interface CandidateSummary {
  id: StableId
  opportunityId?: StableId
  companyName: string
  founderName: KnowledgeValue<string>
  origin: Origin
  workflowState: string
  trigger: string
  freshnessLabel: string
  coverageLabel: string
  coveragePercent: number | null
  thesisFitLabel: string
  overallMatch: MatchOutcome
  unknownFields: string[]
  axes: AxisSummary[]
  contradictionCount: number
  queueReason: string
  elapsedLabel: string
  incomplete: boolean
  recommendation?: RecommendationAction
  outboundStatus?: OutboundCandidateStatus
  activationState?: 'not_activated' | 'activated' | 'contacted'
  publicContactRoutes?: PublicContactRoute[]
  sourcingLoopAudit?: SourcingLoopAudit
}

export interface SearchFilters {
  origin: 'all' | Origin
  knowledgeHandling: 'include_unknown' | 'needs_information' | 'known_only'
}

export interface SearchInput {
  query: string
  filters: SearchFilters
  /** Fixture provenance only; the HTTP client always requests a fresh server plan. */
  plan: ExecutableQueryPlan
  removedCriterionIds?: StableId[]
  removedCriterionFields?: QueryCriterionField[]
}

export interface SearchResponse {
  plan: QueryPlan
  results: CandidateSummary[]
  truncated: boolean
  totalConsidered: number
  updatedAt: string
}

export type TrustState = 'scored' | 'unscored' | 'unsupported'

export interface TrustFactor {
  label: string
  signal: 'strengthens' | 'neutral' | 'weakens' | 'unknown'
  rationale: string
}

export interface SourceLocator {
  kind:
    | 'document_page'
    | 'url_excerpt'
    | 'repository_commit'
    | 'paper_section'
    | 'patent_section'
    | 'interview_segment'
    | 'source_record'
  label: string
  uri?: string
  excerpt: string
}

export interface EvidenceItem {
  id: StableId
  sourceArtifactId: StableId
  sourceName: string
  sourceCategory: string
  classification: 'public' | 'founder_private' | 'investor_internal' | 'unknown'
  collectedAt: string
  sourceEventTime: KnowledgeValue<string>
  availability: 'available' | 'source_unavailable' | 'content_removed' | 'access_restricted'
  locator: SourceLocator
}

export interface ClaimItem {
  id: StableId
  statement: string
  status: 'supported' | 'contradicted' | 'unsupported' | 'asserted_unverified' | 'unresolved'
  verificationLabel: string
  trust: {
    state: TrustState
    score?: number
    reason?: string
    factors: TrustFactor[]
  }
  supportingEvidenceIds: StableId[]
  counterEvidenceIds: StableId[]
}

export interface ContradictionItem {
  id: StableId
  summary: string
  blocking: boolean
  claimIds: StableId[]
  evidenceIds: StableId[]
  smallestNextAction: string
}

export interface FounderScore {
  score: number
  provisional: boolean
  uncertainty: 'low' | 'moderate' | 'high'
  coverageLabel: string
  asOf: string
  version: string
  explanation: string
}

export interface TimelineStage {
  id: StableId
  label: string
  status: 'succeeded' | 'running' | 'queued' | 'failed' | 'skipped' | 'blocked'
  timing: string
  detail: string
  externalWait?: boolean
}

export type RecommendationAction =
  | 'activate'
  | 'advance'
  | 'needs_information'
  | 'manual_review'
  | 'do_not_pursue'

export interface RecommendationView {
  id: StableId
  action: RecommendationAction
  summary: string
  reasons: string[]
  nextActions: string[]
  policyVersion: string
  createdAt: string
}

export type MemoSectionKind =
  | 'company_snapshot'
  | 'investment_hypotheses'
  | 'swot'
  | 'problem_and_product'
  | 'traction_and_kpis'

export interface MemoSection {
  kind: MemoSectionKind
  title: string
  content: KnowledgeValue<string>
  materialClaimIds: StableId[]
}

export interface MemoView {
  id: StableId
  version: string
  generatedAt: string
  evidenceAsOf: string
  thesisVersion: string
  sections: MemoSection[]
  adversarialNotes: Array<{
    title: string
    body: string
    claimIds: StableId[]
  }>
}

export interface OpportunityDetail {
  id: StableId
  company: {
    id: StableId
    name: string
    sector: KnowledgeValue<string>
    geography: KnowledgeValue<string>
  }
  founder: {
    id: StableId
    name: KnowledgeValue<string>
  }
  screeningCase: {
    id: StableId
    status: string
    readiness: 'not_evaluated' | 'blocked' | 'ready' | 'ready_with_accepted_risk'
    readinessReason: string
  }
  origin: Origin
  assessmentId: StableId
  assessmentMode: 'full'
  inputSnapshotId: StableId
  thesisVersion: string
  founderScore: KnowledgeValue<FounderScore>
  axes: AxisSummary[]
  coverageLabel: string
  claims: ClaimItem[]
  evidence: EvidenceItem[]
  contradictions: ContradictionItem[]
  diligenceActions: string[]
  memo: MemoView
  recommendation: RecommendationView
  timeline: TimelineStage[]
  runIds: StableId[]
  pipelineRuns: PipelineRunView[]
  decisionReadyForCommand: boolean
  publicContactRoutes?: PublicContactRoute[]
  sourcingLoopAudit?: SourcingLoopAudit
}

export type ThesisCriterionKey =
  | 'sector'
  | 'stage'
  | 'geography'
  | 'check_size'
  | 'ownership_target'
  | 'risk_appetite'

export interface ThesisCriterion {
  key: ThesisCriterionKey
  label: string
  value: string
  operator: QueryOperator | null
  values: ScalarValue[]
  mode: CriterionMode
  unknownPolicy: UnknownPolicy
}

export interface ThesisView {
  id: StableId
  version: string
  effectiveAt: string
  criteria: ThesisCriterion[]
}

export interface WorkspaceFixture {
  thesis: ThesisView
  search: SearchResponse
  opportunity: OpportunityDetail | null
}

export interface ApplicationInput {
  companyName: string
  deck: File
  idempotencyKey: string
  website?: string
  oneLinePitch?: string
  location?: string
  stage?: string
  contactEmail?: string
  founders?: ApplicationFounderInput[]
  /** Links an application to an already-known outbound candidate when the founder used that path. */
  outboundCandidateId?: StableId
}

export interface ApplicationFounderInput {
  fullName: string
  roleTitle?: string
  email?: string
  linkedinUrl?: string
  githubUrl?: string
  previousCompanies?: string[]
  background?: string
}

export interface ApplicationReceipt {
  applicationId: StableId
  companyId: StableId
  runId: StableId
  sourceArtifactId: StableId
  receivedAt: string
  status: 'received'
  founderStatusCapability: string
  founderStatusUrl: string
  replayed: boolean
}

export interface FounderStatusView {
  applicationId: StableId
  receivedAt: string
  stage: string
  lastUpdatedAt: string
  targetState: 'on_track' | 'approaching' | 'missed' | 'complete'
  targetLabel: string
  informationRequests: string[]
  focusedRequest?: string
  approvedOutcome?: string
  nextAction?: string
  outcomeAt?: string
}

export interface ActivationReceipt {
  candidateId: StableId
  companyId?: StableId
  state: 'activated'
  activatedAt: string
  outreachDraft: string
}

export type PipelineRunStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'partially_succeeded'
  | 'failed'

export interface PipelineFailureView {
  id: StableId
  stageKey: string
  code: string
  message: string
  retryable: boolean
  occurredAt: string
}

export interface PipelineRunView {
  id: StableId
  kind: string
  status: PipelineRunStatus
  queuedAt: string
  startedAt?: string
  completedAt?: string
  acceptedOutputIds: StableId[]
  failures: PipelineFailureView[]
  retryOfRunId?: StableId
  attempt: number
  loopAudit?: SourcingLoopAudit
}

export interface DiscoveryInput {
  query: string
  sourceCategories?: string[]
  allowedDomains?: string[]
  excludedDomains?: string[]
}

export interface WorkspaceCommandResult {
  run: PipelineRunView
  workspace: WorkspaceFixture
  timedOut: boolean
}

export interface OpportunityCommandResult {
  run: PipelineRunView
  opportunity: OpportunityDetail
  timedOut: boolean
}

export type OutreachMethod = 'email' | 'linkedin' | 'introduction' | 'other'

export interface OutreachInput {
  method: OutreachMethod
  status: string
}

export interface OutreachReceipt {
  outreachId: StableId
  candidateId: StableId
  method: OutreachMethod
  status: string
  actorId?: StableId
  occurredAt: string
}

export type DecisionDisposition =
  | 'advance'
  | 'decline'
  | 'hold'
  | 'request_more_information'

export interface DecisionInput {
  opportunityId: StableId
  assessmentId: StableId
  memoId: StableId
  recommendationId: StableId
  disposition: DecisionDisposition
  rationale: string
}

export interface DecisionReceipt {
  decisionId: StableId
  disposition: DecisionDisposition
  actorId?: StableId
  actorLabel: string
  decidedAt: string
}

export interface ApiProblem {
  type: string
  title: string
  status: number
  code: string
  requestId?: string
  detail?: string
  fields?: Array<{ field: string; code: string; message: string }>
}

/**
 * Session-scoped access to protected investor resources. Implementations must never source the
 * credential from a Vite-exposed environment variable or serialize it into application data.
 */
export interface InvestorAccessController {
  hasCredential(): boolean
  setCredential(credential: string): void
  clearCredential(): void
  getCredential(): string | undefined
}

export interface FounderLookupClient {
  readonly runtime: 'fixture' | 'http'
  readonly investorAccess?: InvestorAccessController
  getWorkspace(): Promise<WorkspaceFixture>
  searchOpportunities(input: SearchInput): Promise<SearchResponse>
  saveThesis(criteria: ThesisCriterion[]): Promise<ThesisView>
  discoverCandidates(input: DiscoveryInput): Promise<WorkspaceCommandResult>
  preliminaryAssessCandidate(candidateId: StableId): Promise<WorkspaceCommandResult>
  getOpportunity(opportunityId: StableId): Promise<OpportunityDetail>
  screenOpportunity(opportunityId: StableId): Promise<OpportunityCommandResult>
  retryOpportunityRun(
    opportunityId: StableId,
    runId: StableId,
  ): Promise<OpportunityCommandResult>
  submitApplication(input: ApplicationInput): Promise<ApplicationReceipt>
  getFounderStatus(capability: string): Promise<FounderStatusView>
  activateCandidate(candidateId: StableId, outreachDraft: string): Promise<ActivationReceipt>
  recordOutreach(candidateId: StableId, input: OutreachInput): Promise<OutreachReceipt>
  recordDecision(input: DecisionInput): Promise<DecisionReceipt>
}
