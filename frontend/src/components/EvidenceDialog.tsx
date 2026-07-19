import { ExternalLink, FileSearch, LockKeyhole, X } from 'lucide-react'
import { useEffect, useRef } from 'react'
import type { EvidenceItem } from '../api/types'
import { KnowledgeState } from './KnowledgeState'
import { StatusBadge } from './StatusBadge'

export interface EvidenceDialogProps {
  evidence: EvidenceItem | null
  onClose: () => void
}

export function EvidenceDialog({ evidence, onClose }: EvidenceDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const dialog = dialogRef.current
    if (evidence && dialog && !dialog.open) dialog.showModal()
    if (!evidence && dialog?.open) dialog.close()
  }, [evidence])

  return (
    <dialog
      ref={dialogRef}
      className="evidence-dialog"
      aria-labelledby="evidence-dialog-title"
      onCancel={onClose}
      onClose={onClose}
    >
      {evidence && (
        <div className="dialog-content">
          <header className="dialog-header">
            <div>
              <p className="eyebrow">Evidence · {evidence.sourceCategory}</p>
              <h2 id="evidence-dialog-title">{evidence.sourceName}</h2>
            </div>
            <form method="dialog">
              <button className="icon-button" type="submit" aria-label="Close evidence">
                <X aria-hidden="true" />
              </button>
            </form>
          </header>

          <div className="cluster">
            <StatusBadge tone={evidence.availability === 'available' ? 'positive' : 'warning'}>
              {evidence.availability.replaceAll('_', ' ')}
            </StatusBadge>
            <StatusBadge tone={evidence.classification === 'public' ? 'info' : 'warning'}>
              {evidence.classification === 'founder_private' && <LockKeyhole aria-hidden="true" />}
              {evidence.classification.replaceAll('_', ' ')}
            </StatusBadge>
          </div>

          <dl className="metadata-list">
            <div>
              <dt>Stable source artifact</dt>
              <dd><code>{evidence.sourceArtifactId}</code></dd>
            </div>
            <div>
              <dt>Collected</dt>
              <dd>{new Date(evidence.collectedAt).toLocaleString()}</dd>
            </div>
            <div>
              <dt>Source event time</dt>
              <dd><KnowledgeState value={evidence.sourceEventTime} compact /></dd>
            </div>
          </dl>

          <section className="locator-card" aria-labelledby="source-locator-title">
            <FileSearch aria-hidden="true" />
            <div>
              <h3 id="source-locator-title">Source locator</h3>
              <p><strong>{evidence.locator.label}</strong></p>
              <blockquote>{evidence.locator.excerpt}</blockquote>
              {evidence.locator.uri && (
                <a href={evidence.locator.uri} target="_blank" rel="noreferrer">
                  Open source <ExternalLink aria-hidden="true" />
                </a>
              )}
            </div>
          </section>
        </div>
      )}
    </dialog>
  )
}
