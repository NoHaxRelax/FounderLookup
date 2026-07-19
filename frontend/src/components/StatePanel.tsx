import {
  InboxOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import { Button, Card, Empty, Result, Skeleton } from 'antd'
import type { ViewState } from '../api/types'

export interface StatePanelProps {
  state: Exclude<ViewState, 'ready'>
  entityLabel: string
  onRetry?: () => void
}

export function StatePanel({ state, entityLabel, onRetry }: StatePanelProps) {
  if (state === 'loading') {
    return (
      <Card className="state-panel" aria-busy="true" aria-live="polite">
        <div className="state-panel__loading">
          <h2>Loading {entityLabel}</h2>
          <p>Existing information stays unchanged while this request completes.</p>
          <Skeleton active paragraph={{ rows: 3 }} title={false} />
        </div>
      </Card>
    )
  }

  if (state === 'empty') {
    return (
      <Card className="state-panel">
        <Empty
          image={<InboxOutlined aria-hidden="true" />}
          description={
            <div>
              <h2>No {entityLabel} match the active constraints</h2>
              <p>Unknown values were preserved. Broaden a constraint or include unknown results.</p>
            </div>
          }
        />
      </Card>
    )
  }

  if (state === 'blocked') {
    return (
      <Result
        className="state-panel state-panel--blocked"
        role="status"
        status="warning"
        icon={<SafetyCertificateOutlined aria-hidden="true" />}
        title={<h2>{entityLabel} blocked at the last safe stage</h2>}
        subTitle={
          <span>
            A material contradiction needs human review. Evidence already stored remains available;
            no outreach, recommendation acceptance, or funds movement occurred.
          </span>
        }
      />
    )
  }

  return (
    <Result
      className="state-panel state-panel--error"
      role="alert"
      status="error"
      title={<h2>Could not load {entityLabel}</h2>}
      subTitle="Request ID: demo-req-73af. Your last saved state is unchanged."
      extra={
        onRetry ? (
          <Button icon={<ReloadOutlined />} onClick={onRetry}>
            Retry
          </Button>
        ) : undefined
      }
    />
  )
}
