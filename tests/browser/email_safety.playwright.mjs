import assert from 'node:assert/strict'
import { build } from '/home/mg/.hermes/hermes-agent/node_modules/esbuild/lib/main.js'
import test from 'node:test'
import { chromium } from '/home/mg/.hermes/hermes-agent/node_modules/playwright/index.mjs'

const harness = new URL('./email_safety_harness.mjs', import.meta.url).pathname
const pluginSource = new URL('../../plugins/gmail/desktop/plugin.js', import.meta.url).pathname
const built = await build({
  entryPoints: [harness],
  bundle: true,
  write: false,
  format: 'esm',
  platform: 'browser',
  target: 'chrome120',
  nodePaths: ['/home/mg/.hermes/hermes-agent/node_modules'],
  plugins: [{
    name: 'hermes-sdk-test-double',
    setup(buildApi) {
      buildApi.onResolve({ filter: /^@hermes\/plugin-sdk$/ }, () => ({ path: 'plugin-sdk', namespace: 'test-sdk' }))
      buildApi.onLoad({ filter: /.*/, namespace: 'test-sdk' }, () => ({ resolveDir: '/home/mg/.hermes/hermes-agent/node_modules', contents: `
        import { jsx } from 'react/jsx-runtime'
        export const host = { state: { profile: 'default', gateway: 'open' } }
        export const Button = props => jsx('button', props)
        export const Input = 'input', Textarea = 'textarea'
        export const Dialog = 'div', DialogContent = 'div', DialogHeader = 'div', DialogTitle = 'h2'
        export const DialogDescription = 'p', DialogFooter = 'div'
        export const DropdownMenu = 'div', DropdownMenuContent = 'div', DropdownMenuItem = 'div'
        export const DropdownMenuSeparator = 'hr', DropdownMenuTrigger = 'button'
        export const ROUTES_AREA = 'route', SIDEBAR_NAV_AREA = 'nav', PALETTE_AREA = 'palette'
        export const useValue = value => value
        export { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
      `, loader: 'js' }))
      buildApi.onResolve({ filter: /^\.\/gmail_plugin\.js$/ }, () => ({ path: pluginSource }))
    }
  }]
})
const javascript = built.outputFiles[0].text
const browser = await chromium.launch({ headless: true })
try {
const page = await browser.newPage()
const requests = []
const abortedImageRequests = []
const allRemoteImages = []
page.on('request', request => { if (request.resourceType() === 'image') allRemoteImages.push(request.url()) })
  await page.route('https://images.example.test/**', async route => {
    requests.push(route.request().url())
    imageReferrers.push(route.request().headers().referer || '')
    await route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ZcAAAAASUVORK5CYII=', 'base64') })
  })
  const imageReferrers = []
  await page.route('https://i.ytimg.com/**', async route => {
    requests.push(route.request().url())
    imageReferrers.push(route.request().headers().referer || '')
    await route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ZcAAAAASUVORK5CYII=', 'base64') })
  })
  await page.route('**/*', route => {
    if (route.request().url().startsWith('https://images.example.test/') || route.request().url().startsWith('https://i.ytimg.com/')) return route.fallback()
    if (route.request().resourceType() === 'image') abortedImageRequests.push(route.request().url())
    void route.abort()
  })
  await page.route('http://localhost/**', route => {
    if (route.request().url() === 'http://localhost/harness.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript', body: javascript })
    return route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><html><body><div id="root"></div><script type="module" src="/harness.js"></script></body></html>' })
  })
  const pageErrors = []
  page.on('pageerror', error => pageErrors.push(error.message))
  await page.goto('http://localhost/')
  await page.waitForFunction(() => !!window.GmailEmailHarness)
  await page.setViewportSize({ width: 360, height: 800 })

  const formattedMarkup = '<p>first line<br>second<br>third<hr>fourth</p>'
  await page.evaluate(markup => window.GmailEmailHarness.mountEmail(markup), formattedMarkup)
  await page.waitForTimeout(50)
  assert.deepEqual(pageErrors, [], 'realistic email void elements render without React errors')
  assert.equal(await page.locator('br').count(), 2, 'email line breaks render')
  assert.equal(await page.locator('hr').count(), 1, 'email horizontal rule renders')
  assert.equal(await page.locator('#root').innerText(), 'first line\nsecond\nthird\n\nfourth')

  const reportedHtml = '<p>Video: https://www.youtube.com/watch?v=e7TY56-yIvM.</p>' +
    '<p>Channel: https://youtube.com/@channel, docs https://docs.anthropic.com/en/docs.</p>' +
    '<p>Read more: http://example.test/a?x=1&amp;y=2!</p>' +
    '<table width="560" style="width:560px;max-width:100%;text-align:center;color:#000;background-image:url(https://images.example.test/evil.png);position:fixed"><tbody><tr><td width="48" valign="top" style="padding:4px 8px;font-size:14px"><table width="100%" style="border-collapse:collapse"><tbody><tr><td align="center" style="padding:2px 4px;font-weight:bold">tile</td></tr></tbody></table></td><td style="padding:8px 12px;line-height:1.4">newsletter text</td></tr></tbody></table>' +
    '<img data-email-src="https://i.ytimg.com/vi/e7TY56-yIvM/hqdefault.jpg" width="480" height="360"><br><hr>' +
    '<p>unsafe javascript:alert(1) and data:text/html,boom javascript:https://evil.test/ https://user:pass@example.test/ https://example.test/%0aevil</p>'
  await page.evaluate(markup => {
    document.documentElement.style.setProperty('--ui-text-primary', 'rgb(241, 242, 243)')
    document.documentElement.style.setProperty('--ui-accent', 'rgb(72, 149, 239)')
    window.GmailEmailHarness.mountEmail(markup)
  }, reportedHtml)
  await page.getByRole('button', { name: 'Load images' }).waitFor()
  assert.deepEqual(requests, [], 'the reported YouTube HTTP thumbnail is upgraded but remains inert before opt-in')
  assert.deepEqual(allRemoteImages, [], 'no network image requests occur before the explicit image click')
  assert.deepEqual(abortedImageRequests, [], 'adversarial CSS resource declarations cannot initiate image requests')
  assert.equal(await page.locator('img').count(), 0)
  assert.equal(await page.getByText('Image blocked: no description').count(), 1, 'missing-alt thumbnail has a visible explicit-load placeholder')
  assert.equal(await page.locator('table').first().evaluate(el => el.getBoundingClientRect().width <= el.parentElement.getBoundingClientRect().width), true, 'newsletter table fits the content width at narrow size')
  assert.equal(await page.locator('td').first().evaluate(el => getComputedStyle(el).paddingTop), '4px', 'safe newsletter spacing survives projection')
  assert.equal(await page.locator('td').first().evaluate(el => getComputedStyle(el).fontSize), '14px', 'safe newsletter typography survives projection')
  assert.equal(await page.locator('table').first().evaluate(el => getComputedStyle(el).color), 'rgb(241, 242, 243)', 'email foreground stays on semantic theme color')
  assert.equal(await page.locator('table').first().evaluate(el => getComputedStyle(el).position), 'static', 'positioning tricks are not restored')
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, '360px viewport has no horizontal overflow')
  assert.equal(await page.getByRole('link').count(), 4, 'bare HTTP and HTTPS URLs become actionable text links')
  assert.equal(await page.getByText(/unsafe javascript:alert\(1\)/).getAttribute('role'), null)
  assert.equal(await page.getByRole('link', { name: /evil\.test/ }).count(), 0, 'https text nested inside an unsafe scheme stays inert')
  assert.equal(await page.getByRole('link', { name: 'https://docs.anthropic.com/en/docs' }).evaluate(el => getComputedStyle(el).cursor), 'pointer')
  assert.equal(await page.locator('p').filter({ hasText: 'Video:' }).innerText(), 'Video: https://www.youtube.com/watch?v=e7TY56-yIvM.')
  assert.equal(await page.locator('p').filter({ hasText: 'Channel:' }).innerText(), 'Channel: https://youtube.com/@channel, docs https://docs.anthropic.com/en/docs.')
  assert.equal(await page.locator('p').filter({ hasText: 'Read more:' }).innerText(), 'Read more: http://example.test/a?x=1&y=2!')
  assert.equal(await page.locator('#root > div').evaluate(el => getComputedStyle(el).color), 'rgb(241, 242, 243)', 'email body inherits the active theme foreground')
  assert.equal(await page.getByRole('link', { name: 'https://www.youtube.com/watch?v=e7TY56-yIvM' }).evaluate(el => getComputedStyle(el).color), 'rgb(72, 149, 239)')
  await page.getByRole('link', { name: 'https://www.youtube.com/watch?v=e7TY56-yIvM' }).click()
  await page.waitForFunction(() => window.GmailEmailHarness.opened().length === 1)
  assert.deepEqual(await page.evaluate(() => window.GmailEmailHarness.opened()), ['https://www.youtube.com/watch?v=e7TY56-yIvM'])
  assert.equal(await page.locator('a[href]').count(), 0, 'auto-linked content never has native href navigation')
  await page.getByRole('link', { name: 'http://example.test/a?x=1&y=2' }).click()
  await page.waitForFunction(() => window.GmailEmailHarness.opened().length === 2)
  assert.equal((await page.evaluate(() => window.GmailEmailHarness.opened()))[1], 'http://example.test/a?x=1&y=2')
  await page.getByRole('link', { name: 'https://docs.anthropic.com/en/docs' }).focus()
  await page.getByRole('link', { name: 'https://docs.anthropic.com/en/docs' }).press('Enter')
  await page.waitForFunction(() => window.GmailEmailHarness.opened().length === 3)
  assert.equal((await page.evaluate(() => window.GmailEmailHarness.opened()))[2], 'https://docs.anthropic.com/en/docs')
  await page.getByRole('button', { name: 'Load images' }).click()
  await page.locator('img[src="https://i.ytimg.com/vi/e7TY56-yIvM/hqdefault.jpg"]').waitFor()
  await page.waitForFunction(() => document.querySelector('img')?.complete)
  assert.equal(await page.locator('img').evaluate(el => el.getBoundingClientRect().width <= el.parentElement.getBoundingClientRect().width), true, '480px image presentation width is constrained by the 360px email column')
  assert.equal(requests.length, 1, 'the YouTube thumbnail fetch occurs only after explicit opt-in')
  assert.deepEqual(imageReferrers, [''], 'the remote image request sends no Referer')
  assert.equal(await page.locator('img').getAttribute('referrerpolicy'), 'no-referrer')

  requests.length = 0
  const imageMarkup = '<p>Photo:</p><img data-email-src="https://images.example.test/a.png" alt="remote photo">'
  await page.evaluate(markup => window.GmailEmailHarness.mountEmail(markup), imageMarkup)
  await page.getByRole('button', { name: 'Load images' }).waitFor()
  await page.waitForTimeout(150)
  assert.deepEqual(requests, [], 'no remote image requests before explicit opt-in')
  assert.equal(await page.locator('img').count(), 0, 'remote image element is not rendered before opt-in')
  assert.equal(await page.getByText('Image blocked: remote photo').count(), 1, 'safe placeholder is rendered')
  assert.match(await page.getByText(/Remote images may track/).textContent(), /track/i)
  await page.getByRole('button', { name: 'Load images' }).click()
  await page.locator('img[src="https://images.example.test/a.png"]').waitFor()
  await page.waitForFunction(() => document.querySelector('img')?.complete)
  assert.equal(requests.length, 1, 'remote image element and request appear only after click')
  assert.equal(await page.locator('img').getAttribute('referrerpolicy'), 'no-referrer')
  assert.equal(await page.locator('button', { hasText: 'Hide images' }).count(), 1)
  await page.getByRole('button', { name: 'Hide images' }).click()
  assert.equal(await page.locator('img').count(), 0, 'hide disables all remote image sources again')

  const logoTiles = '<table width="640" style="width:640px"><tbody>' +
    '<tr><td colspan="3"><table width="100%" cellspacing="8" cellpadding="4"><tbody><tr>' +
    '<td width="48"><img data-email-src="https://images.example.test/tile-a.png" width="48" height="48" alt="Tile A">Tile A</td>' +
    '<td width="48"><img data-email-src="https://images.example.test/tile-b.png" width="48" height="48" alt="Tile B">Tile B</td>' +
    '<td width="48"><img data-email-src="https://images.example.test/tile-c.png" width="48" height="48" alt="Tile C">Tile C</td>' +
    '</tr></tbody></table></td></tr>' +
    '<tr><td colspan="3">&nbsp;</td></tr>' +
    '<tr><td colspan="3"><hr></td></tr>' +
    '</tbody></table>'
  const rewardsNewsletter = '<table width="680" style="width:680px"><tbody>' +
    '<tr><td colspan="2" style="padding:12px 16px;font-size:20px;font-weight:bold">Blue Rewards</td></tr>' +
    '<tr><td width="260"><img data-email-src="https://images.example.test/hero.png" width="640" height="240" alt="Rewards hero"></td>' +
    '<td width="420" style="padding:16px;border-bottom:1px solid #ccc;line-height:1.5">A synthetic newsletter offer with readable copy and an intentional separator.</td></tr>' +
    '</tbody></table>'
  for (const [label, markup, imageCount] of [
    ['nested logo tile grid', logoTiles, 3],
    ['newsletter hero and copy', rewardsNewsletter, 1]
  ]) {
    for (const viewport of [{ width: 360, height: 800 }, { width: 1200, height: 900 }]) {
      await page.setViewportSize(viewport)
      await page.evaluate(value => window.GmailEmailHarness.mountEmail(value), markup)
      const loadImages = page.getByRole('button', { name: `Load images (${imageCount})` })
      await loadImages.waitFor()
      await page.waitForFunction(() => document.querySelector('#root table'))
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, `${label} has no horizontal overflow at ${viewport.width}px`)
      assert.equal(await page.locator('#root table').evaluateAll(tables => tables.every(el => el.getBoundingClientRect().width <= el.parentElement.getBoundingClientRect().width + 1)), true, `${label} nested tables fit their pane at ${viewport.width}px`)
      assert.equal(await page.locator('#root td').evaluateAll(cells => cells.every(cell => cell.getBoundingClientRect().width <= cell.parentElement.getBoundingClientRect().width + 1)), true, `${label} cells stay within their rows at ${viewport.width}px`)
      if (viewport.width === 360) {
        assert.equal(await page.locator('#root td').first().evaluate(el => getComputedStyle(el).borderTopWidth), '0px', `${label} has no synthetic cell-outline borders`)
        assert.equal(await page.locator('#root td').evaluateAll(cells => cells.filter(cell => !String(cell.getAttribute('style') || '').includes('padding') && !cell.closest('table')?.hasAttribute('cellpadding')).every(cell => Number.parseFloat(getComputedStyle(cell).paddingTop) <= 1)), true, `${label} has no Hermes-added padding on cells without authored padding`)
      } else {
        assert.equal(await page.locator('#root tr').first().evaluate(el => getComputedStyle(el).display), 'table-row', `${label} preserves desktop table layout`)
      }
      if (label === 'newsletter hero and copy') {
        assert.equal(await page.locator('#root td').last().evaluate(el => getComputedStyle(el).borderBottomStyle), 'solid', 'intentional newsletter divider survives projection')
        assert.equal(await page.locator('#root td').last().evaluate(el => getComputedStyle(el).paddingTop), '16px', 'intentional newsletter padding survives projection')
        assert.match(await page.locator('#root').innerText(), /Blue Rewards[\s\S]*synthetic newsletter offer/, 'hero and copy remain readable text')
      }
      await loadImages.click()
      await page.locator('#root img').first().waitFor()
      assert.equal(await page.locator('#root img').evaluateAll(images => images.every(image => image.getBoundingClientRect().width <= image.parentElement.getBoundingClientRect().width + 1)), true, `${label} images fit their responsive cells at ${viewport.width}px`)
      if (viewport.width === 360) {
        assert.equal(await page.locator('#root hr').count(), label === 'nested logo tile grid' ? 1 : 0, `${label} preserves intentional dividers without inferring empty-row semantics`)
        assert.ok((await page.locator('#root').evaluate(el => el.getBoundingClientRect().height)) < 700, `${label} content height is bounded at 360px`)
      }
    }
  }

  for (const viewport of [{ width: 360, height: 800 }, { width: 1200, height: 900 }]) {
    await page.setViewportSize(viewport)
    await page.evaluate(markup => window.GmailEmailHarness.mountEmail(markup), reportedHtml)
    await page.getByRole('button', { name: /Load images \(1\)/ }).waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, `${viewport.width}px newsletter fixture does not overflow horizontally`)
  }

  await page.setViewportSize({ width: 360, height: 800 })
  requests.length = 0
  imageReferrers.length = 0
  const mailbox = await page.evaluate(() => { window.GmailEmailHarness.mountMailbox(); return true })
  await page.getByRole('button', { name: /Synthetic message 1/ }).waitFor()
  assert.deepEqual(pageErrors, [], 'synthetic mailbox mount has no React errors')
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.mailbox().calls.some(call => call.path.includes('/read'))), false, 'list render does not mark mail read')

  const m2Card = page.locator('article').filter({ hasText: 'Synthetic message 2' })
  const markM2Read = m2Card.getByRole('button', { name: 'Mark as read' })
  assert.equal(await markM2Read.count(), 1, 'unread cards expose an accessible Mark as read action')
  assert.deepEqual(await page.evaluate(() => window.GmailEmailHarness.mailbox().labels.m2), ['INBOX', 'UNREAD', 'IMPORTANT'], 'card actions have no implicit mutation')
  await markM2Read.focus()
  await markM2Read.press('Enter')
  const readPreview = page.getByLabel('Exact action preview')
  await readPreview.waitFor()
  const reviewedRead = JSON.parse(await readPreview.textContent())
  assert.equal(reviewedRead.action, 'labels')
  assert.equal(reviewedRead.message.id, 'm2')
  assert.deepEqual(reviewedRead.removeLabelIds, ['UNREAD'])
  assert.deepEqual(await page.evaluate(() => window.GmailEmailHarness.mailbox().labels.m2), ['INBOX', 'UNREAD', 'IMPORTANT'], 'mark-read is unchanged until explicit confirmation')
  const readPrepare = await page.evaluate(() => window.GmailEmailHarness.mailbox().calls.find(call => call.path === '/actions/prepare'))
  assert.deepEqual(readPrepare.body, { scope: 'synthetic_scope_12345678901234567890', action: 'labels', messageId: 'm2', addLabelIds: [], removeLabelIds: ['UNREAD'] }, 'Mark as read prepares the exact card message and only UNREAD removal')
  await page.getByRole('button', { name: 'Confirm action' }).click()
  await page.waitForFunction(() => !window.GmailEmailHarness.mailbox().labels.m2.includes('UNREAD'))
  await page.waitForFunction(() => !document.querySelector('article')?.innerText.includes('Synthetic message 2') ||
    ![...document.querySelectorAll('article')].find(card => card.innerText.includes('Synthetic message 2'))?.querySelector('button[aria-label="Mark as read"]'))
  assert.deepEqual(await page.evaluate(() => window.GmailEmailHarness.mailbox().labels.m2), ['INBOX', 'IMPORTANT'], 'verified read removes only UNREAD and preserves other labels')
  assert.deepEqual(await page.evaluate(() => window.GmailEmailHarness.mailbox().labels.m1), ['INBOX', 'UNREAD', 'STARRED'], 'marking m2 read does not change m1')
  await m2Card.getByText('Read', { exact: true }).waitFor()
  assert.equal(await m2Card.getByText('Read', { exact: true }).count(), 1, 'verified mark-read updates the card status')
  assert.equal(await m2Card.getByRole('button', { name: 'Mark as read' }).count(), 0, 'read card no longer offers Mark as read')
  const focusAfterRead = await page.evaluate(() => ({ tag: document.activeElement?.tagName, text: document.activeElement?.textContent?.trim(), role: document.activeElement?.getAttribute('role'), cls: document.activeElement?.className, label: document.activeElement?.getAttribute('aria-label') }))
  assert.equal(await m2Card.getByRole('button', { name: /Star/ }).evaluate(button => button === document.activeElement), true,
    `when Mark as read disappears, keyboard focus moves to a remaining m2 action; active=${JSON.stringify(focusAfterRead)}`)
  const refreshButton = page.getByRole('button', { name: 'Refresh' })
  const snippetBeforeRefresh = await m2Card.getByText(/^Privacy-safe fixture/).textContent()
  await refreshButton.focus()
  await refreshButton.click()
  assert.equal(await page.evaluate(() => document.activeElement?.textContent?.trim()), 'Refresh', 'Refresh owns focus before the result update')
  await page.waitForFunction(previous => {
    const card = [...document.querySelectorAll('article')].find(item => item.innerText.includes('Synthetic message 2'))
    const snippet = [...(card?.querySelectorAll('span') || [])].map(item => item.textContent).find(value => value.startsWith('Privacy-safe fixture'))
    return !!snippet && snippet !== previous
  }, snippetBeforeRefresh)
  assert.equal(await page.evaluate(() => document.activeElement?.textContent?.trim()), 'Refresh',
    'a later active search-result refetch does not steal focus back to the completed card')
  const readCommit = await page.evaluate(() => window.GmailEmailHarness.mailbox().calls.find(call => call.path === '/actions/commit'))
  assert.deepEqual(readCommit.body, { scope: 'synthetic_scope_12345678901234567890', confirmationToken: 'synthetic-ticket-1', confirmed: true }, 'read mutation commits only the reviewed authenticated ticket')
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.mailbox().calls.filter(call => call.path === '/actions/commit').length), 1, 'clicking Mark as read twice while busy cannot double-dispatch')

  await page.evaluate(() => window.GmailEmailHarness.mountMailbox({ deferredActionPrepare: true }))
  const pendingM2Read = page.locator('article').filter({ hasText: 'Synthetic message 2' }).getByRole('button', { name: 'Mark as read' })
  await pendingM2Read.click()
  await page.waitForFunction(() => window.GmailEmailHarness.mailbox().pendingActionPrepare.has('m2'))
  assert.equal(await page.getByRole('button', { name: 'Delete' }).first().isDisabled(), true, 'all card actions disable while a reviewed action is pending')
  assert.match(await page.getByRole('status').filter({ hasText: 'Preparing or verifying' }).textContent(), /Preparing or verifying/, 'pending work is announced to assistive technology')
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.mailbox().labels.m2.includes('UNREAD')), true, 'pending prepare performs no implicit label mutation')
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.resolveActionPrepare('m2')), true)
  await page.getByLabel('Exact action preview').waitFor()
  await page.getByRole('button', { name: 'Cancel' }).click()
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.mailbox().labels.m2.includes('UNREAD')), true, 'cancel leaves pending message unread')

  await page.evaluate(() => window.GmailEmailHarness.mountMailbox({ failActionPrepare: true }))
  const failedPrepareRead = page.locator('article').filter({ hasText: 'Synthetic message 2' }).getByRole('button', { name: 'Mark as read' })
  await failedPrepareRead.click()
  await page.getByRole('alert').filter({ hasText: 'Could not prepare this message action' }).waitFor()
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.mailbox().labels.m2.includes('UNREAD')), true, 'failed prepare does not change labels')
  assert.equal(await page.getByLabel('Exact action preview').textContent(), '', 'failed prepare does not show an unverified ticket')

  await page.evaluate(() => window.GmailEmailHarness.mountMailbox())
  const deleteM2 = page.locator('article').filter({ hasText: 'Synthetic message 2' }).getByRole('button', { name: 'Delete' })
  await deleteM2.click()
  await page.waitForFunction(() => {
    const preview = document.querySelector('[aria-label="Exact action preview"]')
    try { return JSON.parse(preview?.textContent || '').action === 'trash' } catch { return false }
  })
  const trashPreview = page.getByLabel('Exact action preview')
  const reviewedTrash = JSON.parse(await trashPreview.textContent())
  assert.equal(reviewedTrash.action, 'trash')
  assert.equal(reviewedTrash.message.id, 'm2')
  assert.match(reviewedTrash.effect, /move this message to Trash/i, 'Delete preview explicitly promises move to Trash')
  assert.deepEqual(await page.evaluate(() => window.GmailEmailHarness.mailbox().labels.m2), ['INBOX', 'UNREAD', 'IMPORTANT'], 'delete leaves message unchanged until confirmation')
  await page.getByRole('button', { name: 'Confirm action' }).click()
  await page.waitForFunction(() => ![...document.querySelectorAll('article')].find(card => card.innerText.includes('Synthetic message 2')))
  assert.deepEqual(await page.evaluate(() => window.GmailEmailHarness.mailbox().labels.m2), ['UNREAD', 'IMPORTANT', 'TRASH'], 'verified Delete moves m2 to Trash and removes only INBOX')
  assert.deepEqual(await page.evaluate(() => window.GmailEmailHarness.mailbox().labels.m1), ['INBOX', 'UNREAD', 'STARRED'], 'deleting m2 leaves m1 unchanged')
  const trashPrepare = await page.evaluate(() => window.GmailEmailHarness.mailbox().calls.find(call => call.path === '/actions/prepare' && call.body.action === 'trash'))
  assert.equal(trashPrepare.body.messageId, 'm2', 'Delete targets the exact card id')
  const trashCommit = await page.evaluate(() => window.GmailEmailHarness.mailbox().calls.find(call => call.path === '/actions/commit' && call.body.confirmationToken === 'synthetic-ticket-1'))
  assert.deepEqual(trashCommit.body, { scope: 'synthetic_scope_12345678901234567890', confirmationToken: 'synthetic-ticket-1', confirmed: true }, 'Delete commits its reviewed ticket')

  await page.evaluate(() => window.GmailEmailHarness.mountMailbox())
  await page.getByRole('button', { name: /Synthetic message 1/ }).waitFor()
  await page.getByRole('button', { name: /Synthetic message 1/ }).click()
  await page.getByRole('button', { name: /← Inbox/ }).waitFor()
  const openM1DeleteM2 = page.locator('article').filter({ hasText: 'Synthetic message 2' }).getByRole('button', { name: 'Delete' })
  await openM1DeleteM2.click()
  await page.getByLabel('Exact action preview').waitFor()
  assert.equal(await page.getByRole('button', { name: /← Inbox/ }).count(), 1, 'another message stays open while reviewing this card action')
  await page.getByRole('button', { name: 'Confirm action' }).click()
  await page.waitForFunction(() => window.GmailEmailHarness.mailbox().labels.m2.includes('TRASH'))
  assert.equal(await page.getByRole('button', { name: /← Inbox/ }).count(), 1, 'trashing m2 does not close unrelated open m1 detail')
  assert.equal(await page.getByRole('heading', { name: 'Synthetic message 1' }).count(), 1, 'unrelated m1 remains the selected detail')

  await page.evaluate(() => window.GmailEmailHarness.mountMailbox({ staleActionCommit: true }))
  const staleM2Delete = page.locator('article').filter({ hasText: 'Synthetic message 2' }).getByRole('button', { name: 'Delete' })
  await staleM2Delete.click()
  await page.getByLabel('Exact action preview').waitFor()
  await page.getByRole('button', { name: 'Confirm action' }).click()
  await page.getByRole('alert').filter({ hasText: 'Action was not verified' }).waitFor()
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.mailbox().labels.m2.includes('TRASH')), false, 'stale exact snapshot fails closed without trashing the message')
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.mailbox().calls.filter(call => call.path === '/actions/commit').length), 1, 'failed verified action is not automatically retried')

  await page.evaluate(() => window.GmailEmailHarness.mountMailbox())
  await page.getByRole('button', { name: 'More' }).last().click()
  const deleteConfirmSetting = page.getByRole('menuitemcheckbox', { name: /Confirm before deleting/ })
  assert.equal(await deleteConfirmSetting.getAttribute('aria-checked'), 'true', 'delete confirmation is enabled by default')
  await deleteConfirmSetting.click()
  const optOutDelete = page.locator('article').filter({ hasText: 'Synthetic message 2' }).getByRole('button', { name: 'Delete' })
  await optOutDelete.click()
  await page.waitForFunction(() => window.GmailEmailHarness.mailbox().labels.m2.includes('TRASH'))
  assert.equal((await page.getByLabel('Exact action preview').textContent()).trim(), '', 'saved delete-confirmation opt-out skips only the second UI dialog')
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.mailbox().calls.filter(call => call.path === '/actions/prepare').length), 1, 'opt-out still obtains the authenticated backend ticket')
  const optedOutCommit = await page.evaluate(() => window.GmailEmailHarness.mailbox().calls.find(call => call.path === '/actions/commit'))
  assert.deepEqual(optedOutCommit.body, { scope: 'synthetic_scope_12345678901234567890', confirmationToken: 'synthetic-ticket-1', confirmed: true }, 'opt-out still commits the exact prepared card action ticket')

  await page.evaluate(() => window.GmailEmailHarness.mountMailbox())
  const openedM2Card = page.locator('article').filter({ hasText: 'Synthetic message 2' })
  await openedM2Card.getByRole('button', { name: /Synthetic message 2/ }).waitFor()
  await page.evaluate(() => {
    const cache = window.GmailEmailHarness.queryClient()
    const active = cache.getQueryCache().getAll().find(query => query.queryKey.includes('search') && query.queryKey.at(-1) === '')
    if (!active) throw new Error('active synthetic inbox search query is missing')
    window.GmailEmailHarness.inactiveSearchKey = [...active.queryKey.slice(0, -1), 'synthetic-inactive-page']
    cache.setQueryData(window.GmailEmailHarness.inactiveSearchKey, {
      ...active.state.data,
      messages: active.state.data.messages.map(message => message.id === 'm2'
        ? { ...message, labelIds: ['INBOX', 'UNREAD', 'IMPORTANT', 'INACTIVE_PAGE_SENTINEL'] } : message)
    })
  })
  await openedM2Card.getByRole('button', { name: /Synthetic message 2/ }).click()
  await page.waitForFunction(() => window.GmailEmailHarness.mailbox().calls.some(call => call.path.includes('/messages/m2/read?')) &&
    !window.GmailEmailHarness.mailbox().labels.m2.includes('UNREAD'))
  await openedM2Card.getByText('Read', { exact: true }).waitFor()
  assert.equal(await openedM2Card.getByRole('button', { name: 'Mark as read' }).count(), 0,
    'verified read-on-open updates the visible card from backend-confirmed labels without reload')
  assert.deepEqual(await page.evaluate(() => {
    const active = window.GmailEmailHarness.queryClient().getQueryCache().getAll()
      .find(query => query.queryKey.includes('search') && query.queryKey.at(-1) === '')
    return active.state.data.messages.find(item => item.id === 'm2').labelIds
  }), ['INBOX', 'IMPORTANT'], 'active page cache uses exact verified backend labels')
  assert.deepEqual(await page.evaluate(() => {
    const active = window.GmailEmailHarness.queryClient().getQueryCache().getAll()
      .find(query => query.queryKey.includes('search') && query.queryKey.at(-1) === '')
    return active.state.data.messages.find(item => item.id === 'm1').labelIds
  }), ['INBOX', 'UNREAD', 'STARRED'], 'read-on-open leaves other active-page cards untouched')
  assert.deepEqual(await page.evaluate(() => {
    const message = window.GmailEmailHarness.queryClient().getQueryData(window.GmailEmailHarness.inactiveSearchKey)
      .messages.find(item => item.id === 'm2')
    return message.labelIds
  }), ['INBOX', 'UNREAD', 'IMPORTANT', 'INACTIVE_PAGE_SENTINEL'], 'read-on-open updates only the current page search cache')

  await page.evaluate(() => window.GmailEmailHarness.mountMailbox({ failRead: true }))
  const failedOpenedM2Card = page.locator('article').filter({ hasText: 'Synthetic message 2' })
  await failedOpenedM2Card.getByRole('button', { name: /Synthetic message 2/ }).click()
  await page.getByRole('alert').filter({ hasText: 'Could not verify read status.' }).waitFor()
  await failedOpenedM2Card.getByText('Unread', { exact: true }).waitFor()
  assert.equal(await failedOpenedM2Card.getByRole('button', { name: 'Mark as read' }).count(), 1,
    'failed read-on-open does not change card state or claim read status')
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.mailbox().labels.m2.includes('UNREAD')), true,
    'failed read-on-open leaves backend message unread')

  await page.evaluate(() => window.GmailEmailHarness.mountMailbox({ deferredReads: true }))
  const pendingOpenedM2Card = page.locator('article').filter({ hasText: 'Synthetic message 2' })
  await pendingOpenedM2Card.getByRole('button', { name: /Synthetic message 2/ }).click()
  await page.waitForFunction(() => window.GmailEmailHarness.mailbox().pending.has('m2'))
  for (const name of ['☆ Star', 'Mark as read', 'Delete']) {
    assert.equal(await pendingOpenedM2Card.getByRole('button', { name }).isDisabled(), true,
      `m2 ${name} stays disabled until read-on-open verification completes`)
  }
  const unrelatedM1Card = page.locator('article').filter({ hasText: 'Synthetic message 1' })
  for (const name of ['★ Unstar', 'Mark as read', 'Delete']) {
    assert.equal(await unrelatedM1Card.getByRole('button', { name }).isDisabled(), false,
      `pending m2 read-on-open does not disable unrelated m1 ${name}`)
  }
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.resolveRead('m2')), true)
  await pendingOpenedM2Card.getByText('Read', { exact: true }).waitFor()
  assert.equal(await pendingOpenedM2Card.getByRole('button', { name: 'Mark as read' }).count(), 0,
    'read-on-open success removes the card action after exact backend readback')

  await page.evaluate(() => window.GmailEmailHarness.mountMailbox({ deferredReads: true }))
  const racedM2Card = page.locator('article').filter({ hasText: 'Synthetic message 2' })
  const racedM1Card = page.locator('article').filter({ hasText: 'Synthetic message 1' })
  await racedM2Card.getByRole('button', { name: /Synthetic message 2/ }).click()
  await page.waitForFunction(() => window.GmailEmailHarness.mailbox().pending.has('m2'))
  await racedM1Card.getByRole('button', { name: /Synthetic message 1/ }).click()
  await page.waitForFunction(() => window.GmailEmailHarness.mailbox().pending.has('m1'))
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.resolveRead('m1')), true)
  await racedM1Card.getByText('Read', { exact: true }).waitFor()
  for (const name of ['★ Unstar', 'Delete']) {
    assert.equal(await racedM1Card.getByRole('button', { name }).isDisabled(), false,
      `verified m1 read-on-open leaves unrelated card action ${name} operable while m2 is pending`)
  }
  assert.equal(await racedM1Card.getByRole('button', { name: /Synthetic message 1/ }).getAttribute('aria-pressed'), 'true',
    'm1 remains selected while m2 read-on-open is pending')
  const m1LabelsAfterItsOwnRead = await page.evaluate(() => window.GmailEmailHarness.mailbox().labels.m1)
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.resolveRead('m2')), true)
  await racedM2Card.getByText('Read', { exact: true }).waitFor()
  assert.equal(await racedM2Card.getByRole('button', { name: 'Mark as read' }).count(), 0,
    'verified m2 readback updates its card after selection moves to m1')
  assert.deepEqual(await page.evaluate(() => window.GmailEmailHarness.mailbox().labels.m1), m1LabelsAfterItsOwnRead,
    'late m2 readback does not mutate m1')
  assert.equal(await racedM1Card.getByRole('button', { name: /Synthetic message 1/ }).getAttribute('aria-pressed'), 'true',
    'late m2 readback does not change selection from m1')

  await page.evaluate(() => window.GmailEmailHarness.mountMailbox())
  await page.getByRole('button', { name: /Synthetic message 1/ }).waitFor()
  await page.getByRole('button', { name: /Synthetic message 1/ }).click()
  await page.getByRole('button', { name: /Load images \(1\)/ }).waitFor()
  await page.waitForFunction(() => window.GmailEmailHarness.mailbox().calls.filter(call => call.path.includes('/read')).length === 1)
  assert.deepEqual(await page.evaluate(() => window.GmailEmailHarness.mailbox().labels.m1), ['INBOX', 'STARRED'], 'open removes only UNREAD and preserves other labels')
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.mailbox().calls.filter(call => call.path.includes('/read')).length), 1, 'one read-on-open request per selected unread message')
  assert.equal(requests.length, 0, 'persistent setting defaults off; opening email does not fetch remote images')
  const imagesSetting = page.getByRole('menuitemcheckbox', { name: /Always show images/ })
  assert.equal(await imagesSetting.getAttribute('aria-checked'), 'false', 'persistent image preference exposes its default unchecked state')
  await imagesSetting.click()
  assert.equal(await imagesSetting.getAttribute('aria-checked'), 'true', 'persistent image preference exposes checked state')
  await page.locator('img[src="https://images.example.test/fixture.png"]').waitFor()
  await page.waitForFunction(() => document.querySelector('img')?.complete)
  assert.equal(requests.length, 1, 'explicit persistent setting opt-in enables validated image fetch')
  assert.deepEqual(imageReferrers, [''], 'persistent opt-in sends no Referer')
  await page.waitForFunction(() => [...window.GmailEmailHarness.mailbox().storage.values()].some(settings => settings.alwaysShowImages === true))
  await page.getByRole('button', { name: /← Inbox/ }).click()
  await page.getByRole('button', { name: /Synthetic message 1/ }).click()
  await page.waitForTimeout(50)
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.mailbox().calls.filter(call => call.path.includes('/read')).length), 1, 'reopening verified read message does not repeat the operation')
  await imagesSetting.click()
  assert.equal(await page.locator('img').count(), 0, 'disabling persistent consent immediately removes image src')
  await page.getByRole('button', { name: /Load images \(1\)/ }).waitFor()
  assert.equal(await imagesSetting.getAttribute('aria-checked'), 'false', 'turning off consent immediately restores the unchecked state')
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.mailbox().storage.size), 1, 'Always show images is persisted in profile/account-scoped settings')

  await page.evaluate(() => window.GmailEmailHarness.mountMailbox({ deferredReads: true }))
  await page.getByRole('button', { name: /Synthetic message 1/ }).waitFor()
  await page.getByRole('button', { name: /Synthetic message 1/ }).click()
  await page.waitForFunction(() => window.GmailEmailHarness.mailbox().pending.has('m1'))
  await page.getByRole('button', { name: /← Inbox/ }).click()
  await page.getByRole('button', { name: /Synthetic message 2/ }).click()
  await page.waitForFunction(() => window.GmailEmailHarness.mailbox().pending.has('m2'))
  await page.evaluate(() => window.GmailEmailHarness.resolveRead('m1'))
  await page.waitForFunction(() => !window.GmailEmailHarness.mailbox().labels.m1.includes('UNREAD'))
  await page.getByRole('button', { name: 'More' }).last().click()
  assert.notEqual(await page.getByText('Mark as read (review first)').getAttribute('aria-disabled'), 'true', 'late verified readback for m1 does not mutate selected unread m2')
  await page.keyboard.press('Escape')

  await page.evaluate(() => window.GmailEmailHarness.mountMailbox({ failRead: true }))
  await page.getByRole('button', { name: /Synthetic message 1/ }).click()
  await page.getByRole('alert').filter({ hasText: 'Could not verify read status.' }).waitFor()
  assert.match(await page.getByRole('alert').textContent(), /Refresh Gmail to check; unread state remains pending\./, 'read-on-open failure copy does not claim remote state when backend verification is unavailable')
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.mailbox().labels.m1.includes('UNREAD')), true, 'failed read verification leaves fixture and visible unread state unchanged')

  await page.evaluate(() => window.GmailEmailHarness.mountMailbox({ deferredReads: true, failReadIds: ['m1'] }))
  await page.getByRole('button', { name: /Synthetic message 1/ }).click()
  await page.waitForFunction(() => window.GmailEmailHarness.mailbox().pending.has('m1'))
  await page.getByRole('button', { name: /← Inbox/ }).click()
  await page.getByRole('button', { name: /Synthetic message 2/ }).click()
  await page.waitForFunction(() => window.GmailEmailHarness.mailbox().pending.has('m2'))
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.rejectRead('m1')), true)
  await page.waitForTimeout(50)
  assert.equal(await page.getByRole('alert').filter({ hasText: 'Could not verify read status.' }).count(), 0, 'stale m1 failure does not show feedback while m2 is selected')
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.mailbox().labels.m2.includes('UNREAD')), true, 'stale m1 failure does not mutate selected m2')
  await page.getByRole('button', { name: /← Inbox/ }).click()
  await page.getByRole('button', { name: /Synthetic message 1/ }).click()
  await page.waitForFunction(() => window.GmailEmailHarness.mailbox().pending.has('m1'))
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.mailbox().calls.filter(call => call.path.includes('/messages/m1/read')).length), 2, 'reopening after a stale failure sends a new read request')
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.rejectRead('m1')), true)
  await page.getByRole('alert').filter({ hasText: 'Could not verify read status.' }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Retry read status' }).count(), 1, 'current failed read request offers retry UI')
  await page.getByRole('button', { name: 'Retry read status' }).click()
  await page.waitForFunction(() => window.GmailEmailHarness.mailbox().pending.has('m1'))
  assert.equal(await page.evaluate(() => window.GmailEmailHarness.mailbox().calls.filter(call => call.path.includes('/messages/m1/read')).length), 3, 'retry control starts another read request')
  await page.evaluate(() => window.GmailEmailHarness.rejectRead('m1'))

  const linkMarkup = '<a href="https://example.test/path?q=1&amp;x=2">trusted link</a> <a href="javascript:alert(1)">unsafe link</a> <a href="mailto:person@example.test">mail</a>'
  await page.evaluate(markup => window.GmailEmailHarness.mountEmail(markup), linkMarkup)
  await page.getByRole('link', { name: 'trusted link' }).click()
  await page.waitForFunction(() => window.GmailEmailHarness.opened().length === 1)
  assert.deepEqual(await page.evaluate(() => window.GmailEmailHarness.opened()), ['https://example.test/path?q=1&x=2'])
  assert.equal(await page.locator('a[href]').count(), 0, 'safe email links do not retain native navigation hrefs')
  assert.equal(await page.getByText('unsafe link').getAttribute('href'), null, 'unsafe destination is never retained in the DOM')
  assert.equal(await page.getByText('unsafe link').getAttribute('role'), null, 'unsafe destination is not exposed as an actionable link')
  const mailLink = page.getByRole('link', { name: 'mail' })
  await mailLink.focus()
  await mailLink.press('Enter')
  await page.waitForFunction(() => window.GmailEmailHarness.opened().length === 2)
  assert.equal((await page.evaluate(() => window.GmailEmailHarness.opened()))[1], 'mailto:person@example.test')

  await page.evaluate(() => window.GmailEmailHarness.mountThread('user+alias@example.com', '1a0f8274c110fca1'))
  await page.getByRole('button', { name: 'Open in browser' }).waitFor()
  assert.deepEqual(await page.evaluate(() => window.GmailEmailHarness.opened()), [], 'does not auto-open Gmail')
  await page.getByRole('button', { name: 'Open in browser' }).click()
  await page.waitForFunction(() => window.GmailEmailHarness.opened().length === 1)
  assert.deepEqual(await page.evaluate(() => window.GmailEmailHarness.opened()), [
    'https://mail.google.com/mail/u/0/?authuser=user%2Balias%40example.com#all/1a0f8274c110fca1'
  ])
  await page.evaluate(() => window.GmailEmailHarness.mountThread('not an email', 'bad/thread'))
  assert.equal(await page.getByRole('button', { name: 'Open in browser' }).count(), 0, 'invalid mailbox or thread identity is not actionable')
  console.log('PASS browser interactions: reported YouTube fixture, inert image opt-in/no-referrer, safe text URL pointer/keyboard bridge, theme foreground, invalid-link inertness, numeric-slot Gmail route')
} finally {
  await browser.close()
}
