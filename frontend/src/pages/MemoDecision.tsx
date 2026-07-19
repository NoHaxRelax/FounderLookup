import {
  BookMarked,
  CheckCircle2,
  CircleHelp,
  FileWarning,
  Gavel,
  ShieldQuestion,
  X,
} from 'lucide-react'
import { useRef, useState, type FormEvent } from 'react'
import type {
  DecisionDisposition,
  DecisionReceipt,
  FounderLookupClient,
  OpportunityDetail,
  ViewState,
} from '../api/types'
import { KnowledgeState } from '../components/KnowledgeState'
import { StatePanel } from '../components/StatePanel'
import { StatusBadge } from '../components/StatusBadge'

interface MemoDecisionProps {
  client: FounderLookupClient
  opportunity: OpportunityDetail
  previewState: ViewState
  announce: (message: string) => void
}

const decisions: Array<{ value: DecisionDisposition; label: string; detail: string }> = [
  { value: 'advance', label: 'Advance', detail: 'Move to the next human diligence stage.' },
  { value: 'request_more_information', label: 'Request more information', detail: 'Resolve a bounded evidence gap.' },
  { value: 'hold', label: 'Hold', detail: 'Keep the opportunity open without progressing it.' },
  { value: 'decline', label: 'Decline', detail: 'Close this opportunity with a recorded rationale.' },
]

const DECISION_FAILURE_MESSAGE =
  'The decision was not recorded. No workflow state changed, and your rationale is still available.'

