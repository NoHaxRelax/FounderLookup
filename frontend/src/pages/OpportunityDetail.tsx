import {
  AlertTriangle,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  BookOpenCheck,
  CircleHelp,
  Clock3,
  FileSearch,
  Fingerprint,
  ShieldCheck,
} from 'lucide-react'
import { useState } from 'react'
import type {
  AxisSummary,
  ClaimItem,
  EvidenceItem,
  OpportunityDetail as OpportunityDetailModel,
  ViewState,
} from '../api/types'
import { EvidenceDialog } from '../components/EvidenceDialog'
import { KnowledgeState } from '../components/KnowledgeState'
import { StatePanel } from '../components/StatePanel'
import { StatusBadge, type BadgeTone } from '../components/StatusBadge'

interface OpportunityDetailProps {
  opportunity: OpportunityDetailModel
  previewState: ViewState
}

const axisTone = (axis: AxisSummary): BadgeTone => {
  if (['strong', 'bullish', 'viable'].includes(axis.rating)) return 'positive'
  if (['weak', 'bear'].includes(axis.rating)) return 'critical'
  return 'warning'
}

const claimTone: Record<ClaimItem['status'], BadgeTone> = {
  supported: 'positive',
  contradicted: 'critical',
  unsupported: 'warning',
  asserted_unverified: 'warning',
  unresolved: 'warning',
}

const trendIcon = (trend: AxisSummary['trend']) => {
  if (trend === 'improving') return <ArrowUpRight aria-hidden="true" />
  if (trend === 'declining') return <ArrowDownRight aria-hidden="true" />
  if (trend === 'unknown') return <CircleHelp aria-hidden="true" />
  return <ArrowRight aria-hidden="true" />
}

