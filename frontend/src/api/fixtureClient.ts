import type {
  ActivationReceipt,
  ApplicationInput,
  ApplicationReceipt,
  DecisionInput,
  DecisionReceipt,
  FounderLookupClient,
  FounderStatusView,
  OpportunityDetail,
  SearchInput,
  SearchResponse,
  StableId,
  WorkspaceFixture,
} from './types'
import {
  opportunityFixture,
  searchFixture,
  workspaceFixture,
} from '../fixtures/vcBrainFixture'

const pause = (milliseconds = 80) =>
  new Promise<void>((resolve) => globalThis.setTimeout(resolve, milliseconds))

const clone = <T>(value: T): T => structuredClone(value)

export class FixtureFounderLookupClient implements FounderLookupClient {
  readonly runtime = 'fixture' as const

  async getWorkspace(): Promise<WorkspaceFixture> {
    await pause()
    return clone(workspaceFixture)
  }

  async searchOpportunities(input: SearchInput): Promise<SearchResponse> {
    await pause(180)
    const normalizedQuery = input.query.trim().toLocaleLowerCase()
    const removed = new Set(input.removedCriterionIds ?? [])
    const response = clone(searchFixture)

    response.plan.rawQuery = input.query.trim()
    response.plan.execution.rawQuery = input.query.trim()
    response.plan.criteria = response.plan.criteria.filter((item) => !removed.has(item.id))
    response.plan.execution.criteria = response.plan.execution.criteria.filter(
      (item) => !removed.has(item.criterionId),
    )

    if (normalizedQuery.includes('no-result-demo')) {
      response.results = []
      response.totalConsidered = 0
      return response
    }

    response.results = response.results.filter((candidate) => {
      if (input.filters.origin !== 'all' && candidate.origin !== input.filters.origin) {
        return false
      }

      if (input.filters.knowledgeHandling === 'known_only') {
        return candidate.unknownFields.length === 0
      }

      if (input.filters.knowledgeHandling === 'needs_information') {
        return candidate.unknownFields.length > 0
      }

      return true
    })

    return response
  }

  async getOpportunity(opportunityId: StableId): Promise<OpportunityDetail> {
    await pause()
    if (opportunityId !== opportunityFixture.id) {
      throw new Error(`Fixture opportunity ${opportunityId} is unavailable.`)
    }
    return clone(opportunityFixture)
  }

  async submitApplication(input: ApplicationInput): Promise<ApplicationReceipt> {
    await pause(220)
    const suffix = input.idempotencyKey.replaceAll(/[^a-zA-Z0-9]/g, '').slice(-8) || 'fixture'
    return {
      applicationId: `application-${suffix}`,
      companyId: `company-${suffix}`,
      runId: `run-intake-${suffix}`,
      sourceArtifactId: `artifact-${suffix}`,
      receivedAt: '2026-07-18T09:04:00Z',
      status: 'received',
      founderStatusCapability: `founder-status-${suffix}`,
      founderStatusUrl: `#/apply/status/founder-status-${suffix}`,
      replayed: false,
    }
  }

  async getFounderStatus(capability: string): Promise<FounderStatusView> {
    await pause()
    return {
      applicationId: `application-${capability.slice(-8)}`,
      receivedAt: '2026-07-18T09:04:00Z',
      stage: 'Evidence review queued',
      lastUpdatedAt: '2026-07-18T09:04:18Z',
      targetState: 'on_track',
      targetLabel: 'Initial review target · within 24 hours',
      informationRequests: [],
      focusedRequest: 'No additional information requested yet.',
      nextAction: 'We are processing the submitted deck.',
    }
  }

  async activateCandidate(candidateId: StableId, outreachDraft: string): Promise<ActivationReceipt> {
    await pause(120)
    return {
      candidateId,
      state: 'activated',
      activatedAt: '2026-07-18T09:06:00Z',
      outreachDraft,
    }
  }

  async recordDecision(input: DecisionInput): Promise<DecisionReceipt> {
    await pause(160)
    return {
      decisionId: 'decision-sable-human-01',
      disposition: input.disposition,
      actorLabel: 'Current investor reviewer',
      decidedAt: '2026-07-18T09:10:00Z',
    }
  }
}

export const fixtureClient = new FixtureFounderLookupClient()
