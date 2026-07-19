import {
  BriefcaseBusiness,
  FileCheck2,
  Inbox,
  Menu,
  Search,
  Sparkles,
  X,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'

export type AppRoute = 'sourcing' | 'opportunity' | 'memo' | 'apply'

const navItems: Array<{ route: AppRoute; label: string; detail: string; icon: ReactNode }> = [
  {
    route: 'sourcing',
    label: 'Sourcing',
    detail: 'Query and candidate queue',
    icon: <Search aria-hidden="true" />,
  },
  {
    route: 'opportunity',
    label: 'Opportunity',
    detail: 'Claims and evidence',
    icon: <BriefcaseBusiness aria-hidden="true" />,
  },
  {
    route: 'memo',
    label: 'Memo & decision',
    detail: 'Cited review',
    icon: <FileCheck2 aria-hidden="true" />,
  },
  {
    route: 'apply',
    label: 'Founder apply',
    detail: 'Deck intake',
    icon: <Inbox aria-hidden="true" />,
  },
]

export interface AppShellProps {
  route: AppRoute
  children: ReactNode
}

export function AppShell({ route, children }: AppShellProps) {
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <header className="mobile-header">
        <a className="brand" href="#/sourcing" aria-label="FounderLookup home" onClick={() => setMenuOpen(false)}>
          <span className="brand__mark"><Sparkles aria-hidden="true" /></span>
          <span>FounderLookup</span>
        </a>
        <button
          className="icon-button"
          type="button"
          aria-expanded={menuOpen}
          aria-controls="primary-navigation"
          aria-label={menuOpen ? 'Close navigation' : 'Open navigation'}
          onClick={() => setMenuOpen((value) => !value)}
        >
          {menuOpen ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
        </button>
      </header>

      <aside className={`sidebar ${menuOpen ? 'sidebar--open' : ''}`}>
        <a className="brand desktop-brand" href="#/sourcing" aria-label="FounderLookup home">
          <span className="brand__mark"><Sparkles aria-hidden="true" /></span>
          <span>
            FounderLookup
            <small>Evidence before conviction</small>
          </span>
        </a>

        <nav id="primary-navigation" aria-label="Primary navigation">
          <ul className="nav-list">
            {navItems.map((item) => (
              <li key={item.route}>
                <a
                  className="nav-link"
                  href={`#/${item.route}`}
                  aria-current={route === item.route ? 'page' : undefined}
                  onClick={() => setMenuOpen(false)}
                >
                  {item.icon}
                  <span>
                    {item.label}
                    <small>{item.detail}</small>
                  </span>
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="sidebar-note">
          <span className="status-dot" aria-hidden="true" />
          <div>
            <strong>Fixture workspace</strong>
            <small>Deterministic · no provider calls</small>
          </div>
        </div>
      </aside>

      <main id="main-content" className="main-content" tabIndex={-1}>
        {children}
      </main>
    </div>
  )
}
