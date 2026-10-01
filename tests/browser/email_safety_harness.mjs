import React from 'react'
import { createRoot } from 'react-dom/client'
import * as Gmail from './gmail_plugin.js'

let root
let opened = []
const ctx = { os: { openExternal: async url => { opened.push(url) } } }

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
  opened: () => opened,
  unmount() { root?.unmount(); root = undefined }
}