export function OpportunityDetail({ opportunity, previewState }: OpportunityDetailProps) {
  const [selectedEvidence, setSelectedEvidence] = useState<EvidenceItem | null>(null)
  const evidenceById = new Map(opportunity.evidence.map((item) => [item.id, item]))
  const readinessBlocked = opportunity.screeningCase.readiness === 'blocked'

  if (previewState !== 'ready' && previewState !== 'blocked') {
    return (
      <div className="page">
        <header className="page-header">
          <div>
            <p className="eyebrow">Opportunity</p>
            <h1 data-page-title tabIndex={-1}>{opportunity.company.name}</h1>
          </div>
        </header>
        <StatePanel state={previewState} entityLabel="opportunity assessment" />
      </div>
    )
  }

  return (
    <div className="page">
      <header className="page-header page-header--bordered">
        <div>
          <div className="cluster">
            <StatusBadge tone="info">{opportunity.origin}</StatusBadge>
            <StatusBadge tone="warning">{opportunity.screeningCase.status}</StatusBadge>
          </div>
          <p className="eyebrow">Opportunity detail</p>
          <h1 data-page-title tabIndex={-1}>{opportunity.company.name}</h1>
          <p className="lede">
            <KnowledgeState value={opportunity.founder.name} compact /> ·{' '}
            <KnowledgeState value={opportunity.company.sector} compact /> ·{' '}
            <KnowledgeState value={opportunity.company.geography} compact />
          </p>
        </div>
        <a className="button button--secondary" href="#/memo">
          <BookOpenCheck aria-hidden="true" /> Review memo
        </a>
      </header>

      <details className="identity-strip">
        <summary><Fingerprint aria-hidden="true" /> Stable assessment identity</summary>
        <dl className="metadata-list metadata-list--inline">
          <div><dt>Opportunity</dt><dd><code>{opportunity.id}</code></dd></div>
          <div><dt>Assessment</dt><dd><code>{opportunity.assessmentId}</code></dd></div>
          <div><dt>Input snapshot</dt><dd><code>{opportunity.inputSnapshotId}</code></dd></div>
          <div><dt>Thesis</dt><dd><code>{opportunity.thesisVersion}</code></dd></div>
        </dl>
      </details>

      <section className={`readiness-banner ${readinessBlocked ? '' : 'readiness-banner--neutral'}`} aria-labelledby="readiness-heading">
        {readinessBlocked ? <AlertTriangle aria-hidden="true" /> : <ShieldCheck aria-hidden="true" />}
        <div>
          <p className="eyebrow">
            Decision readiness · {opportunity.screeningCase.readiness.replaceAll('_', ' ')}
          </p>
          <h2 id="readiness-heading">
            {readinessBlocked
              ? 'Human resolution required'
              : opportunity.screeningCase.readiness === 'not_evaluated'
                ? 'Readiness has not been evaluated'
                : 'Review the current readiness record'}
          </h2>
          <p>{opportunity.screeningCase.readinessReason}</p>
        </div>
      </section>

      {previewState === 'blocked' && (
        <StatePanel state="blocked" entityLabel="opportunity assessment" />
      )}

      <section className="assessment-overview" aria-labelledby="assessment-overview-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Assessment · independent views</p>
            <h2 id="assessment-overview-title">Founder score and three axes</h2>
          </div>
          <p className="muted">Axes are not averaged into the founder score.</p>
        </div>

        <div className="score-and-axes">
          <article className="founder-score-card">
            <p className="eyebrow">Founder score</p>
            {opportunity.founderScore.state === 'known' ? (
              <>
                <div className="score-number">
                  <strong>{opportunity.founderScore.value.score}</strong><span>/100</span>
                </div>
                <StatusBadge tone="warning">
                  {opportunity.founderScore.value.provisional ? 'Provisional' : 'Final'} ·{' '}
                  {opportunity.founderScore.value.uncertainty} uncertainty
                </StatusBadge>
                <p>{opportunity.founderScore.value.explanation}</p>
                <small>{opportunity.founderScore.value.coverageLabel}</small>
              </>
            ) : (
              <KnowledgeState value={opportunity.founderScore} />
            )}
          </article>

          <div className="axes-grid">
            {opportunity.axes.map((axis) => (
              <article className="axis-card" key={axis.key}>
                <div className="axis-card__heading">
                  <h3>{axis.label}</h3>
                  <StatusBadge tone={axisTone(axis)}>{axis.rating}</StatusBadge>
                </div>
                <p className="trend-line">{trendIcon(axis.trend)} {axis.trendLabel}</p>
                <p>{axis.coverageLabel}</p>
                <KnowledgeState
                  value={axis.confidence}
                  format={(value) => `${Math.round(value * 100)}% confidence`}
                />
                <details>
                  <summary>Open questions ({axis.openQuestions.length})</summary>
                  <ul>
                    {axis.openQuestions.map((question) => <li key={question}>{question}</li>)}
                  </ul>
                </details>
              </article>
            ))}
          </div>
        </div>
      </section>

      <div className="opportunity-grid">
        <section aria-labelledby="claims-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Claims · Trust · Evidence</p>
              <h2 id="claims-title">Material claims</h2>
            </div>
            <StatusBadge tone="info">{opportunity.claims.length} claims</StatusBadge>
          </div>
          {opportunity.claims.length === 0 ? (
            <StatePanel state="empty" entityLabel="material claims" />
          ) : (
            <div className="claims-list">
              {opportunity.claims.map((claim) => (
              <article className="claim-card" key={claim.id}>
                <header>
                  <StatusBadge tone={claimTone[claim.status]}>{claim.status.replaceAll('_', ' ')}</StatusBadge>
                  <code>{claim.id}</code>
                </header>
                <h3>{claim.statement}</h3>
                <p>{claim.verificationLabel}</p>

                <details>
                  <summary><ShieldCheck aria-hidden="true" /> Trust rationale</summary>
                  <div className="details-body">
                    {claim.trust.state === 'scored' ? (
                      <p><strong>{claim.trust.score}/100</strong> claim trust — not founder quality.</p>
                    ) : (
                      <p><strong>{claim.trust.state}</strong> — {claim.trust.reason}</p>
                    )}
                    <ul className="factor-list">
                      {claim.trust.factors.map((factor) => (
                        <li key={`${claim.id}-${factor.label}`}>
                          <StatusBadge
                            tone={factor.signal === 'strengthens' ? 'positive' : factor.signal === 'weakens' ? 'critical' : 'neutral'}
                          >
                            {factor.signal}
                          </StatusBadge>
                          <span><strong>{factor.label}:</strong> {factor.rationale}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </details>

                <div className="evidence-buttons" aria-label={`Evidence for claim: ${claim.statement}`}>
                  {[...claim.supportingEvidenceIds, ...claim.counterEvidenceIds].map((evidenceId) => {
                    const evidence = evidenceById.get(evidenceId)
                    if (!evidence) return null
                    const counter = claim.counterEvidenceIds.includes(evidenceId)
                    return (
                      <button
                        className={`evidence-button ${counter ? 'evidence-button--counter' : ''}`}
                        type="button"
                        key={evidenceId}
                        onClick={() => setSelectedEvidence(evidence)}
                      >
                        <FileSearch aria-hidden="true" />
                        <span>{counter ? 'Counter-evidence' : 'Evidence'}<small>{evidence.locator.label}</small></span>
                      </button>
                    )
                  })}
                </div>
              </article>
              ))}
            </div>
          )}
        </section>

        <aside className="diligence-rail">
          {opportunity.contradictions.length === 0 ? (
            <section className="soft-panel" aria-labelledby="contradiction-title">
              <div className="details-body">
                <p className="eyebrow">Contradictions</p>
                <h2 id="contradiction-title">No contradiction records in this response</h2>
                <p>This does not prove the evidence is conflict-free; it means none were returned.</p>
              </div>
            </section>
          ) : (
            <section className="contradiction-panel" aria-labelledby="contradiction-title">
              <div className="panel-icon"><AlertTriangle aria-hidden="true" /></div>
              <p className="eyebrow">Blocking contradiction</p>
              <h2 id="contradiction-title">Material claims need resolution</h2>
              {opportunity.contradictions.map((contradiction) => (
              <div key={contradiction.id}>
                <p>{contradiction.summary}</p>
                <h3>Smallest next action</h3>
                <p>{contradiction.smallestNextAction}</p>
                <div className="cluster cluster--small">
                  {contradiction.evidenceIds.map((evidenceId) => {
                    const evidence = evidenceById.get(evidenceId)
                    return evidence ? (
                      <button
                        className="button button--quiet"
                        type="button"
                        key={evidenceId}
                        onClick={() => setSelectedEvidence(evidence)}
                      >
                        View {evidence.locator.label}
                      </button>
                    ) : null
                  })}
                </div>
              </div>
              ))}
            </section>
          )}

          <section className="soft-panel run-timeline" aria-labelledby="timeline-title">
            <div className="details-body">
              <p className="eyebrow">Processing visibility</p>
              <h2 id="timeline-title">Run timeline</h2>
              <ol>
                {opportunity.timeline.map((stage) => (
                  <li key={stage.id} className={`timeline-stage timeline-stage--${stage.status}`}>
                    <span className="timeline-marker" aria-hidden="true" />
                    <div>
                      <div className="timeline-heading">
                        <strong>{stage.label}</strong>
                        <StatusBadge
                          tone={stage.status === 'succeeded' ? 'positive' : stage.status === 'failed' ? 'critical' : 'warning'}
                        >
                          {stage.status}
                        </StatusBadge>
                      </div>
                      <p>{stage.detail}</p>
                      <small><Clock3 aria-hidden="true" /> {stage.timing}</small>
                      {stage.externalWait && <small>External wait is separate from compute time.</small>}
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          </section>
        </aside>
      </div>

      <EvidenceDialog evidence={selectedEvidence} onClose={() => setSelectedEvidence(null)} />
    </div>
  )
}
