import {
  ArrowRightOutlined,
  CheckCircleOutlined,
  CloseOutlined,
  FilterOutlined,
  QuestionCircleOutlined,
  RadarChartOutlined,
  SearchOutlined,
  SendOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Tag,
  Typography,
} from 'antd'
import { useState, type FormEvent } from 'react'
import type {
  CandidateSummary,
  FounderLookupClient,
  OutreachMethod,
  SearchFilters,
  SearchResponse,
  SourcingLoopAudit,
  ThesisCriterion,
  ThesisView,
  ViewState,
} from '../api/types'
import { KnowledgeState } from '../components/KnowledgeState'
import { PublicContactPanel } from '../components/PublicContactPanel'
import { StatePanel } from '../components/StatePanel'
import { StatusBadge, type BadgeTone } from '../components/StatusBadge'

interface SourcingWorkspaceProps {
  client: FounderLookupClient
  initialSearch: SearchResponse
  thesis: ThesisView
  previewState: ViewState
  announce: (message: string) => void
}

const outcomeTone: Record<CandidateSummary['overallMatch'], BadgeTone> = {
  match: 'positive',
  mismatch: 'critical',
  unknown: 'warning',
  not_evaluated: 'neutral',
}

const axisTone = (axis: CandidateSummary['axes'][number]): BadgeTone => {
  if (['strong', 'bullish', 'viable'].includes(axis.rating)) return 'positive'
  if (['weak', 'bear'].includes(axis.rating)) return 'critical'
  if (axis.rating === 'unknown') return 'neutral'
  return 'warning'
}

const recommendationLabel: Record<NonNullable<CandidateSummary['recommendation']>, string> = {
  activate: 'Activate candidate',
  advance: 'Advance to diligence',
  needs_information: 'Request focused information',
  manual_review: 'Complete manual review',
  do_not_pursue: 'Do not pursue',
}

const recommendationTone: Record<NonNullable<CandidateSummary['recommendation']>, BadgeTone> = {
  activate: 'positive',
  advance: 'positive',
  needs_information: 'warning',
  manual_review: 'warning',
  do_not_pursue: 'critical',
}

const ACTIVATION_FAILURE_MESSAGE =
  'The candidate was not activated. No outreach was sent, and your edited draft is still available.'
const OUTREACH_FAILURE_MESSAGE =
  'Outreach was not recorded. Existing candidate history is unchanged.'

const passiveCandidateState = (candidate: CandidateSummary) => {
  switch (candidate.outboundStatus) {
    case 'contacted':
      return 'Outreach recorded'
    case 'applied':
      return 'Founder application received'
    case 'closed':
      return 'Candidate closed'
    default:
      return candidate.origin === 'inbound'
        ? 'Inbound application already has an Opportunity'
        : 'No candidate action available'
  }
}

