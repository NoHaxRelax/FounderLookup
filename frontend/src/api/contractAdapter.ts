import type {
  ActivationReceipt,
  ApiProblem,
  ApplicationReceipt,
  AxisSummary,
  BoundedRetrievalRequest,
  CandidateSummary,
  DecisionReceipt,
  EvidenceItem,
  ExecutableQueryPlan,
  FounderScore,
  FounderStatusView,
  InterpretedCriterion,
  KnowledgeState,
  KnowledgeValue,
  MemoSection,
  MemoSectionKind,
  MemoView,
  OpportunityDetail,
  OutreachReceipt,
  PipelineRunView,
  PublicContactRoute,
  QueryPlan,
  RecommendationView,
  SearchFilters,
  SearchResponse,
  SourcingLoopAudit,
  ThesisCriterion,
  ThesisView,
  TimelineStage,
  TypedQueryCriterion,
} from './types'
import type {
  WireApplicationReceipt,
  WireAssessmentEnvelope,
  WireCandidateCollection,
  WireCoverageSummary,
  WireEvidence,
  WireFounderStatusView,
  WireHumanDecision,
  WireIndependentAxes,
  WireInvestmentMemo,
  WireInvestmentThesisRevision,
  WireKnowledgeValue,
  WireOpportunityCollection,
  WireOpportunityDetail,
  WireOpportunitySummary,
  WireOutreachRecord,
  WireOutboundCandidate,
  WirePipelineRun,
  WirePublicContactRoute,
  WireProblemDetails,
  WireQueryPlan,
  WireQueryResult,
  WireRecommendation,
  WireThesisCriterion,
  WireSourcingLoopAudit,
} from './wireTypes'

const fieldLabels: Record<TypedQueryCriterion['field'], string> = {
  technical_founder: 'Technical founder',
  geography: 'Geography',
  sector: 'Sector',
  stage: 'Stage',
  check_size: 'Check size',
  ownership_target: 'Ownership target',
  risk_appetite: 'Risk appetite',
  enterprise_traction: 'Enterprise traction',
  prior_vc_backing: 'Prior VC backing',
  accelerator: 'Accelerator',
  source_category: 'Source category',
  origin: 'Origin',
  workflow_state: 'Workflow state',
  recommendation: 'Recommendation',
  founder_axis: 'Founder axis',
  market_axis: 'Market axis',
  idea_vs_market_axis: 'Idea vs market axis',
  trend: 'Trend',
  contradiction_state: 'Contradiction state',
  evidence_coverage: 'Evidence coverage',
  knowledge_state: 'Knowledge state',
}

const thesisLabels = {
  sector: 'Sector',
  stage: 'Stage',
  geography: 'Geography',
  check_size: 'Check size',
  ownership_target: 'Ownership target',
  risk_appetite: 'Risk appetite',
} as const

const memoTitles: Record<MemoSectionKind, string> = {
  company_snapshot: 'Company snapshot',
  investment_hypotheses: 'Investment hypotheses',
  swot: 'SWOT',
  problem_and_product: 'Problem and product',
  traction_and_kpis: 'Traction and KPIs',
}

const requiredMemoKinds: MemoSectionKind[] = [
  'company_snapshot',
  'investment_hypotheses',
  'swot',
  'problem_and_product',
  'traction_and_kpis',
]

const unknown = <T>(reason: string): KnowledgeValue<T> => ({
  state: 'unknown',
  reason,
  evidenceIds: [],
})

export function mapKnowledge<T, R>(
  wire: WireKnowledgeValue<T>,
  mapValue: (value: T) => R,
): KnowledgeValue<R> {
  if (wire.state === 'known' && wire.value !== null) {
    return {
      state: 'known',
      value: mapValue(wire.value),
      evidenceIds: [...wire.evidence_ids],
    }
  }

  if (wire.state === 'conflicted') {
    return {
      state: 'conflicted',
      reason: wire.reason ?? 'The backend returned conflicting source-backed alternatives.',
      alternatives: wire.alternatives.map((alternative) => ({
        value: mapValue(alternative.value),
        evidenceIds: [...alternative.evidence_ids],
      })),
    }
  }

  return {
    state: wire.state === 'known' ? 'unknown' : wire.state,
    reason: wire.reason ?? 'The backend did not provide a known value.',
    evidenceIds: [...wire.evidence_ids],
  }
}

const formatCriterionValue = (criterion: WireThesisCriterion) => {
  if (criterion.mode === 'no_preference') return 'No preference'
  if (criterion.values.length === 0) return criterion.operator?.replaceAll('_', ' ') ?? 'Unknown'
  return criterion.values.map(String).join(criterion.operator === 'between' ? ' – ' : ', ')
}

