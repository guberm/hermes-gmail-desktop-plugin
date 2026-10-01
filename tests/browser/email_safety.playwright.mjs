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
        export const useValue = () => null, useQuery = () => ({}), useMutation = () => ({}), useQueryClient = () => ({})
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
  await page.route('https://images.example.test/**', async route => {
    requests.push(route.request().url())
    await route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ZcAAAAASUVORK5CYII=', 'base64') })
  })
  await page.route('**/*', route => {
    if (route.request().url().startsWith('https://images.example.test/')) return route.fallback()
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

  const formattedMarkup = '<p>first line<br>second<br>third<hr>fourth</p>'
  await page.evaluate(markup => window.GmailEmailHarness.mountEmail(markup), formattedMarkup)
  await page.waitForTimeout(50)
  assert.deepEqual(pageErrors, [], 'realistic email void elements render without React errors')
  assert.equal(await page.locator('br').count(), 2, 'email line breaks render')
  assert.equal(await page.locator('hr').count(), 1, 'email horizontal rule renders')
  assert.equal(await page.locator('#root').innerText(), 'first line\nsecond\nthird\n\nfourth')

  const imageMarkup = '<p>Photo:</p><img data-email-src="https://images.example.test/a.png" alt="remote photo">'
  await page.evaluate(markup => window.GmailEmailHarness.mountEmail(markup), imageMarkup)
  await page.getByRole('button', { name: 'Load images' }).waitFor()
  await page.waitForTimeout(150)
  assert.deepEqual(requests, [], 'no remote image requests before explicit opt-in')
  assert.equal(await page.locator('img').count(), 0, 'remote image element is not rendered before opt-in')
  assert.equal(await page.getByText('[Image: remote photo]').count(), 1, 'safe placeholder is rendered')
  assert.match(await page.getByText(/Remote images can reveal that you opened this email/).textContent(), /tracking/i)
  await page.getByRole('button', { name: 'Load images' }).click()
  await page.locator('img[src="https://images.example.test/a.png"]').waitFor()
  await page.waitForFunction(() => document.querySelector('img')?.complete)
  assert.equal(requests.length, 1, 'remote image element and request appear only after click')
  assert.equal(await page.locator('img').getAttribute('referrerpolicy'), 'no-referrer')
  assert.equal(await page.locator('button', { hasText: 'Hide images' }).count(), 1)
  await page.getByRole('button', { name: 'Hide images' }).click()
  assert.equal(await page.locator('img').count(), 0, 'hide disables all remote image sources again')

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

  await page.evaluate(() => window.GmailEmailHarness.mountThread('user+alias@example.com', 'thread-123_abc'))
  await page.getByRole('button', { name: 'Open in browser' }).waitFor()
  assert.deepEqual(await page.evaluate(() => window.GmailEmailHarness.opened()), [], 'does not auto-open Gmail')
  await page.getByRole('button', { name: 'Open in browser' }).click()
  await page.waitForFunction(() => window.GmailEmailHarness.opened().length === 1)
  assert.deepEqual(await page.evaluate(() => window.GmailEmailHarness.opened()), [
    'https://mail.google.com/mail/u/user%2Balias%40example.com/#all/thread-123_abc'
  ])
  await page.evaluate(() => window.GmailEmailHarness.mountThread('not an email', 'bad/thread'))
  assert.equal(await page.getByRole('button', { name: 'Open in browser' }).count(), 0, 'invalid mailbox or thread identity is not actionable')
  console.log('PASS browser interactions: external image opt-in, no-referrer, hide, safe-link bridge, invalid-link inertness, explicit encoded Gmail thread opening')
} finally {
  await browser.close()
}