export function SourcingWorkspace({
  client,
  initialSearch,
  thesis,
  previewState,
  announce,
}: SourcingWorkspaceProps) {
  const [search, setSearch] = useState(initialSearch)
  const [query, setQuery] = useState(initialSearch.plan.rawQuery)
  const [filters, setFilters] = useState<SearchFilters>({
    origin: 'all',
    knowledgeHandling: 'include_unknown',
  })
  const [removedCriterionIds, setRemovedCriterionIds] = useState<string[]>([])
  const [confirmedPhrases, setConfirmedPhrases] = useState<string[]>([])
  const [searching, setSearching] = useState(false)
  const [discovering, setDiscovering] = useState(false)
  const [lastSourcingLoopAudit, setLastSourcingLoopAudit] = useState<SourcingLoopAudit>()
  const [searchError, setSearchError] = useState('')
  const [thesisCriteria, setThesisCriteria] = useState<ThesisCriterion[]>(() =>
    thesis.criteria.map((criterion) => ({ ...criterion, values: [...criterion.values] })),
  )
  const [savedThesis, setSavedThesis] = useState(thesis)
  const [thesisNotice, setThesisNotice] = useState('')
  const [thesisError, setThesisError] = useState('')
  const [savingThesis, setSavingThesis] = useState(false)
  const [candidateActionId, setCandidateActionId] = useState<string | null>(null)
  const [selectedCandidate, setSelectedCandidate] = useState<CandidateSummary | null>(null)
  const [outreachDraft, setOutreachDraft] = useState('')
  const [activationNotice, setActivationNotice] = useState('')
  const [activationError, setActivationError] = useState('')
  const [activating, setActivating] = useState(false)
  const [outreachCandidate, setOutreachCandidate] = useState<CandidateSummary | null>(null)
  const [outreachMethod, setOutreachMethod] = useState<OutreachMethod>('email')
  const [outreachStatus, setOutreachStatus] = useState('sent by investor')
  const [outreachError, setOutreachError] = useState('')
  const [recordingOutreach, setRecordingOutreach] = useState(false)

  const runSearch = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setSearching(true)
    setSearchError('')
    try {
      const response = await client.searchOpportunities({
        query,
        filters,
        plan: search.plan.execution,
        removedCriterionIds,
      })
      setSearch(response)
      announce(`${response.results.length} candidates returned. Unknown values remain explicit.`)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'The typed query could not be executed.'
      setSearchError(message)
      announce('The query was not executed. Existing results remain unchanged.')
    } finally {
      setSearching(false)
    }
  }

  const runDiscovery = async () => {
    setDiscovering(true)
    setSearchError('')
    try {
      const result = await client.discoverCandidates({ query })
      setSearch(result.workspace.search)
      setLastSourcingLoopAudit(result.run.loopAudit)
      const suffix = result.timedOut ? 'Polling timed out; the run remains observable.' : `Run ${result.run.status}.`
      announce(`Source discovery accepted. ${suffix}`)
    } catch (error) {
      setSearchError(error instanceof Error ? error.message : 'Source discovery could not start.')
      announce('Source discovery was not started. Existing results remain unchanged.')
    } finally {
      setDiscovering(false)
    }
  }

  const runPreliminaryAssessment = async (candidate: CandidateSummary) => {
    setCandidateActionId(candidate.id)
    setSearchError('')
    try {
      const result = await client.preliminaryAssessCandidate(candidate.id)
      setSearch(result.workspace.search)
      announce(`Preliminary assessment for ${candidate.companyName} is ${result.run.status}.`)
    } catch (error) {
      setSearchError(error instanceof Error ? error.message : 'Preliminary assessment could not start.')
      announce('Preliminary assessment was not started. Existing results remain unchanged.')
    } finally {
      setCandidateActionId(null)
    }
  }

  const openActivation = (candidate: CandidateSummary) => {
    if (candidate.origin !== 'outbound' || candidate.outboundStatus !== 'ready_for_activation') return
    setSelectedCandidate(candidate)
    setActivationError('')
    setActivationNotice('')
    setOutreachDraft('')
  }

  const closeActivation = () => {
    if (activating) return
    setSelectedCandidate(null)
    setActivationError('')
  }

  const confirmActivation = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!selectedCandidate || selectedCandidate.outboundStatus !== 'ready_for_activation') return
    const candidate = selectedCandidate
    setActivating(true)
    setActivationError('')
    try {
      await client.activateCandidate(candidate.id, outreachDraft)
      setSearch((current) => ({
        ...current,
        results: current.results.map((result) =>
          result.id === candidate.id
            ? {
                ...result,
                workflowState: 'Activated',
                outboundStatus: 'activated',
                activationState: 'activated',
              }
            : result,
        ),
      }))
      setActivationNotice(
        `${candidate.companyName} was activated. The outreach draft was saved but not sent.`,
      )
      announce(`${candidate.companyName} activated. No outreach was sent.`)
      setSelectedCandidate(null)
    } catch {
      setActivationError(ACTIVATION_FAILURE_MESSAGE)
      announce(ACTIVATION_FAILURE_MESSAGE)
    } finally {
      setActivating(false)
    }
  }

  const openOutreach = (candidate: CandidateSummary) => {
    if (candidate.outboundStatus !== 'activated') return
    setOutreachCandidate(candidate)
    setOutreachError('')
  }

  const recordOutreach = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!outreachCandidate) return
    const candidate = outreachCandidate
    setRecordingOutreach(true)
    setOutreachError('')
    try {
      const receipt = await client.recordOutreach(candidate.id, {
        method: outreachMethod,
        status: outreachStatus,
      })
      setSearch((current) => ({
        ...current,
        results: current.results.map((result) =>
          result.id === candidate.id
            ? {
                ...result,
                workflowState: 'Contacted',
                outboundStatus: 'contacted',
                activationState: 'contacted',
              }
            : result,
        ),
      }))
      setActivationNotice(`${candidate.companyName} outreach was recorded as ${receipt.status}.`)
      setOutreachCandidate(null)
      announce(`Human-controlled outreach recorded for ${candidate.companyName}.`)
    } catch {
      setOutreachError(OUTREACH_FAILURE_MESSAGE)
      announce(OUTREACH_FAILURE_MESSAGE)
    } finally {
      setRecordingOutreach(false)
    }
  }

  const updateThesisCriterion = (
    key: string,
    field: 'value' | 'mode' | 'unknownPolicy',
    value: string,
  ) => {
    setThesisCriteria((criteria) =>
      criteria.map((criterion) => {
        if (criterion.key !== key) return criterion
        if (field === 'value') return { ...criterion, value, values: value.trim() ? [value.trim()] : [] }
        if (field === 'mode') {
          const mode = value as ThesisCriterion['mode']
          return {
            ...criterion,
            mode,
            operator: mode === 'no_preference' ? null : criterion.operator ?? 'equals',
            values: mode === 'no_preference' ? [] : criterion.values,
          }
        }
        return { ...criterion, unknownPolicy: value as ThesisCriterion['unknownPolicy'] }
      }),
    )
    setThesisNotice('')
    setThesisError('')
  }

  const saveThesis = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setSavingThesis(true)
    setThesisNotice('')
    setThesisError('')
    try {
      const revision = await client.saveThesis(thesisCriteria)
      setSavedThesis(revision)
      setThesisCriteria(revision.criteria.map((criterion) => ({ ...criterion, values: [...criterion.values] })))
      setThesisNotice(`Thesis revision ${revision.version} saved. Run search again to re-evaluate results.`)
      announce('Thesis revision saved. Existing results were not silently reclassified.')
    } catch (error) {
      setThesisError(error instanceof Error ? error.message : 'The thesis revision could not be saved.')
      announce('The thesis revision was not saved. Existing results remain unchanged.')
    } finally {
      setSavingThesis(false)
    }
  }

  const displayState = searching ? 'loading' : previewState
  const activeCriteria = search.plan.criteria.filter(
    (criterion) => !removedCriterionIds.includes(criterion.id),
  )
  const filterSummary = `${filters.origin === 'all' ? 'both origins' : filters.origin} · ${filters.knowledgeHandling.replaceAll('_', ' ')}`
  const warningCount = search.plan.unresolvedPhrases.length

  return (
    <div className="page page--wide">
      <header className="page-header">
        <div>
          <p className="eyebrow">Sourcing</p>
          <h1 data-page-title tabIndex={-1}>Find signals, keep uncertainty</h1>
          <p className="lede">
            Start one bounded sourcing request, then decide which candidates deserve the next
            human action.
          </p>
        </div>
        <Card className="header-fact" size="small">
          <RadarChartOutlined aria-hidden="true" />
          <span><strong>{search.totalConsidered}</strong> records considered</span>
        </Card>
      </header>

      <section aria-labelledby="sourcing-action-title">
        <Card className="search-surface" role="search">
          <p className="eyebrow">Act</p>
          <h2 id="sourcing-action-title">Source the next Opportunity</h2>
          <Form layout="vertical" onSubmitCapture={runSearch}>
            <Form.Item
              label="Describe the founder and opportunity in one request"
              htmlFor="compound-query"
              required
              extra="Every interpreted criterion remains inspectable. Search silence never proves a negative."
            >
              <Input.TextArea
                id="compound-query"
                name="query"
                rows={3}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                required
              />
            </Form.Item>
            <div className="query-actions">
              <Button
                type="primary"
                htmlType="submit"
                icon={<SearchOutlined aria-hidden="true" />}
                loading={searching}
                size="large"
              >
                Search Opportunities
              </Button>
              <Button
                icon={<ThunderboltOutlined aria-hidden="true" />}
                loading={discovering}
                disabled={!query.trim()}
                onClick={runDiscovery}
                size="large"
              >
                Run source discovery
              </Button>
            </div>
          </Form>
        </Card>
      </section>

      <section className="secondary-controls" aria-labelledby="sourcing-understand-title">
        <p className="eyebrow">Understand</p>
        <h2 id="sourcing-understand-title" className="visually-hidden">Sourcing controls and interpretation</h2>
        <Collapse
          items={[
            {
              key: 'filters',
              label: <span><FilterOutlined aria-hidden="true" /> Refine search</span>,
              extra: <Tag>{filterSummary}</Tag>,
              children: (
                <div className="filter-row" aria-label="Search constraints">
                  <div className="filter-row__legend">Deterministic filters</div>
                  <Form.Item className="filter-origin" label="Origin" htmlFor="origin-filter">
                    <Select<SearchFilters['origin']>
                      id="origin-filter"
                      aria-label="Origin"
                      value={filters.origin}
                      onChange={(origin) => setFilters((current) => ({ ...current, origin }))}
                      options={[
                        { value: 'all', label: 'Inbound and outbound' },
                        { value: 'inbound', label: 'Inbound only' },
                        { value: 'outbound', label: 'Outbound only' },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item className="filter-unknown" label="Unknown handling" htmlFor="unknown-filter">
                    <Select<SearchFilters['knowledgeHandling']>
                      id="unknown-filter"
                      aria-label="Unknown handling"
                      value={filters.knowledgeHandling}
                      onChange={(knowledgeHandling) =>
                        setFilters((current) => ({ ...current, knowledgeHandling }))
                      }
                      options={[
                        { value: 'include_unknown', label: 'Include Unknown' },
                        { value: 'needs_information', label: 'Needs information only' },
                        { value: 'known_only', label: 'Known values only' },
                      ]}
                    />
                  </Form.Item>
                </div>
              ),
            },
            {
              key: 'plan',
              label: <span><SettingOutlined aria-hidden="true" /> Audit query plan</span>,
              extra: <Tag>{activeCriteria.length} criteria · {warningCount} unresolved</Tag>,
              children: (
                <div className="details-body">
                  <Typography.Text type="secondary">
                    {search.plan.planningMode.replaceAll('_', ' ')} · {search.plan.version}
                  </Typography.Text>
                  <ul className="criteria-list">
                    {activeCriteria.map((criterion) => (
                      <li key={criterion.id} className="criteria-list__item">
                        <div className="criteria-list__copy">
                          <strong>{criterion.label}</strong>
                          <p>{criterion.valueLabel}</p>
                          <Space wrap size="small">
                            <StatusBadge tone={criterion.outcome === 'match' ? 'positive' : 'warning'}>
                              {criterion.outcome.replaceAll('_', ' ')}
                            </StatusBadge>
                            <Typography.Text type="secondary">
                              {criterion.knowledgeState.replaceAll('_', ' ')}
                            </Typography.Text>
                          </Space>
                        </div>
                        <Button
                          type="text"
                          danger
                          icon={<CloseOutlined />}
                          aria-label={`Remove ${criterion.label} criterion`}
                          onClick={() => setRemovedCriterionIds((ids) => [...ids, criterion.id])}
                        />
                      </li>
                    ))}
                  </ul>
                  {search.plan.unresolvedPhrases.map((phrase) => (
                    <Alert
                      className="interpretation-warning"
                      key={phrase.text}
                      type="warning"
                      showIcon
                      icon={<QuestionCircleOutlined />}
                      title={`“${phrase.text}” needs a fund definition`}
                      description={phrase.reason}
                      action={
                        <Button
                          type="link"
                          onClick={() => setConfirmedPhrases((items) => [...items, phrase.text])}
                          disabled={confirmedPhrases.includes(phrase.text)}
                        >
                          {confirmedPhrases.includes(phrase.text)
                            ? 'Marked for human definition'
                            : 'Mark for definition'}
                        </Button>
                      }
                    />
                  ))}
                  <div>
                    <h3>Planned source categories</h3>
                    <Space wrap>{search.plan.sourceCategories.map((source) => <Tag key={source}>{source}</Tag>)}</Space>
                  </div>
                  {lastSourcingLoopAudit && (
                    <Card className="sourcing-loop-summary" size="small" title="Latest bounded sourcing loop">
                      <Descriptions
                        size="small"
                        column={1}
                        items={[
                          {
                            key: 'rounds',
                            label: 'Rounds',
                            children: `${lastSourcingLoopAudit.roundsCompleted}${lastSourcingLoopAudit.roundLimit ? ` of ${lastSourcingLoopAudit.roundLimit}` : ''}`,
                          },
                          { key: 'stop', label: 'Stop reason', children: lastSourcingLoopAudit.stopReason },
                        ]}
                      />
                    </Card>
                  )}
                </div>
              ),
            },
            {
              key: 'thesis',
              label: 'Edit active thesis',
              extra: <Tag>{savedThesis.version}</Tag>,
              children: (
                <Form className="thesis-editor" layout="vertical" onSubmitCapture={saveThesis}>
                  {thesisCriteria.map((criterion) => (
                    <Card key={criterion.key} size="small" title={criterion.label}>
                      <Form.Item label="Value" htmlFor={`thesis-${criterion.key}-value`}>
                        <Input
                          id={`thesis-${criterion.key}-value`}
                          value={criterion.value}
                          disabled={criterion.mode === 'no_preference'}
                          onChange={(event) =>
                            updateThesisCriterion(criterion.key, 'value', event.target.value)
                          }
                        />
                      </Form.Item>
                      <Form.Item label="Weighting" htmlFor={`thesis-${criterion.key}-mode`}>
                        <Select
                          id={`thesis-${criterion.key}-mode`}
                          value={criterion.mode}
                          onChange={(value) => updateThesisCriterion(criterion.key, 'mode', value)}
                          options={[
                            { value: 'hard_constraint', label: 'Hard constraint' },
                            { value: 'scored_preference', label: 'Scored preference' },
                            { value: 'no_preference', label: 'No Preference' },
                          ]}
                        />
                      </Form.Item>
                      <Form.Item label="Unknown policy" htmlFor={`thesis-${criterion.key}-unknown`}>
                        <Select
                          id={`thesis-${criterion.key}-unknown`}
                          value={criterion.unknownPolicy}
                          onChange={(value) =>
                            updateThesisCriterion(criterion.key, 'unknownPolicy', value)
                          }
                          options={[
                            { value: 'preserve_as_unknown', label: 'Preserve as Unknown' },
                            { value: 'needs_information', label: 'Needs information' },
                            { value: 'manual_review', label: 'Manual review' },
                          ]}
                        />
                      </Form.Item>
                    </Card>
                  ))}
                  <Typography.Paragraph type="secondary">
                    No Preference is explicit and never changes an Unknown data value.
                  </Typography.Paragraph>
                  {thesisNotice && <Alert type="success" showIcon title={thesisNotice} />}
                  {thesisError && <Alert type="error" showIcon title="Thesis not saved" description={thesisError} />}
                  <Button htmlType="submit" loading={savingThesis}>Save thesis revision</Button>
                </Form>
              ),
            },
          ]}
        />
      </section>

      <section className="results-region" aria-labelledby="candidate-results-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Act · review queue</p>
            <h2 id="candidate-results-title">Candidate results</h2>
          </div>
          <StatusBadge tone="info">Unknown values included</StatusBadge>
        </div>

        {searchError && (
          <Alert
            className="error-summary"
            type="error"
            showIcon
            role="alert"
            title={<h3>Action not completed</h3>}
            description={`${searchError} Existing results are unchanged.`}
          />
        )}
        {activationNotice && (
          <Alert type="success" showIcon icon={<CheckCircleOutlined />} title={activationNotice} />
        )}

        {displayState !== 'ready' ? (
          <StatePanel state={displayState} entityLabel="candidate results" />
        ) : search.results.length === 0 ? (
          <StatePanel state="empty" entityLabel="candidate results" />
        ) : (
          <ul className="candidate-list">
            {search.results.map((candidate) => (
              <li key={candidate.id}>
                <article className="candidate-card">
                  <Card className="candidate-card__surface">
                    <header className="candidate-card__header">
                      <div className="candidate-card__identity">
                        <Space wrap size="small">
                          <StatusBadge tone={candidate.origin === 'inbound' ? 'info' : 'neutral'}>
                            {candidate.origin === 'inbound' ? 'Inbound' : 'Outbound'}
                          </StatusBadge>
                          <StatusBadge tone={candidate.incomplete ? 'warning' : 'positive'} pending={candidate.incomplete}>
                            {candidate.incomplete ? 'Review incomplete' : 'Review complete'}
                          </StatusBadge>
                        </Space>
                        <h3>{candidate.companyName}</h3>
                        <p className="candidate-founder">
                          <span>Founder</span>
                          <KnowledgeState value={candidate.founderName} compact />
                        </p>
                        <p className="candidate-source">
                          <span>Source</span>
                          {candidate.origin === 'inbound'
                            ? 'Founder-submitted application'
                            : 'Bounded public sourcing'}
                        </p>
                      </div>
                      <div className="candidate-coverage" aria-label={candidate.coverageLabel}>
                        <strong>{candidate.coveragePercent === null ? 'Unknown' : `${candidate.coveragePercent}%`}</strong>
                        <span>Evidence coverage</span>
                      </div>
                    </header>

                    <div className="candidate-first-read">
                      <section className="candidate-thesis" aria-label="Thesis match">
                        <p className="summary-label">Thesis match</p>
                        <StatusBadge tone={outcomeTone[candidate.overallMatch]}>
                          {candidate.overallMatch.replaceAll('_', ' ')}
                        </StatusBadge>
                        <p>{candidate.thesisFitLabel}</p>
                      </section>

                      <section className="candidate-axes" aria-label="Three independent assessment axes">
                        <p className="summary-label">Three independent axes · never averaged</p>
                        <div className="axis-strip">
                          {candidate.axes.map((axis) => (
                            <div className="axis-summary-chip" key={axis.key}>
                              <span>{axis.label}</span>
                              <StatusBadge tone={axisTone(axis)}>{axis.rating}</StatusBadge>
                            </div>
                          ))}
                        </div>
                      </section>

                      <section className="candidate-next-action" aria-label="Readiness and next action">
                        <p className="summary-label">Next human action</p>
                        {candidate.recommendation ? (
                          <StatusBadge tone={recommendationTone[candidate.recommendation]}>
                            {recommendationLabel[candidate.recommendation]}
                          </StatusBadge>
                        ) : (
                          <StatusBadge>Awaiting Recommendation</StatusBadge>
                        )}
                        <p>{candidate.queueReason}</p>
                      </section>
                    </div>

                    <div className="candidate-card__actions">
                      <Space wrap>
                        {candidate.origin === 'outbound' &&
                        ['discovered', 'preliminary_assessment'].includes(candidate.outboundStatus ?? '') ? (
                          <Button
                            loading={candidateActionId === candidate.id}
                            onClick={() => runPreliminaryAssessment(candidate)}
                          >
                            Run preliminary assessment
                          </Button>
                        ) : candidate.origin === 'outbound' && candidate.outboundStatus === 'ready_for_activation' ? (
                          <Button icon={<SendOutlined aria-hidden="true" />} onClick={() => openActivation(candidate)}>
                            Activate candidate
                          </Button>
                        ) : candidate.origin === 'outbound' && candidate.outboundStatus === 'activated' ? (
                          <Button onClick={() => openOutreach(candidate)}>Record outreach</Button>
                        ) : (
                          <Typography.Text type="secondary">{passiveCandidateState(candidate)}</Typography.Text>
                        )}
                      </Space>
                      {candidate.opportunityId ? (
                        <Button
                          type="link"
                          href={`#/opportunity/${encodeURIComponent(candidate.opportunityId)}`}
                          icon={<ArrowRightOutlined aria-hidden="true" />}
                          iconPlacement="end"
                        >
                          Open Opportunity
                        </Button>
                      ) : (
                        <Typography.Text type="secondary">Opportunity not created</Typography.Text>
                      )}
                    </div>

                    <Collapse
                      className="candidate-disclosures"
                      items={[
                        {
                          key: 'understand',
                          label: 'Understand this queue position',
                          extra: <Tag>{candidate.axes.length} axes · {candidate.contradictionCount} contradictions</Tag>,
                          children: (
                            <div className="candidate-understand">
                              <p>{candidate.queueReason}</p>
                              <ul>
                                {candidate.axes.map((axis) => (
                                  <li key={axis.key}>
                                    <strong>{axis.label}:</strong> {axis.trendLabel} · {axis.coverageLabel}
                                  </li>
                                ))}
                              </ul>
                            </div>
                          ),
                        },
                        {
                          key: 'audit',
                          label: 'Audit source, timing, unknowns, and contact',
                          extra: <Tag>{candidate.unknownFields.length} unknown</Tag>,
                          children: (
                            <div className="candidate-audit">
                              <Descriptions
                                className="candidate-metadata"
                                size="small"
                                column={{ xs: 1, sm: 2 }}
                                items={[
                                  { key: 'trigger', label: 'Trigger', children: candidate.trigger },
                                  { key: 'workflow', label: 'Workflow', children: candidate.workflowState },
                                  { key: 'freshness', label: 'Freshness', children: candidate.freshnessLabel },
                                  { key: 'timing', label: 'Timing', children: candidate.elapsedLabel },
                                ]}
                              />
                              {candidate.unknownFields.length > 0 && (
                                <Alert
                                  className="unknown-row"
                                  type="info"
                                  showIcon
                                  icon={<QuestionCircleOutlined />}
                                  title={<span><strong>Unknown:</strong> {candidate.unknownFields.join(', ')}</span>}
                                />
                              )}
                              {candidate.origin === 'outbound' && (
                                <PublicContactPanel
                                  routes={candidate.publicContactRoutes}
                                  loopAudit={candidate.sourcingLoopAudit}
                                />
                              )}
                            </div>
                          ),
                        },
                      ]}
                    />
                  </Card>
                </article>
              </li>
            ))}
          </ul>
        )}
      </section>

      <Modal
        open={selectedCandidate !== null}
        onCancel={closeActivation}
        footer={null}
        mask={{ closable: !activating }}
        closable={!activating}
        destroyOnHidden={false}
        title={selectedCandidate ? <div><p className="eyebrow">Explicit human action</p><h2>Activate {selectedCandidate.companyName}?</h2></div> : undefined}
      >
        {selectedCandidate && (
          <Form layout="vertical" onSubmitCapture={confirmActivation} aria-busy={activating}>
            <Typography.Paragraph>
              Activation records intent to pursue an Application. It does not send outreach or
              approve an investment.
            </Typography.Paragraph>
            <Form.Item label="Optional outreach draft" htmlFor="outreach-draft">
              <Input.TextArea
                id="outreach-draft"
                rows={5}
                value={outreachDraft}
                onChange={(event) => setOutreachDraft(event.target.value)}
              />
            </Form.Item>
            {activationError && (
              <Alert className="error-summary" type="error" showIcon role="alert" title={<h3>Candidate not activated</h3>} description={activationError} />
            )}
            <div className="dialog-actions">
              <Button onClick={closeActivation} disabled={activating}>Cancel</Button>
              <Button type="primary" htmlType="submit" loading={activating}>
                Confirm activation · do not send
              </Button>
            </div>
          </Form>
        )}
      </Modal>

      <Modal
        open={outreachCandidate !== null}
        onCancel={() => !recordingOutreach && setOutreachCandidate(null)}
        footer={null}
        closable={!recordingOutreach}
        mask={{ closable: !recordingOutreach }}
        title={outreachCandidate ? <div><p className="eyebrow">Human-controlled outreach</p><h2>Record outreach for {outreachCandidate.companyName}</h2></div> : undefined}
      >
        {outreachCandidate && (
          <Form layout="vertical" onSubmitCapture={recordOutreach} aria-busy={recordingOutreach}>
            <Form.Item label="Channel" htmlFor="outreach-method" required>
              <Select<OutreachMethod>
                id="outreach-method"
                value={outreachMethod}
                onChange={setOutreachMethod}
                options={[
                  { value: 'email', label: 'Email' },
                  { value: 'linkedin', label: 'LinkedIn' },
                  { value: 'introduction', label: 'Introduction' },
                  { value: 'other', label: 'Other approved channel' },
                ]}
              />
            </Form.Item>
            <Form.Item label="Recorded status" htmlFor="outreach-status" required>
              <Input
                id="outreach-status"
                value={outreachStatus}
                onChange={(event) => setOutreachStatus(event.target.value)}
                required
              />
            </Form.Item>
            <p className="muted">This records an action already controlled by a person; it does not send a message.</p>
            {outreachError && <Alert type="error" showIcon title="Outreach not recorded" description={outreachError} />}
            <div className="dialog-actions">
              <Button onClick={() => setOutreachCandidate(null)} disabled={recordingOutreach}>Cancel</Button>
              <Button type="primary" htmlType="submit" loading={recordingOutreach}>Record outreach</Button>
            </div>
          </Form>
        )}
      </Modal>
    </div>
  )
}