export function mapThesisRevision(wire: WireInvestmentThesisRevision): ThesisView {
  const criteria = (Object.keys(thesisLabels) as Array<keyof typeof thesisLabels>).map((key) => {
    const criterion = wire[key]
    return {
      key,
      label: thesisLabels[key],
      value: formatCriterionValue(criterion),
      operator: criterion.operator,
      values: [...criterion.values],
      mode: criterion.mode,
      unknownPolicy: criterion.unknown_policy,
    } satisfies ThesisCriterion
  })

  return {
    id: wire.thesis_id,
    version: wire.thesis_version_id,
    effectiveAt: wire.created_at,
    criteria,
  }
}

export function buildExecutablePlanFromThesis(
  wire: WireInvestmentThesisRevision,
): ExecutableQueryPlan {
  const criteria = (Object.keys(thesisLabels) as Array<keyof typeof thesisLabels>).flatMap(
    (field) => {
      const criterion = wire[field]
      if (criterion.mode === 'no_preference' || criterion.operator === null) return []
      return [
        {
          criterionId: `thesis-${field}-${wire.revision_number}`,
          field,
          operator: criterion.operator,
          operands: [...criterion.values],
          strength: criterion.mode,
          unknownPolicy: criterion.unknown_policy,
          sourceText: `${thesisLabels[field]}: ${formatCriterionValue(criterion)}`,
        } satisfies TypedQueryCriterion,
      ]
    },
  )
  const rawQuery = `Active thesis revision ${wire.revision_number}`
  return {
    schemaVersion: 'opportunity-query-plan.v0',
    queryPlanId: `thesis-query-${wire.thesis_id}`,
    queryPlanVersionId: `thesis-query-${wire.thesis_version_id}`,
    rawQuery,
    planningMode: 'deterministic',
    plannerVersion: 'frontend-thesis-adapter.v0',
    state: criteria.length > 0 ? 'validated' : 'draft',
    criteria,
    retrievalRequests: [],
    unresolvedPhrases: [],
    maxResults: 50,
    createdAt: wire.created_at,
  }
}

const mapRetrievalRequest = (wire: WireQueryPlan['retrieval_requests'][number]) => ({
  retrievalRequestId: wire.retrieval_request_id,
  query: wire.query,
  sourceCategories: [...wire.source_categories],
  allowedDomains: [...wire.allowed_domains],
  excludedDomains: [...wire.excluded_domains],
  publishedAfter: wire.published_after,
  publishedBefore: wire.published_before,
  maxResults: wire.max_results,
  maxPages: wire.max_pages,
  timeoutSeconds: wire.timeout_seconds,
}) satisfies BoundedRetrievalRequest

export function mapWireExecutablePlan(wire: WireQueryPlan): ExecutableQueryPlan {
  return {
    schemaVersion: wire.schema_version,
    queryPlanId: wire.query_plan_id,
    queryPlanVersionId: wire.query_plan_version_id,
    supersedesQueryPlanVersionId: wire.supersedes_query_plan_version_id,
    rawQuery: wire.raw_query,
    planningMode: wire.planning_mode,
    plannerVersion: wire.planner_version,
    state: wire.state,
    criteria: wire.criteria.map((criterion) => ({
      criterionId: criterion.criterion_id,
      field: criterion.field,
      operator: criterion.operator,
      operands: [...criterion.operands],
      strength: criterion.strength,
      unknownPolicy: criterion.unknown_policy,
      sourceText: criterion.source_text,
    })),
    retrievalRequests: wire.retrieval_requests.map(mapRetrievalRequest),
    unresolvedPhrases: wire.unresolved_phrases.map((phrase) => ({
      text: phrase.text,
      startOffset: phrase.start_offset,
      endOffset: phrase.end_offset,
      reason: phrase.reason,
    })),
    semanticRerank: wire.semantic_rerank
      ? {
          query: wire.semantic_rerank.query,
          methodVersion: wire.semantic_rerank.method_version,
          maxResults: wire.semantic_rerank.max_results,
        }
      : undefined,
    maxResults: wire.max_results,
    createdAt: wire.created_at,
  }
}

const aggregateKnowledgeState = (states: KnowledgeState[]): KnowledgeState => {
  for (const state of ['conflicted', 'unknown', 'not_disclosed', 'not_applicable', 'known'] as const) {
    if (states.includes(state)) return state
  }
  return 'unknown'
}

