export const normalizeCompanyName = (value: string) => value.trim().replaceAll(/\s+/g, ' ')

export async function applicationPayloadFingerprint(
  companyName: string,
  deck: File,
  supplementalPayload = '',
): Promise<string> {
  const companyBytes = new TextEncoder().encode(normalizeCompanyName(companyName).toLocaleLowerCase())
  const supplementalBytes = new TextEncoder().encode(supplementalPayload)
  const deckBytes = new Uint8Array(await deck.arrayBuffer())
  const payload = new Uint8Array(companyBytes.length + supplementalBytes.length + deckBytes.length + 2)
  payload.set(companyBytes)
  payload[companyBytes.length] = 0
  payload.set(supplementalBytes, companyBytes.length + 1)
  payload[companyBytes.length + supplementalBytes.length + 1] = 0
  payload.set(deckBytes, companyBytes.length + supplementalBytes.length + 2)
  const digest = await globalThis.crypto.subtle.digest('SHA-256', payload)
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}
