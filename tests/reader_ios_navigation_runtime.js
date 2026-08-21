const assert = require('node:assert/strict')
const { webkit } = require('playwright')

const readerUrl = process.env.OOHSTORY_READER_URL
  || 'http://127.0.0.1:8091/books/demo-book/chapters/2'
const readerOrigin = process.env.OOHSTORY_READER_ORIGIN
  || 'https://reader.example.com'

;(async () => {
  const browser = await webkit.launch({ headless: true })
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1',
    extraHTTPHeaders: { Origin: readerOrigin }
  })
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.addInitScript(() => {
    localStorage.setItem('oohstory-reader', JSON.stringify({ mode: 'slide' }))
  })

  try {
    await page.goto(readerUrl, { waitUntil: 'domcontentloaded' })
    await page.locator('.reader-content').waitFor({ state: 'visible', timeout: 30000 })

    const beforeHistory = await page.evaluate(() => history.length)
    await page.evaluate(() => {
      const next = document.querySelector('.reader-nav-actions button:last-child')
      if (!next) throw new Error('mobile next button missing')
      next.click()
      next.click()
    })
    await page.waitForFunction(() => document.querySelector('.reader-content'))
    const afterHistory = await page.evaluate(() => history.length)
    assert.equal(afterHistory, beforeHistory + 1, 'duplicate mobile taps must create one chapter navigation')

    await page.evaluate(() => {
      document.querySelector('.reader-nav-actions button:nth-child(2)')?.click()
    })
    await page.locator('.reader-chapter-item').nth(8).waitFor({ state: 'attached' })
    const targets = await page.evaluate(() => {
      const links = [...document.querySelectorAll('.reader-chapter-item')]
        .filter(link => !link.classList.contains('active'))
      return [links[6]?.href, links[7]?.href]
    })
    assert.ok(targets[0] && targets[1], 'catalog must provide two race targets')
    const firstId = new URL(targets[0]).pathname.split('/').pop()
    const secondId = new URL(targets[1]).pathname.split('/').pop()

    await page.route('**/api/v1/books/*/chapters/*', async route => {
      const chapterId = new URL(route.request().url()).pathname.split('/').pop()
      if (chapterId === firstId) await new Promise(resolve => setTimeout(resolve, 1200))
      if (chapterId === secondId) await new Promise(resolve => setTimeout(resolve, 40))
      await route.continue()
    })
    await page.evaluate(([first, second]) => {
      const firstLink = [...document.querySelectorAll('.reader-chapter-item')]
        .find(link => link.href === first)
      firstLink?.click()
      history.pushState(null, '', second)
      window.dispatchEvent(new PopStateEvent('popstate'))
    }, targets)
    await page.waitForURL(url => url.pathname.endsWith(`/chapters/${secondId}`), { timeout: 30000 })
    await page.locator('.reader-content').waitFor({ state: 'visible', timeout: 30000 })
    await page.waitForTimeout(1500)
    await page.evaluate(() => {
      document.querySelector('.reader-nav-actions button:nth-child(2)')?.click()
    })
    await page.waitForFunction(
      chapterId => document.querySelector('.reader-chapter-item.active')?.href.endsWith(`/chapters/${chapterId}`),
      secondId
    )
    const result = await page.evaluate(() => ({
      pathname: location.pathname,
      activeHref: document.querySelector('.reader-chapter-item.active')?.href || '',
      progress: JSON.parse(localStorage.getItem('oohstory-reading-progress') || '{}')
    }))
    assert.ok(
      result.pathname.endsWith(`/chapters/${secondId}`),
      `latest reader route must remain visible: ${JSON.stringify(result)}`
    )
    assert.ok(
      result.activeHref.endsWith(`/chapters/${secondId}`),
      `stale response must not replace the active chapter: ${JSON.stringify(result)}`
    )
    const saved = Object.values(result.progress.books || {})[0]
    assert.equal(String(saved?.chapterId), String(secondId), 'latest chapter must own the reading record')
    assert.deepEqual(errors, [], 'iOS WebKit reader must not raise page errors')
    console.log('iOS WebKit reader navigation race checks passed')
  } finally {
    await context.close()
    await browser.close()
  }
})().catch(error => {
  console.error(error)
  process.exitCode = 1
})