export function mapQueryPlan(
  wire: WireQueryPlan,
  results: WireQueryResult['results'] = [],
): QueryPlan {
  const execution = mapWireExecutablePlan(wire)
  const criteria: InterpretedCriterion[] = wire.criteria.map((criterion) => {
    const evaluations = results.flatMap((result) =>
      result.criteria.filter((item) => item.criterion_id === criterion.criterion_id),
    )
    const outcome =
      evaluations.length === 0
        ? 'not_evaluated'
        : evaluations.some((item) => item.outcome === 'mismatch')
          ? 'mismatch'
          : evaluations.some((item) => item.outcome === 'unknown')
            ? 'unknown'
            : 'match'
    const rationale = [...new Set(evaluations.map((item) => item.rationale))].join(' · ')
    return {
      id: criterion.criterion_id,
      label: fieldLabels[criterion.field],
      sourceText: criterion.source_text,
      mode: criterion.strength,
      outcome,
      knowledgeState: aggregateKnowledgeState(evaluations.map((item) => item.knowledge_state)),
      valueLabel:
        rationale ||
        `${criterion.operator.replaceAll('_', ' ')} ${criterion.operands.map(String).join(', ')}`.trim(),
      unknownPolicy: criterion.unknown_policy,
    }
  })

  return {
    id: wire.query_plan_id,
    version: wire.query_plan_version_id,
    planningMode: wire.planning_mode,
    rawQuery: wire.raw_query,
    criteria,
    sourceCategories: [...new Set(wire.retrieval_requests.flatMap((item) => item.source_categories))],
    unresolvedPhrases: wire.unresolved_phrases.map((phrase) => ({
      text: phrase.text,
      reason: phrase.reason,
    })),
    maxResults: wire.max_results,
    execution,
  }
}

const coverageLabel = (coverage: WireCoverageSummary) =>
  `${coverage.level} coverage · ${coverage.source_count} source(s), ${coverage.evidence_count} evidence item(s)`

const mapAxis = (wire: WireIndependentAxes[keyof WireIndependentAxes]): AxisSummary => ({
  key: wire.axis,
  label: wire.axis === 'idea_vs_market' ? 'Idea vs market' : fieldLabels[`${wire.axis}_axis`],
  rating: wire.rating as AxisSummary['rating'],
  trend: wire.trend,
  trendLabel: wire.trend === 'unknown' ? 'Trend is Unknown' : `Trend is ${wire.trend}`,
  confidence: mapKnowledge(wire.confidence, (value) => value),
  coverageLabel: coverageLabel(wire.coverage),
  supportingClaimIds: [...wire.supporting_claim_ids],
  counterClaimIds: [...wire.counter_claim_ids],
  openQuestions: [...wire.open_questions],
})

const emptyAxes = (): AxisSummary[] => [
  {
    key: 'founder',
    label: 'Founder',
    rating: 'unknown',
    trend: 'unknown',
    trendLabel: 'No assessment has run',
    confidence: unknown('No preliminary or full assessment is available.'),
    coverageLabel: 'Not evaluated',
    supportingClaimIds: [],
    counterClaimIds: [],
    openQuestions: ['Run the appropriate assessment before relying on this axis.'],
  },
  {
    key: 'market',
    label: 'Market',
    rating: 'unknown',
    trend: 'unknown',
    trendLabel: 'No assessment has run',
    confidence: unknown('No preliminary or full assessment is available.'),
    coverageLabel: 'Not evaluated',
    supportingClaimIds: [],
    counterClaimIds: [],
    openQuestions: ['Run the appropriate assessment before relying on this axis.'],
  },
  {
    key: 'idea_vs_market',
    label: 'Idea vs market',
    rating: 'unknown',
    trend: 'unknown',
    trendLabel: 'No assessment has run',
    confidence: unknown('No preliminary or full assessment is available.'),
    coverageLabel: 'Not evaluated',
    supportingClaimIds: [],
    counterClaimIds: [],
    openQuestions: ['Run the appropriate assessment before relying on this axis.'],
  },
]

const mapAssessmentAxes = (assessment: WireAssessmentEnvelope | null) =>
  assessment
    ? [
        mapAxis(assessment.axes.founder),
        mapAxis(assessment.axes.market),
        mapAxis(assessment.axes.idea_vs_market),
      ]
    : emptyAxes()

const safePublicContactHref = (route: WirePublicContactRoute): string | undefined => {
  const value = route.value.trim()
  const candidate = route.href?.trim() ||
    (route.kind === 'public_email' ? `mailto:${value}` : value)
  try {
    const parsed = new URL(candidate)
    if (route.kind === 'public_email') {
      if (parsed.protocol !== 'mailto:' || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) return undefined
      return decodeURIComponent(parsed.pathname).toLowerCase() === value.toLowerCase()
        ? parsed.toString()
        : undefined
    }
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) return undefined
    return parsed.toString()
  } catch {
    return undefined
  }
}

export const mapPublicContactRoutes = (
  routes: WirePublicContactRoute[] | undefined,
): PublicContactRoute[] =>
  (routes ?? []).flatMap((route) => {
    if (route.classification !== 'public') return []
    return [{
      id: route.route_id,
      kind: route.kind === 'contact_url' ? 'contact_page' : route.kind,
      label: route.label,
      displayValue: route.value,
      href: safePublicContactHref(route),
      sourceArtifactId: route.source_artifact_id,
      sourceName: route.source_name,
      sourceLocator: route.source_locator,
      collectedAt: route.collected_at ?? undefined,
    }]
  })

