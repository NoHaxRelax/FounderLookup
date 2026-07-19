import {
  CheckCircle2,
  Clock3,
  FileKey2,
  FileText,
  Inbox,
  LockKeyhole,
  ShieldCheck,
  UploadCloud,
} from 'lucide-react'
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
    <section className="receipt-card" aria-labelledby="receipt-title">
      <div className="receipt-card__success"><CheckCircle2 aria-hidden="true" /></div>
      <StatusBadge tone={status.targetState === 'missed' ? 'warning' : 'positive'}>
        {status.stage}
      </StatusBadge>
      <h2 id="receipt-title">
        {receipt ? 'Your application is safely in the queue' : 'Private founder status'}
      </h2>
      <p>
        This bounded view contains receipt, stage, timing, and focused requests only. It does not
        expose investor scoring. No investment decision was made.
      </p>

      <dl className="status-steps">
        <div>
          <dt><ShieldCheck aria-hidden="true" /> Application</dt>
          <dd>{status.stage}</dd>
        </div>
        <div>
          <dt><Clock3 aria-hidden="true" /> Review target</dt>
          <dd>{status.targetLabel}</dd>
        </div>
        <div>
          <dt><FileText aria-hidden="true" /> Focused request</dt>
          <dd>{status.focusedRequest ?? 'None'}</dd>
        </div>
      </dl>

      {receipt && (
        <div className="capability-card">
          <FileKey2 aria-hidden="true" />
          <div>
            <h3>Keep your private status link</h3>
            <p>Anyone with this capability can view the bounded founder status. Do not share it publicly.</p>
            <a href={receipt.founderStatusUrl}>Open private status</a>
          </div>
        </div>
      )}

      <p className="receipt-id">
        Application <code>{status.applicationId}</code>
        {receipt && <> · Run <code>{receipt.runId}</code></>}
      </p>
    </section>
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
          <div className="apply-hero__icon"><FileKey2 aria-hidden="true" /></div>
          <p className="eyebrow">Capability-scoped view</p>
          <h1 data-page-title tabIndex={-1}>Check your application status</h1>
          <p className="lede">The capability stays in this browser route and is sent only in the status header.</p>
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

  return (
    <div className="page page--narrow">
      <header className="apply-hero">
        <div className="apply-hero__icon"><Inbox aria-hidden="true" /></div>
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
          <form className="intake-form" onSubmit={submit} noValidate>
            {(errors.length > 0 || submitError) && (
              <div className="error-summary" role="alert" tabIndex={-1} ref={errorSummaryRef}>
                <h2>{submitError ? 'Application not submitted' : 'Check the application'}</h2>
                {submitError && <p>{submitError} Your entries and retry key were preserved.</p>}
                {errors.length > 0 && <ul>{errors.map((error) => <li key={error}>{error}</li>)}</ul>}
              </div>
            )}

            <div className="field-group">
              <label htmlFor="company-name">Company name <span aria-hidden="true">*</span></label>
              <input
                id="company-name"
                name="companyName"
                value={companyName}
                onChange={(event) => setCompanyName(event.target.value)}
                autoComplete="organization"
                required
              />
            </div>

            <div className="field-group">
              <label htmlFor="deck">Pitch deck (PDF) <span aria-hidden="true">*</span></label>
              <div className="file-drop">
                <UploadCloud aria-hidden="true" />
                <div>
                  <strong>{deck ? deck.name : 'Choose a PDF deck'}</strong>
                  <span>{deck ? `${(deck.size / 1024 / 1024).toFixed(1)} MiB selected` : 'Maximum 10 MiB'}</span>
                </div>
                <input
                  id="deck"
                  name="deck"
                  type="file"
                  accept="application/pdf,.pdf"
                  required
                  onChange={(event) => {
                    setDeck(event.target.files?.[0] ?? null)
                    setErrors([])
                  }}
                />
              </div>
            </div>

            <div className="authorization-note">
              <ShieldCheck aria-hidden="true" />
              <p>
                By submitting, you confirm you are authorized to share this deck for investment
                review. Founder-private evidence stays distinct from public and investor-internal sources.
              </p>
            </div>

            <button className="button button--primary button--large" type="submit" disabled={submitting}>
              {submitting ? 'Storing application…' : 'Submit application'}
            </button>
            <p className="form-footnote"><LockKeyhole aria-hidden="true" /> Submission does not imply approval or funding.</p>
          </form>

          <aside className="intake-assurance" aria-labelledby="what-happens-title">
            <h2 id="what-happens-title">What happens next</h2>
            <ol>
              <li><span>1</span><div><strong>Persist first</strong><p>Your deck and source metadata are stored before processing.</p></div></li>
              <li><span>2</span><div><strong>Evidence-aware review</strong><p>Claims stay linked to locators; gaps remain Unknown.</p></div></li>
              <li><span>3</span><div><strong>Bounded status</strong><p>Your private link shows receipt, stage, timing, and focused requests only.</p></div></li>
            </ol>
            <div className="privacy-note">
              <LockKeyhole aria-hidden="true" />
              <p>Investor scores, other opportunities, and internal notes never appear in founder status.</p>
            </div>
          </aside>
        </div>
      )}
    </div>
  )
}
