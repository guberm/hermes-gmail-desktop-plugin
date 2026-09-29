export function deriveReplyAllRecipients(message, activeAccount, maxRecipients = 100) {
  const extract = value => [...String(value || '').matchAll(/<([^<>\s@]+@[^<>\s@]+)>|\b([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})\b/gi)]
    .map(match => (match[1] || match[2]).toLowerCase())
  const active = extract(activeAccount)[0]
  const to = []
  const cc = []
  const seen = new Set()
  const add = (target, value) => {
    for (const address of extract(value)) {
      if (address === active || seen.has(address)) continue
      seen.add(address)
      if (to.length + cc.length >= maxRecipients) return
      target.push(address)
    }
  }
  add(to, message?.from)
  add(to, message?.to)
  add(cc, message?.cc)
  if (!to.length && !cc.length) throw new Error('reply-all has no recipients')
  return { to: to.join(', '), cc: cc.join(', ') }
}