export const mapSourcingLoopAudit = (
  wire: WireSourcingLoopAudit | undefined,
): SourcingLoopAudit | undefined =>
  wire
    ? {
        status: wire.status,
        roundsCompleted: wire.rounds_completed,
        roundLimit: wire.round_limit ?? undefined,
        stopReason: wire.stop_reason,
        runId: wire.run_id ?? undefined,
      }
    : undefined

export function mapOutboundCandidate(wire: WireOutboundCandidate): CandidateSummary {
  const assessment = wire.preliminary_assessment
  const missingFields = assessment?.coverage.missing_fields ?? []
  const founderUnknown = wire.founder_id.state !== 'known' ? ['founder identity'] : []
  return {
    id: wire.outbound_candidate_id,
    companyName: wire.company_name,
    founderName: mapKnowledge(wire.founder_id, (id) => `Founder record · ${id}`),
    origin: 'outbound',
    workflowState: wire.status.replaceAll('_', ' '),
    trigger: `${wire.source_artifact_ids.length} persisted source artifact(s)`,
    freshnessLabel: `Updated ${new Date(wire.updated_at).toLocaleString()}`,
    coverageLabel: assessment ? coverageLabel(assessment.coverage) : 'Assessment not started',
    coveragePercent: null,
    thesisFitLabel: assessment ? 'Preliminary assessment available' : 'Not evaluated',
    overallMatch: 'not_evaluated',
    unknownFields: [...new Set([...founderUnknown, ...missingFields])],
    axes: mapAssessmentAxes(assessment),
    contradictionCount: assessment?.contradictions.length ?? 0,
    queueReason:
      assessment?.recommendation?.reasons[0]?.summary ??
      'Outbound discovery is persisted; run preliminary assessment before activation.',
    elapsedLabel: `Discovered ${new Date(wire.discovered_at).toLocaleString()}`,
    incomplete: assessment === null,
    recommendation: assessment?.recommendation?.action,
    outboundStatus: wire.status,
    activationState:
      wire.status === 'contacted'
        ? 'contacted'
        : wire.status === 'activated'
          ? 'activated'
          : 'not_activated',
    publicContactRoutes: mapPublicContactRoutes(
      wire.public_contact_routes ?? wire.contact_routes,
    ),
    sourcingLoopAudit: mapSourcingLoopAudit(wire.sourcing_audit ?? wire.agent_loop),
  }
}

export function mapOpportunitySummary(wire: WireOpportunitySummary): CandidateSummary {
  return {
    id: `summary-${wire.opportunity_id}`,
    opportunityId: wire.opportunity_id,
    companyName: `Company record · ${wire.company_id}`,
    founderName: unknown('Founder display name is not included in the Opportunity summary response.'),
    origin: wire.origin,
    workflowState: wire.screening_status.replaceAll('_', ' '),
    trigger: wire.origin === 'inbound' ? 'Founder application' : 'Activated outbound candidate',
    freshnessLabel: `Updated ${new Date(wire.updated_at).toLocaleString()}`,
    coverageLabel: 'Open Opportunity detail to inspect coverage',
    coveragePercent: null,
    thesisFitLabel: 'Query not evaluated',
    overallMatch: 'not_evaluated',
    unknownFields: ['company display name', 'founder display name', 'evidence coverage'],
    axes: emptyAxes(),
    contradictionCount: 0,
    queueReason: wire.recommendation?.replaceAll('_', ' ') ?? 'No Recommendation is available.',
    elapsedLabel: `Last updated ${new Date(wire.updated_at).toLocaleString()}`,
    incomplete: true,
    recommendation: wire.recommendation as CandidateSummary['recommendation'],
    outboundStatus: wire.origin === 'outbound' ? 'activated' : undefined,
    activationState: wire.origin === 'outbound' ? 'activated' : undefined,
  }
}

const findAssessmentKnowledge = (
  assessment: WireAssessmentEnvelope | null,
  field: string,
): KnowledgeValue<string> => {
  const input = assessment?.deterministic_results
    .flatMap((result) => result.inputs)
    .find((item) => item.field === field)
  return input
    ? mapKnowledge(input.value, (value) => String(value))
    : unknown(`${field} is not included in the Opportunity detail response.`)
}

const mapEvidence = (wire: WireEvidence): EvidenceItem => {
  const locatorIsUrl = /^https?:\/\//i.test(wire.locator.locator)
  return {
    id: wire.evidence_id,
    sourceArtifactId: wire.source_artifact_id,
    sourceName: `Source artifact · ${wire.source_artifact_id}`,
    sourceCategory: 'Not included in Opportunity detail response',
    classification: 'unknown',
    collectedAt: wire.collected_at,
    sourceEventTime: mapKnowledge(wire.source_event_time, (value) => value),
    availability: wire.availability,
    locator: {
      kind: wire.locator.kind,
      label: wire.locator.locator,
      uri: locatorIsUrl ? wire.locator.locator : undefined,
      excerpt: wire.locator.excerpt ?? 'No excerpt was supplied with this Evidence locator.',
    },
  }
}

