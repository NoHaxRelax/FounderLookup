import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  FileProtectOutlined,
  FileTextOutlined,
  InboxOutlined,
  LockOutlined,
  SafetyCertificateOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  Result,
  Steps,
  Typography,
} from 'antd'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import type {
  ApplicationReceipt,
  FounderLookupClient,
  FounderStatusView,
  ViewState,
} from '../api/types'
import {
  applicationPayloadFingerprint,
  normalizeCompanyName,
} from '../api/applicationFingerprint'
import { StatePanel } from '../components/StatePanel'
import { StatusBadge } from '../components/StatusBadge'

interface FounderIntakeProps {
  client: FounderLookupClient
  previewState: ViewState
  announce: (message: string) => void
  statusCapability?: string
}

const maxDeckBytes = 10 * 1024 * 1024

function FounderStatusCard({
  status,
  receipt,
}: {
  status: FounderStatusView
  receipt?: ApplicationReceipt
}) {
  return (
    <Card className="receipt-card" aria-labelledby="receipt-title">
      <Result
        status="success"
        icon={<CheckCircleOutlined />}
        title={
          <h2 id="receipt-title">
            {receipt ? 'Your application is safely in the queue' : 'Private founder status'}
          </h2>
        }
        subTitle="This bounded view contains receipt, stage, timing, and focused requests only. Internal review details stay private. No investment decision was made; no final outcome is implied while review is in progress."
      />

      <div className="receipt-card__status">
        <StatusBadge tone={status.targetState === 'missed' ? 'warning' : 'positive'}>
          {status.stage}
        </StatusBadge>
      </div>

      <Descriptions
        className="status-steps"
        column={1}
        items={[
          {
            key: 'received',
            label: <span><SafetyCertificateOutlined /> Received</span>,
            children: new Date(status.receivedAt).toLocaleString(),
          },
          {
            key: 'stage',
            label: <span><SafetyCertificateOutlined /> Current stage</span>,
            children: status.stage,
          },
          {
            key: 'updated',
            label: <span><ClockCircleOutlined /> Last update</span>,
            children: new Date(status.lastUpdatedAt).toLocaleString(),
          },
          {
            key: 'target',
            label: <span><ClockCircleOutlined /> Review target</span>,
            children: status.targetLabel,
          },
          {
            key: 'request',
            label: <span><FileTextOutlined /> Focused request</span>,
            children: status.focusedRequest ?? 'None',
          },
          {
            key: 'outcome',
            label: <span><CheckCircleOutlined /> Outcome or next action</span>,
            children: (
              <span>
                {status.approvedOutcome ?? status.nextAction ?? 'Review is still in progress.'}
                {status.outcomeAt && <> · {new Date(status.outcomeAt).toLocaleString()}</>}
              </span>
            ),
          },
        ]}
      />

      {status.informationRequests.length > 0 && (
        <Alert
          type="info"
          showIcon
          title="Information requested"
          description={<ul>{status.informationRequests.map((request) => <li key={request}>{request}</li>)}</ul>}
        />
      )}

      {receipt && (
        <Alert
          className="capability-card"
          type="warning"
          showIcon
          icon={<FileProtectOutlined />}
          title="Keep your private status link"
          description={
            <div>
              <p>Anyone with this capability can view the bounded founder status. Do not share it publicly.</p>
              <Button href={receipt.founderStatusUrl}>Open private status</Button>
            </div>
          }
        />
      )}

      <Typography.Paragraph className="receipt-id" type="secondary">
        Application <Typography.Text code>{status.applicationId}</Typography.Text>
        {receipt && <> · Run <Typography.Text code>{receipt.runId}</Typography.Text></>}
      </Typography.Paragraph>
    </Card>
  )
}

