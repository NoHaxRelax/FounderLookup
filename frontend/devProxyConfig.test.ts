import { describe, expect, it } from 'vitest'
import { resolveDataSource, resolveHttpDevProxySettings } from './devProxyConfig'

describe('frontend development proxy configuration', () => {
  it('keeps fixture data as the zero-configuration default', () => {
    expect(resolveDataSource(undefined)).toBe('fixture')
    expect(resolveDataSource(' fixture ')).toBe('fixture')
  })

  it('accepts only an explicit supported data source', () => {
    expect(resolveDataSource('http')).toBe('http')
    expect(() => resolveDataSource('live')).toThrow(/VITE_DATA_SOURCE/)
  })

  it('fails before serving HTTP mode when either proxy requirement is absent', () => {
    expect(() => resolveHttpDevProxySettings({})).toThrow(
      /FOUNDERLOOKUP_API_PROXY_TARGET/,
    )
    expect(() =>
      resolveHttpDevProxySettings({
        FOUNDERLOOKUP_API_PROXY_TARGET: 'http://127.0.0.1:8000',
      }),
    ).toThrow(/FOUNDERLOOKUP_INVESTOR_API_KEY/)
  })

  it('keeps the credential server-side and permits only a loopback proxy origin', () => {
    expect(
      resolveHttpDevProxySettings({
        FOUNDERLOOKUP_API_PROXY_TARGET: 'http://localhost:8000',
        FOUNDERLOOKUP_INVESTOR_API_KEY: 'local-investor-secret',
      }),
    ).toEqual({
      target: 'http://localhost:8000',
      authorization: 'Bearer local-investor-secret',
    })

    expect(() =>
      resolveHttpDevProxySettings({
        FOUNDERLOOKUP_API_PROXY_TARGET: 'https://api.example.com',
        FOUNDERLOOKUP_INVESTOR_API_KEY: 'must-not-leave-the-machine',
      }),
    ).toThrow(/loopback origin/)
  })
})
