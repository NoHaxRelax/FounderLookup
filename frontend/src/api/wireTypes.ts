import type {
  CriterionStrength,
  DecisionDisposition,
  KnowledgeState,
  Origin,
  OutboundCandidateStatus,
  QueryCriterionField,
  QueryOperator,
  QueryPlanningMode,
  QueryPlanState,
  ScalarValue,
  StableId,
  UnknownPolicy,
} from './types'

export interface WireKnowledgeAlternative<T> {
  value: T
  evidence_ids: StableId[]
}

export interface WireKnowledgeValue<T> {
  schema_version: 'knowledge-value.v0'
  state: KnowledgeState
  value: T | null
  reason: string | null
  evidence_ids: StableId[]
  alternatives: Array<WireKnowledgeAlternative<T>>
}

export interface WireThesisCriterion {
  mode: 'hard_constraint' | 'scored_preference' | 'no_preference'
  operator: QueryOperator | null
  values: ScalarValue[]
  unknown_policy: UnknownPolicy
  configured_outcome?: 'pass' | 'fail' | 'indeterminate' | 'not_evaluated' | null
}

export interface WireInvestmentThesisRevision {
  thesis_id: StableId
  thesis_version_id: StableId
  revision_number: number
  created_at: string
  created_by: StableId
  sector: WireThesisCriterion
  stage: WireThesisCriterion
  geography: WireThesisCriterion
  check_size: WireThesisCriterion
  ownership_target: WireThesisCriterion
  risk_appetite: WireThesisCriterion
}

export interface WireApplicationReceipt {
  application_id: StableId
  company_id: StableId
  run_id: StableId
  source_artifact_id: StableId
  status: 'received'
  received_at: string
  founder_status_capability: string
  replayed: boolean
}

export interface WireFounderStatusView {
  application_id: StableId
  received_at: string
  stage: 'received' | 'processing' | 'needs_information' | 'under_review' | 'complete'
  last_updated_at: string
  target_state: 'on_track' | 'approaching' | 'missed' | 'complete'
  information_requests: string[]
  outcome: string | null
  next_action: string | null
  outcome_at: string | null
}

export interface WireCoverageSummary {
  level: 'low' | 'medium' | 'high'
  source_count: number
  artifact_count: number
  evidence_count: number
  source_categories: string[]
  missing_fields: string[]
  conflicted_fields: string[]
  freshest_evidence_at: WireKnowledgeValue<string>
}

export interface WireAxisAssessment {
  schema_version: 'axis-assessment.v0'
  assessment_id: StableId
  assessment_version_id: StableId
  rubric_version: string
  axis: 'founder' | 'market' | 'idea_vs_market'
  rating: string
  trend: 'improving' | 'stable' | 'declining' | 'unknown'
  confidence: WireKnowledgeValue<number>
  coverage: WireCoverageSummary
  supporting_claim_ids: StableId[]
  counter_claim_ids: StableId[]
  open_questions: string[]
}

export interface WireIndependentAxes {
  founder: WireAxisAssessment
  market: WireAxisAssessment
  idea_vs_market: WireAxisAssessment
}

export interface WireFounderScoreSnapshot {
  schema_version: 'founder-score.v0'
  snapshot_id: StableId
  snapshot_version_id: StableId
  founder_id: StableId
  score_policy_version: string
  as_of: string
  score: number
  coverage: WireCoverageSummary
  uncertainty: 'low' | 'moderate' | 'high'
  provisional: boolean
}

export interface WireContradiction {
  contradiction_id: StableId
  contradiction_version_id: StableId
  claim_ids: StableId[]
  evidence_ids: StableId[]
  status: 'unresolved' | 'resolved' | 'accepted_risk'
  blocking: boolean
  summary: string
  detected_at: string
  resolution: string | null
}

export interface WireDiligenceAction {
  action_id: StableId
  status: 'open' | 'completed' | 'dismissed'
  description: string
  resolves_claim_ids: StableId[]
  resolves_contradiction_ids: StableId[]
  requested_evidence: string
}

export interface WireDecisionReadiness {
  status: 'not_evaluated' | 'blocked' | 'ready' | 'ready_with_accepted_risk'
  blockers: Array<{
    blocker_id: StableId
    check_key: string
    reason: string
    related_record_ids: StableId[]
  }>
}

export interface WireMemoSection {
  kind:
    | 'company_snapshot'
    | 'investment_hypotheses'
    | 'swot'
    | 'problem_and_product'
    | 'traction_and_kpis'
    | 'risks_and_diligence'
    | 'team'
    | 'financials'
  content: WireKnowledgeValue<string>
  material_claim_ids: StableId[]
}

