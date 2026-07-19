import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { FixtureFounderLookupClient } from './api/client'

describe('FounderLookup starter workflow', () => {
  afterEach(() => cleanup())

  beforeEach(() => {
    globalThis.location.hash = ''
  })

  it('interprets a compound query without hiding unknown values', async () => {
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Find signals, keep uncertainty' })).toBeVisible()
    expect(
      (screen.getByLabelText(/Describe the founder and opportunity/i) as HTMLTextAreaElement).value,
    ).toContain('technical founder, Berlin, AI infra')
    expect(screen.getByLabelText('Unknown handling')).toHaveValue('include_unknown')
    expect(screen.getByText(/search silence is not proof/i)).toBeVisible()
    expect(screen.getAllByText(/Unknown:/i).length).toBeGreaterThan(0)
  })

  it('edits and explicitly saves a thesis draft without silently reclassifying results', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('heading', { name: 'Find signals, keep uncertainty' })

    await user.click(screen.getByText('Active thesis'))
    const geography = screen.getByLabelText('Value', { selector: '#thesis-geography-value' })
    await user.clear(geography)
    await user.type(geography, 'Europe')
    await user.click(screen.getByRole('button', { name: 'Save thesis draft' }))

    expect(screen.getByText(/Thesis draft saved locally/i)).toBeVisible()
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
    expect(
      within(nonReadyOutboundCard!).queryByRole('button', { name: 'Activate candidate' }),
    ).not.toBeInTheDocument()
    expect(
      within(nonReadyOutboundCard!).getByText(/preliminary assessment did not mark this candidate ready/i),
    ).toBeVisible()

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
    expect(within(outboundCard!).getByText('Candidate already activated')).toBeVisible()
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

    const dialog = screen.getByRole('dialog', { name: 'Activate Lantern Runtime?' })
    const draft = within(dialog).getByLabelText('Optional outreach draft')
    await user.clear(draft)
    await user.type(draft, 'Keep this reviewed draft after a failed command.')
    await user.click(within(dialog).getByRole('button', { name: 'Confirm activation · do not send' }))

    expect(await within(dialog).findByRole('heading', { name: 'Candidate not activated' })).toBeVisible()
    expect(dialog).toBeVisible()
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
      name: 'Founder score and three axes',
    })
    expect(within(assessment).getByText('72')).toBeVisible()
    expect(within(assessment).getByRole('heading', { name: 'Founder' })).toBeVisible()
    expect(within(assessment).getByRole('heading', { name: 'Market' })).toBeVisible()
    expect(within(assessment).getByRole('heading', { name: 'Idea vs market' })).toBeVisible()
    expect(screen.getByText(/Axes are not averaged into the founder score/i)).toBeVisible()
  })

  it('opens exact evidence and source locators in a native dialog', async () => {
    const user = userEvent.setup()
    globalThis.location.hash = '#/opportunity'
    render(<App />)
    await screen.findByRole('heading', { name: 'Sable Systems', level: 1 })

    const tractionClaim = screen
      .getByRole('heading', { name: 'Sable Systems has three paid enterprise pilots.' })
      .closest('article')
    expect(tractionClaim).not.toBeNull()
    await user.click(within(tractionClaim!).getByRole('button', { name: /EvidencePage 8 · Traction/i }))

    const dialog = screen.getByRole('dialog', { name: 'Sable Systems seed deck' })
    expect(dialog).toBeVisible()
    expect(within(dialog).getByRole('heading', { name: 'Source locator' })).toBeVisible()
    expect(within(dialog).getByText(/Three paid enterprise pilots/i)).toBeVisible()
    expect(within(dialog).getByText('artifact-deck-sha256-71d0')).toBeVisible()
  })

  it('previews empty, error, and blocked UX states deterministically', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('heading', { name: 'Find signals, keep uncertainty' })

    await user.click(screen.getByText('Demo state preview'))
    const stateSelect = screen.getByLabelText('Page state')

    await user.selectOptions(stateSelect, 'empty')
    expect(screen.getByRole('heading', { name: /No candidate results match/i })).toBeVisible()

    await user.selectOptions(stateSelect, 'error')
    expect(screen.getByRole('heading', { name: /Could not load candidate results/i })).toBeVisible()

    await user.selectOptions(stateSelect, 'blocked')
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
    await user.click(screen.getByRole('button', { name: 'Submit application' }))

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

    expect(screen.getByText('The deck must be 10 MiB or smaller.')).toBeVisible()
  })

  it('requires human confirmation and states that a decision moves no funds', async () => {
    const user = userEvent.setup()
    globalThis.location.hash = '#/memo'
    render(<App />)
    await screen.findByRole('heading', { name: 'Sable Systems', level: 1 })

    expect(screen.getByRole('heading', { name: 'needs information' })).toBeVisible()
    expect(screen.queryByRole('radio', { name: /Advance/i })).not.toBeInTheDocument()
    expect(screen.getByText(/Advance is unavailable until Decision Readiness is Ready/i)).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Review decision' }))
    expect(screen.getByRole('dialog', { name: /Record “request more information”/i })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Record decision · no funds move' }))

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
    await user.type(rationale, ' Preserve this reviewed rationale after failure.')
    const preservedRationale =
      'Resolve the paid-versus-unpaid pilot contradiction before advancing. Preserve this reviewed rationale after failure.'
    await user.click(screen.getByRole('button', { name: 'Review decision' }))

    const dialog = screen.getByRole('dialog', {
      name: /Record “request more information”/i,
    })
    await user.click(within(dialog).getByRole('button', { name: 'Record decision · no funds move' }))

    expect(await within(dialog).findByRole('heading', { name: 'Decision not recorded' })).toBeVisible()
    expect(dialog).toBeVisible()
    expect(rationale).toHaveValue(preservedRationale)
    expect(recordDecision).toHaveBeenCalledOnce()
    await waitFor(() =>
      expect(document.querySelector('[aria-live="polite"]')).toHaveTextContent(
        'The decision was not recorded.',
      ),
    )
    expect(document.body).not.toHaveTextContent('sensitive persistence failure')
  })
})