const mapClaims = (detail: WireOpportunityDetail) =>
  detail.claims.map((claim) => ({
    id: claim.claim_id,
    statement: claim.statement,
    status: claim.status,
    verificationLabel: `Backend Claim status · ${claim.status.replaceAll('_', ' ')}`,
    trust: {
      state: claim.trust.state,
      score: claim.trust.score ?? undefined,
      reason: claim.trust.reason ?? undefined,
      factors: claim.trust.factors.map((factor) => ({
        label: factor.kind.replaceAll('_', ' '),
        signal:
          factor.signal.state === 'known' && factor.signal.value !== null
            ? factor.signal.value
            : ('unknown' as const),
        rationale:
          factor.signal.state === 'known'
            ? factor.rationale
            : `${factor.rationale} Signal is ${factor.signal.state}.`,
      })),
    },
    supportingEvidenceIds: [...claim.supporting_evidence_ids],
    counterEvidenceIds: [...claim.counter_evidence_ids],
  }))

const emptyMemo = (detail: WireOpportunityDetail, assessmentId: string): MemoView => ({
  id: `memo-unavailable-${detail.opportunity_id}`,
  version: 'not-generated',
  generatedAt: detail.timing.last_updated_at,
  evidenceAsOf: detail.timing.last_updated_at,
  thesisVersion: 'unknown',
  sections: requiredMemoKinds.map((kind) => ({
    kind,
    title: memoTitles[kind],
    content: unknown('No memo revision has been generated for this Opportunity.'),
    materialClaimIds: [],
  })),
  adversarialNotes: [
    {
      title: 'Memo unavailable',
      body: `Assessment ${assessmentId} has not produced a cited memo.`,
      claimIds: [],
    },
  ],
})

const mapMemo = (
  wire: WireInvestmentMemo | null,
  detail: WireOpportunityDetail,
  assessmentId: string,
): MemoView => {
  if (!wire) return emptyMemo(detail, assessmentId)
  const provided = new Map(wire.sections.map((section) => [section.kind, section]))
  const sections: MemoSection[] = requiredMemoKinds.map((kind) => {
    const section = provided.get(kind)
    return {
      kind,
      title: memoTitles[kind],
      content: section
        ? mapKnowledge(section.content, (value) => value)
        : unknown(`The backend memo omitted the required ${memoTitles[kind]} section.`),
      materialClaimIds: section ? [...section.material_claim_ids] : [],
    }
  })
  return {
    id: wire.memo_id,
    version: wire.memo_version_id,
    generatedAt: wire.generated_at,
    evidenceAsOf: wire.evidence_as_of,
    thesisVersion: wire.thesis_version,
    sections,
    adversarialNotes: [],
  }
}

const mapRecommendation = (
  wire: WireRecommendation | null,
  detail: WireOpportunityDetail,
  assessmentId: string,
): RecommendationView => {
  if (!wire) {
    return {
      id: `recommendation-unavailable-${detail.opportunity_id}`,
      action: 'manual_review',
      summary: 'No system Recommendation is available.',
      reasons: ['Run full Screening before recording a human Decision.'],
      nextActions: ['Start full Screening for this Opportunity.'],
      policyVersion: 'not-evaluated',
      createdAt: detail.timing.last_updated_at,
    }
  }
  return {
    id: wire.recommendation_id,
    action: wire.action,
    summary: wire.reasons[0]?.summary ?? `Recommendation for assessment ${assessmentId}`,
    reasons: wire.reasons.map((reason) => reason.summary),
    nextActions: [...wire.next_actions],
    policyVersion: wire.policy_version,
    createdAt: wire.created_at,
  }
}

const durationLabel = (startedAt: string | null, completedAt: string | null) => {
  if (!startedAt) return 'Not started'
  if (!completedAt) return `Running since ${new Date(startedAt).toLocaleString()}`
  const milliseconds = Math.max(0, Date.parse(completedAt) - Date.parse(startedAt))
  return `${(milliseconds / 1000).toFixed(1)}s`
}

const mapRuns = (runs: WirePipelineRun[]): TimelineStage[] =>
  runs.flatMap((run) =>
    run.stages.map((stage) => {
      const failure = run.failures.find((item) => stage.failure_ids.includes(item.failure_id))
      return {
        id: `${run.run_id}-${stage.stage_key}`,
        label: `${run.kind.replaceAll('_', ' ')} · ${stage.stage_key.replaceAll('_', ' ')}`,
        status: stage.status,
        timing: durationLabel(stage.started_at, stage.completed_at),
        detail: failure?.safe_message ?? `Run ${run.run_id} · ${run.status.replaceAll('_', ' ')}`,
        externalWait: false,
      } satisfies TimelineStage
    }),
  )