export function FounderIntake({
  client,
  previewState,
  announce,
  statusCapability,
}: FounderIntakeProps) {
  const [companyName, setCompanyName] = useState('')
  const [deck, setDeck] = useState<File | null>(null)
  const [errors, setErrors] = useState<string[]>([])
  const [submitError, setSubmitError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [receipt, setReceipt] = useState<ApplicationReceipt | null>(null)
  const [status, setStatus] = useState<FounderStatusView | null>(null)
  const [statusLoadState, setStatusLoadState] = useState<'idle' | 'loading' | 'error'>(
    statusCapability ? 'loading' : 'idle',
  )
  const [statusRetry, setStatusRetry] = useState(0)
  const errorSummaryRef = useRef<HTMLDivElement>(null)
  const idempotencyRef = useRef<{ fingerprint: string; key: string } | null>(null)

  useEffect(() => {
    if (!statusCapability) return undefined
    let active = true
    void client.getFounderStatus(statusCapability).then(
      (nextStatus) => {
        if (!active) return
        setStatus(nextStatus)
        setStatusLoadState('idle')
      },
      () => {
        if (active) setStatusLoadState('error')
      },
    )
    return () => {
      active = false
    }
  }, [client, statusCapability, statusRetry])

  const validate = () => {
    const nextErrors: string[] = []
    if (!companyName.trim()) nextErrors.push('Enter the company name.')
    if (!deck) {
      nextErrors.push('Choose a PDF deck.')
    } else {
      if (deck.type !== 'application/pdf') nextErrors.push('The deck must be a PDF file.')
      if (deck.size > maxDeckBytes) nextErrors.push('The deck must be 10 MiB or smaller.')
    }
    return nextErrors
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const nextErrors = validate()
    setErrors(nextErrors)
    setSubmitError('')
    if (nextErrors.length > 0 || !deck) {
      requestAnimationFrame(() => errorSummaryRef.current?.focus())
      return
    }

    setSubmitting(true)
    try {
      const fingerprint = await applicationPayloadFingerprint(companyName, deck)
      if (idempotencyRef.current?.fingerprint !== fingerprint) {
        idempotencyRef.current = { fingerprint, key: globalThis.crypto.randomUUID() }
      }
      const nextReceipt = await client.submitApplication({
        companyName: normalizeCompanyName(companyName),
        deck,
        idempotencyKey: idempotencyRef.current.key,
      })
      setReceipt(nextReceipt)
      const nextStatus = await client.getFounderStatus(nextReceipt.founderStatusCapability)
      setStatus(nextStatus)
      announce('Application received and stored. A private status capability was created.')
    } catch (error) {
      const message = error instanceof Error ? error.message : 'The application could not be submitted.'
      setSubmitError(message)
      requestAnimationFrame(() => errorSummaryRef.current?.focus())
    } finally {
      setSubmitting(false)
    }
  }

  if (previewState !== 'ready') {
    return (
      <div className="page page--narrow">
        <header className="page-header">
          <div>
            <p className="eyebrow">Founder application</p>
            <h1 data-page-title tabIndex={-1}>Share your company</h1>
          </div>
        </header>
        <StatePanel state={previewState} entityLabel="founder application" />
      </div>
    )
  }

  if (statusCapability) {
    return (
      <div className="page page--narrow">
        <header className="apply-hero">
          <div className="apply-hero__icon"><FileProtectOutlined aria-hidden="true" /></div>
          <p className="eyebrow">Capability-scoped view</p>
          <h1 data-page-title tabIndex={-1}>Check your application status</h1>
          <p className="lede">Your private link opens only this bounded status view.</p>
        </header>
        {statusLoadState === 'loading' ? (
          <StatePanel state="loading" entityLabel="founder status" />
        ) : statusLoadState === 'error' || !status ? (
          <StatePanel
            state="error"
            entityLabel="founder status"
            onRetry={() => {
              setStatusLoadState('loading')
              setStatusRetry((attempt) => attempt + 1)
            }}
          />
        ) : (
          <FounderStatusCard status={status} />
        )}
      </div>
    )
  }

  const companyError = errors.find((error) => error === 'Enter the company name.')
  const deckErrors = errors.filter((error) => error !== 'Enter the company name.')

  return (
    <div className="page page--narrow">
      <header className="apply-hero">
        <div className="apply-hero__icon"><InboxOutlined aria-hidden="true" /></div>
        <p className="eyebrow">Founder application · minimum intake</p>
        <h1 data-page-title tabIndex={-1}>Start with your company and deck</h1>
        <p className="lede">
          We ask for only the minimum required to begin a review. Unknown information remains
          Unknown; a submission is not an investment decision.
        </p>
      </header>

      {receipt && status ? (
        <FounderStatusCard status={status} receipt={receipt} />
      ) : (
        <div className="intake-layout">
          <Card className="intake-form-card">
            <Form
              className="intake-form"
              layout="vertical"
              onSubmitCapture={submit}
              noValidate
              aria-busy={submitting}
            >
              {(errors.length > 0 || submitError) && (
                <div className="error-summary" role="alert" tabIndex={-1} ref={errorSummaryRef}>
                  <Alert
                    type="error"
                    showIcon
                    title={<h2>{submitError ? 'Application not submitted' : 'Check the application'}</h2>}
                    description={
                      <div>
                        {submitError && <p>{submitError} Your entries and retry key were preserved.</p>}
                        {errors.length > 0 && <ul>{errors.map((error) => <li key={error}>{error}</li>)}</ul>}
                      </div>
                    }
                  />
                </div>
              )}

              <Form.Item label="Company name" htmlFor="company-name" required>
                <Input
                  id="company-name"
                  name="companyName"
                  value={companyName}
                  onChange={(event) => setCompanyName(event.target.value)}
                  autoComplete="organization"
                  aria-invalid={Boolean(companyError)}
                  aria-describedby={companyError ? 'company-name-error' : undefined}
                  required
                />
                {companyError && <p id="company-name-error" className="field-error">{companyError}</p>}
              </Form.Item>

              <Form.Item
                label="Pitch deck (PDF)"
                htmlFor="deck"
                required
                extra="Maximum 10 MiB"
              >
                <div className="native-file-control">
                  <UploadOutlined aria-hidden="true" />
                  <input
                    id="deck"
                    name="deck"
                    type="file"
                    accept="application/pdf,.pdf"
                    aria-invalid={deckErrors.length > 0}
                    aria-describedby={`deck-help${deckErrors.length > 0 ? ' deck-error' : ''}`}
                    onChange={(event) => {
                      setDeck(event.target.files?.[0] ?? null)
                      setErrors([])
                    }}
                    required
                  />
                  <p id="deck-help" className="upload-hint">
                    {deck ? `${deck.name} · ${(deck.size / 1024 / 1024).toFixed(1)} MiB selected` : 'PDF only · Maximum 10 MiB'}
                  </p>
                  {deckErrors.length > 0 && (
                    <p id="deck-error" className="field-error">{deckErrors.join(' ')}</p>
                  )}
                </div>
              </Form.Item>

              <Alert
                className="authorization-note"
                type="info"
                showIcon
                icon={<SafetyCertificateOutlined />}
                title="Authorized deck sharing"
                description="By submitting, you confirm you are authorized to share this deck for the review described here. Your upload stays private."
              />

              <Button
                type="primary"
                htmlType="submit"
                size="large"
                loading={submitting}
                aria-label="Submit application"
                block
              >
                {submitting ? 'Storing application…' : 'Submit application'}
              </Button>
              <p className="form-footnote"><LockOutlined /> Submission does not imply approval or funding.</p>
            </Form>
          </Card>

          <Card className="intake-assurance" title="What happens next">
            <Steps
              orientation="vertical"
              current={0}
              items={[
                {
                  title: 'Persist first',
                  content: 'Your deck and source metadata are stored before processing.',
                },
                {
                  title: 'Evidence-aware review',
                  content: 'The review keeps sourced facts and unresolved gaps distinct.',
                },
                {
                  title: 'Bounded status',
                  content: 'Your private link shows receipt, stage, timing, and focused requests only.',
                },
              ]}
            />
            <Alert
              className="privacy-note"
              type="warning"
              showIcon
              icon={<LockOutlined />}
              title="Private status boundary"
              description="Other applications and internal review details never appear in your status view."
            />
          </Card>
        </div>
      )}
    </div>
  )
}
