import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  FileProtectOutlined,
  FileTextOutlined,
  InboxOutlined,
  LockOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Form,
  Input,
  Result,
  Typography,
} from 'antd'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import type {
  ApplicationFounderInput,
  ApplicationInput,
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

interface FounderDraft {
  id: string
  fullName: string
  roleTitle: string
  email: string
  linkedinUrl: string
  githubUrl: string
  previousCompanies: string
  background: string
}

interface IntakeError {
  field: string
  message: string
}

const isValidEmail = (value: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)

const isValidWebUrl = (value: string) => {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' || url.protocol === 'http:'
  } catch {
    return false
  }
}

const isValidLinkedInProfile = (value: string) => {
  try {
    const url = new URL(value)
    return url.protocol === 'https:'
      && ['linkedin.com', 'www.linkedin.com'].includes(url.hostname.toLocaleLowerCase())
      && /^\/in\/[^/]+\/?$/.test(url.pathname)
  } catch {
    return false
  }
}

const isValidGitHubProfile = (value: string) => {
  try {
    const url = new URL(value)
    return url.protocol === 'https:'
      && url.hostname.toLocaleLowerCase() === 'github.com'
      && /^\/[^/]+\/?$/.test(url.pathname)
  } catch {
    return false
  }
}

const optionalText = (value: string) => value.trim() || undefined

