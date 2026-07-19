import { AlertOctagon, ArchiveX, CircleSlash2, RefreshCw } from 'lucide-react'
import type { ViewState } from '../api/types'

export interface StatePanelProps {
  state: Exclude<ViewState, 'ready'>
  entityLabel: string
  onRetry?: () => void
}

export function StatePanel({ state, entityLabel, onRetry }: StatePanelProps) {
  if (state === 'loading') {
    return (
      <section className="state-panel" aria-busy="true" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        <div>
          <h2>Loading {entityLabel}</h2>
          <p>Existing information stays unchanged while this request completes.</p>
        </div>
      </section>
    )
  }

  if (state === 'empty') {
    return (
      <section className="state-panel">
        <ArchiveX aria-hidden="true" />
        <div>
          <h2>No {entityLabel} match the active constraints</h2>
          <p>Unknown values were preserved. Broaden a constraint or include unknown results.</p>
        </div>
      </section>
    )
  }

  if (state === 'blocked') {
    return (
      <section className="state-panel state-panel--blocked" role="status">
        <CircleSlash2 aria-hidden="true" />
        <div>
          <h2>{entityLabel} blocked at the last safe stage</h2>
          <p>
            A material contradiction needs human review. Evidence already stored remains available;
            no outreach, recommendation acceptance, or funds movement occurred.
          </p>
        </div>
      </section>
    )
  }

  return (
    <section className="state-panel state-panel--error" role="alert">
      <AlertOctagon aria-hidden="true" />
      <div>
        <h2>Could not load {entityLabel}</h2>
        <p>Request ID: demo-req-73af. Your last saved state is unchanged.</p>
        {onRetry && (
          <button className="button button--secondary" type="button" onClick={onRetry}>
            <RefreshCw aria-hidden="true" /> Retry
          </button>
        )}
      </div>
    </section>
  )
}
