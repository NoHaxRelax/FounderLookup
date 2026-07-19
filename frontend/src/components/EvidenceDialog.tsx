import {
  ExportOutlined,
  FileSearchOutlined,
  LockOutlined,
} from '@ant-design/icons'
import { Button, Card, Descriptions, Modal, Space, Typography } from 'antd'
import type { EvidenceItem } from '../api/types'
import { KnowledgeState } from './KnowledgeState'
import { StatusBadge } from './StatusBadge'

export interface EvidenceDialogProps {
  evidence: EvidenceItem | null
  onClose: () => void
}

export function EvidenceDialog({ evidence, onClose }: EvidenceDialogProps) {
  return (
    <Modal
      className="evidence-dialog"
      open={evidence !== null}
      onCancel={onClose}
      footer={null}
      width="min(44rem, calc(100dvi - 2rem))"
      title={
        evidence ? (
          <div>
            <p className="eyebrow">Evidence · {evidence.sourceCategory}</p>
            <h2 id="evidence-dialog-title">{evidence.sourceName}</h2>
          </div>
        ) : undefined
      }
    >
      {evidence && (
        <div className="dialog-content">
          <Space wrap>
            <StatusBadge tone={evidence.availability === 'available' ? 'positive' : 'warning'}>
              {evidence.availability.replaceAll('_', ' ')}
            </StatusBadge>
            <StatusBadge tone={evidence.classification === 'public' ? 'info' : 'warning'}>
              {evidence.classification === 'founder_private' && <LockOutlined aria-hidden="true" />}
              {evidence.classification.replaceAll('_', ' ')}
            </StatusBadge>
          </Space>

          <Descriptions
            className="evidence-metadata"
            column={1}
            items={[
              {
                key: 'artifact',
                label: 'Stable source artifact',
                children: <Typography.Text code>{evidence.sourceArtifactId}</Typography.Text>,
              },
              {
                key: 'collected',
                label: 'Collected',
                children: new Date(evidence.collectedAt).toLocaleString(),
              },
              {
                key: 'event-time',
                label: 'Source event time',
                children: <KnowledgeState value={evidence.sourceEventTime} compact />,
              },
            ]}
          />

          <Card className="locator-card" aria-labelledby="source-locator-title">
            <FileSearchOutlined aria-hidden="true" />
            <div>
              <h3 id="source-locator-title">Source locator</h3>
              <p><strong>{evidence.locator.label}</strong></p>
              <blockquote>{evidence.locator.excerpt}</blockquote>
              {evidence.locator.uri && (
                <Button
                  href={evidence.locator.uri}
                  target="_blank"
                  rel="noreferrer"
                  icon={<ExportOutlined />}
                >
                  Open source
                </Button>
              )}
            </div>
          </Card>
        </div>
      )}
    </Modal>
  )
}