export function MemoDecision({ client, opportunity, previewState, announce }: MemoDecisionProps) {
  const [disposition, setDisposition] = useState<DecisionDisposition>('request_more_information')
  const [rationale, setRationale] = useState(
    'Resolve the paid-versus-unpaid pilot contradiction before advancing.',
  )
  const [receipt, setReceipt] = useState<DecisionReceipt | null>(null)
  const [saving, setSaving] = useState(false)
  const [decisionError, setDecisionError] = useState('')
  const dialogRef = useRef<HTMLDialogElement>(null)

  const canAdvance =
    opportunity.screeningCase.readiness === 'ready' ||
    opportunity.screeningCase.readiness === 'ready_with_accepted_risk'
  const availableDecisions = canAdvance
    ? decisions
    : decisions.filter((decision) => decision.value !== 'advance')

  if (previewState !== 'ready' && previewState !== 'blocked') {
    return (
      <div className="page">
        <header className="page-header">
          <div>
            <p className="eyebrow">Memo & decision</p>
            <h1 data-page-title tabIndex={-1}>{opportunity.company.name}</h1>
          </div>
        </header>
        <StatePanel state={previewState} entityLabel="investment memo" />
      </div>
    )
  }

  const openConfirmation = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (disposition === 'advance' && !canAdvance) {
      announce('Advance is unavailable until Decision Readiness is ready.')
      return
    }
    setDecisionError('')
    dialogRef.current?.showModal()
  }

  const recordDecision = async () => {
    setSaving(true)
    setDecisionError('')
    try {
      const decision = await client.recordDecision({
        opportunityId: opportunity.id,
        assessmentId: opportunity.assessmentId,
        memoId: opportunity.memo.id,
        recommendationId: opportunity.recommendation.id,
        disposition,
        rationale,
      })
      setReceipt(decision)
      dialogRef.current?.close()
      announce(`Human decision ${disposition.replaceAll('_', ' ')} recorded. No funds moved.`)
    } catch {
      setDecisionError(DECISION_FAILURE_MESSAGE)
      announce(DECISION_FAILURE_MESSAGE)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="page">
      <header className="page-header page-header--bordered">
        <div>
          <p className="eyebrow">Cited investment memo</p>
          <h1 data-page-title tabIndex={-1}>{opportunity.company.name}</h1>
          <p className="lede">
            Memo {opportunity.memo.version} · evidence as of{' '}
            {new Date(opportunity.memo.evidenceAsOf).toLocaleString()} · thesis {opportunity.memo.thesisVersion}
          </p>
        </div>
        <StatusBadge
          tone={
            opportunity.screeningCase.readiness === 'ready' ||
            opportunity.screeningCase.readiness === 'ready_with_accepted_risk'
              ? 'positive'
              : 'warning'
          }
        >
          Decision readiness {opportunity.screeningCase.readiness.replaceAll('_', ' ')}
        </StatusBadge>
      </header>

      {previewState === 'blocked' && <StatePanel state="blocked" entityLabel="investment memo" />}

      <div className="memo-layout">
        <article className="memo-document" aria-labelledby="memo-sections-title">
          <div className="memo-title-block">
            <BookMarked aria-hidden="true" />
            <div>
              <p className="eyebrow">Generated draft · investor review required</p>
              <h2 id="memo-sections-title">Investment review</h2>
            </div>
          </div>

          {opportunity.memo.sections.map((section, index) => (
            <section className="memo-section" key={section.kind}>
              <span className="section-number" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
              <div>
                <h3>{section.title}</h3>
                <KnowledgeState value={section.content} />
                <p className="citation-line">
                  Material claims: {section.materialClaimIds.map((id) => <code key={id}>{id}</code>)}
                </p>
              </div>
            </section>
          ))}

          <details className="adversarial-notes" open>
            <summary><ShieldQuestion aria-hidden="true" /> Adversarial review</summary>
            <div className="details-body">
              {opportunity.memo.adversarialNotes.map((note) => (
                <article key={note.title}>
                  <h3>{note.title}</h3>
                  <p>{note.body}</p>
                  <p className="citation-line">Claims: {note.claimIds.join(', ')}</p>
                </article>
              ))}
            </div>
          </details>
        </article>

        <aside className="decision-rail">
          <section className="recommendation-card" aria-labelledby="recommendation-title">
            <div className="recommendation-card__label">
              <CircleHelp aria-hidden="true" /> System recommendation
            </div>
            <h2 id="recommendation-title">
              {opportunity.recommendation.action.replaceAll('_', ' ')}
            </h2>
            <p>{opportunity.recommendation.summary}</p>
            <h3>Why</h3>
            <ul>{opportunity.recommendation.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
            <h3>Suggested next actions</h3>
            <ol>{opportunity.recommendation.nextActions.map((action) => <li key={action}>{action}</li>)}</ol>
            <small>Policy {opportunity.recommendation.policyVersion}</small>
          </section>

          <section className="human-decision-card" aria-labelledby="human-decision-title">
            <div className="human-decision-card__label"><Gavel aria-hidden="true" /> Human decision</div>
            <h2 id="human-decision-title">Record your disposition</h2>
            <p>The recommendation above is advisory. Only this explicit form records a decision.</p>

            {receipt ? (
              <div className="decision-receipt" role="status">
                <CheckCircle2 aria-hidden="true" />
                <div>
                  <h3>Decision recorded</h3>
                  <p>
                    <strong>{receipt.disposition.replaceAll('_', ' ')}</strong> by {receipt.actorLabel}.
                  </p>
                  <p>No outreach was sent and no funds moved.</p>
                  <code>{receipt.decisionId}</code>
                </div>
              </div>
            ) : !opportunity.decisionReadyForCommand ? (
              <div className="confirmation-warning" role="status">
                <FileWarning aria-hidden="true" />
                <div>
                  <h3>Decision command unavailable</h3>
                  <p>A real assessment, memo, and Recommendation are required before a Decision can be recorded.</p>
                </div>
              </div>
            ) : (
              <form onSubmit={openConfirmation}>
                <fieldset className="decision-options">
                  <legend>Disposition</legend>
                  {availableDecisions.map((decision) => (
                    <label key={decision.value}>
                      <input
                        type="radio"
                        name="disposition"
                        value={decision.value}
                        checked={disposition === decision.value}
                        onChange={() => setDisposition(decision.value)}
                      />
                      <span><strong>{decision.label}</strong><small>{decision.detail}</small></span>
                    </label>
                  ))}
                </fieldset>
                {!canAdvance && (
                  <p className="field-help" role="status">
                    Advance is unavailable until Decision Readiness is Ready or Ready with accepted risk.
                  </p>
                )}
                <label htmlFor="decision-rationale">Rationale</label>
                <textarea
                  id="decision-rationale"
                  rows={5}
                  required
                  minLength={12}
                  value={rationale}
                  onChange={(event) => setRationale(event.target.value)}
                />
                <p className="field-help">Required · preserved with assessment and memo IDs.</p>
                <button className="button button--decision" type="submit">
                  Review decision
                </button>
              </form>
            )}
          </section>
        </aside>
      </div>

      <dialog
        ref={dialogRef}
        className="confirmation-dialog"
        aria-labelledby="decision-confirmation-title"
        onClose={() => setDecisionError('')}
      >
        <div className="dialog-content" aria-busy={saving}>
          <header className="dialog-header">
            <div>
              <p className="eyebrow">Human confirmation</p>
              <h2 id="decision-confirmation-title">Record “{disposition.replaceAll('_', ' ')}”?</h2>
            </div>
            <button
              className="icon-button"
              type="button"
              aria-label="Cancel decision"
              onClick={() => dialogRef.current?.close()}
            >
              <X aria-hidden="true" />
            </button>
          </header>

          <div className="confirmation-warning">
            <FileWarning aria-hidden="true" />
            <div>
              <h3>Unresolved evidence gaps remain</h3>
              <ul>
                <li>Paid versus unpaid pilot status is conflicted.</li>
                <li>Prior institutional backing is Unknown.</li>
                <li>Enterprise buyer ownership is Unknown.</li>
              </ul>
            </div>
          </div>
          <p><strong>Your rationale:</strong> {rationale}</p>
          <p className="muted">
            This writes an auditable human decision. It never triggers outreach or movement of funds.
          </p>
          {decisionError && (
            <div className="error-summary" role="alert">
              <h3>Decision not recorded</h3>
              <p>{decisionError}</p>
            </div>
          )}
          <div className="dialog-actions">
            <button
              className="button button--quiet"
              type="button"
              onClick={() => dialogRef.current?.close()}
              disabled={saving}
            >
              Go back
            </button>
            <button className="button button--decision" type="button" onClick={recordDecision} disabled={saving}>
              {saving ? 'Recording…' : 'Record decision · no funds move'}
            </button>
          </div>
        </div>
      </dialog>
    </div>
  )
}
