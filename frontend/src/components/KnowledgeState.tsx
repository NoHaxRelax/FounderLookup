import {
  EyeInvisibleOutlined,
  MinusCircleOutlined,
  QuestionCircleOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { Alert, Typography } from 'antd'
import type { KnowledgeValue } from '../api/types'
import { StatusBadge } from './StatusBadge'

export interface KnowledgeStateProps<T> {
  value: KnowledgeValue<T>
  format?: (value: T) => string
  compact?: boolean
}

const defaultFormat = <T,>(value: T) => String(value)

export function KnowledgeState<T>({
  value,
  format = defaultFormat,
  compact = false,
}: KnowledgeStateProps<T>) {
  if (value.state === 'known') {
    return compact ? (
      <Typography.Text>{format(value.value)}</Typography.Text>
    ) : (
      <div className="knowledge knowledge--known">
        <StatusBadge tone="positive">Known</StatusBadge>
        <span>{format(value.value)}</span>
      </div>
    )
  }

  if (compact) {
    if (value.state === 'conflicted') {
      return (
        <Typography.Text className="knowledge-inline knowledge-inline--conflicted" type="danger">
          Conflicted — {value.reason}
        </Typography.Text>
      )
    }
    const compactLabels = {
      unknown: 'Unknown',
      not_disclosed: 'Not disclosed',
      not_applicable: 'Not applicable',
    } as const
    return (
      <Typography.Text className={`knowledge-inline knowledge-inline--${value.state}`} type="secondary">
        {compactLabels[value.state]} — {value.reason}
      </Typography.Text>
    )
  }

  if (value.state === 'conflicted') {
    return (
      <Alert
        className="knowledge knowledge--conflicted"
        type="error"
        showIcon
        title={<StatusBadge tone="critical">Conflicted</StatusBadge>}
        description={
          <div>
            <p>{value.reason}</p>
            <ul>
              {value.alternatives.map((alternative) => (
                <li key={`${format(alternative.value)}-${alternative.evidenceIds.join('-')}`}>
                  {format(alternative.value)}
                  <span className="muted"> · {alternative.evidenceIds.length} evidence item(s)</span>
                </li>
              ))}
            </ul>
          </div>
        }
      />
    )
  }

  const labels = {
    unknown: 'Unknown',
    not_disclosed: 'Not disclosed',
    not_applicable: 'Not applicable',
  } as const

  const Icon =
    value.state === 'unknown'
      ? QuestionCircleOutlined
      : value.state === 'not_disclosed'
        ? EyeInvisibleOutlined
        : MinusCircleOutlined

  return (
    <Alert
      className={`knowledge knowledge--${value.state}`}
      type="info"
      showIcon
      icon={<Icon aria-hidden="true" />}
      title={labels[value.state]}
      description={
        <div>
          <span>{value.reason}</span>
          {value.evidenceIds.length > 0 && (
            <span className="muted knowledge__evidence-count">
              <WarningOutlined aria-hidden="true" /> {value.evidenceIds.length} related evidence item(s)
            </span>
          )}
        </div>
      }
    />
  )
}
