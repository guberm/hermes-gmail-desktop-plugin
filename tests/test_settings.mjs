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
