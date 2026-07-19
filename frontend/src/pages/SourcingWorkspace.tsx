import {
  ArrowRight,
  CheckCircle2,
  CircleHelp,
  Filter,
  Radar,
  Search,
  Send,
  SlidersHorizontal,
  X,
} from 'lucide-react'
import { useRef, useState, type FormEvent } from 'react'
import type {
  CandidateSummary,
  FounderLookupClient,
  SearchFilters,
  SearchResponse,
  ThesisCriterion,
  ThesisView,
  ViewState,
} from '../api/types'
import { KnowledgeState } from '../components/KnowledgeState'
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

const ACTIVATION_FAILURE_MESSAGE =
  'The candidate was not activated. No outreach was sent, and your edited draft is still available.'

const activationUnavailableLabel = (candidate: CandidateSummary) => {
  switch (candidate.outboundStatus) {
    case 'discovered':
      return 'Activation unavailable · preliminary assessment required'
    case 'preliminary_assessment':
      return 'Activation unavailable · preliminary assessment did not mark this candidate ready'
    case 'activated':
      return 'Candidate already activated'
    case 'contacted':
      return 'Candidate already contacted'
    case 'applied':
      return 'Founder application received'
    case 'closed':
      return 'Candidate closed'
    case 'ready_for_activation':
      return 'Candidate ready for activation'
    default:
      return 'Activation unavailable · lifecycle status is Unknown'
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
  const [searchError, setSearchError] = useState('')
  const [thesisCriteria, setThesisCriteria] = useState<ThesisCriterion[]>(() =>
    thesis.criteria.map((criterion) => ({ ...criterion })),
  )
  const [thesisNotice, setThesisNotice] = useState('')
  const [selectedCandidate, setSelectedCandidate] = useState<CandidateSummary | null>(null)
  const [outreachDraft, setOutreachDraft] = useState('')
  const [activationNotice, setActivationNotice] = useState('')
  const [activationError, setActivationError] = useState('')
  const [activating, setActivating] = useState(false)
  const activationDialogRef = useRef<HTMLDialogElement>(null)

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

  const openActivation = (candidate: CandidateSummary) => {
    if (candidate.origin !== 'outbound' || candidate.outboundStatus !== 'ready_for_activation') {
      return
    }
    setSelectedCandidate(candidate)
    setActivationError('')
    setActivationNotice('')
    setOutreachDraft(
      `Hi — we noticed ${candidate.companyName}'s recent work in AI infrastructure and would like to learn more.`,
    )
    activationDialogRef.current?.showModal()
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
      activationDialogRef.current?.close()
      setActivationNotice(
        `${candidate.companyName} was activated for investor review. The outreach draft was saved but not sent.`,
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

  const updateThesisCriterion = (
    key: string,
    field: 'value' | 'mode' | 'unknownPolicy',
    value: string,
  ) => {
    setThesisCriteria((criteria) =>
      criteria.map((criterion) =>
        criterion.key === key ? { ...criterion, [field]: value } as ThesisCriterion : criterion,
      ),
    )
    setThesisNotice('')
  }

  const saveThesisDraft = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setThesisNotice('Thesis draft saved locally. Run search again to apply the revised criteria.')
    announce('Thesis draft saved. Existing results were not silently reclassified.')
  }

  const displayState = searching ? 'loading' : previewState

  return (
    <div className="page page--wide">
      <header className="page-header">
        <div>
          <p className="eyebrow">Investor sourcing workspace</p>
          <h1 data-page-title tabIndex={-1}>Find signals, keep uncertainty</h1>
          <p className="lede">
            Translate one compound request into reviewable criteria, then compare inbound and
            outbound candidates through the same evidence-first assessment.
          </p>
        </div>
        <div className="header-fact">
          <Radar aria-hidden="true" />
          <span><strong>{search.totalConsidered}</strong> records considered</span>
        </div>
      </header>

      <div className="search-surface" role="search" aria-label="Candidate search">
        <form onSubmit={runSearch}>
          <label htmlFor="compound-query">Describe the founder and opportunity in one request</label>
          <div className="query-row">
            <textarea
              id="compound-query"
              name="query"
              rows={3}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              required
              aria-describedby="query-help"
            />
            <button className="button button--primary" type="submit" disabled={searching}>
              <Search aria-hidden="true" />{' '}
              {searching
                ? 'Executing…'
                : client.runtime === 'http'
                  ? 'Execute typed plan'
                  : 'Interpret & search'}
            </button>
          </div>
          <p id="query-help" className="field-help">
            {client.runtime === 'http'
              ? 'The API executes the visible typed criteria. The backend has no natural-language planner endpoint, so editing this provenance text does not create hidden criteria.'
              : 'The fixture planner exposes every interpretation. Ambiguous phrases require confirmation; search silence never becomes a negative fact.'}
          </p>

          <fieldset className="filter-row">
            <legend><Filter aria-hidden="true" /> Search constraints</legend>
            <label>
              Origin
              <select
                value={filters.origin}
                onChange={(event) =>
                  setFilters((current) => ({
                    ...current,
                    origin: event.target.value as SearchFilters['origin'],
                  }))
                }
              >
                <option value="all">Inbound and outbound</option>
                <option value="inbound">Inbound only</option>
                <option value="outbound">Outbound only</option>
              </select>
            </label>
            <label>
              Unknown handling
              <select
                value={filters.knowledgeHandling}
                onChange={(event) =>
                  setFilters((current) => ({
                    ...current,
                    knowledgeHandling: event.target.value as SearchFilters['knowledgeHandling'],
                  }))
                }
              >
                <option value="include_unknown">Include Unknown</option>
                <option value="needs_information">Needs information only</option>
                <option value="known_only">Known values only</option>
              </select>
            </label>
          </fieldset>
        </form>
      </div>

      <div className="workspace-grid">
        <aside className="workspace-rail" aria-label="Query interpretation and thesis">
          <details className="soft-panel" open>
            <summary>
              <span><SlidersHorizontal aria-hidden="true" /> Interpreted query</span>
              <span className="summary-count">
                {search.plan.criteria.filter((criterion) => !removedCriterionIds.includes(criterion.id)).length}
              </span>
            </summary>
            <div className="details-body">
              <p className="muted">Deterministic plan · {search.plan.version}</p>
              <ul className="criteria-list">
                {search.plan.criteria
                  .filter((criterion) => !removedCriterionIds.includes(criterion.id))
                  .map((criterion) => (
                    <li key={criterion.id}>
                      <div>
                        <strong>{criterion.label}</strong>
                        <span>{criterion.valueLabel}</span>
                        <div className="cluster cluster--small">
                          <StatusBadge tone={criterion.outcome === 'match' ? 'positive' : 'warning'}>
                            {criterion.outcome.replaceAll('_', ' ')}
                          </StatusBadge>
                          <span className="knowledge-word">{criterion.knowledgeState.replaceAll('_', ' ')}</span>
                        </div>
                      </div>
                      <button
                        className="icon-button icon-button--small"
                        type="button"
                        aria-label={`Remove ${criterion.label} criterion`}
                        onClick={() => setRemovedCriterionIds((ids) => [...ids, criterion.id])}
                      >
                        <X aria-hidden="true" />
                      </button>
                    </li>
                  ))}
              </ul>

              {search.plan.unresolvedPhrases.map((phrase) => (
                <div className="interpretation-warning" key={phrase.text}>
                  <CircleHelp aria-hidden="true" />
                  <div>
                    <strong>“{phrase.text}” needs a fund definition</strong>
                    <p>{phrase.reason}</p>
                    <button
                      className="button button--quiet"
                      type="button"
                      onClick={() => setConfirmedPhrases((items) => [...items, phrase.text])}
                      disabled={confirmedPhrases.includes(phrase.text)}
                    >
                      {confirmedPhrases.includes(phrase.text) ? 'Marked for human definition' : 'Mark for definition'}
                    </button>
                  </div>
                </div>
              ))}

              <div>
                <h3>Planned source categories</h3>
                <ul className="tag-list">
                  {search.plan.sourceCategories.map((source) => <li key={source}>{source}</li>)}
                </ul>
              </div>
            </div>
          </details>

          <details className="soft-panel">
            <summary>
              <span>Active thesis</span>
              <span className="summary-count">v{thesis.version}</span>
            </summary>
            <form className="details-body thesis-editor" onSubmit={saveThesisDraft}>
              {thesisCriteria.map((criterion) => (
                <fieldset key={criterion.key}>
                  <legend>{criterion.label}</legend>
                  <label htmlFor={`thesis-${criterion.key}-value`}>
                    Value
                    <input
                      id={`thesis-${criterion.key}-value`}
                      value={criterion.value}
                      onChange={(event) => updateThesisCriterion(criterion.key, 'value', event.target.value)}
                    />
                  </label>
                  <label htmlFor={`thesis-${criterion.key}-mode`}>
                    Weighting
                    <select
                      id={`thesis-${criterion.key}-mode`}
                      value={criterion.mode}
                      onChange={(event) => updateThesisCriterion(criterion.key, 'mode', event.target.value)}
                    >
                      <option value="hard_constraint">Hard constraint</option>
                      <option value="scored_preference">Scored preference</option>
                      <option value="no_preference">No preference</option>
                    </select>
                  </label>
                  <label htmlFor={`thesis-${criterion.key}-unknown`}>
                    Unknown policy
                    <select
                      id={`thesis-${criterion.key}-unknown`}
                      value={criterion.unknownPolicy}
                      onChange={(event) =>
                        updateThesisCriterion(criterion.key, 'unknownPolicy', event.target.value)
                      }
                    >
                      <option value="preserve_as_unknown">Preserve as Unknown</option>
                      <option value="needs_information">Needs information</option>
                      <option value="manual_review">Manual review</option>
                    </select>
                  </label>
                </fieldset>
              ))}
              <p className="field-help">No Preference is stored explicitly; it is not an empty filter.</p>
              {thesisNotice && <p className="notice notice--success" role="status">{thesisNotice}</p>}
              <button className="button button--secondary" type="submit">Save thesis draft</button>
            </form>
          </details>
        </aside>

        <section className="results-region" aria-labelledby="candidate-results-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Review queue</p>
              <h2 id="candidate-results-title">Candidate results</h2>
            </div>
            <StatusBadge tone="info">Unknown values included</StatusBadge>
          </div>

          {searchError && (
            <div className="error-summary" role="alert">
              <h3>Query not executed</h3>
              <p>{searchError} Existing results are unchanged.</p>
            </div>
          )}

          {activationNotice && (
            <div className="notice notice--success" role="status">
              <CheckCircle2 aria-hidden="true" /> {activationNotice}
            </div>
          )}

          {displayState !== 'ready' ? (
            <StatePanel state={displayState} entityLabel="candidate results" />
          ) : search.results.length === 0 ? (
            <StatePanel state="empty" entityLabel="candidate results" />
          ) : (
            <div className="candidate-list">
              {search.results.map((candidate) => (
                <article className="candidate-card" key={candidate.id}>
                  <header>
                    <div>
                      <div className="cluster cluster--small">
                        <StatusBadge tone={candidate.origin === 'inbound' ? 'info' : 'neutral'}>
                          {candidate.origin}
                        </StatusBadge>
                        <StatusBadge tone={outcomeTone[candidate.overallMatch]}>
                          {candidate.overallMatch}
                        </StatusBadge>
                      </div>
                      <h3>{candidate.companyName}</h3>
                      <p><KnowledgeState value={candidate.founderName} compact /></p>
                    </div>
                    <div
                      className="coverage-meter"
                      aria-label={
                        candidate.coveragePercent === null
                          ? 'Numeric evidence coverage unknown'
                          : `${candidate.coveragePercent}% evidence coverage`
                      }
                    >
                      <strong>{candidate.coveragePercent === null ? 'Unknown' : `${candidate.coveragePercent}%`}</strong>
                      <span>coverage</span>
                    </div>
                  </header>

                  <p className="queue-reason">{candidate.queueReason}</p>

                  <dl className="candidate-metadata">
                    <div><dt>Trigger</dt><dd>{candidate.trigger}</dd></div>
                    <div><dt>Workflow</dt><dd>{candidate.workflowState}</dd></div>
                    <div><dt>Freshness</dt><dd>{candidate.freshnessLabel}</dd></div>
                    <div><dt>Timing</dt><dd>{candidate.elapsedLabel}</dd></div>
                  </dl>

                  <div className="axis-strip" aria-label="Independent assessment axes">
                    {candidate.axes.map((axis) => (
                      <div key={axis.key}>
                        <span>{axis.label}</span>
                        <strong>{axis.rating}</strong>
                      </div>
                    ))}
                  </div>

                  {candidate.unknownFields.length > 0 && (
                    <div className="unknown-row">
                      <CircleHelp aria-hidden="true" />
                      <span><strong>Unknown:</strong> {candidate.unknownFields.join(', ')}</span>
                    </div>
                  )}

                  <footer>
                    {candidate.origin === 'outbound' &&
                    candidate.outboundStatus === 'ready_for_activation' ? (
                      <button
                        className="button button--secondary"
                        type="button"
                        onClick={() => openActivation(candidate)}
                      >
                        <Send aria-hidden="true" /> Activate candidate
                      </button>
                    ) : candidate.origin === 'outbound' ? (
                      <span className="muted">{activationUnavailableLabel(candidate)}</span>
                    ) : (
                      <span className="muted">Inbound application already has an opportunity</span>
                    )}
                    {candidate.opportunityId ? (
                      <a className="text-link" href="#/opportunity">
                        Open opportunity <ArrowRight aria-hidden="true" />
                      </a>
                    ) : (
                      <span className="muted">Opportunity not created</span>
                    )}
                  </footer>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>

      <dialog
        ref={activationDialogRef}
        className="confirmation-dialog"
        aria-labelledby="activation-dialog-title"
        onClose={() => {
          setSelectedCandidate(null)
          setActivationError('')
        }}
      >
        {selectedCandidate && (
          <form onSubmit={confirmActivation} aria-busy={activating}>
            <header className="dialog-header">
              <div>
                <p className="eyebrow">Explicit investor action</p>
                <h2 id="activation-dialog-title">Activate {selectedCandidate.companyName}?</h2>
              </div>
              <button
                className="icon-button"
                type="button"
                aria-label="Cancel activation"
                onClick={() => activationDialogRef.current?.close()}
                disabled={activating}
              >
                <X aria-hidden="true" />
              </button>
            </header>
            <p>
              Activation creates an investor review record. It does not send outreach and cannot
              approve an investment.
            </p>
            <label htmlFor="outreach-draft">Optional outreach draft</label>
            <textarea
              id="outreach-draft"
              rows={5}
              value={outreachDraft}
              onChange={(event) => setOutreachDraft(event.target.value)}
            />
            {activationError && (
              <div className="error-summary" role="alert">
                <h3>Candidate not activated</h3>
                <p>{activationError}</p>
              </div>
            )}
            <div className="dialog-actions">
              <button
                className="button button--quiet"
                type="button"
                onClick={() => activationDialogRef.current?.close()}
                disabled={activating}
              >
                Cancel
              </button>
              <button className="button button--primary" type="submit" disabled={activating}>
                {activating ? 'Activating…' : 'Confirm activation · do not send'}
              </button>
            </div>
          </form>
        )}
      </dialog>
    </div>
  )
}
