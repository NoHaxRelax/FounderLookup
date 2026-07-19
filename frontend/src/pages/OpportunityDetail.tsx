import {
  ArrowDownOutlined,
  ArrowRightOutlined,
  ArrowUpOutlined,
  BookOutlined,
  ClockCircleOutlined,
  FileSearchOutlined,
  IdcardOutlined,
  PlayCircleOutlined,
  QuestionCircleOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Progress,
  Space,
  Statistic,
  Tag,
  Timeline,
  Typography,
} from 'antd'
import { useState } from 'react'
import type {
  AxisSummary,
  ClaimItem,
  EvidenceItem,
  FounderLookupClient,
  OpportunityDetail as OpportunityDetailModel,
  ViewState,
} from '../api/types'
import { EvidenceDialog } from '../components/EvidenceDialog'
import { KnowledgeState } from '../components/KnowledgeState'
import { PublicContactPanel } from '../components/PublicContactPanel'
import { StatePanel } from '../components/StatePanel'
import { StatusBadge, type BadgeTone } from '../components/StatusBadge'

interface OpportunityDetailProps {
  client: FounderLookupClient
  opportunity: OpportunityDetailModel
  previewState: ViewState
  announce: (message: string) => void
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
  if (trend === 'improving') return <ArrowUpOutlined aria-hidden="true" />
  if (trend === 'declining') return <ArrowDownOutlined aria-hidden="true" />
  if (trend === 'unknown') return <QuestionCircleOutlined aria-hidden="true" />
  return <ArrowRightOutlined aria-hidden="true" />
}

