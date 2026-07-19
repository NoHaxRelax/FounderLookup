import { ExperimentOutlined } from '@ant-design/icons'
import { App as AntApp, Collapse, ConfigProvider, Select } from 'antd'
import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { fixtureClient } from './api/client'
import { FounderLookupApiError } from './api/httpClient'
import type { FounderLookupClient, OpportunityDetail, ViewState, WorkspaceFixture } from './api/types'
import {
  FounderShell,
  InvestorShell,
  LandingShell,
  type AppRoute,
  type InvestorRoute,
} from './components/AppShell'
import { InvestorAccessGate } from './components/InvestorAccessGate'
import { StatePanel } from './components/StatePanel'
import { founderLookupTheme } from './theme'

const FounderIntake = lazy(async () => {
  const module = await import('./pages/FounderIntake')
  return { default: module.FounderIntake }
})
const LandingPage = lazy(async () => {
  const module = await import('./pages/LandingPage')
  return { default: module.LandingPage }
})
const MemoDecision = lazy(async () => {
  const module = await import('./pages/MemoDecision')
  return { default: module.MemoDecision }
})
const OpportunityDetailPage = lazy(async () => {
  const module = await import('./pages/OpportunityDetail')
  return { default: module.OpportunityDetail }
})
const SourcingWorkspace = lazy(async () => {
  const module = await import('./pages/SourcingWorkspace')
  return { default: module.SourcingWorkspace }
})

const validRoutes: AppRoute[] = ['home', 'sourcing', 'opportunity', 'memo', 'apply']

const routeTitles: Record<AppRoute, string> = {
  home: 'Evidence-first founder discovery',
  sourcing: 'Sourcing workspace',
  opportunity: 'Opportunity detail',
  memo: 'Memo & decision',
  apply: 'Founder application',
}

interface AppLocation {
  route: AppRoute
  resourceId?: string
  founderStatusCapability?: string
}

const decodeHashSegment = (value: string | undefined) => {
  if (!value) return undefined
  try {
    return decodeURIComponent(value)
  } catch {
    return undefined
  }
}

