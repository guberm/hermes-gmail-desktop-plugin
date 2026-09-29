import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const plugin = fs.readFileSync(new URL('../plugins/gmail/desktop/plugin.js', import.meta.url), 'utf8')
const functionSource = plugin.match(/export function deriveReplyAllRecipients[\s\S]*?const ID/)
assert.ok(functionSource, 'inline reply-all function must be present in plugin.js')
const deriveReplyAllRecipients = Function(`${functionSource[0].replace(/\n\nconst ID$/, '').replace('export function ', 'function ')}; return deriveReplyAllRecipients`)()

test('reply all preserves sender and original To/Cc, excluding active account', () => {
  assert.deepEqual(
    deriveReplyAllRecipients({
      from: 'Sender Name <SENDER@example.com>',
      to: 'Me <me@example.com>, To Person <to@example.com>',
      cc: 'Cc Person <cc@example.com>, sender@example.com'
    }, 'ME@example.com'),
    { to: 'sender@example.com, to@example.com', cc: 'cc@example.com' }
  )
})

test('reply all de-duplicates and bounds recipients', () => {
  const message = {
    from: 'sender@example.com',
    to: Array.from({ length: 130 }, (_, i) => `to${i}@example.com`).join(', '),
    cc: 'sender@example.com, to1@example.com, cc@example.com'
  }
  const result = deriveReplyAllRecipients(message, 'me@example.com')
  const recipients = `${result.to}, ${result.cc}`.split(', ').filter(Boolean)
  assert.equal(recipients.length, 100)
  assert.equal(new Set(recipients).size, recipients.length)
})

test('reply all rejects a message with no recipient after account exclusion', () => {
  assert.throws(() => deriveReplyAllRecipients({ from: 'me@example.com', to: 'ME@example.com' }, 'me@example.com'))
})