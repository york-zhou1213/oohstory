const assert = require('node:assert/strict')
const { webkit } = require('playwright')

const READER_URL = process.env.OOHSTORY_READER_URL
  || 'http://localhost:8091/#/read/oV5RFWgNywznCvqzb2Iqdg/2'

const fakeAudioScript = () => {
  Object.defineProperty(navigator, 'standalone', { configurable: true, get: () => true })
  window.__ttsProbe = {
    instances: [], plays: [], pauses: 0, loads: 0,
    rejectNext: false, invalidateAuthorizationOnLoad: false
  }
  class FakeAudio {
    constructor() {
      this.paused = true
      this.ended = false
      this.currentTime = 0
      this.preload = 'auto'
      this.src = ''
      this.authorized = true
      this.onended = null
      this.onerror = null
      window.__ttsProbe.instances.push(this)
    }
    play() {
      this.paused = false
      this.ended = false
      window.__ttsProbe.plays.push(this.src)
      if (window.__ttsProbe.rejectNext || !this.authorized) {
        window.__ttsProbe.rejectNext = false
        this.paused = true
        const error = new DOMException('User gesture required', 'NotAllowedError')
        return Promise.reject(error)
      }
      return Promise.resolve()
    }
    pause() {
      this.paused = true
      window.__ttsProbe.pauses++
    }
    load() {
      window.__ttsProbe.loads++
      if (window.__ttsProbe.invalidateAuthorizationOnLoad) this.authorized = false
    }
    removeAttribute(name) {
      if (name === 'src') this.src = ''
    }
    finish() {
      this.paused = true
      this.ended = true
      if (this.onended) this.onended()
    }
    fail() {
      this.paused = true
      if (this.onerror) this.onerror(new Event('error'))
    }
  }
  window.Audio = FakeAudio
  localStorage.setItem('oohstory-reader', JSON.stringify({
    ttsMode: 'normal',
    ttsVoice: 'nuanxi',
    ttsNarrator: 'mocheng',
    ttsRate: 1
  }))
}

async function openReader(page) {
  await page.goto(READER_URL, { waitUntil: 'domcontentloaded' })
  await page.locator('.reader-content').waitFor({ state: 'visible', timeout: 30000 })
  await page.getByRole('button', { name: '设置', exact: true }).evaluate(element => element.click())
  await page.locator('.reader-settings-panel').waitFor({ state: 'visible' })
}

async function startFromSettings(page) {
  await page.locator('.reader-settings-panel button').filter({ hasText: /^听书$/ }).click()
  await page.waitForFunction(() => window.__ttsProbe.plays.length >= 1)
}

async function testHotSwitch(browser) {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148'
  })
  const page = await context.newPage()
  await page.addInitScript(fakeAudioScript)
  await page.route('**/api/v1/tts/speak?**', route => route.abort('failed'))
  await openReader(page)
  await startFromSettings(page)
  await page.getByRole('button', { name: '设置', exact: true }).evaluate(element => element.click())
  const mode = page.locator('select').filter({ has: page.locator('option[value="smart"]') })
  await mode.selectOption('smart')

  const beforeEnd = await page.evaluate(() => ({
    plays: window.__ttsProbe.plays.length,
    pauses: window.__ttsProbe.pauses,
    loads: window.__ttsProbe.loads,
    standalone: navigator.standalone
  }))
  assert.equal(beforeEnd.standalone, true)
  assert.equal(beforeEnd.plays, 1, 'switch must not replace the currently playing sentence')
  assert.equal(beforeEnd.pauses, 0, 'switch must not pause the unlocked iOS Audio element')
  assert.equal(beforeEnd.loads, 0, 'switch must not call load() and lose iOS media authorization')

  for (let index = 0; index < 24; index++) {
    await page.evaluate(() => window.__ttsProbe.instances[0].finish())
    await page.waitForTimeout(5)
  }
  const plays = await page.evaluate(() => window.__ttsProbe.plays.slice())
  const voices = new Set(plays.slice(1).map(value => new URL(value, 'http://localhost:8091').searchParams.get('voice')).filter(Boolean))
  assert.ok(voices.has('mocheng'), `smart plan should contain narrator voice, got ${[...voices]}`)
  assert.ok(voices.size >= 2, `smart plan should use multiple voices, got ${[...voices]}`)
  await context.close()
}

async function testPolicyRejection(browser) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const page = await context.newPage()
  await page.addInitScript(fakeAudioScript)
  await page.route('**/api/v1/tts/speak?**', route => route.abort('failed'))
  await openReader(page)
  await page.evaluate(() => { window.__ttsProbe.rejectNext = true })
  await startFromSettings(page)
  await page.getByRole('button', { name: '点击继续听书', exact: true }).waitFor({ state: 'visible' })
  const rejectedUrl = await page.evaluate(() => window.__ttsProbe.plays.at(-1))
  await page.getByRole('button', { name: '点击继续听书', exact: true }).click()
  await page.waitForFunction(() => window.__ttsProbe.plays.length >= 2)
  const resumedUrl = await page.evaluate(() => window.__ttsProbe.plays.at(-1))
  assert.equal(resumedUrl, rejectedUrl, 'Safari rejection must resume the same item instead of skipping it')
  await context.close()
}

