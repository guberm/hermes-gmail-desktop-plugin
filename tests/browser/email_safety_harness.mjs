import React from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import * as Gmail from './gmail_plugin.js'

let root
let opened = []
let mailboxState
const ctx = { os: { openExternal: async url => { opened.push(url) } } }

function syntheticMailbox({ storage = new Map(), failRead = false, failReadIds = [], deferredReads = false } = {}) {
  const state = { calls: [], storage, labels: { m1: ['INBOX', 'UNREAD', 'STARRED'], m2: ['INBOX', 'UNREAD'] }, pending: new Map(), failRead, failReadIds: new Set(failReadIds), deferredReads }
  const msgs = ['m1', 'm2'].map((id, index) => ({
    id, threadId: 't' + id, from: 'sender@example.test', subject: `Synthetic message ${index + 1}`,
    snippet: 'Privacy-safe fixture', labelIds: [...state.labels[id]]
  }))
  state.ctx = {
    os: { openExternal: async url => { opened.push(url) }, writeClipboard: async () => true },
    storage: { get: key => storage.get(key), set: (key, value) => storage.set(key, value) },
    rest: async (path, options = {}) => {
      const method = options.method || 'GET'
      state.calls.push({ method, path })
      const pathname = path.split('?')[0]
      if (pathname === '/status') return { scope: 'synthetic_scope_12345678901234567890', account: 'fixture@example.test' }
      if (pathname === '/search') return { messages: msgs.map(message => ({ ...message, labelIds: [...state.labels[message.id]] })) }
      if (pathname === '/labels') return { labels: [] }
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
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } } })
    root.render(React.createElement(QueryClientProvider, { client: queryClient },
      React.createElement(Gmail.GmailPage, { ctx: mailboxState.ctx })))
    return mailboxState
  },
  mailbox: () => mailboxState,
  resolveRead: id => { const pending = mailboxState?.pending.get(id); if (!pending) return false; mailboxState.pending.delete(id); pending.resolve(); return true },
  rejectRead: id => { const pending = mailboxState?.pending.get(id); if (!pending) return false; mailboxState.pending.delete(id); pending.reject(new Error('synthetic read failure')); return true },
  opened: () => opened,
  unmount() { root?.unmount(); root = undefined }
}
