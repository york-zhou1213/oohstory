const assert = require('node:assert/strict')
const { chromium } = require('playwright')

const readerUrl = process.env.OOHSTORY_READER_URL
  || 'https://reader.example.com/#/read/oV5RFWgNywznCvqzb2Iqdg/2'

async function verifySurface(browser, label, viewport) {
  const context = await browser.newContext({ viewport })
  const page = await context.newPage()
  const pageErrors = []
  page.on('pageerror', error => pageErrors.push(String(error)))
  await page.addInitScript(() => {
    const NativeAudio = window.Audio
    window.__oohAudioProbe = []
    window.Audio = function (...args) {
      const audio = new NativeAudio(...args)
      window.__oohAudioProbe.push(audio)
      return audio
    }
    window.Audio.prototype = NativeAudio.prototype
  })
  await page.goto(readerUrl, { waitUntil: 'domcontentloaded' })
  await page.locator('.reader-content').waitFor({ state: 'visible', timeout: 30000 })
  const firstSegment = page.waitForResponse(response =>
    response.request().method() === 'POST'
      && /\/api\/v1\/audiobook\/sessions\/[0-9a-f]{32}\/segments\/[0-9a-f]{64}\/\d+$/.test(new URL(response.url()).pathname)
      && response.status() === 200,
  { timeout: 60000 })
  if (viewport.width <= 720) {
    await page.getByRole('button', { name: '设置', exact: true }).evaluate(element => element.click())
    await page.locator('.reader-settings-panel').waitFor({ state: 'visible' })
    await page.locator('.reader-settings-panel button').filter({ hasText: /^听书$/ }).click()
  } else {
    await page.locator('button[title="听书"]').click()
  }
  await page.locator('#tts-player').waitFor({ state: 'visible' })
  await firstSegment
  await page.waitForTimeout(4000)
  const state = await page.evaluate(() => {
    const audio = window.__oohAudioProbe[0]
    return {
      count: window.__oohAudioProbe.length,
      paused: audio.paused,
      muted: audio.muted,
      volume: audio.volume,
      readyState: audio.readyState,
      currentTime: audio.currentTime,
      duration: audio.duration,
      networkState: audio.networkState,
      error: audio.error ? { code: audio.error.code, message: audio.error.message } : null,
      canPlayMpeg: audio.canPlayType('audio/mpeg'),
      source: audio.src.startsWith('blob:') ? 'blob' : new URL(audio.src).protocol,
      src: audio.src,
      heading: document.querySelector('#tts-player-heading')?.textContent || '',
      playerVisible: !document.querySelector('#tts-player')?.hidden,
    }
  })
  console.log(`${label}: ${JSON.stringify(state)}`)
  assert.equal(state.count, 1, `${label}: playback must reuse one Audio element`)
  assert.equal(state.paused, false, `${label}: audio must be playing`)
  assert.equal(state.muted, false, `${label}: audio must not be muted`)
  assert.equal(state.volume, 1, `${label}: volume must be audible`)
  assert.ok(state.readyState >= 2, `${label}: audio must have playable data`)
  assert.ok(state.currentTime > 0.1, `${label}: playback position must advance`)
  assert.equal(state.playerVisible, true, `${label}: player must remain visible`)
  assert.equal(pageErrors.length, 0, `${label}: page errors: ${pageErrors.join('; ')}`)
  await page.locator('#tts-player-stop').click()
  await context.close()
  return state
}

;(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.OOHSTORY_CHROMIUM_EXECUTABLE || undefined,
  })
  try {
    const mobile = await verifySurface(browser, 'mobile-web', { width: 390, height: 844 })
    const desktop = await verifySurface(browser, 'desktop-web', { width: 1440, height: 900 })
    console.log(JSON.stringify({ mobile, desktop }))
  } finally {
    await browser.close()
  }
})().catch(error => {
  console.error(error)
  process.exitCode = 1
})
