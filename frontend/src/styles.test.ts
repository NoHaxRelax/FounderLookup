import { describe, expect, it } from 'vitest'
import stylesheet from './styles.css?raw'

describe('responsive CSS safety rails', () => {
  it('uses named grid areas for the application shell and navigation regions', () => {
    expect(stylesheet).toMatch(/\.app-shell \{[\s\S]*?"sidebar main"/)
    expect(stylesheet).toMatch(/\.sidebar \{[\s\S]*?grid-template-areas:[\s\S]*?"navigation"/)
    expect(stylesheet).toContain('grid-template-areas: "nav-icon nav-copy";')
    expect(stylesheet).toContain('grid-template-areas: "mobile-brand mobile-toggle";')
    expect(stylesheet).toContain('grid-template-areas: "workspace-rail results";')
    expect(stylesheet).toContain('grid-template-areas: "claims diligence";')
    expect(stylesheet).toContain('grid-template-areas: "memo decision";')
    expect(stylesheet).toContain('grid-template-areas: "intake assurance";')
  })

  it('removes the closed off-canvas navigation from keyboard visibility', () => {
    expect(stylesheet).toMatch(/@media \(max-width: 60rem\)[\s\S]*?\.sidebar \{[\s\S]*?visibility: hidden;/)
    expect(stylesheet).toMatch(/\.sidebar--open \{[\s\S]*?visibility: visible;/)
  })

  it('uses component containers and intrinsic tracks instead of viewport breakpoints', () => {
    expect(stylesheet).toContain('container-name: results-region;')
    expect(stylesheet).toContain('@container results-region (inline-size >= 62rem)')
    expect(stylesheet).toContain('@container page (inline-size < 54rem)')
    expect(stylesheet).toMatch(/repeat\(auto-fit, minmax\(min\(100%, 17rem\), 1fr\)\)/)
    expect(stylesheet.match(/@media \((?:min|max)-width:/g)).toHaveLength(1)
  })

  it('keeps sizing fluid and viewport behavior logical', () => {
    expect(stylesheet).toContain('--sidebar-width: clamp(')
    expect(stylesheet).toContain('min-block-size: 100dvb;')
    expect(stylesheet).toContain('inline-size: min(18rem, calc(100dvi - 2rem));')
    expect(stylesheet).toContain('inset-inline-start: 0;')
    expect(stylesheet).toContain('border-inline-end: 1px solid')
  })

  it('preserves explicit accessibility modes and visible keyboard focus', () => {
    expect(stylesheet).toContain(':focus-visible {')
    expect(stylesheet).toContain('@media (pointer: coarse)')
    expect(stylesheet).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.sidebar \{[\s\S]*?transition: none;/,
    )
    expect(stylesheet).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.spinner \{[\s\S]*?animation: none;/,
    )
    expect(stylesheet).toMatch(
      /@media \(forced-colors: active\)[\s\S]*?:focus-visible \{[\s\S]*?outline-color: Highlight;/,
    )
  })
})
