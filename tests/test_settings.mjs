import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(new URL('../plugins/gmail/desktop/plugin.js', import.meta.url), 'utf8')
function loadFunction(name, nextName, prefix = '') {
  const start = source.indexOf(`export function ${name}(`)
  assert.notEqual(start, -1, `${name} is present`)
  const namedEnd = nextName ? source.indexOf(`export function ${nextName}(`, start + 1) : -1
  const nextExport = source.indexOf('\nexport function ', start + 1)
  const menuEnd = source.indexOf('\nfunction menuSetting', start)
  const end = [namedEnd, nextExport, menuEnd].filter(index => index > start).sort((a, b) => a - b)[0]
  assert.ok(end, `${name} has a bounded source region`)
  const fn = source.slice(start, end).replace(`export function ${name}`, `function ${name}`)
  return Function(`${prefix}\n${fn}\nreturn ${name}`)()
}

const defaults = 'const DEFAULT_SETTINGS = Object.freeze({ autoRefresh: false, unreadOnly: false, deleteConfirmation: true, alwaysShowImages: false })'
const settings = loadFunction('gmailSettings', 'settingsStorageKey', defaults)
const storageKey = loadFunction('settingsStorageKey', 'toggleGmailSetting')
const toggle = loadFunction('toggleGmailSetting', 'inboxQuery', defaults)
const query = loadFunction('inboxQuery', 'autoRefreshInterval')
const interval = loadFunction('autoRefreshInterval', 'shouldConfirmDelete')
const confirmDelete = loadFunction('shouldConfirmDelete', null)
const toggleSelection = loadFunction('toggleMessageSelection', 'gmailSettings', 'const BATCH_LIMIT = 20')
const starChange = loadFunction('starLabelChange', 'contextText')
const chips = loadFunction('userLabelChips', 'senderName', 'const SYSTEM_CHIP_LABELS = new Set([\'INBOX\', \'UNREAD\', \'SENT\', \'DRAFT\', \'TRASH\', \'IMPORTANT\', \'SPAM\', \'STARRED\', \'CATEGORY_PERSONAL\', \'CATEGORY_SOCIAL\', \'CATEGORY_UPDATES\', \'CATEGORY_FORUMS\', \'CATEGORY_PROMOTIONS\'])')
const nameOf = loadFunction('senderName', 'shortDate')
const dateOf = loadFunction('shortDate', 'formatAttachmentSize')
const sizeFmt = loadFunction('formatAttachmentSize', 'newMessageIds')
const newIds = loadFunction('newMessageIds', 'menuSetting')

test('preference defaults and persisted booleans are explicit', () => {
  assert.deepEqual(settings(null), { autoRefresh: false, unreadOnly: false, deleteConfirmation: true, alwaysShowImages: false })
  assert.deepEqual(settings({ autoRefresh: true, unreadOnly: 'yes', deleteConfirmation: false, alwaysShowImages: true, unknown: true }),
    { autoRefresh: true, unreadOnly: false, deleteConfirmation: false, alwaysShowImages: true })
})

test('Always show images is independently toggleable and cannot change another preference', () => {
  const original = settings(null)
  const enabled = toggle(original, 'alwaysShowImages')
  assert.equal(original.alwaysShowImages, false)
  assert.equal(enabled.alwaysShowImages, true)
  assert.equal(enabled.autoRefresh, original.autoRefresh)
  assert.equal(enabled.unreadOnly, original.unreadOnly)
  assert.equal(toggle(enabled, 'alwaysShowImages').alwaysShowImages, false)
})

test('settings persist under isolated profile and normalized account keys', () => {
  assert.notEqual(storageKey('default', 'a@example.com'), storageKey('work', 'a@example.com'))
  assert.equal(storageKey('default', 'A@EXAMPLE.COM'), storageKey('default', 'a@example.com'))
  assert.notEqual(storageKey('default', 'a@example.com'), storageKey('default', 'b@example.com'))
})

test('auto-refresh toggle controls only bounded one-minute mailbox polling', () => {
  const off = settings(null)
  const on = toggle(off, 'autoRefresh')
  assert.equal(interval(off), false)
  assert.equal(interval(on), 60000)
  assert.equal(interval(toggle(on, 'autoRefresh')), false)
})

test('unread filter appends Gmail operator once and restores original query when off', () => {
  assert.equal(query('in:inbox', false), 'in:inbox')
  assert.equal(query('in:inbox', true), 'in:inbox is:unread')
  assert.equal(query('from:person is:unread', true), 'from:person is:unread')
  assert.equal(query('', true), 'is:unread')
})

test('delete prompt defaults on; opt-out changes prompt only, not backend pipeline', () => {
  const enabled = settings(null)
  const disabled = toggle(enabled, 'deleteConfirmation')
  assert.equal(confirmDelete(enabled), true)
  assert.equal(confirmDelete(disabled), false)
  assert.match(source, /if \(mounted\.current && kind === 'trash' && !shouldConfirmDelete\(current\.settings\)\) commitWithoutPrompt = prepared/)
  assert.match(source, /await commit\(commitWithoutPrompt\)/)
  assert.match(source, /confirmationToken: approved\.confirmationToken, confirmed: true/)
  assert.match(source, /if \(result\.status !== 'verified'\)/)
})

