import {
  AlertTriangle,
  Check,
  CircleHelp,
  Clock3,
  Minus,
  ShieldAlert,
} from 'lucide-react'
import type { ReactNode } from 'react'

export type BadgeTone = 'positive' | 'warning' | 'critical' | 'info' | 'neutral'

const icons: Record<BadgeTone, ReactNode> = {
  positive: <Check aria-hidden="true" />,
  warning: <AlertTriangle aria-hidden="true" />,
  critical: <ShieldAlert aria-hidden="true" />,
  info: <CircleHelp aria-hidden="true" />,
  neutral: <Minus aria-hidden="true" />,
}

export interface StatusBadgeProps {
  children: ReactNode
  tone?: BadgeTone
  pending?: boolean
}

export function StatusBadge({ children, tone = 'neutral', pending = false }: StatusBadgeProps) {
  return (
    <span className={`status-badge status-badge--${tone}`}>
      {pending ? <Clock3 aria-hidden="true" /> : icons[tone]}
      <span>{children}</span>
    </span>
  )
}
