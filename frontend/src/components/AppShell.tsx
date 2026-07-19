import {
  BulbOutlined,
  FileDoneOutlined,
  LockOutlined,
  MenuOutlined,
  SearchOutlined,
  SolutionOutlined,
} from '@ant-design/icons'
import { Button, Drawer, Layout, Menu, type MenuProps } from 'antd'
import { useState, type ReactNode } from 'react'

export type AppRoute = 'home' | 'sourcing' | 'opportunity' | 'memo' | 'apply'
export type InvestorRoute = Exclude<AppRoute, 'home' | 'apply'>

interface NavigationItem {
  route: InvestorRoute
  label: string
  detail: string
  icon: ReactNode
}

const navItems: NavigationItem[] = [
  {
    route: 'sourcing',
    label: 'Sourcing',
    detail: 'Query and candidate queue',
    icon: <SearchOutlined aria-hidden="true" />,
  },
  {
    route: 'opportunity',
    label: 'Opportunity',
    detail: 'Claims and evidence',
    icon: <SolutionOutlined aria-hidden="true" />,
  },
  {
    route: 'memo',
    label: 'Memo & decision',
    detail: 'Cited review',
    icon: <FileDoneOutlined aria-hidden="true" />,
  },
]

const menuItems = (
  route: InvestorRoute,
  opportunityId?: string,
  closeMenu?: () => void,
): MenuProps['items'] =>
  navItems.map((item) => ({
    key: item.route,
    icon: item.icon,
    label: (
      <a
        className="nav-link"
        href={
          item.route === 'sourcing' || !opportunityId
            ? `#/${item.route}`
            : `#/${item.route}/${encodeURIComponent(opportunityId)}`
        }
        aria-current={route === item.route ? 'page' : undefined}
        onClick={closeMenu}
      >
        <span>{item.label}</span>
        <small>{item.detail}</small>
      </a>
    ),
  }))

function Brand({
  compact = false,
  href,
  onClick,
}: {
  compact?: boolean
  href: string
  onClick?: () => void
}) {
  return (
    <a className="brand" href={href} aria-label="FounderLookup home" onClick={onClick}>
      <span className="brand__mark"><BulbOutlined aria-hidden="true" /></span>
      <span className="brand__copy">
        FounderLookup
        {!compact && <small>Evidence before conviction</small>}
      </span>
    </a>
  )
}

export interface AppShellProps {
  route: InvestorRoute
  runtime: 'fixture' | 'http'
  opportunityId?: string
  onLock?: () => void
  children: ReactNode
}

export function InvestorShell({
  route,
  opportunityId,
  onLock,
  children,
}: AppShellProps) {
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <Layout className="app-shell">
      <a className="skip-link" href="#main-content">Skip to main content</a>

      <Layout.Header className="mobile-header">
        <Brand compact href="#/sourcing" onClick={() => setMenuOpen(false)} />
        <Button
          className="mobile-menu-button"
          type="text"
          icon={<MenuOutlined />}
          aria-expanded={menuOpen}
          aria-controls="mobile-primary-navigation"
          aria-label="Open investor navigation"
          onClick={() => setMenuOpen(true)}
        />
      </Layout.Header>

      <aside className="sidebar">
        <Brand href="#/sourcing" />
        <nav aria-label="Investor workspace">
          <Menu
            mode="inline"
            selectedKeys={[route]}
            items={menuItems(route, opportunityId)}
            className="primary-menu"
          />
        </nav>
        {onLock && (
          <Button
            className="sidebar-lock-button"
            type="text"
            icon={<LockOutlined aria-hidden="true" />}
            onClick={onLock}
          >
            Lock workspace
          </Button>
        )}
      </aside>

      <Layout.Content id="main-content" className="main-content" tabIndex={-1}>
        {children}
      </Layout.Content>

      <Drawer
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
        placement="left"
        size="min(20rem, calc(100dvi - 2rem))"
        title={<Brand compact href="#/sourcing" onClick={() => setMenuOpen(false)} />}
        classNames={{ body: 'mobile-drawer__body' }}
        rootClassName="mobile-navigation-drawer"
      >
        <nav id="mobile-primary-navigation" aria-label="Mobile investor workspace">
          <Menu
            mode="inline"
            selectedKeys={[route]}
            items={menuItems(route, opportunityId, () => setMenuOpen(false))}
            className="primary-menu"
          />
        </nav>
        {onLock && (
          <Button
            className="sidebar-lock-button"
            type="text"
            icon={<LockOutlined aria-hidden="true" />}
            onClick={() => {
              setMenuOpen(false)
              onLock()
            }}
          >
            Lock workspace
          </Button>
        )}
      </Drawer>
    </Layout>
  )
}

export function FounderShell({ children }: { children: ReactNode }) {
  return (
    <div className="founder-shell">
      <a className="skip-link" href="#founder-main-content">Skip to main content</a>
      <header className="founder-header">
        <Brand href="#/apply" />
        <span className="founder-header__context">Application &amp; private status</span>
      </header>
      <main id="founder-main-content" className="founder-main-content" tabIndex={-1}>
        {children}
      </main>
      <footer className="founder-footer">
        <span>FounderLookup</span>
        <span>Only your application and bounded status appear here.</span>
      </footer>
    </div>
  )
}

export function LandingShell({ children }: { children: ReactNode }) {
  return (
    <div className="landing-shell">
      <header className="landing-header" aria-label="FounderLookup">
        <span className="brand__mark"><BulbOutlined aria-hidden="true" /></span>
        <span className="brand__copy">FounderLookup<small>Evidence before conviction</small></span>
      </header>
      <main id="landing-main-content" className="landing-main-content">
        {children}
      </main>
    </div>
  )
}
