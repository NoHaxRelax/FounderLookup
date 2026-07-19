import {
  CheckOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  MinusOutlined,
  QuestionCircleOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { Tag } from 'antd'
import type { ReactNode } from 'react'

export type BadgeTone = 'positive' | 'warning' | 'critical' | 'info' | 'neutral'

const icons: Record<BadgeTone, ReactNode> = {
  positive: <CheckOutlined aria-hidden="true" />,
  warning: <WarningOutlined aria-hidden="true" />,
  critical: <ExclamationCircleOutlined aria-hidden="true" />,
  info: <QuestionCircleOutlined aria-hidden="true" />,
  neutral: <MinusOutlined aria-hidden="true" />,
}

const colors: Record<BadgeTone, string> = {
  positive: 'success',
  warning: 'warning',
  critical: 'error',
  info: 'processing',
  neutral: 'default',
}

export interface StatusBadgeProps {
  children: ReactNode
  tone?: BadgeTone
  pending?: boolean
}

export function StatusBadge({ children, tone = 'neutral', pending = false }: StatusBadgeProps) {
  return (
    <Tag
      className={`status-badge status-badge--${tone}`}
      color={colors[tone]}
      icon={pending ? <ClockCircleOutlined aria-hidden="true" /> : icons[tone]}
      variant="filled"
    >
      {children}
    </Tag>
  )
}
