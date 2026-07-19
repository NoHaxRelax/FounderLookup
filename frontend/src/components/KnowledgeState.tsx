import { AlertTriangle, CircleHelp, EyeOff, MinusCircle } from 'lucide-react'
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
      <span>{format(value.value)}</span>
    ) : (
      <div className="knowledge knowledge--known">
        <StatusBadge tone="positive">Known</StatusBadge>
        <span>{format(value.value)}</span>
      </div>
    )
  }

  if (compact) {
    if (value.state === 'conflicted') {
      return <span className="knowledge-inline knowledge-inline--conflicted">Conflicted — {value.reason}</span>
    }
    const compactLabels = {
      unknown: 'Unknown',
      not_disclosed: 'Not disclosed',
      not_applicable: 'Not applicable',
    } as const
    return (
      <span className={`knowledge-inline knowledge-inline--${value.state}`}>
        {compactLabels[value.state]} — {value.reason}
      </span>
    )
  }

  if (value.state === 'conflicted') {
    return (
      <div className="knowledge knowledge--conflicted">
        <StatusBadge tone="critical">Conflicted</StatusBadge>
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
    )
  }

  const labels = {
    unknown: 'Unknown',
    not_disclosed: 'Not disclosed',
    not_applicable: 'Not applicable',
  } as const

  const Icon =
    value.state === 'unknown' ? CircleHelp : value.state === 'not_disclosed' ? EyeOff : MinusCircle

  return (
    <div className={`knowledge knowledge--${value.state}`}>
      <span className="knowledge__label">
        <Icon aria-hidden="true" />
        {labels[value.state]}
      </span>
      <span>{value.reason}</span>
      {value.evidenceIds.length > 0 && (
        <span className="muted">
          <AlertTriangle aria-hidden="true" /> {value.evidenceIds.length} related evidence item(s)
        </span>
      )}
    </div>
  )
}