const mapFounderDraft = (founder: FounderDraft): ApplicationFounderInput => ({
  fullName: founder.fullName.trim(),
  ...(optionalText(founder.roleTitle) ? { roleTitle: founder.roleTitle.trim() } : {}),
  ...(optionalText(founder.email) ? { email: founder.email.trim() } : {}),
  ...(optionalText(founder.linkedinUrl) ? { linkedinUrl: founder.linkedinUrl.trim() } : {}),
  ...(optionalText(founder.githubUrl) ? { githubUrl: founder.githubUrl.trim() } : {}),
  ...(optionalText(founder.previousCompanies)
    ? {
        previousCompanies: founder.previousCompanies
          .split(',')
          .map((company) => company.trim())
          .filter(Boolean),
      }
    : {}),
  ...(optionalText(founder.background) ? { background: founder.background.trim() } : {}),
})

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
  const [website, setWebsite] = useState('')
  const [oneLinePitch, setOneLinePitch] = useState('')
  const [companyLocation, setCompanyLocation] = useState('')
  const [companyStage, setCompanyStage] = useState('')
  const [contactEmail, setContactEmail] = useState('')
  const [founders, setFounders] = useState<FounderDraft[]>([])
  const [errors, setErrors] = useState<IntakeError[]>([])
  const [submitError, setSubmitError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [receipt, setReceipt] = useState<ApplicationReceipt | null>(null)
  const [status, setStatus] = useState<FounderStatusView | null>(null)
  const [statusLoadState, setStatusLoadState] = useState<'idle' | 'loading' | 'error'>(
    statusCapability ? 'loading' : 'idle',
  )
  const [statusRetry, setStatusRetry] = useState(0)
  const errorSummaryRef = useRef<HTMLDivElement>(null)
  const deckInputRef = useRef<HTMLInputElement>(null)
  const founderSequenceRef = useRef(0)
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
    const nextErrors: IntakeError[] = []
    if (!companyName.trim()) nextErrors.push({ field: 'companyName', message: 'Enter the company name.' })
    if (!deck) {
      nextErrors.push({ field: 'deck', message: 'Choose a PDF deck.' })
    } else {
      if (deck.type !== 'application/pdf') nextErrors.push({ field: 'deck', message: 'The deck must be a PDF file.' })
      if (deck.size > maxDeckBytes) nextErrors.push({ field: 'deck', message: 'The deck must be 10 MiB or smaller.' })
    }
    if (website.trim() && !isValidWebUrl(website.trim())) {
      nextErrors.push({ field: 'website', message: 'Enter a valid company website URL.' })
    }
    if (contactEmail.trim() && !isValidEmail(contactEmail.trim())) {
      nextErrors.push({ field: 'contactEmail', message: 'Enter a valid company contact email.' })
    }
    founders.forEach((founder, index) => {
      const prefix = `founder-${founder.id}`
      if (!founder.fullName.trim()) {
        nextErrors.push({ field: `${prefix}-fullName`, message: `Enter a full name for founder ${index + 1}.` })
      }
      if (founder.email.trim() && !isValidEmail(founder.email.trim())) {
        nextErrors.push({ field: `${prefix}-email`, message: `Enter a valid email for founder ${index + 1}.` })
      }
      if (founder.linkedinUrl.trim() && !isValidLinkedInProfile(founder.linkedinUrl.trim())) {
        nextErrors.push({ field: `${prefix}-linkedinUrl`, message: `Use an https://linkedin.com/in/… profile URL for founder ${index + 1}.` })
      }
      if (founder.githubUrl.trim() && !isValidGitHubProfile(founder.githubUrl.trim())) {
        nextErrors.push({ field: `${prefix}-githubUrl`, message: `Use an https://github.com/username profile URL for founder ${index + 1}.` })
      }
    })
    return nextErrors
  }

  const addFounder = () => {
    founderSequenceRef.current += 1
    setFounders((current) => [
      ...current,
      {
        id: `founder-${founderSequenceRef.current}`,
        fullName: '',
        roleTitle: '',
        email: '',
        linkedinUrl: '',
        githubUrl: '',
        previousCompanies: '',
        background: '',
      },
    ])
  }

  const updateFounder = (id: string, field: keyof Omit<FounderDraft, 'id'>, value: string) => {
    setFounders((current) => current.map((founder) => (
      founder.id === id ? { ...founder, [field]: value } : founder
    )))
    setErrors([])
  }

  const optionalApplicationInput = (): Omit<ApplicationInput, 'companyName' | 'deck' | 'idempotencyKey'> => ({
    ...(optionalText(website) ? { website: website.trim() } : {}),
    ...(optionalText(oneLinePitch) ? { oneLinePitch: oneLinePitch.trim() } : {}),
    ...(optionalText(companyLocation) ? { location: companyLocation.trim() } : {}),
    ...(optionalText(companyStage) ? { stage: companyStage.trim() } : {}),
    ...(optionalText(contactEmail) ? { contactEmail: contactEmail.trim() } : {}),
    ...(founders.length > 0 ? { founders: founders.map(mapFounderDraft) } : {}),
  })

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
      const optionalInput = optionalApplicationInput()
      const fingerprint = await applicationPayloadFingerprint(
        companyName,
        deck,
        JSON.stringify(optionalInput),
      )
      if (idempotencyRef.current?.fingerprint !== fingerprint) {
        idempotencyRef.current = { fingerprint, key: globalThis.crypto.randomUUID() }
      }
      const nextReceipt = await client.submitApplication({
        companyName: normalizeCompanyName(companyName),
        deck,
        idempotencyKey: idempotencyRef.current.key,
        ...optionalInput,
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

  const errorFor = (field: string) => errors.find((error) => error.field === field)?.message
  const companyError = errorFor('companyName')
  const deckErrors = errors.filter((error) => error.field === 'deck').map((error) => error.message)

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
                        {errors.length > 0 && <ul>{errors.map((error) => <li key={`${error.field}-${error.message}`}>{error.message}</li>)}</ul>}
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
                  onChange={(event) => {
                    setCompanyName(event.target.value)
                    setErrors([])
                  }}
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
              >
                <div className="deck-picker">
                  <input
                    ref={deckInputRef}
                    id="deck"
                    name="deck"
                    className="visually-hidden"
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
                  <label className="deck-picker__trigger" htmlFor="deck">
                    <UploadOutlined aria-hidden="true" />
                    <span>
                      <strong>{deck ? 'Replace pitch deck' : 'Choose pitch deck'}</strong>
                      <small id="deck-help">PDF · up to 10 MiB</small>
                    </span>
                  </label>
                  {deck && (
                    <div className="selected-deck" aria-live="polite">
                      <FileTextOutlined aria-hidden="true" />
                      <span><strong>{deck.name}</strong><small>{(deck.size / 1024 / 1024).toFixed(1)} MiB</small></span>
                      <Button
                        type="text"
                        htmlType="button"
                        icon={<DeleteOutlined aria-hidden="true" />}
                        aria-label={`Remove ${deck.name}`}
                        onClick={() => {
                          setDeck(null)
                          if (deckInputRef.current) deckInputRef.current.value = ''
                          setErrors((current) => current.filter((error) => error.field !== 'deck'))
                        }}
                      >
                        Remove
                      </Button>
                    </div>
                  )}
                  {deckErrors.length > 0 && (
                    <p id="deck-error" className="field-error">{deckErrors.join(' ')}</p>
                  )}
                </div>
              </Form.Item>

              <Collapse
                className="optional-intake-details"
                items={[
                  {
                    key: 'optional-founder-details',
                    label: 'Add founder details (optional)',
                    children: (
                      <div className="optional-intake-details__body">
                        <section aria-labelledby="optional-company-title">
                          <div className="optional-section-heading">
                            <h3 id="optional-company-title">Company context</h3>
                            <span>Optional</span>
                          </div>
                          <div className="optional-fields-grid">
                            <Form.Item label="Company website" htmlFor="company-website" extra="Prefer HTTPS; HTTP is accepted for local or development sites.">
                              <Input
                                id="company-website"
                                type="url"
                                value={website}
                                placeholder="https://example.com"
                                autoComplete="url"
                                aria-invalid={Boolean(errorFor('website'))}
                                aria-describedby={errorFor('website') ? 'company-website-error' : undefined}
                                onChange={(event) => {
                                  setWebsite(event.target.value)
                                  setErrors([])
                                }}
                              />
                              {errorFor('website') && <p id="company-website-error" className="field-error">{errorFor('website')}</p>}
                            </Form.Item>
                            <Form.Item label="Contact email" htmlFor="company-contact-email" extra="Private applicant data">
                              <Input
                                id="company-contact-email"
                                type="email"
                                value={contactEmail}
                                autoComplete="email"
                                aria-invalid={Boolean(errorFor('contactEmail'))}
                                aria-describedby={errorFor('contactEmail') ? 'company-contact-email-error' : undefined}
                                onChange={(event) => {
                                  setContactEmail(event.target.value)
                                  setErrors([])
                                }}
                              />
                              {errorFor('contactEmail') && <p id="company-contact-email-error" className="field-error">{errorFor('contactEmail')}</p>}
                            </Form.Item>
                            <Form.Item className="optional-field--wide" label="One-line pitch" htmlFor="company-pitch">
                              <Input
                                id="company-pitch"
                                value={oneLinePitch}
                                maxLength={180}
                                showCount
                                onChange={(event) => setOneLinePitch(event.target.value)}
                              />
                            </Form.Item>
                            <Form.Item label="Location" htmlFor="company-location">
                              <Input
                                id="company-location"
                                value={companyLocation}
                                autoComplete="address-level2"
                                onChange={(event) => setCompanyLocation(event.target.value)}
                              />
                            </Form.Item>
                            <Form.Item label="Stage" htmlFor="company-stage">
                              <Input
                                id="company-stage"
                                value={companyStage}
                                placeholder="Pre-seed, seed, or other"
                                onChange={(event) => setCompanyStage(event.target.value)}
                              />
                            </Form.Item>
                          </div>
                        </section>

                        <section aria-labelledby="optional-founders-title">
                          <div className="optional-section-heading">
                            <div>
                              <h3 id="optional-founders-title">Founders</h3>
                              <p>Add only what helps us identify the right people. No account is created.</p>
                            </div>
                            <Button type="default" htmlType="button" icon={<PlusOutlined aria-hidden="true" />} onClick={addFounder}>
                              Add founder
                            </Button>
                          </div>

                          <div className="founder-editors">
                            {founders.map((founder, index) => {
                              const prefix = `founder-${founder.id}`
                              return (
                                <fieldset className="founder-editor" key={founder.id}>
                                  <legend>Founder {index + 1}</legend>
                                  <Button
                                    className="founder-editor__remove"
                                    type="text"
                                    danger
                                    htmlType="button"
                                    icon={<DeleteOutlined aria-hidden="true" />}
                                    aria-label={`Remove founder ${index + 1}`}
                                    onClick={() => {
                                      setFounders((current) => current.filter((item) => item.id !== founder.id))
                                      setErrors([])
                                    }}
                                  >
                                    Remove
                                  </Button>
                                  <div className="optional-fields-grid">
                                    <Form.Item label="Full name" htmlFor={`${prefix}-full-name`} required>
                                      <Input
                                        id={`${prefix}-full-name`}
                                        value={founder.fullName}
                                        autoComplete="name"
                                        aria-invalid={Boolean(errorFor(`${prefix}-fullName`))}
                                        aria-describedby={errorFor(`${prefix}-fullName`) ? `${prefix}-full-name-error` : undefined}
                                        onChange={(event) => updateFounder(founder.id, 'fullName', event.target.value)}
                                      />
                                      {errorFor(`${prefix}-fullName`) && <p id={`${prefix}-full-name-error`} className="field-error">{errorFor(`${prefix}-fullName`)}</p>}
                                    </Form.Item>
                                    <Form.Item label="Role or title" htmlFor={`${prefix}-role-title`}>
                                      <Input
                                        id={`${prefix}-role-title`}
                                        value={founder.roleTitle}
                                        autoComplete="organization-title"
                                        onChange={(event) => updateFounder(founder.id, 'roleTitle', event.target.value)}
                                      />
                                    </Form.Item>
                                    <Form.Item label="Email" htmlFor={`${prefix}-email`} extra="Private applicant data">
                                      <Input
                                        id={`${prefix}-email`}
                                        type="email"
                                        value={founder.email}
                                        autoComplete="email"
                                        aria-invalid={Boolean(errorFor(`${prefix}-email`))}
                                        aria-describedby={errorFor(`${prefix}-email`) ? `${prefix}-email-error` : undefined}
                                        onChange={(event) => updateFounder(founder.id, 'email', event.target.value)}
                                      />
                                      {errorFor(`${prefix}-email`) && <p id={`${prefix}-email-error`} className="field-error">{errorFor(`${prefix}-email`)}</p>}
                                    </Form.Item>
                                    <Form.Item label="LinkedIn URL" htmlFor={`${prefix}-linkedin`}>
                                      <Input
                                        id={`${prefix}-linkedin`}
                                        type="url"
                                        value={founder.linkedinUrl}
                                        placeholder="https://www.linkedin.com/in/..."
                                        aria-invalid={Boolean(errorFor(`${prefix}-linkedinUrl`))}
                                        aria-describedby={errorFor(`${prefix}-linkedinUrl`) ? `${prefix}-linkedin-error` : undefined}
                                        onChange={(event) => updateFounder(founder.id, 'linkedinUrl', event.target.value)}
                                      />
                                      {errorFor(`${prefix}-linkedinUrl`) && <p id={`${prefix}-linkedin-error`} className="field-error">{errorFor(`${prefix}-linkedinUrl`)}</p>}
                                    </Form.Item>
                                    <Form.Item label="GitHub URL" htmlFor={`${prefix}-github`}>
                                      <Input
                                        id={`${prefix}-github`}
                                        type="url"
                                        value={founder.githubUrl}
                                        placeholder="https://github.com/..."
                                        aria-invalid={Boolean(errorFor(`${prefix}-githubUrl`))}
                                        aria-describedby={errorFor(`${prefix}-githubUrl`) ? `${prefix}-github-error` : undefined}
                                        onChange={(event) => updateFounder(founder.id, 'githubUrl', event.target.value)}
                                      />
                                      {errorFor(`${prefix}-githubUrl`) && <p id={`${prefix}-github-error`} className="field-error">{errorFor(`${prefix}-githubUrl`)}</p>}
                                    </Form.Item>
                                    <Form.Item label="Previous companies" htmlFor={`${prefix}-previous-companies`} extra="Comma-separated">
                                      <Input
                                        id={`${prefix}-previous-companies`}
                                        value={founder.previousCompanies}
                                        onChange={(event) => updateFounder(founder.id, 'previousCompanies', event.target.value)}
                                      />
                                    </Form.Item>
                                    <Form.Item className="optional-field--wide" label="Short background" htmlFor={`${prefix}-background`}>
                                      <Input.TextArea
                                        id={`${prefix}-background`}
                                        value={founder.background}
                                        rows={3}
                                        maxLength={500}
                                        showCount
                                        onChange={(event) => updateFounder(founder.id, 'background', event.target.value)}
                                      />
                                    </Form.Item>
                                  </div>
                                </fieldset>
                              )
                            })}
                          </div>
                        </section>
                      </div>
                    ),
                  },
                ]}
              />

              <Alert
                className="authorization-note"
                type="info"
                showIcon
                icon={<SafetyCertificateOutlined />}
                title="Authorized deck sharing"
                description="Submit only material you are authorized to share. The deck and optional contact details stay private."
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
            <ol className="intake-steps">
              <li><span>1</span><div><strong>Stored safely</strong><p>Your deck is persisted before processing begins.</p></div></li>
              <li><span>2</span><div><strong>Evidence-aware review</strong><p>Sourced facts stay distinct from unresolved gaps.</p></div></li>
              <li><span>3</span><div><strong>Private status</strong><p>Your capability link shows timing and focused requests only.</p></div></li>
            </ol>
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
