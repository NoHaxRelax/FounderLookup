import { describe, expect, it } from 'vitest'
import stylesheet from './styles.css?raw'

describe('responsive CSS safety rails', () => {
  it('keeps the app shell display contract above unlayered framework styles', () => {
    expect(stylesheet).toMatch(
      /@layer reset, foundation, layout, components, utilities, accessibility;[\s\S]*?\.app-shell\.app-shell\s*\{\s*display:\s*grid;/,
    )
    expect(stylesheet).toMatch(
      /\.mobile-header\.mobile-header\s*\{[\s\S]*?padding:\s*0\.65rem clamp\(1rem, 4vi, 2rem\);/,
    )
  })

  it('uses named grid areas for the shell and every major two-column workflow', () => {
    expect(stylesheet).toContain("grid-template-areas: 'sidebar main';")
    expect(stylesheet).toMatch(/\.sidebar \{[\s\S]*?grid-template-areas:[\s\S]*?'navigation'/)
    expect(stylesheet).toContain("grid-template-areas: 'mobile-brand mobile-toggle';")
    expect(stylesheet).toContain("grid-template-areas: 'workspace-rail results';")
    expect(stylesheet).toContain("grid-template-areas: 'claims diligence';")
    expect(stylesheet).toContain("grid-template-areas: 'memo decision';")
    expect(stylesheet).toContain("grid-template-areas: 'intake assurance';")
  })

  it('uses component containers and intrinsic tracks instead of viewport width queries', () => {
    expect(stylesheet).toContain('container: app-root / inline-size;')
    expect(stylesheet).toContain('container: results-region / inline-size;')
    expect(stylesheet).toContain('@container app-root (inline-size < 60rem)')
    expect(stylesheet).toContain('@container page (inline-size < 68rem)')
    expect(stylesheet).toMatch(/repeat\(auto-fit, minmax\(min\(100%, 17rem\), 1fr\)\)/)
    expect(stylesheet).not.toMatch(/@media \((?:min|max)-width:/)
    expect(stylesheet).not.toContain('overflow-x: clip')
  })

  it('keeps sizing fluid and viewport behavior logical', () => {
    expect(stylesheet).toContain('--sidebar-width: clamp(')
    expect(stylesheet).toContain('min-block-size: 100dvb;')
    expect(stylesheet).toContain('font-size: clamp(')
    expect(stylesheet).toContain('inset-inline-start: 0;')
    expect(stylesheet).toContain('border: 0;')
  })

  it('keeps the full Soft UI depth vocabulary centralized and avoids brittle Ant internals', () => {
    expect(stylesheet).toContain('--shadow-tactile:')
    expect(stylesheet).toContain('--shadow-raised:')
    expect(stylesheet).toContain('--shadow-inset:')
    expect(stylesheet).toContain('--shadow-pressed:')
    expect(stylesheet).toMatch(/\.search-surface,[\s\S]*?box-shadow: var\(--shadow-tactile\)/)
    expect(stylesheet).toMatch(/\[role='menuitem'\]:has\(\.nav-link\[aria-current='page'\]\)[\s\S]*?box-shadow: var\(--shadow-pressed\)/)
    expect(stylesheet).not.toMatch(/\.ant-[a-z]/)
  })

  it('preserves explicit accessibility modes and visible keyboard focus', () => {
    expect(stylesheet).toContain(':focus-visible {')
    expect(stylesheet).toMatch(/#root :where\([\s\S]*?outline: 3px solid var\(--jade-700\)/)
    expect(stylesheet).toContain('@media (pointer: coarse)')
    expect(stylesheet).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*?animation-duration: 0\.01ms !important;/,
    )
    expect(stylesheet).toMatch(
      /@media \(forced-colors: active\)[\s\S]*?outline-color: Highlight;/,
    )
  })
})