async function testDialogueAudioError(browser) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const page = await context.newPage()
  await page.addInitScript(fakeAudioScript)
  await page.route('**/api/v1/tts/speak?**', route => route.abort('failed'))
  await openReader(page)
  await page.locator('select').filter({ has: page.locator('option[value="smart"]') }).selectOption('smart')
  await startFromSettings(page)
  const failedUrl = await page.evaluate(() => {
    const audio = window.__ttsProbe.instances[0]
    const url = audio.src
    audio.fail()
    return url
  })
  await page.waitForFunction(() => window.__ttsProbe.plays.length >= 2)
  assert.equal(await page.evaluate(() => window.__ttsProbe.plays.at(-1)), failedUrl, 'transient audio errors must retry the same dialogue item')
  assert.equal(await page.evaluate(() => window.__ttsProbe.instances.length), 1, 'retry must keep the unlocked Audio element')
  await context.close()
}

async function testDetachedListeningAndReturn(browser) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const page = await context.newPage()
  await page.addInitScript(fakeAudioScript)
  await page.route('**/api/v1/tts/speak?**', route => route.abort('failed'))
  await openReader(page)
  await startFromSettings(page)
  const firstUrl = await page.evaluate(() => window.__ttsProbe.plays.at(-1))

  await page.locator('a.brand').evaluate(element => element.click())
  await page.locator('#global-tts-return').waitFor({ state: 'visible' })
  const detachedPath = await page.evaluate(() => location.pathname)
  assert.equal(await page.evaluate(() => window.__ttsProbe.instances.length), 1)
  assert.equal(await page.evaluate(() => window.__ttsProbe.instances[0].paused), false, 'leaving the book must not pause TTS')

  await page.evaluate(() => window.__ttsProbe.instances[0].finish())
  await page.waitForFunction(() => window.__ttsProbe.plays.length >= 2)
  assert.notEqual(await page.evaluate(() => window.__ttsProbe.plays.at(-1)), firstUrl, 'detached TTS must continue to the next segment')

  await page.locator('#global-tts-return').click()
  await page.locator('#tts-player').waitFor({ state: 'visible' })
  assert.equal(await page.evaluate(() => location.pathname), detachedPath)
  assert.equal(await page.evaluate(() => window.__ttsProbe.instances[0].paused), false, 'opening the player must not pause TTS')
  await page.locator('#tts-player-return').click()
  await page.locator('.reader-content').waitFor({ state: 'visible' })
  await page.locator('#global-tts-return').waitFor({ state: 'hidden' })
  const result = await page.evaluate(() => ({
    instances: window.__ttsProbe.instances.length,
    active: document.querySelector('.reader-tts-state')?.hidden === false,
    highlighted: Boolean(document.querySelector('.tts-active-line'))
  }))
  assert.equal(result.instances, 1, 'returning to progress must reuse the same Audio element')
  assert.equal(result.active, true, 'returned reader must show active listening state')
  assert.equal(result.highlighted, true, 'returned reader must restore the current spoken paragraph')
  await context.close()
}

async function testAutomaticNextChapterKeepsAudioAuthorization(browser) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const page = await context.newPage()
  await page.addInitScript(fakeAudioScript)
  await page.route('**/api/v1/tts/speak?**', route => route.abort('failed'))
  await openReader(page)
  await startFromSettings(page)
  const initialHash = await page.evaluate(() => location.hash)
  await page.evaluate(() => { window.__ttsProbe.invalidateAuthorizationOnLoad = true })

  for (let index = 0; index < 500; index++) {
    await page.evaluate(() => window.__ttsProbe.instances[0].finish())
    if (await page.evaluate(hash => location.hash !== hash, initialHash)) break
  }
  await page.waitForFunction(hash => location.hash !== hash, initialHash)
  const playsAtRoute = await page.evaluate(() => window.__ttsProbe.plays.length)
  await page.locator('.reader-content').waitFor({ state: 'visible' })
  await page.waitForFunction(count => window.__ttsProbe.plays.length > count, playsAtRoute)

  const result = await page.evaluate(() => ({
    instances: window.__ttsProbe.instances.length,
    authorized: window.__ttsProbe.instances[0].authorized,
    stateText: document.querySelector('.reader-tts-state')?.textContent || ''
  }))
  assert.equal(result.instances, 1, 'automatic chapter routing must reuse the initially unlocked Audio element')
  assert.equal(result.authorized, true, 'automatic chapter routing must not call load() on the unlocked Audio element')
  assert.match(result.stateText, /停止.*听书/, 'next chapter must remain in active TTS state')
  await context.close()
}

;(async () => {
  const browser = await webkit.launch({ headless: true })
  try {
    await testHotSwitch(browser)
    await testPolicyRejection(browser)
    await testDialogueAudioError(browser)
    await testAutomaticNextChapterKeepsAudioAuthorization(browser)
    await testDetachedListeningAndReturn(browser)
    console.log('iOS WebKit TTS runtime checks passed')
  } finally {
    await browser.close()
  }
})().catch(error => {
  console.error(error)
  process.exitCode = 1
})