const getLocation = (): AppLocation => {
  const [candidate, actionOrId, encodedCapability] = globalThis.location.hash
    .replace(/^#\//, '')
    .split('/')
  const route = validRoutes.includes(candidate as AppRoute) ? (candidate as AppRoute) : 'home'
  if (route === 'apply') {
    if (actionOrId !== 'status') return { route }
    return { route, founderStatusCapability: decodeHashSegment(encodedCapability) }
  }
  if (route === 'opportunity' || route === 'memo') {
    return { route, resourceId: decodeHashSegment(actionOrId) }
  }
  return { route }
}

const isAccessFailure = (error: unknown) =>
  error instanceof FounderLookupApiError && [401, 403].includes(error.problem.status)

export interface AppProps {
  client?: FounderLookupClient
}

export default function App({ client = fixtureClient }: AppProps) {
  const [location, setLocation] = useState<AppLocation>(getLocation)
  const [workspace, setWorkspace] = useState<WorkspaceFixture | null>(null)
  const [opportunity, setOpportunity] = useState<OpportunityDetail | null>(null)
  const [workspaceFailed, setWorkspaceFailed] = useState(false)
  const [detailFailureId, setDetailFailureId] = useState<string | null>(null)
  const [reloadCount, setReloadCount] = useState(0)
  const [previewState, setPreviewState] = useState<ViewState>('ready')
  const [announcement, setAnnouncement] = useState('')
  const [accessRevision, setAccessRevision] = useState(0)
  const [accessError, setAccessError] = useState('')
  const founderRoute = location.route === 'apply'
  const landingRoute = location.route === 'home'
  const investorAccess = client.investorAccess
  const investorUnlocked = !investorAccess || investorAccess.hasCredential()

  const defaultOpportunityId = useMemo(
    () =>
      location.resourceId ??
      workspace?.opportunity?.id ??
      workspace?.search.results.find((candidate) => candidate.opportunityId)?.opportunityId,
    [location.resourceId, workspace],
  )

  useEffect(() => {
    const onHashChange = () => setLocation(getLocation())
    globalThis.addEventListener('hashchange', onHashChange)
    return () => globalThis.removeEventListener('hashchange', onHashChange)
  }, [])

  useEffect(() => {
    if (founderRoute || landingRoute || !investorUnlocked) return undefined
    let active = true
    void client.getWorkspace().then(
      (nextWorkspace) => {
        if (!active) return
        setWorkspaceFailed(false)
        setWorkspace(nextWorkspace)
        setOpportunity(nextWorkspace.opportunity)
      },
      (error: unknown) => {
        if (!active) return
        if (investorAccess && isAccessFailure(error)) {
          investorAccess.clearCredential()
          setAccessError('That access key was rejected. Check it and try again.')
          setAccessRevision((revision) => revision + 1)
          return
        }
        setWorkspaceFailed(true)
      },
    )
    return () => {
      active = false
    }
  }, [accessRevision, client, founderRoute, investorAccess, investorUnlocked, landingRoute, reloadCount])

  useEffect(() => {
    if (
      founderRoute ||
      landingRoute ||
      !investorUnlocked ||
      !workspace ||
      location.route === 'sourcing' ||
      !defaultOpportunityId
    ) {
      return undefined
    }
    if (workspace.opportunity?.id === defaultOpportunityId) {
      return undefined
    }

    let active = true
    void client.getOpportunity(defaultOpportunityId).then(
      (nextOpportunity) => {
        if (active) {
          setDetailFailureId(null)
          setOpportunity(nextOpportunity)
        }
      },
      (error: unknown) => {
        if (!active) return
        if (investorAccess && isAccessFailure(error)) {
          investorAccess.clearCredential()
          setAccessError('Your investor session expired. Enter the access key again.')
          setAccessRevision((revision) => revision + 1)
          return
        }
        setDetailFailureId(defaultOpportunityId)
      },
    )
    return () => {
      active = false
    }
  }, [
    client,
    defaultOpportunityId,
    founderRoute,
    investorAccess,
    investorUnlocked,
    landingRoute,
    location.route,
    workspace,
  ])

  useEffect(() => {
    const subject = opportunity && ['opportunity', 'memo'].includes(location.route)
      ? `${opportunity.company.name} · `
      : ''
    document.title = `${subject}${routeTitles[location.route]} | FounderLookup`
    requestAnimationFrame(() => document.querySelector<HTMLElement>('[data-page-title]')?.focus())
  }, [location.route, opportunity, investorUnlocked])

  const announce = (message: string) => {
    setAnnouncement('')
    requestAnimationFrame(() => setAnnouncement(message))
  }

  const retryWorkspace = () => {
    setWorkspaceFailed(false)
    setDetailFailureId(null)
    setWorkspace(null)
    setOpportunity(null)
    setReloadCount((value) => value + 1)
  }

  const unlockInvestorWorkspace = (credential: string) => {
    investorAccess?.setCredential(credential)
    setAccessError('')
    setAccessRevision((revision) => revision + 1)
  }

  const lockInvestorWorkspace = () => {
    investorAccess?.clearCredential()
    setWorkspace(null)
    setOpportunity(null)
    setAccessError('')
    setAccessRevision((revision) => revision + 1)
  }

  const liveRegion = (
    <div className="visually-hidden" aria-live="polite" aria-atomic="true">
      {announcement}
    </div>
  )

  const loadingFallback = (
    <div className="page"><StatePanel state="loading" entityLabel="page" /></div>
  )

  if (landingRoute) {
    return (
      <ConfigProvider theme={founderLookupTheme}>
        <AntApp className="founderlookup-app founderlookup-app--public">
          <LandingShell>
            <Suspense fallback={loadingFallback}><LandingPage /></Suspense>
          </LandingShell>
        </AntApp>
      </ConfigProvider>
    )
  }

  if (founderRoute) {
    return (
      <ConfigProvider theme={founderLookupTheme}>
        <AntApp className="founderlookup-app founderlookup-app--public">
          <FounderShell>
            {liveRegion}
            <Suspense fallback={loadingFallback}>
              <FounderIntake
                key={location.founderStatusCapability ?? 'application-form'}
                client={client}
                previewState="ready"
                announce={announce}
                statusCapability={location.founderStatusCapability}
              />
            </Suspense>
          </FounderShell>
        </AntApp>
      </ConfigProvider>
    )
  }

  const investorRoute = location.route as InvestorRoute

  return (
    <ConfigProvider theme={founderLookupTheme}>
      <AntApp className="founderlookup-app">
        <InvestorShell
          route={investorRoute}
          runtime={client.runtime}
          opportunityId={defaultOpportunityId}
          onLock={investorAccess && investorUnlocked ? lockInvestorWorkspace : undefined}
        >
          {client.runtime === 'fixture' && (
            <div className="demo-controls">
              <Collapse
                ghost
                items={[
                  {
                    key: 'preview',
                    label: <span><ExperimentOutlined /> Fixture state preview</span>,
                    children: (
                      <div className="demo-controls__body">
                        <label htmlFor="preview-state">Page state</label>
                        <Select<ViewState>
                          id="preview-state"
                          aria-label="Page state"
                          value={previewState}
                          onChange={setPreviewState}
                          virtual={false}
                          options={[
                            { value: 'ready', label: 'Ready' },
                            { value: 'loading', label: 'Loading' },
                            { value: 'empty', label: 'Empty' },
                            { value: 'error', label: 'Error' },
                            { value: 'blocked', label: 'Blocked' },
                          ]}
                        />
                        <p>Deterministic states only; no records are changed.</p>
                      </div>
                    ),
                  },
                ]}
              />
            </div>
          )}

          {liveRegion}

          {!investorUnlocked ? (
            <InvestorAccessGate error={accessError} onUnlock={unlockInvestorWorkspace} />
          ) : (
            <Suspense fallback={loadingFallback}>
              {workspaceFailed ? (
                <div className="page">
                  <StatePanel state="error" entityLabel="workspace" onRetry={retryWorkspace} />
                </div>
              ) : !workspace ? (
                <div className="page"><StatePanel state="loading" entityLabel="workspace" /></div>
              ) : investorRoute === 'sourcing' ? (
                <SourcingWorkspace
                  client={client}
                  initialSearch={workspace.search}
                  thesis={workspace.thesis}
                  previewState={previewState}
                  announce={announce}
                />
              ) : detailFailureId === defaultOpportunityId ? (
                <div className="page">
                  <StatePanel state="error" entityLabel="opportunity" onRetry={retryWorkspace} />
                </div>
              ) : !defaultOpportunityId ? (
                <div className="page"><StatePanel state="empty" entityLabel="opportunities" /></div>
              ) : !opportunity ? (
                <div className="page"><StatePanel state="loading" entityLabel="opportunity" /></div>
              ) : investorRoute === 'opportunity' ? (
                <OpportunityDetailPage
                  client={client}
                  opportunity={opportunity}
                  previewState={previewState}
                  announce={announce}
                />
              ) : (
                <MemoDecision
                  client={client}
                  opportunity={opportunity}
                  previewState={previewState}
                  announce={announce}
                />
              )}
            </Suspense>
          )}
        </InvestorShell>
      </AntApp>
    </ConfigProvider>
  )
}
