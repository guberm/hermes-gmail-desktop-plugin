import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const plugin = fs.readFileSync(new URL('../plugins/gmail/desktop/plugin.js', import.meta.url), 'utf8')
const functionSource = plugin.match(/export function deriveReplyAllRecipients[\s\S]*?(?=\n\nconst EMAIL_TAGS)/)
assert.ok(functionSource, 'inline reply-all function must be present in plugin.js')
const deriveReplyAllRecipients = Function(`${functionSource[0].replace('export function ', 'function ')}; return deriveReplyAllRecipients`)()

function pureHelper(name, args, prefix = '') {
  const expression = new RegExp(`export function ${name}\\(${args}\\) \\{[\\s\\S]*?\\n\\}`)
  const match = plugin.match(expression)
  assert.ok(match, `${name} helper is present`)
  return Function(`${prefix}\n${match[0].replace('export function ', 'function ')}; return ${name}`)()
}
const buildGmailThreadUrl = pureHelper('buildGmailThreadUrl', 'account, threadId', "const MAIL_ACCOUNT_RE = /^[^\\s@<>]+@[^\\s@<>]+\\.[^\\s@<>]+$/")
const safeExternalHref = pureHelper('safeExternalHref', 'value')
const safeExternalHrefSource = plugin.match(/export function safeExternalHref\(value\) \{[\s\S]*?\n\}/)
assert.ok(safeExternalHrefSource, 'safe href validator is present')
const safeExternalImageHref = pureHelper('safeExternalImageHref', 'value', safeExternalHrefSource[0].replace('export function ', 'function '))

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

test('Gmail thread URL encodes verified account and backend thread id', () => {
  assert.equal(buildGmailThreadUrl('user+alias@example.com', 'thread-123_abc'),
    'https://mail.google.com/mail/u/0/?authuser=user%2Balias%40example.com#all/thread-123_abc')
  assert.equal(buildGmailThreadUrl('not an email', 'thread-123'), null)
  assert.equal(buildGmailThreadUrl('user@example.com', 'bad/thread'), null)
})

test('email link protocols are explicit and reject credentials, controls, and active schemes', () => {
  for (const value of ['https://example.test/a', 'http://example.test/a', 'mailto:user@example.test'])
    assert.equal(safeExternalHref(value), value)
  for (const value of ['javascript:alert(1)', 'data:text/html,boom', 'https://u:p@example.test',
    'https://example.test/\ntrack', 'https://example.test/%0afoo'])
    assert.equal(safeExternalHref(value), null)
})

test('remote email images require HTTPS after explicit opt-in', () => {
  assert.equal(safeExternalImageHref('https://images.example.test/pixel.png'), 'https://images.example.test/pixel.png')
  for (const value of ['http://images.example.test/pixel.png', 'javascript:alert(1)',
    'https://u:p@example.test/pixel.png', 'https://example.test/\\ntrack'])
    assert.equal(safeExternalImageHref(value), null)
})