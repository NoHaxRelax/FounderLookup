import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { FixtureFounderLookupClient } from './api/client'
import type { FounderLookupClient, InvestorAccessController } from './api/types'

describe('FounderLookup starter workflow', () => {
  afterEach(() => cleanup())

  beforeEach(() => {
    globalThis.location.hash = '#/sourcing'
  })

  it('opens on a focused landing page with exactly two product paths', async () => {
    const client = new FixtureFounderLookupClient()
    const getWorkspace = vi.spyOn(client, 'getWorkspace')
    globalThis.location.hash = '#/'

    render(<App client={client} />)

    expect(await screen.findByRole('heading', { name: 'Find overlooked founders. Decide from Evidence.' })).toBeVisible()
    const paths = screen.getByRole('navigation', { name: 'Choose how to continue' })
    expect(within(paths).getByRole('link', { name: 'Enter investor workspace' })).toHaveAttribute('href', '#/sourcing')
    expect(within(paths).getByRole('link', { name: 'Start founder application' })).toHaveAttribute('href', '#/apply')
    expect(within(paths).getAllByRole('link')).toHaveLength(2)
    expect(screen.getByText(/Private by design/i)).toBeVisible()
    expect(getWorkspace).not.toHaveBeenCalled()
  })

  it('interprets a compound query without hiding unknown values', async () => {
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Find signals, keep uncertainty' })).toBeVisible()
    expect(
      (screen.getByLabelText(/Describe the founder and opportunity/i) as HTMLTextAreaElement).value,
    ).toContain('technical founder, Berlin, AI infra')
    const refineSearch = screen.getByRole('button', { name: /Refine search/i })
    expect(refineSearch).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(refineSearch)
    const unknownHandling = screen.getByLabelText('Unknown handling')
    expect(unknownHandling).toHaveAttribute('role', 'combobox')
    expect(unknownHandling.closest('.ant-select')).toHaveTextContent('Include Unknown')
    expect(screen.getByText(/Search silence never proves a negative/i)).toBeVisible()
    expect(screen.getAllByText(/Unknown:/i).length).toBeGreaterThan(0)
  })

  it('edits and explicitly saves a thesis draft without silently reclassifying results', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('heading', { name: 'Find signals, keep uncertainty' })

    await user.click(screen.getByText('Edit active thesis'))
    const sector = screen.getByLabelText('Value', { selector: '#thesis-sector-value' })
    await user.clear(sector)
    await user.type(sector, 'AI systems')
    await user.click(screen.getByRole('button', { name: 'Save thesis revision' }))

    expect(await screen.findByText(/Thesis revision fixture-thesis-saved-v2 saved/i)).toBeVisible()
  })

  it('only activates backend-ready outbound candidates and persists the edited outreach draft', async () => {
    const user = userEvent.setup()
    const client = new FixtureFounderLookupClient()
    const activate = vi.spyOn(client, 'activateCandidate')
    render(<App client={client} />)
    await screen.findByRole('heading', { name: 'Find signals, keep uncertainty' })

    const inboundCard = screen.getByRole('heading', { name: 'Sable Systems' }).closest('article')
    const outboundCard = screen.getByRole('heading', { name: 'Lantern Runtime' }).closest('article')
    const nonReadyOutboundCard = screen.getByRole('heading', { name: 'Cedar Evals' }).closest('article')
    expect(inboundCard).not.toBeNull()
    expect(outboundCard).not.toBeNull()
    expect(nonReadyOutboundCard).not.toBeNull()
    expect(within(inboundCard!).queryByRole('button', { name: 'Activate candidate' })).not.toBeInTheDocument()
    expect(within(nonReadyOutboundCard!).queryByRole('button', { name: 'Activate candidate' })).not.toBeInTheDocument()
    expect(within(nonReadyOutboundCard!).getByRole('button', { name: 'Run preliminary assessment' })).toBeVisible()

    const publicRoutes = within(outboundCard!).getByRole('button', { name: /Public follow-up routes/i })
    expect(publicRoutes).toHaveAttribute('aria-expanded', 'false')
    await user.click(publicRoutes)
    const suppliedLinks = within(outboundCard!)
      .getAllByText('Open supplied route')
      .map((label) => label.closest('a'))
    expect(suppliedLinks).toHaveLength(2)
    expect(suppliedLinks[0]).toHaveAttribute('href', 'https://lantern-runtime.example/')
    expect(suppliedLinks[1]).toHaveAttribute('href', 'mailto:hello@lantern-runtime.example')
    expect(within(outboundCard!).getAllByText('Public product launch').length).toBeGreaterThan(0)
    expect(
      within(outboundCard!).getAllByText('artifact-lantern-launch-2026-07-18').length,
    ).toBeGreaterThan(0)
    expect(within(outboundCard!).getAllByText('2 of 3').length).toBeGreaterThan(0)
    expect(
      within(outboundCard!).getAllByText(/satisfied the bounded source plan/i).length,
    ).toBeGreaterThan(0)
    await user.click(publicRoutes)

    await user.click(within(outboundCard!).getByRole('button', { name: 'Activate candidate' }))
    const draft = screen.getByLabelText('Optional outreach draft')
    await user.clear(draft)
    await user.type(draft, 'Edited human-reviewed outreach draft.')
    await user.click(screen.getByRole('button', { name: 'Confirm activation · do not send' }))

    expect(activate).toHaveBeenCalledWith(
      'candidate-lantern-runtime',
      'Edited human-reviewed outreach draft.',
    )
    expect(await screen.findByText(/outreach draft was saved but not sent/i)).toBeVisible()
    expect(within(outboundCard!).queryByRole('button', { name: 'Activate candidate' })).not.toBeInTheDocument()
    expect(within(outboundCard!).getByRole('button', { name: 'Record outreach' })).toBeVisible()
  })

  it('keeps the activation dialog and edited draft when activation fails safely', async () => {
    const user = userEvent.setup()
    const client = new FixtureFounderLookupClient()
    const activate = vi
      .spyOn(client, 'activateCandidate')
      .mockRejectedValueOnce(new Error('sensitive adapter response'))
    render(<App client={client} />)
    await screen.findByRole('heading', { name: 'Find signals, keep uncertainty' })

    const candidateCard = screen.getByRole('heading', { name: 'Lantern Runtime' }).closest('article')
    expect(candidateCard).not.toBeNull()
    await user.click(within(candidateCard!).getByRole('button', { name: 'Activate candidate' }))

    const dialog = await screen.findByRole('dialog', { hidden: true })
    expect(within(dialog).getByRole('heading', { name: 'Activate Lantern Runtime?' })).toBeInTheDocument()
    const draft = within(dialog).getByLabelText('Optional outreach draft')
    await user.clear(draft)
    await user.type(draft, 'Keep this reviewed draft after a failed command.')
    await user.click(within(dialog).getByRole('button', { name: 'Confirm activation · do not send' }))

    expect(await within(dialog).findByRole('heading', { name: 'Candidate not activated' })).toBeInTheDocument()
    expect(dialog).toBeInTheDocument()
    expect(draft).toHaveValue('Keep this reviewed draft after a failed command.')
    expect(activate).toHaveBeenCalledOnce()
    await waitFor(() =>
      expect(document.querySelector('[aria-live="polite"]')).toHaveTextContent(
        'The candidate was not activated.',
      ),
    )
    expect(document.body).not.toHaveTextContent('sensitive adapter response')
  })

  it('keeps the founder score separate from the three independent axes', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('heading', { name: 'Find signals, keep uncertainty' })

    await user.click(screen.getByRole('link', { name: /OpportunityClaims and evidence/i }))

    const assessment = await screen.findByRole('region', {
      name: 'Founder Score and three independent axes',
    })
    expect(screen.getByRole('button', { name: /Understand the Recommendation/i })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    expect(screen.getByRole('button', { name: /Audit Claims, Evidence, Trust, and runs/i })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    expect(within(assessment).getByText('72')).toBeVisible()
    expect(within(assessment).getByRole('heading', { name: 'Founder' })).toBeVisible()
    expect(within(assessment).getByRole('heading', { name: 'Market' })).toBeVisible()
    expect(within(assessment).getByRole('heading', { name: 'Idea vs market' })).toBeVisible()
    expect(screen.getByText(/Axes are never averaged into.*Founder Score/i)).toBeVisible()
  })

  it('opens exact evidence and source locators in an accessible dialog', async () => {
    const user = userEvent.setup()
    globalThis.location.hash = '#/opportunity'
    render(<App />)
    await screen.findByRole('heading', { name: 'Sable Systems', level: 1 })

    await user.click(screen.getByText(/Audit Claims, Evidence, Trust, and runs/i))

    const tractionClaim = screen
      .getByRole('heading', { name: 'Sable Systems has three paid enterprise pilots.' })
      .closest('article')
    expect(tractionClaim).not.toBeNull()
    await user.click(within(tractionClaim!).getByRole('button', { name: /Evidence Page 8 · Traction/i }))

    const dialog = await screen.findByRole('dialog', { hidden: true })
    expect(within(dialog).getByRole('heading', { name: 'Sable Systems seed deck' })).toBeInTheDocument()
    expect(dialog).toBeInTheDocument()
    expect(within(dialog).getByRole('heading', { name: 'Source locator' })).toBeInTheDocument()
    expect(dialog).toHaveTextContent(/Three paid enterprise pilots/i)
    expect(dialog).toHaveTextContent('artifact-deck-sha256-71d0')
  }, 10_000)

  it('previews empty, error, and blocked UX states deterministically', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('heading', { name: 'Find signals, keep uncertainty' })

    await user.click(screen.getByText('Fixture state preview'))
    const stateSelect = screen.getByLabelText('Page state')

    stateSelect.focus()
    await user.keyboard('{Enter}')
    await user.click(await screen.findByRole('option', { name: 'Empty' }))
    expect(screen.getByRole('heading', { name: /No candidate results match/i })).toBeVisible()

    await user.keyboard('{Enter}')
    await user.click(await screen.findByRole('option', { name: 'Error' }))
    expect(screen.getByRole('heading', { name: /Could not load candidate results/i })).toBeVisible()

    await user.keyboard('{Enter}')
    await user.click(await screen.findByRole('option', { name: 'Blocked' }))
    expect(screen.getByRole('heading', { name: /candidate results blocked at the last safe stage/i })).toBeVisible()
  })

  it('accepts the minimum founder intake and returns bounded private status', async () => {
    const user = userEvent.setup()
    const client = new FixtureFounderLookupClient()
    const originalSubmit = client.submitApplication.bind(client)
    const submit = vi
      .spyOn(client, 'submitApplication')
      .mockRejectedValueOnce(new Error('Network unavailable.'))
      .mockImplementation(originalSubmit)
    globalThis.location.hash = '#/apply'
    render(<App client={client} />)
    await screen.findByRole('heading', { name: 'Start with your company and deck' })

    await user.type(screen.getByLabelText(/Company name/i), 'Orchid Compute')
    expect(screen.getByText('Maximum 10 MiB')).toBeVisible()
    const deck = new File(['fixture deck'], 'orchid-deck.pdf', { type: 'application/pdf' })
    const readDeck = deck.arrayBuffer.bind(deck)
    vi.spyOn(deck, 'arrayBuffer').mockImplementationOnce(async () => {
      await new Promise((resolve) => globalThis.setTimeout(resolve, 1_100))
      return readDeck()
    })
    await user.upload(screen.getByLabelText(/Pitch deck/i), deck)
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Submit application' }))

    await waitFor(
      () => {
        expect(submit).toHaveBeenCalledTimes(1)
        expect(
          screen.getByRole('heading', { name: 'Application not submitted' }),
        ).toBeVisible()
      },
      { timeout: 3_000 },
    )
    expect(screen.getByText(/Network unavailable/i)).toBeVisible()
    const retryButton = screen.getByRole('button', { name: 'Submit application' })
    await waitFor(() => expect(retryButton).toBeEnabled())
    await user.click(retryButton)

    expect(await screen.findByRole('heading', { name: /safely in the queue/i })).toBeVisible()
    expect(screen.getByText(/Initial review target · within 24 hours/i)).toBeVisible()
    expect(screen.getByText(/No investment decision was made/i)).toBeVisible()
    expect(screen.getByRole('link', { name: 'Open private status' })).toBeVisible()
    expect(submit).toHaveBeenCalledTimes(2)
    expect(submit.mock.calls[0]?.[0].idempotencyKey).toBe(submit.mock.calls[1]?.[0].idempotencyKey)
  })

  it('restores a founder status hash through the capability-scoped client call only', async () => {
    const capability = 'private/status-capability'
    const client = new FixtureFounderLookupClient()
    const workspace = vi.spyOn(client, 'getWorkspace')
    const getFounderStatus = vi.spyOn(client, 'getFounderStatus')
    globalThis.location.hash = `#/apply/status/${encodeURIComponent(capability)}`

    render(<App client={client} />)

    expect(
      await screen.findByRole('heading', { name: 'Check your application status' }),
    ).toBeVisible()
    expect(await screen.findByRole('heading', { name: 'Private founder status' })).toBeVisible()
    expect(getFounderStatus).toHaveBeenCalledOnce()
    expect(getFounderStatus).toHaveBeenCalledWith(capability)
    expect(workspace).not.toHaveBeenCalled()
    expect(document.body).not.toHaveTextContent(capability)
  })

  it('rejects a PDF above the backend-aligned 10 MiB intake limit', async () => {
    const user = userEvent.setup()
    globalThis.location.hash = '#/apply'
    render(<App />)
    await screen.findByRole('heading', { name: 'Start with your company and deck' })

    await user.type(screen.getByLabelText(/Company name/i), 'Oversize Labs')
    const oversizedDeck = new File(['fixture'], 'oversized.pdf', { type: 'application/pdf' })
    Object.defineProperty(oversizedDeck, 'size', { value: 10 * 1024 * 1024 + 1 })
    await user.upload(screen.getByLabelText(/Pitch deck/i), oversizedDeck)
    await user.click(screen.getByRole('button', { name: 'Submit application' }))

    expect(screen.getAllByText('The deck must be 10 MiB or smaller.').length).toBeGreaterThan(0)
  })

  it('requires human confirmation and states that a decision moves no funds', async () => {
    const user = userEvent.setup()
    globalThis.location.hash = '#/memo'
    render(<App />)
    await screen.findByRole('heading', { name: 'Sable Systems', level: 1 })

    expect(screen.getByRole('heading', { name: 'needs information' })).toBeVisible()
    expect(screen.queryByRole('radio', { name: /Advance/i })).not.toBeInTheDocument()
    expect(screen.getByText(/Advance is unavailable until Decision Readiness is Ready/i)).toBeVisible()
    expect(screen.getByRole('button', { name: /Audit the cited memo/i })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    await user.click(screen.getByRole('button', { name: 'Review Decision' }))
    expect(screen.getByRole('dialog', { name: /Record “request more information”/i })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Record Decision · no funds move' }))

    expect(await screen.findByRole('heading', { name: 'Decision recorded' })).toBeVisible()
    expect(screen.getByText(/No outreach was sent and no funds moved/i)).toBeVisible()
  })

  it('keeps decision confirmation and rationale intact when recording fails safely', async () => {
    const user = userEvent.setup()
    const client = new FixtureFounderLookupClient()
    const recordDecision = vi
      .spyOn(client, 'recordDecision')
      .mockRejectedValueOnce(new Error('sensitive persistence failure'))
    globalThis.location.hash = '#/memo'
    render(<App client={client} />)
    const pageTitle = await screen.findByRole('heading', { name: 'Sable Systems', level: 1 })
    await waitFor(() => expect(pageTitle).toHaveFocus())

    const rationale = screen.getByLabelText('Rationale')
    const initialRationale = (rationale as HTMLTextAreaElement).value
    await user.type(rationale, ' Preserve this reviewed rationale after failure.')
    const preservedRationale = `${initialRationale} Preserve this reviewed rationale after failure.`
    await user.click(screen.getByRole('button', { name: 'Review Decision' }))

    const dialog = screen.getByRole('dialog', {
      name: /Record “request more information”/i,
    })
    await user.click(within(dialog).getByRole('button', { name: 'Record Decision · no funds move' }))

    expect(await within(dialog).findByRole('heading', { name: 'Decision not recorded' })).toBeInTheDocument()
    expect(dialog).toBeInTheDocument()
    expect(rationale).toHaveValue(preservedRationale)
    expect(recordDecision).toHaveBeenCalledOnce()
    await waitFor(() =>
      expect(document.querySelector('[aria-live="polite"]')).toHaveTextContent(
        /The Decision was not recorded/i,
      ),
    )
    expect(document.body).not.toHaveTextContent('sensitive persistence failure')
  })

  it('keeps founder routes public and outside the investor shell', async () => {
    const client = new FixtureFounderLookupClient()
    const workspace = vi.spyOn(client, 'getWorkspace')
    globalThis.location.hash = '#/apply'

    render(<App client={client} />)

    expect(await screen.findByRole('heading', { name: 'Start with your company and deck' })).toBeVisible()
    expect(screen.queryByRole('navigation', { name: /Investor workspace/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/Fixture state preview/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/analyst/i)).not.toBeInTheDocument()
    expect(workspace).not.toHaveBeenCalled()
  })

  it('gates only the HTTP investor workspace with a session-scoped access key', async () => {
    const user = userEvent.setup()
    const fixture = new FixtureFounderLookupClient()
    let credential = ''
    const investorAccess: InvestorAccessController = {
      hasCredential: () => Boolean(credential),
      getCredential: () => credential || undefined,
      setCredential: (next) => { credential = next },
      clearCredential: () => { credential = '' },
    }
    const client = new Proxy(fixture, {
      get(target, property) {
        if (property === 'runtime') return 'http'
        if (property === 'investorAccess') return investorAccess
        const value = Reflect.get(target, property)
        return typeof value === 'function' ? value.bind(target) : value
      },
    }) as unknown as FounderLookupClient
    const workspace = vi.spyOn(fixture, 'getWorkspace')

    render(<App client={client} />)

    expect(await screen.findByRole('heading', { name: 'Enter your access key' })).toBeVisible()
    expect(workspace).not.toHaveBeenCalled()
    await user.type(screen.getByLabelText('Investor access key'), 'session-only-key')
    await user.click(screen.getByRole('button', { name: 'Unlock investor workspace' }))
    expect(await screen.findByRole('heading', { name: 'Find signals, keep uncertainty' })).toBeVisible()
    expect(credential).toBe('session-only-key')
    expect(screen.queryByRole('link', { name: /Founder apply/i })).not.toBeInTheDocument()
  })

  it('uses stable Opportunity IDs in detail and memo routes', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('heading', { name: 'Find signals, keep uncertainty' })

    const opportunityLink = screen.getByRole('link', { name: /OpportunityClaims and evidence/i })
    expect(opportunityLink).toHaveAttribute('href', '#/opportunity/opportunity-sable-systems')
    await user.click(opportunityLink)
    expect(await screen.findByRole('heading', { name: 'Sable Systems', level: 1 })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Review memo & decide' })).toHaveAttribute(
      'href',
      '#/memo/opportunity-sable-systems',
    )
  })
})
