import { FlaskConical } from 'lucide-react'
import { useEffect, useState } from 'react'
import { fixtureClient } from './api/client'
import type { FounderLookupClient, ViewState, WorkspaceFixture } from './api/types'
import { AppShell, type AppRoute } from './components/AppShell'
import { StatePanel } from './components/StatePanel'
import { FounderIntake } from './pages/FounderIntake'
import { MemoDecision } from './pages/MemoDecision'
import { OpportunityDetail } from './pages/OpportunityDetail'
import { SourcingWorkspace } from './pages/SourcingWorkspace'

const validRoutes: AppRoute[] = ['sourcing', 'opportunity', 'memo', 'apply']

const routeTitles: Record<AppRoute, string> = {
  sourcing: 'Sourcing workspace',
  opportunity: 'Opportunity detail',
  memo: 'Memo & decision',
  apply: 'Founder application',
}

interface AppLocation {
  route: AppRoute
  founderStatusCapability?: string
}

const getLocation = (): AppLocation => {
  const [candidate, action, encodedCapability] = globalThis.location.hash
    .replace(/^#\//, '')
    .split('/')
  const route = validRoutes.includes(candidate as AppRoute) ? (candidate as AppRoute) : 'sourcing'
  if (route !== 'apply' || action !== 'status' || !encodedCapability) return { route }
  try {
    return { route, founderStatusCapability: decodeURIComponent(encodedCapability) }
  } catch {
    return { route }
  }
}

export interface AppProps {
  client?: FounderLookupClient
}

export default function App({ client = fixtureClient }: AppProps) {
  const [location, setLocation] = useState<AppLocation>(getLocation)
  const route = location.route
  const [workspace, setWorkspace] = useState<WorkspaceFixture | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [reloadCount, setReloadCount] = useState(0)
  const [previewState, setPreviewState] = useState<ViewState>('ready')
  const [announcement, setAnnouncement] = useState('')

  useEffect(() => {
    if (route === 'apply') return undefined
    let active = true
    void client.getWorkspace().then(
      (nextWorkspace) => {
        if (active) setWorkspace(nextWorkspace)
      },
      () => {
        if (active) setLoadFailed(true)
      },
    )
    return () => {
      active = false
    }
  }, [client, reloadCount, route])

  useEffect(() => {
    const onHashChange = () => setLocation(getLocation())
    globalThis.addEventListener('hashchange', onHashChange)
    return () => globalThis.removeEventListener('hashchange', onHashChange)
  }, [])

  useEffect(() => {
    document.title = `${routeTitles[route]} | FounderLookup`
    requestAnimationFrame(() => document.querySelector<HTMLElement>('[data-page-title]')?.focus())
  }, [route, workspace])

  const announce = (message: string) => {
    setAnnouncement('')
    requestAnimationFrame(() => setAnnouncement(message))
  }

  const retryWorkspace = () => {
    setLoadFailed(false)
    setWorkspace(null)
    setReloadCount((value) => value + 1)
  }

  return (
    <AppShell route={route}>
      <div className="demo-controls">
        <details>
          <summary><FlaskConical aria-hidden="true" /> Demo state preview</summary>
          <div className="demo-controls__body">
            <label htmlFor="preview-state">Page state</label>
            <select
              id="preview-state"
              value={previewState}
              onChange={(event) => setPreviewState(event.target.value as ViewState)}
            >
              <option value="ready">Ready</option>
              <option value="loading">Loading</option>
              <option value="empty">Empty</option>
              <option value="error">Error</option>
              <option value="blocked">Blocked</option>
            </select>
            <p>Deterministic preview for design review; it does not mutate fixture data.</p>
          </div>
        </details>
      </div>

      <div className="visually-hidden" aria-live="polite" aria-atomic="true">{announcement}</div>

      {route === 'apply' ? (
        <FounderIntake
          key={location.founderStatusCapability ?? 'application-form'}
          client={client}
          previewState={previewState}
          announce={announce}
          statusCapability={location.founderStatusCapability}
        />
      ) : loadFailed ? (
        <div className="page"><StatePanel state="error" entityLabel="workspace" onRetry={retryWorkspace} /></div>
      ) : !workspace ? (
        <div className="page"><StatePanel state="loading" entityLabel="workspace" /></div>
      ) : route === 'sourcing' ? (
        <SourcingWorkspace
          client={client}
          initialSearch={workspace.search}
          thesis={workspace.thesis}
          previewState={previewState}
          announce={announce}
        />
      ) : route === 'opportunity' ? (
        workspace.opportunity ? (
          <OpportunityDetail opportunity={workspace.opportunity} previewState={previewState} />
        ) : (
          <div className="page"><StatePanel state="empty" entityLabel="opportunities" /></div>
        )
      ) : route === 'memo' ? (
        workspace.opportunity ? (
          <MemoDecision
            client={client}
            opportunity={workspace.opportunity}
            previewState={previewState}
            announce={announce}
          />
        ) : (
          <div className="page"><StatePanel state="empty" entityLabel="investment memos" /></div>
        )
      ) : (
        <div className="page"><StatePanel state="error" entityLabel="route" /></div>
      )}
    </AppShell>
  )
}
