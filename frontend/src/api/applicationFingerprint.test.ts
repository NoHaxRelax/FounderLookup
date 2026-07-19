import { describe, expect, it } from 'vitest'
import { applicationPayloadFingerprint } from './applicationFingerprint'

describe('applicationPayloadFingerprint', () => {
  it('hashes the exact deck bytes even when file metadata is identical', async () => {
    const first = new File(['AA'], 'same.pdf', {
      type: 'application/pdf',
      lastModified: 1_700_000_000_000,
    })
    const second = new File(['BB'], 'same.pdf', {
      type: 'application/pdf',
      lastModified: 1_700_000_000_000,
    })

    expect(first.size).toBe(second.size)
    expect(first.name).toBe(second.name)
    expect(first.lastModified).toBe(second.lastModified)
    await expect(applicationPayloadFingerprint('Jade Systems', first)).resolves.not.toBe(
      await applicationPayloadFingerprint('Jade Systems', second),
    )
  })

  it('normalizes company whitespace and case before hashing an otherwise identical payload', async () => {
    const first = new File(['same bytes'], 'first-name.pdf', { type: 'application/pdf' })
    const second = new File(['same bytes'], 'second-name.pdf', { type: 'application/pdf' })

    await expect(applicationPayloadFingerprint('  JADE   Systems ', first)).resolves.toBe(
      await applicationPayloadFingerprint('jade systems', second),
    )
  })
})