export function OpportunityDetail({
  client,
  opportunity,
  previewState,
  announce,
}: OpportunityDetailProps) {
  const [record, setRecord] = useState(opportunity)
  const [selectedEvidence, setSelectedEvidence] = useState<EvidenceItem | null>(null)
  const [runningCommand, setRunningCommand] = useState<'screen' | 'retry' | null>(null)
  const [commandError, setCommandError] = useState('')
  const evidenceById = new Map(record.evidence.map((item) => [item.id, item]))
  const readinessBlocked = record.screeningCase.readiness === 'blocked'
  const blockingContradictions = record.contradictions.filter((item) => item.blocking)
  const retryableRun = record.pipelineRuns.find((run) => run.failures.some((failure) => failure.retryable))

  const screenOpportunity = async () => {
    setRunningCommand('screen')
    setCommandError('')
    try {
      const result = await client.screenOpportunity(record.id)
      setRecord(result.opportunity)
      announce(`Screening run ${result.run.status}. Accepted evidence remains available.`)
    } catch (error) {
      setCommandError(error instanceof Error ? error.message : 'Screening could not start.')
      announce('Screening was not started. Existing Opportunity data is unchanged.')
    } finally {
      setRunningCommand(null)
    }
  }

  const retryRun = async () => {
    if (!retryableRun) return
    setRunningCommand('retry')
    setCommandError('')
    try {
      const result = await client.retryOpportunityRun(record.id, retryableRun.id)
      setRecord(result.opportunity)
      announce(`Retry run ${result.run.status}. Previously accepted outputs were preserved.`)
    } catch (error) {
      setCommandError(error instanceof Error ? error.message : 'The run could not be retried.')
      announce('The run was not retried. Previously accepted outputs remain unchanged.')
    } finally {
      setRunningCommand(null)
    }
  }

  if (previewState !== 'ready' && previewState !== 'blocked') {
    return (
      <div className="page">
        <header className="page-header">
          <div><p className="eyebrow">Opportunity</p><h1 data-page-title tabIndex={-1}>{record.company.name}</h1></div>
        </header>
        <StatePanel state={previewState} entityLabel="opportunity assessment" />
      </div>
    )
  }

  const auditContent = (
    <div className="audit-stack">
      <section aria-labelledby="stable-identity-title">
        <h3 id="stable-identity-title"><IdcardOutlined aria-hidden="true" /> Stable assessment identity</h3>
        <Descriptions
          size="small"
          column={{ xs: 1, sm: 2 }}
          items={[
            { key: 'opportunity', label: 'Opportunity', children: <Typography.Text code>{record.id}</Typography.Text> },
            { key: 'assessment', label: 'Assessment', children: <Typography.Text code>{record.assessmentId}</Typography.Text> },
            { key: 'snapshot', label: 'Input snapshot', children: <Typography.Text code>{record.inputSnapshotId}</Typography.Text> },
            { key: 'thesis', label: 'Thesis', children: <Typography.Text code>{record.thesisVersion}</Typography.Text> },
          ]}
        />
      </section>

      <section aria-labelledby="claims-title">
        <div className="section-heading">
          <h3 id="claims-title">Material Claims, Trust, and Evidence</h3>
          <StatusBadge tone="info">{record.claims.length} claims</StatusBadge>
        </div>
        {record.claims.length === 0 ? (
          <StatePanel state="empty" entityLabel="material claims" />
        ) : (
          <ul className="claims-list">
            {record.claims.map((claim) => (
              <li key={claim.id}>
                <article className="claim-card">
                  <Card className="claim-card__surface" title={<h3>{claim.statement}</h3>}>
                    <Space wrap>
                      <StatusBadge tone={claimTone[claim.status]}>{claim.status.replaceAll('_', ' ')}</StatusBadge>
                      <Typography.Text code>{claim.id}</Typography.Text>
                    </Space>
                    <p>{claim.verificationLabel}</p>
                    <Collapse
                      items={[
                        {
                          key: 'trust',
                          label: (
                            <span>
                              <SafetyCertificateOutlined aria-hidden="true" /> Trust factors ·{' '}
                              {claim.trust.state === 'scored' ? `${claim.trust.score}/100` : claim.trust.state}
                            </span>
                          ),
                          children: (
                            <div className="details-body">
                              <p>
                                {claim.trust.state === 'scored'
                                  ? `${claim.trust.score}/100 Claim Trust — never founder quality.`
                                  : `${claim.trust.state} — ${claim.trust.reason}`}
                              </p>
                              <ul className="factor-list">
                                {claim.trust.factors.map((factor) => (
                                  <li key={`${factor.label}-${factor.signal}`}>
                                    <Space align="start">
                                      <StatusBadge tone={factor.signal === 'strengthens' ? 'positive' : factor.signal === 'weakens' ? 'critical' : 'neutral'}>
                                        {factor.signal}
                                      </StatusBadge>
                                      <span><strong>{factor.label}:</strong> {factor.rationale}</span>
                                    </Space>
                                  </li>
                                ))}
                              </ul>
                            </div>
                          ),
                        },
                      ]}
                    />
                    <div className="evidence-buttons" aria-label={`Evidence for claim: ${claim.statement}`}>
                      {[...claim.supportingEvidenceIds, ...claim.counterEvidenceIds].map((evidenceId) => {
                        const evidence = evidenceById.get(evidenceId)
                        if (!evidence) return null
                        const counter = claim.counterEvidenceIds.includes(evidenceId)
                        return (
                          <Button
                            className="evidence-button"
                            danger={counter}
                            icon={<FileSearchOutlined aria-hidden="true" />}
                            key={evidenceId}
                            aria-label={`${counter ? 'Counter-evidence' : 'Evidence'} ${evidence.locator.label}`}
                            onClick={() => setSelectedEvidence(evidence)}
                          >
                            {counter ? 'Counter-evidence' : 'Evidence'} · {evidence.locator.label}
                          </Button>
                        )
                      })}
                    </div>
                  </Card>
                </article>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="run-history-title">
        <h3 id="run-history-title">Run history</h3>
        {record.timeline.length === 0 ? (
          <p>No run stages were returned.</p>
        ) : (
          <Timeline
            items={record.timeline.map((stage) => ({
              color: stage.status === 'succeeded' ? 'green' : stage.status === 'failed' ? 'red' : 'orange',
              content: (
                <div className="timeline-stage">
                  <div className="timeline-heading">
                    <strong>{stage.label}</strong>
                    <StatusBadge tone={stage.status === 'succeeded' ? 'positive' : stage.status === 'failed' ? 'critical' : 'warning'}>
                      {stage.status}
                    </StatusBadge>
                  </div>
                  <p>{stage.detail}</p>
                  <Typography.Text type="secondary"><ClockCircleOutlined /> {stage.timing}</Typography.Text>
                  {stage.externalWait && <Typography.Text type="secondary">External wait is separate from compute time.</Typography.Text>}
                </div>
              ),
            }))}
          />
        )}
      </section>
    </div>
  )

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <Space wrap size="small">
            <StatusBadge tone="info">{record.origin}</StatusBadge>
            <StatusBadge tone="warning">{record.screeningCase.status}</StatusBadge>
          </Space>
          <p className="eyebrow">Opportunity</p>
          <h1 data-page-title tabIndex={-1}>{record.company.name}</h1>
          <p className="lede">
            <KnowledgeState value={record.founder.name} compact /> ·{' '}
            <KnowledgeState value={record.company.sector} compact /> ·{' '}
            <KnowledgeState value={record.company.geography} compact />
          </p>
        </div>
        <Button href={`#/memo/${encodeURIComponent(record.id)}`} icon={<BookOutlined aria-hidden="true" />} size="large">
          Review memo &amp; decide
        </Button>
      </header>

      <section className="opportunity-act" aria-labelledby="opportunity-act-title">
        <p className="eyebrow">Act</p>
        <div className="decision-summary-grid">
          <Card className="recommendation-card">
            <StatusBadge tone={readinessBlocked ? 'warning' : 'positive'}>
              Readiness {record.screeningCase.readiness.replaceAll('_', ' ')}
            </StatusBadge>
            <h2 id="opportunity-act-title">{record.recommendation.action.replaceAll('_', ' ')}</h2>
            <p>{record.recommendation.summary}</p>
            <p className="muted">System Recommendation · awaiting an explicit human Decision.</p>
          </Card>
          <Card className="blocker-summary" title={`Material blockers (${blockingContradictions.length})`}>
            {blockingContradictions.length > 0 ? (
              <ul>
                {blockingContradictions.map((contradiction) => <li key={contradiction.id}>{contradiction.summary}</li>)}
              </ul>
            ) : (
              <p>No blocking contradiction was returned. This is not proof that evidence is complete.</p>
            )}
            <p>{record.screeningCase.readinessReason}</p>
          </Card>
        </div>
        {commandError && <Alert type="error" showIcon title="Command not completed" description={commandError} />}
        <div className="opportunity-actions">
          <Button type="primary" icon={<PlayCircleOutlined aria-hidden="true" />} loading={runningCommand === 'screen'} onClick={screenOpportunity}>
            Run full Screening
          </Button>
          {retryableRun && (
            <Button icon={<ReloadOutlined aria-hidden="true" />} loading={runningCommand === 'retry'} onClick={retryRun}>
              Retry failed stage
            </Button>
          )}
        </div>
      </section>

      {previewState === 'blocked' && <StatePanel state="blocked" entityLabel="opportunity assessment" />}

      <section className="assessment-overview" aria-labelledby="assessment-overview-title" aria-label="Founder score and three axes">
        <div className="section-heading">
          <div><p className="eyebrow">Decision summary</p><h2 id="assessment-overview-title">Founder Score and three independent axes</h2></div>
          <Typography.Text type="secondary">The axes are never averaged into the Founder Score.</Typography.Text>
        </div>
        <div className="score-and-axes">
          <Card className="founder-score-card" title="Founder Score · persistent across Opportunities">
            {record.founderScore.state === 'known' ? (
              <>
                <Statistic value={record.founderScore.value.score} suffix="/100" />
                <StatusBadge tone="warning">
                  {record.founderScore.value.provisional ? 'Provisional' : 'Established'} · {record.founderScore.value.uncertainty} uncertainty
                </StatusBadge>
                <p>{record.founderScore.value.coverageLabel}</p>
              </>
            ) : <KnowledgeState value={record.founderScore} />}
          </Card>
          <div className="axes-grid">
            {record.axes.map((axis) => (
              <Card className="axis-card" key={axis.key} title={<h3>{axis.label}</h3>} extra={<StatusBadge tone={axisTone(axis)}>{axis.rating}</StatusBadge>}>
                <p className="trend-line">{trendIcon(axis.trend)} {axis.trendLabel}</p>
                <p>{axis.coverageLabel}</p>
                {axis.confidence.state === 'known' ? (
                  <Progress percent={Math.round(axis.confidence.value * 100)} status="normal" aria-label={`${Math.round(axis.confidence.value * 100)}% confidence`} />
                ) : <KnowledgeState value={axis.confidence} />}
                <p className="muted">{axis.openQuestions.length} open question(s)</p>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {record.origin === 'outbound' && (
        <PublicContactPanel
          routes={record.publicContactRoutes}
          loopAudit={record.sourcingLoopAudit}
        />
      )}

      <section className="progressive-stack" aria-label="Opportunity explanation and audit detail">
        <Collapse
          items={[
            {
              key: 'understand',
              label: <span><QuestionCircleOutlined aria-hidden="true" /> Understand the Recommendation</span>,
              extra: <Tag>{record.recommendation.reasons.length} reasons · {record.diligenceActions.length} gaps</Tag>,
              children: (
                <div className="understand-grid">
                  <div><h3>Why</h3><ul>{record.recommendation.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></div>
                  <div><h3>Next actions</h3><ol>{record.recommendation.nextActions.map((action) => <li key={action}>{action}</li>)}</ol></div>
                  {record.founderScore.state === 'known' && <p>{record.founderScore.value.explanation}</p>}
                  {record.contradictions.map((contradiction) => (
                    <Alert
                      key={contradiction.id}
                      type={contradiction.blocking ? 'warning' : 'info'}
                      showIcon
                      icon={<WarningOutlined />}
                      title={contradiction.summary}
                      description={`Smallest next action: ${contradiction.smallestNextAction}`}
                    />
                  ))}
                </div>
              ),
            },
            {
              key: 'audit',
              label: <span><SafetyCertificateOutlined aria-hidden="true" /> Audit Claims, Evidence, Trust, and runs</span>,
              extra: <Tag>{record.claims.length} claims · {record.evidence.length} evidence · {record.runIds.length} runs</Tag>,
              children: auditContent,
            },
          ]}
        />
      </section>

      <EvidenceDialog evidence={selectedEvidence} onClose={() => setSelectedEvidence(null)} />
    </div>
  )
}
