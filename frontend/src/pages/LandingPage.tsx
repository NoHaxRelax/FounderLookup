import {
  ArrowRightOutlined,
  LockOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import { Button } from 'antd'

export function LandingPage() {
  return (
    <section className="landing-hero" aria-labelledby="landing-title">
      <div className="landing-hero__copy">
        <p className="eyebrow">Sourcing-first investment intelligence</p>
        <h1 id="landing-title" data-page-title tabIndex={-1}>
          Find overlooked founders. Decide from Evidence.
        </h1>
        <p className="landing-promise">
          FounderLookup turns inbound decks and bounded public signals into an inspectable path
          from first signal to a human Decision—without hiding uncertainty.
        </p>
      </div>

      <nav className="landing-paths" aria-label="Choose how to continue">
        <article className="landing-path landing-path--investor">
          <SearchOutlined aria-hidden="true" />
          <h2>Investor workspace</h2>
          <p>Source candidates, inspect Evidence, review memos, and record the human Decision.</p>
          <Button type="primary" href="#/sourcing" icon={<ArrowRightOutlined aria-hidden="true" />} iconPlacement="end">
            Enter investor workspace
          </Button>
        </article>
        <article className="landing-path landing-path--founder">
          <UploadOutlined aria-hidden="true" />
          <h2>Founder application</h2>
          <p>Submit only your company name and one PDF deck, then keep a private status link.</p>
          <Button href="#/apply" icon={<ArrowRightOutlined aria-hidden="true" />} iconPlacement="end">
            Start founder application
          </Button>
        </article>
      </nav>

      <aside className="landing-trust" aria-label="Trust and privacy">
        <SafetyCertificateOutlined aria-hidden="true" />
        <p>
          <strong>Private by design.</strong> Founder decks stay protected, credentials never ship
          in the browser bundle, and a Recommendation never becomes a Decision or moves funds.
        </p>
        <LockOutlined aria-hidden="true" />
      </aside>
    </section>
  )
}
