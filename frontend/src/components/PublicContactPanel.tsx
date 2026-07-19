import {
  AuditOutlined,
  ExportOutlined,
  GlobalOutlined,
  MailOutlined,
  SafetyCertificateOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { Alert, Button, Card, Collapse, Descriptions, Tag, Typography } from 'antd'
import type {
  PublicContactKind,
  PublicContactRoute,
  SourcingLoopAudit,
} from '../api/types'
import { StatusBadge } from './StatusBadge'

interface PublicContactPanelProps {
  routes?: PublicContactRoute[]
  loopAudit?: SourcingLoopAudit
}

const contactIcon = (kind: PublicContactKind) => {
  if (kind === 'public_email') return <MailOutlined aria-hidden="true" />
  if (kind === 'public_profile') return <UserOutlined aria-hidden="true" />
  return <GlobalOutlined aria-hidden="true" />
}

export function PublicContactPanel({ routes = [], loopAudit }: PublicContactPanelProps) {
  const suppliedLabel = `${routes.length} public`

  return (
    <Collapse
      className="public-contact-panel"
      items={[
        {
          key: 'public-contact',
          label: (
            <span>
              <SafetyCertificateOutlined aria-hidden="true" /> Public follow-up routes
            </span>
          ),
          extra: <Tag>{suppliedLabel}</Tag>,
          children: (
            <div className="public-contact-panel__body">
              <Alert
                type="info"
                showIcon
                title="Supplied public data only"
                description="Every route below was explicitly present in a public source and keeps its provenance. Missing contact data stays missing—no private lookup, inferred email, or automated outreach."
              />

              {routes.length > 0 ? (
                <ul className="public-contact-list">
                  {routes.map((route) => {
                    const opensNewTab = route.href?.startsWith('https://') ?? false
                    return (
                      <li key={route.id}>
                        <Card
                          className="public-contact-route"
                          size="small"
                          title={
                            <span>
                              {contactIcon(route.kind)} {route.label}
                            </span>
                          }
                        >
                          <Typography.Paragraph copyable className="public-contact-route__value">
                            {route.displayValue}
                          </Typography.Paragraph>
                          <div className="public-contact-route__action">
                            {route.href ? (
                              <Button
                                type="link"
                                href={route.href}
                                target={opensNewTab ? '_blank' : undefined}
                                rel={opensNewTab ? 'noreferrer' : undefined}
                                icon={<ExportOutlined aria-hidden="true" />}
                              >
                                Open supplied route
                              </Button>
                            ) : (
                              <Typography.Text type="secondary">Not safely linkable</Typography.Text>
                            )}
                          </div>
                          <Descriptions
                            className="public-contact-provenance"
                            size="small"
                            column={1}
                            items={[
                              { key: 'source', label: 'Public source', children: route.sourceName },
                              { key: 'locator', label: 'Locator', children: route.sourceLocator },
                              {
                                key: 'artifact',
                                label: 'Artifact',
                                children: <Typography.Text code>{route.sourceArtifactId}</Typography.Text>,
                              },
                              ...(route.collectedAt
                                ? [{
                                    key: 'collected',
                                    label: 'Collected',
                                    children: new Date(route.collectedAt).toLocaleString(),
                                  }]
                                : []),
                            ]}
                          />
                        </Card>
                      </li>
                    )
                  })}
                </ul>
              ) : (
                <Alert
                  type="warning"
                  title="No public follow-up route was supplied"
                  description="Use a human introduction or request contact information directly; do not infer an address from a name or domain."
                />
              )}

              {loopAudit && (
                <section className="sourcing-loop-audit" aria-labelledby={`loop-audit-${loopAudit.runId ?? 'current'}`}>
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Outbound audit</p>
                      <h3 id={`loop-audit-${loopAudit.runId ?? 'current'}`}>
                        <AuditOutlined aria-hidden="true" /> Bounded sourcing loop
                      </h3>
                    </div>
                    <StatusBadge tone={loopAudit.status === 'failed' ? 'critical' : 'neutral'}>
                      {loopAudit.status}
                    </StatusBadge>
                  </div>
                  <Descriptions
                    size="small"
                    column={1}
                    items={[
                      {
                        key: 'rounds',
                        label: 'Rounds',
                        children: `${loopAudit.roundsCompleted}${loopAudit.roundLimit ? ` of ${loopAudit.roundLimit}` : ''}`,
                      },
                      { key: 'reason', label: 'Stop reason', children: loopAudit.stopReason },
                      ...(loopAudit.runId
                        ? [{
                            key: 'run',
                            label: 'Run',
                            children: <Typography.Text code>{loopAudit.runId}</Typography.Text>,
                          }]
                        : []),
                    ]}
                  />
                </section>
              )}
            </div>
          ),
        },
      ]}
    />
  )
}
