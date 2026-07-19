import {
  AuditOutlined,
  BookOutlined,
  CheckCircleOutlined,
  QuestionCircleOutlined,
  SafetyCertificateOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Collapse,
  Form,
  Input,
  Modal,
  Radio,
  Result,
  Space,
  Tag,
  Typography,
} from 'antd'
import { useState, type FormEvent } from 'react'
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
  { value: 'advance', label: 'Advance with accepted risk', detail: 'Continue to human diligence and preserve the open risks.' },
  { value: 'request_more_information', label: 'Request more information', detail: 'Resolve a bounded Evidence gap.' },
  { value: 'hold', label: 'Hold', detail: 'Keep the Opportunity open without progressing it.' },
  { value: 'decline', label: 'Decline', detail: 'Close this Opportunity with a recorded rationale.' },
]

const DECISION_FAILURE_MESSAGE =
  'The Decision was not recorded. No workflow state changed, and your rationale is still available.'

const sentenceCase = (value: string) => {
  const normalized = value.replaceAll('_', ' ').trim()
  return normalized ? normalized.charAt(0).toLocaleUpperCase() + normalized.slice(1) : normalized
}

export function MemoDecision({ client, opportunity, previewState, announce }: MemoDecisionProps) {
  const suggestedRationale =
    opportunity.contradictions.find((item) => item.blocking)?.smallestNextAction ??
    opportunity.recommendation.nextActions[0] ??
    'Record the evidence-backed reason for this disposition.'
  const [disposition, setDisposition] = useState<DecisionDisposition>('request_more_information')
  const [rationale, setRationale] = useState(suggestedRationale)
  const [receipt, setReceipt] = useState<DecisionReceipt | null>(null)
  const [saving, setSaving] = useState(false)
  const [decisionError, setDecisionError] = useState('')
  const [confirmationOpen, setConfirmationOpen] = useState(false)
  // The fixture is the judge-facing demo: expose the safe human command immediately.
  // HTTP remains progressively disclosed because it can mutate persisted state.
  const [decisionFormOpen, setDecisionFormOpen] = useState(client.runtime === 'fixture')

  const canAdvance =
    opportunity.screeningCase.readiness === 'ready' ||
    opportunity.screeningCase.readiness === 'ready_with_accepted_risk'
  const canOverrideInDemo = client.runtime === 'fixture'
  const availableDecisions = canAdvance || canOverrideInDemo ? decisions : decisions.filter((item) => item.value !== 'advance')
  const materialWarnings = [
    ...opportunity.contradictions.map((item) => item.summary),
    ...opportunity.diligenceActions,
  ]

  if (previewState !== 'ready' && previewState !== 'blocked') {
    return (
      <div className="page">
        <header className="page-header">
          <div><p className="eyebrow">Memo &amp; Decision</p><h1 data-page-title tabIndex={-1}>{opportunity.company.name}</h1></div>
        </header>
        <StatePanel state={previewState} entityLabel="investment memo" />
      </div>
    )
  }

  const openConfirmation = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (disposition === 'advance' && !canAdvance && !canOverrideInDemo) {
      announce('Advance is unavailable until Decision Readiness is Ready.')
      return
    }
    if (rationale.trim().length < 12) {
      setDecisionError('Add a rationale of at least 12 characters before review.')
      return
    }
    setDecisionError('')
    setConfirmationOpen(true)
  }

  const closeConfirmation = () => {
    if (saving) return
    setConfirmationOpen(false)
    setDecisionError('')
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
        rationale: rationale.trim(),
      })
      setReceipt(decision)
      setConfirmationOpen(false)
      announce(`Human Decision ${disposition.replaceAll('_', ' ')} recorded. No funds moved.`)
    } catch {
      setDecisionError(DECISION_FAILURE_MESSAGE)
      announce(DECISION_FAILURE_MESSAGE)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Review &amp; decide</p>
          <h1 data-page-title tabIndex={-1}>{opportunity.company.name}</h1>
          <p className="lede">
            Memo {opportunity.memo.version} · Evidence as of {new Date(opportunity.memo.evidenceAsOf).toLocaleString()}
          </p>
        </div>
        <Button href={`#/opportunity/${encodeURIComponent(opportunity.id)}`}>Back to Opportunity</Button>
      </header>

      {previewState === 'blocked' && <StatePanel state="blocked" entityLabel="investment memo" />}

      <section className="memo-act" aria-labelledby="decision-action-title">
        <p className="eyebrow">Recommendation</p>
        <div className="decision-summary-grid">
          <Card className="recommendation-card">
            <h2 className="decision-card-heading"><QuestionCircleOutlined aria-hidden="true" /> System Recommendation</h2>
            <StatusBadge tone={canAdvance ? 'positive' : 'warning'}>
              Readiness {sentenceCase(opportunity.screeningCase.readiness)}
            </StatusBadge>
            <h3>{sentenceCase(opportunity.recommendation.action)}</h3>
            <p>{opportunity.recommendation.summary}</p>
            <p className="muted">Advisory only. You make the final decision.</p>
          </Card>
          <Card className="blocker-summary">
            <h2 className="decision-card-heading">Evidence gaps and contradictions ({materialWarnings.length})</h2>
            {materialWarnings.length > 0 ? (
              <ul>{materialWarnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul>
            ) : (
              <p>No material warning was returned. Review the cited memo before acting.</p>
            )}
          </Card>
        </div>

        <Card className="human-decision-card">
          <h2 id="decision-action-title" className="decision-card-heading"><AuditOutlined aria-hidden="true" /> Make a decision</h2>
          <p className="muted">Choose the next step and record why. Nothing is sent and no funds move.</p>
          {receipt ? (
            <Result
              className="decision-receipt"
              status="success"
              icon={<CheckCircleOutlined />}
              title={<h3>Decision recorded</h3>}
              subTitle={
                <span>
                  <strong>{receipt.disposition.replaceAll('_', ' ')}</strong> by {receipt.actorLabel}.
                  No outreach was sent and no funds moved.
                </span>
              }
            >
              <Typography.Text code>{receipt.decisionId}</Typography.Text>
            </Result>
          ) : !opportunity.decisionReadyForCommand ? (
            <Alert
              type="warning"
              showIcon
              icon={<WarningOutlined />}
              title={<h3>Decision command unavailable</h3>}
              description="A real Assessment, memo, and Recommendation are required before a Decision can be recorded."
            />
          ) : (
            <div className="decision-entry">
              {client.runtime !== 'fixture' && (
                <div className="decision-entry__prompt">
                  <div>
                    <strong>Ready for an explicit human command</strong>
                    <p>Choose a disposition and record the evidence-backed rationale only when you are ready.</p>
                  </div>
                  <Button
                    type="primary"
                    aria-expanded={decisionFormOpen}
                    aria-controls="human-decision-form"
                    onClick={() => setDecisionFormOpen((open) => !open)}
                  >
                    {decisionFormOpen ? 'Close Decision form' : 'Start human Decision'}
                  </Button>
                </div>
              )}

              {decisionFormOpen && (
                <Form id="human-decision-form" className="human-decision-form" layout="vertical" onSubmitCapture={openConfirmation}>
                  <label className="decision-field-label" htmlFor="decision-disposition"><span aria-hidden="true">*</span> Choose a disposition</label>
                  <Form.Item className="decision-disposition-field">
                    <Radio.Group
                      id="decision-disposition"
                      className="decision-options"
                      value={disposition}
                      onChange={(event) => setDisposition(event.target.value as DecisionDisposition)}
                    >
                      <Space orientation="vertical">
                        {availableDecisions.map((decision) => (
                          <Radio key={decision.value} value={decision.value}>
                            <span className="decision-option__copy"><strong>{decision.label}</strong><small>{decision.detail}</small></span>
                          </Radio>
                        ))}
                      </Space>
                    </Radio.Group>
                  </Form.Item>
                  {!canAdvance && !canOverrideInDemo && <p className="decision-policy-note">Advance is hidden until the evidence is decision-ready.</p>}
                  {!canAdvance && canOverrideInDemo && <p className="decision-policy-note">Demo mode: advancing records explicit accepted risk; it does not send outreach or move funds.</p>}
                  <label className="decision-field-label" htmlFor="decision-rationale"><span aria-hidden="true">*</span> Rationale</label>
                  <Form.Item className="decision-rationale-field" extra="Required · preserved with the reviewed Assessment and memo identifiers.">
                    <Input.TextArea
                      id="decision-rationale"
                      aria-label="Rationale"
                      rows={3}
                      required
                      minLength={12}
                      value={rationale}
                      aria-invalid={Boolean(decisionError && !confirmationOpen)}
                      onChange={(event) => {
                        setRationale(event.target.value)
                        setDecisionError('')
                      }}
                    />
                  </Form.Item>
                  {decisionError && !confirmationOpen && <Alert type="error" showIcon title="Decision not ready for review" description={decisionError} />}
                  <Button type="primary" htmlType="submit">Review and confirm</Button>
                </Form>
              )}
            </div>
          )}
        </Card>
      </section>

      <section className="progressive-stack" aria-label="Memo explanation and audit detail">
        <Collapse
          items={[
            {
              key: 'understand',
              label: (
                <span className="disclosure-label">
                  <span><SafetyCertificateOutlined aria-hidden="true" /> Understand the Recommendation</span>
                  <small>{opportunity.recommendation.reasons.length} reasons · {opportunity.recommendation.nextActions.length} actions</small>
                </span>
              ),
              children: (
                <div className="understand-grid">
                  <div><h3>Why</h3><ul>{opportunity.recommendation.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></div>
                  <div><h3>Suggested next actions</h3><ol>{opportunity.recommendation.nextActions.map((action) => <li key={action}>{action}</li>)}</ol></div>
                  <p>{opportunity.screeningCase.readinessReason}</p>
                  <Typography.Text type="secondary">Policy {opportunity.recommendation.policyVersion}</Typography.Text>
                </div>
              ),
            },
            {
              key: 'audit',
              label: (
                <span className="disclosure-label">
                  <span><BookOutlined aria-hidden="true" /> Audit the cited memo</span>
                  <small>{opportunity.memo.sections.length} required sections · {opportunity.memo.adversarialNotes.length} adversarial notes</small>
                </span>
              ),
              children: (
                <article className="memo-document" aria-labelledby="memo-sections-title">
                  <Card className="memo-document__surface">
                    <div className="memo-title-block">
                      <BookOutlined className="memo-title-block__icon" aria-hidden="true" />
                      <div>
                        <p className="eyebrow">Generated draft · human review required</p>
                        <h2 id="memo-sections-title">Investment memo</h2>
                      </div>
                    </div>
                    <div className="memo-sections">
                      {opportunity.memo.sections.map((section, index) => (
                        <section className="memo-section" key={section.kind}>
                          <span className="section-number" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
                          <div>
                            <h3>{section.title}</h3>
                            <KnowledgeState value={section.content} />
                            <Space className="citation-line" wrap size="small">
                              <Typography.Text type="secondary">Material Claims:</Typography.Text>
                              {section.materialClaimIds.length > 0
                                ? section.materialClaimIds.map((id) => <Tag key={id}>{id}</Tag>)
                                : <span>None cited</span>}
                            </Space>
                          </div>
                        </section>
                      ))}
                    </div>
                    <section className="adversarial-notes" aria-labelledby="adversarial-title">
                      <h3 id="adversarial-title">Adversarial review</h3>
                      {opportunity.memo.adversarialNotes.length > 0 ? (
                        <div className="adversarial-notes__list">
                          {opportunity.memo.adversarialNotes.map((note) => (
                            <article key={note.title}>
                              <h3>{note.title}</h3><p>{note.body}</p>
                              <Typography.Text type="secondary">Claims: {note.claimIds.join(', ') || 'None cited'}</Typography.Text>
                            </article>
                          ))}
                        </div>
                      ) : <p>No adversarial notes were returned in this memo revision.</p>}
                    </section>
                    <p className="muted">
                      Generated {new Date(opportunity.memo.generatedAt).toLocaleString()} · Thesis {opportunity.memo.thesisVersion}
                    </p>
                  </Card>
                </article>
              ),
            },
          ]}
        />
      </section>

      <Modal
        open={confirmationOpen}
        onCancel={closeConfirmation}
        footer={null}
        closable={!saving}
        mask={{ closable: !saving }}
        title={<div><p className="eyebrow">Human confirmation</p><h2>Record “{disposition.replaceAll('_', ' ')}”?</h2></div>}
      >
        <div className="dialog-content" aria-busy={saving}>
          <Alert
            className="confirmation-warning"
            type={materialWarnings.length > 0 ? 'warning' : 'info'}
            showIcon
            icon={<WarningOutlined />}
            title={<h3>{materialWarnings.length > 0 ? 'Review unresolved items' : 'Confirm the reviewed record'}</h3>}
            description={materialWarnings.length > 0 ? <ul>{materialWarnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul> : 'No material warning was returned.'}
          />
          <p><strong>Your rationale:</strong> {rationale}</p>
          <Typography.Paragraph type="secondary">
            This writes an auditable human Decision. It never triggers outreach or movement of funds.
          </Typography.Paragraph>
          {decisionError && (
            <Alert className="error-summary" type="error" showIcon role="alert" title={<h3>Decision not recorded</h3>} description={decisionError} />
          )}
          <div className="dialog-actions">
            <Button onClick={closeConfirmation} disabled={saving}>Go back</Button>
            <Button type="primary" danger onClick={recordDecision} loading={saving}>Record Decision · no funds move</Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