export const mapPipelineRun = (wire: WirePipelineRun): PipelineRunView => ({
  id: wire.run_id,
  kind: wire.kind,
  status: wire.status,
  queuedAt: wire.queued_at,
  startedAt: wire.started_at ?? undefined,
  completedAt: wire.completed_at ?? undefined,
  acceptedOutputIds: [...(wire.accepted_output_ids ?? [])],
  failures: wire.failures.map((failure) => ({
    id: failure.failure_id,
    stageKey: failure.stage_key,
    code: failure.safe_code,
    message: failure.safe_message,
    retryable: failure.retryable,
    occurredAt: failure.occurred_at,
  })),
  retryOfRunId: wire.retry_of_run_id ?? undefined,
  attempt: wire.attempt ?? 1,
  loopAudit: mapSourcingLoopAudit(wire.sourcing_audit ?? wire.agent_loop),
})

export function mapOpportunityDetail(
  detail: WireOpportunityDetail,
  runs: WirePipelineRun[] = [],
  companyName?: string,
  outboundCandidate?: WireOutboundCandidate,
): OpportunityDetail {
  const assessment = detail.latest_assessment
  const assessmentId = assessment?.assessment_id ?? `assessment-not-evaluated-${detail.opportunity_id}`
  const memoWire = detail.latest_memo ?? assessment?.memo ?? null
  const recommendationWire = detail.latest_recommendation ?? assessment?.recommendation ?? null
  const readiness = assessment?.decision_readiness
  const readinessReason =
    readiness?.blockers.map((blocker) => blocker.reason).join(' · ') ||
    (readiness ? `Decision readiness is ${readiness.status}.` : 'Decision readiness has not been evaluated.')
  const diligence = assessment?.diligence_actions ?? []
  const founderScore = assessment
    ? mapKnowledge<WireAssessmentEnvelope['founder_score']['value'], FounderScore>(
        assessment.founder_score,
        (score) => ({
          score: score?.score ?? 0,
          provisional: score?.provisional ?? true,
          uncertainty: score?.uncertainty ?? 'high',
          coverageLabel: score ? coverageLabel(score.coverage) : 'Score snapshot unavailable',
          asOf: score?.as_of ?? assessment.created_at,
          version: score?.score_policy_version ?? 'unknown',
          explanation: score
            ? `Backend Founder Score snapshot ${score.snapshot_id}; policy ${score.score_policy_version}.`
            : 'The backend did not return a Founder Score snapshot.',
        }),
      )
    : unknown<FounderScore>('Full Screening has not produced a Founder Score snapshot.')

  const contradictions = (assessment?.contradictions ?? []).map((contradiction) => {
    const action = diligence.find((item) =>
      item.resolves_contradiction_ids.includes(contradiction.contradiction_id),
    )
    return {
      id: contradiction.contradiction_id,
      summary: contradiction.summary,
      blocking: contradiction.blocking,
      claimIds: [...contradiction.claim_ids],
      evidenceIds: [...contradiction.evidence_ids],
      smallestNextAction:
        action?.requested_evidence ??
        (contradiction.resolution
          ? `Recorded resolution: ${contradiction.resolution}`
          : 'Review the cited claims and request the smallest resolving Evidence item.'),
    }
  })

  const timeline = mapRuns(runs)
  if (readiness?.status === 'blocked') {
    timeline.push({
      id: `readiness-${detail.screening_case_id}`,
      label: 'Decision readiness',
      status: 'blocked',
      timing: 'Awaiting human review',
      detail: readinessReason,
    })
  }

  return {
    id: detail.opportunity_id,
    company: {
      id: detail.company_id,
      name: companyName ?? `Company record · ${detail.company_id}`,
      sector: findAssessmentKnowledge(assessment, 'sector'),
      geography: findAssessmentKnowledge(assessment, 'geography'),
    },
    founder: {
      id:
        detail.founder_id.state === 'known' && detail.founder_id.value
          ? detail.founder_id.value
          : `founder-unresolved-${detail.opportunity_id}`,
      name: mapKnowledge(detail.founder_id, (id) => `Founder record · ${id}`),
    },
    screeningCase: {
      id: detail.screening_case_id,
      status: detail.screening_status.replaceAll('_', ' '),
      readiness: readiness?.status ?? 'not_evaluated',
      readinessReason,
    },
    origin: detail.origin,
    assessmentId,
    assessmentMode: 'full',
    inputSnapshotId: assessment?.input_snapshot_id ?? `snapshot-unavailable-${detail.opportunity_id}`,
    thesisVersion: memoWire?.thesis_version ?? 'unknown',
    founderScore,
    axes: mapAssessmentAxes(assessment),
    coverageLabel: assessment ? coverageLabel(assessment.coverage) : 'Assessment not available',
    claims: mapClaims(detail),
    evidence: detail.evidence.map(mapEvidence),
    contradictions,
    diligenceActions: diligence.map((action) => action.description),
    memo: mapMemo(memoWire, detail, assessmentId),
    recommendation: mapRecommendation(recommendationWire, detail, assessmentId),
    timeline,
    runIds: [...detail.related_run_ids],
    pipelineRuns: runs.map(mapPipelineRun),
    decisionReadyForCommand: Boolean(assessment && memoWire && recommendationWire),
    publicContactRoutes: mapPublicContactRoutes(
      detail.public_contact_routes ??
      detail.contact_routes ??
      outboundCandidate?.public_contact_routes ??
      outboundCandidate?.contact_routes,
    ),
    sourcingLoopAudit: mapSourcingLoopAudit(
      detail.sourcing_audit ??
      detail.agent_loop ??
      outboundCandidate?.sourcing_audit ??
      outboundCandidate?.agent_loop,
    ),
  }
}

