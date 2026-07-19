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
}

export interface SearchFilters {
  origin: 'all' | Origin
  knowledgeHandling: 'include_unknown' | 'needs_information' | 'known_only'
}

export interface SearchInput {
  query: string
  filters: SearchFilters
  plan: ExecutableQueryPlan
  removedCriterionIds?: StableId[]
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
  decisionReadyForCommand: boolean
}

export interface ThesisCriterion {
  key: string
  label: string
  value: string
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

export interface FounderLookupClient {
  readonly runtime: 'fixture' | 'http'
  getWorkspace(): Promise<WorkspaceFixture>
  searchOpportunities(input: SearchInput): Promise<SearchResponse>
  getOpportunity(opportunityId: StableId): Promise<OpportunityDetail>
  submitApplication(input: ApplicationInput): Promise<ApplicationReceipt>
  getFounderStatus(capability: string): Promise<FounderStatusView>
  activateCandidate(candidateId: StableId, outreachDraft: string): Promise<ActivationReceipt>
  recordDecision(input: DecisionInput): Promise<DecisionReceipt>
}