export interface WireInvestmentMemo {
  schema_version: 'investment-memo.v0'
  memo_id: StableId
  memo_version_id: StableId
  opportunity_id: StableId
  screening_case_id: StableId
  assessment_id: StableId
  run_id: StableId
  thesis_version: string
  evidence_as_of: string
  generated_at: string
  sections: WireMemoSection[]
}

export interface WireRecommendation {
  schema_version: 'recommendation.v0'
  recommendation_id: StableId
  recommendation_version_id: StableId
  assessment_id: StableId
  policy_version: string
  action: 'activate' | 'advance' | 'needs_information' | 'manual_review' | 'do_not_pursue'
  reasons: Array<{ summary: string; claim_ids: StableId[] }>
  next_actions: string[]
  created_at: string
}

export interface WireAssessmentEnvelope {
  schema_version: 'assessment-envelope.v0'
  assessment_id: StableId
  assessment_version_id: StableId
  identity: {
    mode: 'preliminary' | 'full'
    origin: Origin
    founder_id: WireKnowledgeValue<StableId>
    company_id: StableId | WireKnowledgeValue<StableId>
  }
  input_snapshot_id: StableId
  input_snapshot_as_of: string
  coverage: WireCoverageSummary
  deterministic_results: Array<{
    result_id: StableId
    rule_id: StableId
    outcome: 'pass' | 'fail' | 'indeterminate' | 'not_evaluated'
    inputs: Array<{ field: string; value: WireKnowledgeValue<ScalarValue> }>
    reason: string
  }>
  founder_score: WireKnowledgeValue<WireFounderScoreSnapshot>
  axes: WireIndependentAxes
  contradictions: WireContradiction[]
  diligence_actions: WireDiligenceAction[]
  decision_readiness: WireDecisionReadiness | null
  memo: WireInvestmentMemo | null
  recommendation: WireRecommendation | null
  run_id: StableId
  created_at: string
}

export interface WireTrustFactor {
  kind: string
  signal: WireKnowledgeValue<'strengthens' | 'neutral' | 'weakens'>
  evidence_ids: StableId[]
  rationale: string
}

export interface WireClaim {
  claim_id: StableId
  statement: string
  status: 'asserted_unverified' | 'supported' | 'contradicted' | 'unsupported' | 'unresolved'
  as_of: string
  supporting_evidence_ids: StableId[]
  counter_evidence_ids: StableId[]
  trust: {
    state: 'scored' | 'unscored' | 'unsupported'
    trust_policy_version: string
    score: number | null
    factors: WireTrustFactor[]
    reason: string | null
  }
}

export interface WireSourceLocator {
  kind:
    | 'document_page'
    | 'url_excerpt'
    | 'repository_commit'
    | 'paper_section'
    | 'patent_section'
    | 'interview_segment'
    | 'source_record'
  locator: string
  excerpt: string | null
}

export interface WireEvidence {
  evidence_id: StableId
  claim_id: StableId
  source_artifact_id: StableId
  stance: 'supports' | 'refutes' | 'context'
  locator: WireSourceLocator
  collected_at: string
  source_event_time: WireKnowledgeValue<string>
  availability: 'available' | 'source_unavailable' | 'content_removed' | 'access_restricted'
}

export interface WireHumanDecision {
  decision_id: StableId
  screening_case_id: StableId
  opportunity_id: StableId
  assessment_id: StableId
  memo_id: StableId
  reviewed_recommendation_id: StableId
  disposition: DecisionDisposition
  actor_id: StableId
  rationale: string
  decided_at: string
}

export interface WireOpportunityDetail {
  opportunity_id: StableId
  origin: Origin
  application_id: StableId
  outbound_candidate_id: StableId | null
  founder_id: WireKnowledgeValue<StableId>
  company_id: StableId
  screening_case_id: StableId
  screening_status: string
  latest_assessment: WireAssessmentEnvelope | null
  claims: WireClaim[]
  evidence: WireEvidence[]
  latest_memo: WireInvestmentMemo | null
  latest_recommendation: WireRecommendation | null
  human_decisions: WireHumanDecision[]
  related_run_ids: StableId[]
  timing: {
    started_at: string
    last_updated_at: string
    decision_readiness_target_at: string
    elapsed_seconds: number
    target_state: 'on_track' | 'approaching' | 'missed' | 'complete'
  }
  public_contact_routes?: WirePublicContactRoute[]
  contact_routes?: WirePublicContactRoute[]
  sourcing_audit?: WireSourcingLoopAudit
  agent_loop?: WireSourcingLoopAudit
}

export interface WireOpportunitySummary {
  opportunity_id: StableId
  origin: Origin
  company_id: StableId
  screening_case_id: StableId
  screening_status: string
  recommendation: string | null
  updated_at: string
}

export interface WireOpportunityCollection {
  items: WireOpportunitySummary[]
  limit: number
  truncated: boolean
  applied_filters: string[]
  ordering: string
}