const overallOutcome = (
  item: WireQueryResult['results'][number] | undefined,
): CandidateSummary['overallMatch'] => {
  if (!item) return 'not_evaluated'
  if (item.criteria.some((criterion) => criterion.outcome === 'mismatch')) return 'mismatch'
  if (item.criteria.some((criterion) => criterion.outcome === 'unknown')) return 'unknown'
  return 'match'
}

const mapDetailCandidate = (
  detail: WireOpportunityDetail,
  queryItem?: WireQueryResult['results'][number],
  companyName?: string,
): CandidateSummary => {
  const assessment = detail.latest_assessment
  const mapped = mapOpportunityDetail(detail, [], companyName)
  const unknownFields = assessment?.coverage.missing_fields ?? ['assessment coverage']
  return {
    id: `opportunity-candidate-${detail.opportunity_id}`,
    opportunityId: detail.opportunity_id,
    companyName: mapped.company.name,
    founderName: mapped.founder.name,
    origin: detail.origin,
    workflowState: detail.screening_status.replaceAll('_', ' '),
    trigger: detail.origin === 'inbound' ? 'Founder application' : 'Activated outbound candidate',
    freshnessLabel: `Updated ${new Date(detail.timing.last_updated_at).toLocaleString()}`,
    coverageLabel: mapped.coverageLabel,
    coveragePercent: null,
    thesisFitLabel: queryItem
      ? `${queryItem.matched_preferences}/${queryItem.evaluated_preferences} evaluated preferences match`
      : 'Not evaluated by this Query Plan',
    overallMatch: overallOutcome(queryItem),
    unknownFields: [...unknownFields],
    axes: mapped.axes,
    contradictionCount: mapped.contradictions.length,
    queueReason: mapped.recommendation.summary,
    elapsedLabel: `${detail.timing.elapsed_seconds}s since Opportunity start`,
    incomplete: assessment === null,
    recommendation: mapped.recommendation.action,
    outboundStatus: detail.origin === 'outbound' ? 'activated' : undefined,
    activationState: detail.origin === 'outbound' ? 'activated' : undefined,
  }
}

export function composeInitialSearch(
  thesis: WireInvestmentThesisRevision,
  candidates: WireCandidateCollection,
  opportunities: WireOpportunityCollection,
): SearchResponse {
  const execution = buildExecutablePlanFromThesis(thesis)
  const wirePlan = serializeExecutablePlan(execution)
  const items = [
    ...candidates.items.map(mapOutboundCandidate),
    ...opportunities.items.map(mapOpportunitySummary),
  ]
  const timestamps = [
    thesis.created_at,
    ...candidates.items.map((item) => item.updated_at),
    ...opportunities.items.map((item) => item.updated_at),
  ]
  return {
    plan: mapQueryPlan(wirePlan),
    results: items,
    truncated: candidates.truncated || opportunities.truncated,
    totalConsidered: items.length,
    updatedAt: timestamps.sort().at(-1) ?? thesis.created_at,
  }
}

export function composeQuerySearch(
  result: WireQueryResult,
  details: WireOpportunityDetail[],
  filters: SearchFilters,
  companyNames: Map<string, string>,
): SearchResponse {
  const detailById = new Map(details.map((detail) => [detail.opportunity_id, detail]))
  const candidates = result.results.flatMap((item) => {
    const detail = detailById.get(item.opportunity_id)
    if (!detail) return []
    if (filters.origin !== 'all' && detail.origin !== filters.origin) return []
    const hasUnknown = item.criteria.some((criterion) => criterion.outcome === 'unknown')
    if (filters.knowledgeHandling === 'known_only' && hasUnknown) return []
    if (filters.knowledgeHandling === 'needs_information' && !hasUnknown) return []
    return [mapDetailCandidate(detail, item, companyNames.get(detail.company_id))]
  })
  return {
    plan: mapQueryPlan(result.plan, result.results),
    results: candidates,
    truncated: result.truncated,
    totalConsidered: result.eligible_count,
    updatedAt: new Date().toISOString(),
  }
}