test('More dropdown wires read-state actions to confirmed label preparation', () => {
  assert.match(source, /DropdownMenuTrigger[\s\S]*?DropdownMenuContent/)
  assert.match(source, /onClick: \(\) => \{ void prepare\('labels-add', 'UNREAD'\) \}/)
  assert.match(source, /onClick: \(\) => \{ void prepare\('labels-remove', 'UNREAD'\) \}/)
  assert.match(source, /jsx\(Confirmation, \{ ticket: statusUnavailable \? null : ticket/)
})

test('message selection is bounded at 20, deduplicated, and safely toggleable', () => {
  const empty = new Set()
  const first = toggleSelection(empty, 'm1')
  assert.deepEqual([...first], ['m1'])
  assert.deepEqual([...toggleSelection(first, 'm1')], [])
  const full = new Set(Array.from({ length: 20 }, (_, index) => `m${index}`))
  assert.deepEqual([...toggleSelection(full, 'extra')], [...full])
  assert.equal(toggleSelection(full, 'm3').size, 19)
  assert.match(source, /BATCH_LIMIT\s*=\s*20/)
})

test('cards expose selection and star action, and bulk toolbar dispatches one batch prepare', () => {
  assert.ok(source.includes("'aria-label': `Select message"))
  assert.match(source, /Star selected|Unstar selected/)
  assert.match(source, /prepareBatch\('star'/)
  assert.match(source, /action: 'batch'/)
  assert.match(source, /Archive selected/)
  assert.match(source, /Trash selected/)
  assert.match(source, /Mark selected as read|Mark selected as unread/)
})

test('star card action prepares reversible system-label changes', () => {
  assert.deepEqual(starChange({ labelIds: ['INBOX'] }), { addLabelIds: ['STARRED'], removeLabelIds: [] })
  assert.deepEqual(starChange({ labelIds: ['INBOX', 'STARRED'] }), { addLabelIds: [], removeLabelIds: ['STARRED'] })
  assert.match(source, /prepareOneLabel\(message\)/)
})

test('label chips filter system/category labels, resolve names, and stay bounded', () => {
  const byId = { L1: 'Projects', L2: 'Later' }
  assert.deepEqual(chips({ labelIds: ['INBOX', 'UNREAD', 'L1', 'CATEGORY_SOCIAL', 'L2'] }, byId), ['Projects', 'Later'])
  assert.deepEqual(chips({ labelIds: ['INBOX', 'CATEGORY_UPDATES'] }, byId), [])
  assert.deepEqual(chips({ labelIds: ['L1'] }, null), ['L1'])
  assert.deepEqual(chips(null, byId), [])
  const many = chips({ labelIds: ['L1', 'L2', 'X1', 'X2', 'X3', 'X4', 'X5', 'X6', 'X7'] }, byId)
  assert.equal(many.length, 8)
})

test('attachment sizes format human-readable and reject invalid input', () => {
  assert.equal(sizeFmt(512), '512 B')
  assert.equal(sizeFmt(2048), '2 KB')
  assert.equal(sizeFmt(5 * 1024 * 1024), '5.0 MB')
  assert.equal(sizeFmt(-1), '')
  assert.equal(sizeFmt('x'), '')
})

test('new-mail diff finds unseen ids, bounded, robust to non-sets', () => {
  assert.deepEqual(newIds(new Set(['a', 'b']), ['b', 'c', 'd']), ['c', 'd'])
  assert.deepEqual(newIds(new Set(), []), [])
  assert.deepEqual(newIds(null, ['a']), ['a'])
  assert.deepEqual(newIds(new Set(['a']), 'not-a-list'), [])
  const many = newIds(new Set(), Array.from({ length: 50 }, (_, i) => `m${i}`))
  assert.equal(many.length, 20)
})

test('sender name extraction mirrors the Telegram bot GetSenderName', () => {
  assert.equal(nameOf('Amazon <ship-confirm@amazon.com>'), 'Amazon')
  assert.equal(nameOf('"Support, AWS" <no-reply@aws.com>'), 'Support, AWS')
  assert.equal(nameOf('bare@example.com'), 'bare@example.com')
  assert.equal(nameOf(''), '(unknown sender)')
  assert.equal(nameOf(null), '(unknown sender)')
  assert.equal(nameOf('Plain Name'), 'Plain Name')
})

test('short date renders local YYYY-MM-DD HH:MM and falls back to raw', () => {
  assert.equal(dateOf('Sat, 26 Sep 2026 12:00:00 -0400').length, 16)
  assert.equal(dateOf('Sat, 26 Sep 2026 12:00:00 -0400'), dateOf(new Date('Sat, 26 Sep 2026 16:00:00 +0000').toString()))
  assert.equal(dateOf('not a date'), 'not a date')
  assert.equal(dateOf(''), '')
})
