export const normalizeCompanyName = (value: string) => value.trim().replaceAll(/\s+/g, ' ')

export async function applicationPayloadFingerprint(
  companyName: string,
  deck: File,
): Promise<string> {
  const companyBytes = new TextEncoder().encode(normalizeCompanyName(companyName).toLocaleLowerCase())
  const deckBytes = new Uint8Array(await deck.arrayBuffer())
  const payload = new Uint8Array(companyBytes.length + 1 + deckBytes.length)
  payload.set(companyBytes)
  payload[companyBytes.length] = 0
  payload.set(deckBytes, companyBytes.length + 1)
  const digest = await globalThis.crypto.subtle.digest('SHA-256', payload)
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}