export function serializeExecutablePlan(plan: ExecutableQueryPlan): WireQueryPlan {
  return {
    schema_version: plan.schemaVersion,
    query_plan_id: plan.queryPlanId,
    query_plan_version_id: plan.queryPlanVersionId,
    supersedes_query_plan_version_id: plan.supersedesQueryPlanVersionId,
    raw_query: plan.rawQuery,
    planning_mode: plan.planningMode,
    planner_version: plan.plannerVersion,
    state: plan.state,
    criteria: plan.criteria.map((criterion) => ({
      criterion_id: criterion.criterionId,
      field: criterion.field,
      operator: criterion.operator,
      operands: [...criterion.operands],
      strength: criterion.strength,
      unknown_policy: criterion.unknownPolicy,
      source_text: criterion.sourceText,
    })),
    retrieval_requests: plan.retrievalRequests.map((request) => ({
      retrieval_request_id: request.retrievalRequestId,
      query: request.query,
      source_categories: [...request.sourceCategories],
      allowed_domains: [...request.allowedDomains],
      excluded_domains: [...request.excludedDomains],
      published_after: request.publishedAfter,
      published_before: request.publishedBefore,
      max_results: request.maxResults,
      max_pages: request.maxPages,
      timeout_seconds: request.timeoutSeconds,
    })),
    unresolved_phrases: plan.unresolvedPhrases.map((phrase) => ({
      text: phrase.text,
      start_offset: phrase.startOffset,
      end_offset: phrase.endOffset,
      reason: phrase.reason,
    })),
    semantic_rerank: plan.semanticRerank
      ? {
          query: plan.semanticRerank.query,
          method_version: plan.semanticRerank.methodVersion,
          max_results: plan.semanticRerank.maxResults,
        }
      : undefined,
    max_results: plan.maxResults,
    created_at: plan.createdAt,
  }
}

export const mapApplicationReceipt = (wire: WireApplicationReceipt): ApplicationReceipt => ({
  applicationId: wire.application_id,
  companyId: wire.company_id,
  runId: wire.run_id,
  sourceArtifactId: wire.source_artifact_id,
  receivedAt: wire.received_at,
  status: wire.status,
  founderStatusCapability: wire.founder_status_capability,
  founderStatusUrl: `#/apply/status/${encodeURIComponent(wire.founder_status_capability)}`,
  replayed: wire.replayed,
})

const targetLabels: Record<WireFounderStatusView['target_state'], string> = {
  on_track: 'Initial review target · on track',
  approaching: 'Initial review target · approaching',
  missed: 'Initial review target · missed',
  complete: 'Initial review complete',
}

export const mapFounderStatus = (wire: WireFounderStatusView): FounderStatusView => ({
  applicationId: wire.application_id,
  receivedAt: wire.received_at,
  stage: wire.stage.replaceAll('_', ' '),
  lastUpdatedAt: wire.last_updated_at,
  targetState: wire.target_state,
  targetLabel: targetLabels[wire.target_state],
  informationRequests: [...wire.information_requests],
  focusedRequest: wire.information_requests[0] ?? wire.next_action ?? undefined,
  approvedOutcome: wire.outcome ?? undefined,
  nextAction: wire.next_action ?? undefined,
  outcomeAt: wire.outcome_at ?? undefined,
})

export const mapActivation = (wire: WireOutboundCandidate): ActivationReceipt => ({
  candidateId: wire.outbound_candidate_id,
  companyId: wire.company_id,
  state: 'activated',
  activatedAt: wire.updated_at,
  outreachDraft: wire.outreach_draft ?? '',
})

export const mapDecision = (wire: WireHumanDecision): DecisionReceipt => ({
  decisionId: wire.decision_id,
  disposition: wire.disposition,
  actorId: wire.actor_id,
  actorLabel: `Investor · ${wire.actor_id}`,
  decidedAt: wire.decided_at,
})

export const mapOutreach = (wire: WireOutreachRecord): OutreachReceipt => ({
  outreachId: wire.outreach_id,
  candidateId: wire.outbound_candidate_id,
  method: wire.method,
  status: wire.status,
  actorId: wire.actor_id,
  occurredAt: wire.occurred_at,
})

export const mapProblem = (wire: WireProblemDetails, fallbackStatus: number): ApiProblem => ({
  type: wire.type,
  title: wire.title,
  status: wire.status ?? fallbackStatus,
  code: wire.code,
  requestId: wire.request_id,
  detail: wire.detail ?? undefined,
  fields: wire.fields,
})
