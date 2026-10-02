import React from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import * as Gmail from './gmail_plugin.js'

let root
let opened = []
let mailboxState
let mailboxQueryClient
const ctx = { os: { openExternal: async url => { opened.push(url) } } }

function syntheticMailbox({ storage = new Map(), failRead = false, failReadIds = [], deferredReads = false,
  failActionPrepare = false, deferredActionPrepare = false, staleActionCommit = false } = {}) {
  const state = { calls: [], searchCalls: 0, storage, labels: { m1: ['INBOX', 'UNREAD', 'STARRED'], m2: ['INBOX', 'UNREAD', 'IMPORTANT'] },
    pending: new Map(), pendingActionPrepare: new Map(), tickets: new Map(), nextTicket: 0,
    failRead, failReadIds: new Set(failReadIds), deferredReads, failActionPrepare, deferredActionPrepare, staleActionCommit }
  const msgs = ['m1', 'm2'].map((id, index) => ({
    id, threadId: 't' + id, from: 'sender@example.test', subject: `Synthetic message ${index + 1}`,
    snippet: 'Privacy-safe fixture', labelIds: [...state.labels[id]]
  }))
  state.ctx = {
    os: { openExternal: async url => { opened.push(url) }, writeClipboard: async () => true },
    storage: { get: key => storage.get(key), set: (key, value) => storage.set(key, value) },
    rest: async (path, options = {}) => {
      const method = options.method || 'GET'
      state.calls.push({ method, path, body: options.body })
      const pathname = path.split('?')[0]
      if (pathname === '/status') return { scope: 'synthetic_scope_12345678901234567890', account: 'fixture@example.test' }
      if (pathname === '/search') {
        state.searchCalls += 1
        return { messages: msgs.filter(message => state.labels[message.id].includes('INBOX'))
          .map(message => ({ ...message, snippet: `Privacy-safe fixture ${state.searchCalls}`, labelIds: [...state.labels[message.id]] })) }
      }
      if (pathname === '/labels') return { labels: [] }
      if (pathname === '/actions/prepare') {
        const body = options.body
        const messageId = body?.messageId
        if (state.deferredActionPrepare) await new Promise((resolve, reject) => state.pendingActionPrepare.set(messageId, { resolve, reject }))
        if (state.failActionPrepare) throw new Error('synthetic action prepare failure')
        const labels = [...state.labels[messageId]]
        const preview = body.action === 'trash'
          ? { action: 'trash', account: 'fixture@example.test', message: { id: messageId, labelIds: labels }, effect: 'Move this message to Trash' }
          : { action: 'labels', account: 'fixture@example.test', message: { id: messageId, labelIds: labels },
            addLabelIds: body.addLabelIds, removeLabelIds: body.removeLabelIds,
            addLabels: [], removeLabels: body.removeLabelIds.map(id => ({ id, name: id })) }
        const confirmationToken = `synthetic-ticket-${++state.nextTicket}`
        state.tickets.set(confirmationToken, { body, messageId, labels, preview })
        return { scope: body.scope, confirmationToken, preview }
      }
      if (pathname === '/actions/commit') {
        const { scope, confirmationToken, confirmed } = options.body
        const ticket = state.tickets.get(confirmationToken)
        if (!ticket || scope !== 'synthetic_scope_12345678901234567890' || confirmed !== true) throw new Error('invalid synthetic ticket')
        state.tickets.delete(confirmationToken)
        if (state.staleActionCommit) {
          state.staleActionCommit = false
          state.labels[ticket.messageId] = [...state.labels[ticket.messageId], 'STARRED']
        }
        if (JSON.stringify(state.labels[ticket.messageId]) !== JSON.stringify(ticket.labels)) throw new Error('stale synthetic snapshot')
        if (ticket.preview.action === 'trash') {
          state.labels[ticket.messageId] = [...state.labels[ticket.messageId].filter(id => id !== 'INBOX' && id !== 'TRASH'), 'TRASH']
        } else {
          state.labels[ticket.messageId] = [...new Set([...state.labels[ticket.messageId], ...ticket.body.addLabelIds])]
            .filter(id => !ticket.body.removeLabelIds.includes(id))
        }
        return { status: 'verified', id: ticket.messageId }
      }
      if (pathname.startsWith('/messages/') && pathname.endsWith('/read')) {
        const id = decodeURIComponent(pathname.split('/')[2])
        if (state.deferredReads) await new Promise((resolve, reject) => state.pending.set(id, { resolve, reject }))
        if (state.failRead || state.failReadIds.has(id)) throw new Error('synthetic read failure')
        state.labels[id] = state.labels[id].filter(label => label !== 'UNREAD')
        return { status: 'verified', id, labelIds: [...state.labels[id]] }
      }
      if (pathname.startsWith('/messages/')) {
        const id = decodeURIComponent(pathname.split('/')[2])
        const message = msgs.find(item => item.id === id)
        return { ...message, labelIds: [...state.labels[id]], body: 'Privacy-safe fixture', htmlBody: '' }
      }
      if (pathname.startsWith('/threads/')) {
        const id = pathname.split('/')[2].slice(1)
        return { id: 't' + id, messages: [{
          id, threadId: 't' + id, from: 'sender@example.test', labelIds: [...state.labels[id]],
          htmlBody: '<table width="560" style="width:560px;max-width:100%;text-align:center"><tr><td width="48" style="padding:4px 8px;font-size:14px">Tile</td></tr></table><img data-email-src="https://images.example.test/fixture.png" width="48" height="48">'
        }] }
      }
      throw new Error('Unexpected synthetic request: ' + method + ' ' + path)
    }
  }
  return state
}

window.GmailEmailHarness = {
  mountEmail(markup) {
    root?.unmount()
    opened = []
    root = createRoot(document.getElementById('root'))
    if (typeof Gmail.EmailBody !== 'function') throw new Error('candidate React email renderer is missing')
    root.render(React.createElement(Gmail.EmailBody, { markup, ctx }))
  },
  mountThread(account, threadId) {
    root?.unmount()
    opened = []
    root = createRoot(document.getElementById('root'))
    if (typeof Gmail.GmailThreadAction !== 'function') throw new Error('candidate Gmail thread action is missing')
    root.render(React.createElement(Gmail.GmailThreadAction, { account, threadId, ctx }))
  },
  mountMailbox(options) {
    root?.unmount()
    opened = []
    mailboxState = syntheticMailbox(options)
    root = createRoot(document.getElementById('root'))
    mailboxQueryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 60000 } } })
    root.render(React.createElement(QueryClientProvider, { client: mailboxQueryClient },
      React.createElement(Gmail.GmailPage, { ctx: mailboxState.ctx })))
    return mailboxState
  },
  mailbox: () => mailboxState,
  queryClient: () => mailboxQueryClient,
  resolveRead: id => { const pending = mailboxState?.pending.get(id); if (!pending) return false; mailboxState.pending.delete(id); pending.resolve(); return true },
  rejectRead: id => { const pending = mailboxState?.pending.get(id); if (!pending) return false; mailboxState.pending.delete(id); pending.reject(new Error('synthetic read failure')); return true },
  resolveActionPrepare: id => { const pending = mailboxState?.pendingActionPrepare.get(id); if (!pending) return false; mailboxState.pendingActionPrepare.delete(id); pending.resolve(); return true },
  opened: () => opened,
  unmount() { root?.unmount(); root = undefined }
}