export interface WireOutboundCandidate {
  outbound_candidate_id: StableId
  company_id: StableId
  company_name: string
  founder_id: WireKnowledgeValue<StableId>
  status: OutboundCandidateStatus
  discovered_at: string
  source_artifact_ids: StableId[]
  preliminary_assessment: WireAssessmentEnvelope | null
  application_id?: StableId | null
  outreach_draft: string | null
  updated_at: string
  public_contact_routes?: WirePublicContactRoute[]
  contact_routes?: WirePublicContactRoute[]
  sourcing_audit?: WireSourcingLoopAudit
  agent_loop?: WireSourcingLoopAudit
}

export interface WirePublicContactRoute {
  route_id: StableId
  kind: 'website' | 'contact_page' | 'contact_url' | 'public_email' | 'public_profile' | 'other'
  label: string
  value: string
  href?: string | null
  classification: 'public' | 'founder_private' | 'investor_internal' | 'unknown'
  source_artifact_id: StableId
  source_name: string
  source_locator: string
  collected_at?: string | null
}

export interface WireSourcingLoopAudit {
  status: 'running' | 'stopped' | 'completed' | 'failed'
  rounds_completed: number
  round_limit?: number | null
  stop_reason: string
  run_id?: StableId | null
}

export interface WireOutreachRecord {
  outreach_id: StableId
  outbound_candidate_id: StableId
  method: 'email' | 'linkedin' | 'introduction' | 'other'
  status: string
  actor_id: StableId
  occurred_at: string
}

export interface WireCandidateCollection {
  items: WireOutboundCandidate[]
  limit: number
  truncated: boolean
  applied_filters: string[]
  ordering: string
}

export interface WireQueryCriterion {
  criterion_id: StableId
  field: QueryCriterionField
  operator: QueryOperator
  operands: ScalarValue[]
  strength: CriterionStrength
  unknown_policy: UnknownPolicy
  source_text: string
}

export interface WireRetrievalRequest {
  retrieval_request_id: StableId
  query: string
  source_categories: string[]
  allowed_domains: string[]
  excluded_domains: string[]
  published_after?: string
  published_before?: string
  max_results: number
  max_pages: number
  timeout_seconds: number
}

export interface WireQueryPlan {
  schema_version: 'opportunity-query-plan.v0'
  query_plan_id: StableId
  query_plan_version_id: StableId
  supersedes_query_plan_version_id?: StableId
  raw_query: string
  planning_mode: QueryPlanningMode
  planner_version: string
  state: QueryPlanState
  criteria: WireQueryCriterion[]
  retrieval_requests: WireRetrievalRequest[]
  unresolved_phrases: Array<{
    text: string
    start_offset: number
    end_offset: number
    reason: string
  }>
  semantic_rerank?: {
    query: string
    method_version: string
    max_results: number
  }
  max_results: number
  created_at: string
}

export interface WireQueryResult {
  plan: WireQueryPlan
  results: Array<{
    opportunity_id: StableId
    criteria: Array<{
      criterion_id: StableId
      field: QueryCriterionField
      strength: CriterionStrength
      outcome: 'match' | 'mismatch' | 'unknown'
      rationale: string
      knowledge_state: KnowledgeState
      unknown_policy: UnknownPolicy
    }>
    matched_preferences: number
    evaluated_preferences: number
  }>
  eligible_count: number
  truncated: boolean
  ordering: string
  sourcing_run_id: StableId | null
}

export interface WirePipelineRun {
  schema_version?: 'pipeline-run.v0'
  run_id: StableId
  kind: string
  status: 'queued' | 'running' | 'succeeded' | 'partially_succeeded' | 'failed'
  versions?: Record<string, string>
  input_snapshot_id?: StableId
  input_snapshot_as_of?: string
  queued_at: string
  started_at: string | null
  completed_at: string | null
  stages: Array<{
    stage_key: string
    status: 'queued' | 'running' | 'succeeded' | 'skipped' | 'failed'
    queued_at: string
    started_at: string | null
    completed_at: string | null
    accepted_output_ids?: StableId[]
    failure_ids: StableId[]
  }>
  accepted_output_ids?: StableId[]
  failures: Array<{
    failure_id: StableId
    stage_key: string
    safe_code: string
    safe_message: string
    retryable: boolean
    occurred_at: string
  }>
  retry_of_run_id?: StableId | null
  attempt?: number
  sourcing_audit?: WireSourcingLoopAudit
  agent_loop?: WireSourcingLoopAudit
}

export interface WireRunAccepted {
  run_id: StableId
  status_url: string
  run: WirePipelineRun
}

export interface WireProblemDetails {
  type: string
  title: string
  status: number
  code: string
  request_id: string
  detail: string | null
  fields: Array<{ field: string; code: string; message: string }>
}
