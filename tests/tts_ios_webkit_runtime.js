const assert = require('node:assert/strict')
const { webkit } = require('playwright')

const READER_URL = process.env.OOHSTORY_READER_URL
  || 'https://reader.example.com/#/read/oV5RFWgNywznCvqzb2Iqdg/2'
const contextOptions = options => ({
  ...options,
  ...(READER_URL.startsWith('http://127.0.0.1:8091/')
    ? { extraHTTPHeaders: { Origin: 'https://reader.example.com' } }
    : {})
})

const fakeAudioScript = () => {
  Object.defineProperty(navigator, 'standalone', { configurable: true, get: () => true })
  window.__ttsProbe = {
    instances: [], plays: [], pauses: 0, loads: 0,
    unlocks: 0, rejectNext: false, stallNext: false, invalidateAuthorizationOnLoad: false
  }
  class FakeAudio {
    constructor() {
      this.paused = true
      this.ended = false
      this.currentTime = 0
      this.preload = 'auto'
      this.src = ''
      this.authorized = false
      this.onended = null
      this.onerror = null
      this.ontimeupdate = null
      this.seeking = false
      window.__ttsProbe.instances.push(this)
    }
    play() {
      this.paused = false
      this.ended = false
      if (this.src.startsWith('data:audio/wav;base64,')) {
        this.authorized = true
        window.__ttsProbe.unlocks++
        return Promise.resolve()
      }
      window.__ttsProbe.plays.push(this.src)
      if (window.__ttsProbe.stallNext && this.src.includes('/stream.mp3')) {
        window.__ttsProbe.stallNext = false
        return new Promise(() => {})
      }
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
    tick(seconds) {
      this.currentTime = Number(seconds) || 0
      if (this.ontimeupdate) this.ontimeupdate()
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
  const context = await browser.newContext(contextOptions({
    viewport: { width: 390, height: 844 },
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148'
  }))
  const page = await context.newPage()
  const manifests = []
  page.on('response', response => {
    const url = new URL(response.url())
    if (url.pathname !== '/api/v1/audiobook/sessions' || response.request().method() !== 'POST') return
    response.json().then(payload => {
      if (payload.current) manifests.push(payload.current)
    }).catch(() => {})
  })
  await page.addInitScript(fakeAudioScript)
  await page.route('**/api/v1/tts/speak?**', route => route.abort('failed'))
  await openReader(page)
  await startFromSettings(page)
  const firstStream = await page.evaluate(() => window.__ttsProbe.plays.at(-1))
  assert.match(firstStream, /\/chapters\/[0-9a-f]{64}\/stream\.mp3\?start=\d+(?:&[^#]+)?$/, 'playback must use one continuous source per five-segment batch')
  await page.getByRole('button', { name: '设置', exact: true }).evaluate(element => element.click())
  const mode = page.locator('select').filter({ has: page.locator('option[value="smart"]') })
  await mode.selectOption('smart')
  await page.waitForFunction(() => window.__ttsProbe.plays.length >= 2)

  const beforeEnd = await page.evaluate(() => ({
    plays: window.__ttsProbe.plays.length,
    unlocks: window.__ttsProbe.unlocks,
    pauses: window.__ttsProbe.pauses,
    loads: window.__ttsProbe.loads,
    standalone: navigator.standalone
  }))
  assert.equal(beforeEnd.standalone, true)
  assert.equal(beforeEnd.unlocks, 1, 'first tap must synchronously unlock the persistent iOS Audio element')
  assert.equal(beforeEnd.plays, 2, 'settings switch must rebuild one bounded batch from the active paragraph')
  assert.equal(beforeEnd.pauses, 0, 'switch must not pause the unlocked iOS Audio element')
  assert.equal(beforeEnd.loads, 0, 'switch must not call load() and lose iOS media authorization')
  const beforeTimelineAdvance = beforeEnd.plays
  await page.evaluate(() => window.__ttsProbe.instances[0].tick(60))
  const plays = await page.evaluate(() => window.__ttsProbe.plays.slice())
  assert.equal(plays.length, beforeTimelineAdvance, 'crossing dialogue boundaries must not replace audio.src or call play again')
  assert.match(plays.at(-1), /\/chapters\/[0-9a-f]{64}\/stream\.mp3\?start=\d+(?:&[^#]+)?$/)
  for (let attempt = 0; attempt < 50 && manifests.length < 2; attempt++) await page.waitForTimeout(20)
  const voices = new Set(manifests.flatMap(manifest => manifest.segments || []).map(item => item.voice).filter(Boolean))
  assert.equal(plays.length, 2, `hot-switched playback should still use one source for the active batch, got ${plays.length}`)
  assert.ok(manifests.length >= 2, `hot switch should rebuild the authoritative manifest, got ${manifests.length}`)
  assert.ok(voices.has('mocheng'), `smart plan should contain narrator voice, got ${[...voices]}`)
  await context.close()
}

async function testPolicyRejection(browser) {
  const context = await browser.newContext(contextOptions({ viewport: { width: 390, height: 844 } }))
  const page = await context.newPage()
  await page.addInitScript(fakeAudioScript)
  await page.route('**/api/v1/tts/speak?**', route => route.abort('failed'))
  await openReader(page)
  await page.evaluate(() => { window.__ttsProbe.rejectNext = true })
  await startFromSettings(page)
  await page.getByRole('button', { name: '点击继续听书', exact: true }).waitFor({ state: 'visible' })
  const rejectedUrl = await page.evaluate(() => window.__ttsProbe.plays.at(-1))
  await page.getByRole('button', { name: '继续听书', exact: true }).click()
  await page.waitForFunction(() => window.__ttsProbe.plays.length >= 2)
  const resumedUrl = await page.evaluate(() => window.__ttsProbe.plays.at(-1))
  assert.equal(
    resumedUrl.replace(/&stream_id=[^&]+/, ''),
    rejectedUrl.replace(/&stream_id=[^&]+/, ''),
    'Safari rejection must resume the same item instead of skipping it'
  )
  await context.close()
}

async function testDialogueAudioError(browser) {
  const context = await browser.newContext(contextOptions({ viewport: { width: 390, height: 844 } }))
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
  assert.equal(
    (await page.evaluate(() => window.__ttsProbe.plays.at(-1))).replace(/&stream_id=[^&]+/, ''),
    failedUrl.replace(/&stream_id=[^&]+/, ''),
    'transient audio errors must retry the same dialogue item'
  )
  assert.equal(await page.evaluate(() => window.__ttsProbe.instances.length), 1, 'retry must keep the unlocked Audio element')
  await context.close()
}

async function testStalledChapterStreamFallsBack(browser) {
  const context = await browser.newContext(contextOptions({ viewport: { width: 390, height: 844 } }))
  const page = await context.newPage()
  await page.addInitScript(fakeAudioScript)
  await page.addInitScript(() => { window.OOHStoryAudiobookConnectTimeoutMs = 50 })
  await openReader(page)
  await page.locator('select').filter({ has: page.locator('option[value="smart"]') }).selectOption('smart')
  await page.evaluate(() => { window.__ttsProbe.stallNext = true })
  await startFromSettings(page)
  await page.waitForFunction(() => window.__ttsProbe.plays.length >= 2, null, { timeout: 30000 })
  const result = await page.evaluate(() => ({
    plays: window.__ttsProbe.plays.slice(),
    instances: window.__ttsProbe.instances.length,
    playerText: document.querySelector('#tts-player')?.textContent || ''
  }))
  assert.match(result.plays[0], /\/stream\.mp3\?/, 'the normal five-segment batch must be attempted first')
  assert.match(result.plays.at(-1), /^blob:/, 'a stalled batch must fall back to finite segment audio')
  assert.equal(result.instances, 1, 'fallback must reuse the page-lifetime Audio element')
  assert.doesNotMatch(result.playerText, /正在连接音频/, 'the connection label must clear after fallback starts')
  await page.locator('#tts-player-stop').click()
  await context.close()
}

async function testRepeatedExitResumeAndNarratorContract(browser) {
  const context = await browser.newContext(contextOptions({ viewport: { width: 390, height: 844 } }))
  const page = await context.newPage()
  const sessionRequests = []
  const sessionResponses = []
  await page.addInitScript(fakeAudioScript)
  page.on('request', request => {
    const url = new URL(request.url())
    if (url.pathname !== '/api/v1/audiobook/sessions' || request.method() !== 'POST') return
    sessionRequests.push(request.postDataJSON())
  })
  page.on('response', response => {
    const url = new URL(response.url())
    if (url.pathname !== '/api/v1/audiobook/sessions' || response.request().method() !== 'POST') return
    response.json().then(payload => sessionResponses.push(payload)).catch(() => {})
  })
  await openReader(page)
  const mode = page.locator('select').filter({ has: page.locator('option[value="smart"]') })
  await mode.selectOption('smart')
  const narrator = page.locator('select').filter({ has: page.locator('option[value="lingxian"]') }).last()
  await narrator.selectOption('lingxian')

  for (let round = 0; round < 3; round++) {
    const playsBefore = await page.evaluate(() => window.__ttsProbe.plays.length)
    await startFromSettings(page)
    await page.waitForFunction(count => window.__ttsProbe.plays.length > count, playsBefore)
    assert.equal(
      await page.evaluate(() => window.__ttsProbe.instances.length),
      1,
      `round ${round + 1}: a full exit/re-entry must reuse the page-lifetime Audio element`,
    )
    await page.locator('#tts-player-stop').click()
    await page.waitForFunction(() => document.querySelector('#tts-player')?.hidden === true)
    await page.getByRole('button', { name: '设置', exact: true }).evaluate(element => element.click())
    await page.locator('.reader-settings-panel').waitFor({ state: 'visible' })
  }

  const playsBeforeFinalStart = await page.evaluate(() => window.__ttsProbe.plays.length)
  await startFromSettings(page)
  await page.waitForFunction(count => window.__ttsProbe.plays.length > count, playsBeforeFinalStart)
  await page.locator('#tts-player-toggle').click()
  assert.equal(await page.evaluate(() => window.__ttsProbe.instances.at(-1).paused), true, 'pause must stop the active stream')
  await page.locator('#tts-player-toggle').click()
  await page.waitForFunction(() => window.__ttsProbe.instances.at(-1).paused === false)

  for (let attempt = 0; attempt < 50 && sessionResponses.length < 4; attempt++) await page.waitForTimeout(20)
  assert.equal(sessionRequests.length, 4, 'each explicit start must create exactly one audiobook session')
  assert.ok(sessionRequests.every(payload => payload.mode === 'smart' && payload.narrator === 'lingxian'),
    `smart narrator request must remain lingxian: ${JSON.stringify(sessionRequests)}`)
  assert.equal(sessionResponses.length, 4, 'all audiobook sessions must return a manifest')
  assert.ok(sessionResponses.every(payload => payload.current?.requested_narrator === 'lingxian'
    && payload.current?.effective_narrator === 'lingxian'),
  `server must preserve requested smart narrator: ${JSON.stringify(sessionResponses.map(payload => payload.current))}`)
  await page.locator('#tts-player-stop').click()
  await context.close()
}

async function testDetachedListeningAndReturn(browser) {
  const context = await browser.newContext(contextOptions({ viewport: { width: 390, height: 844 } }))
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
  assert.notEqual(await page.evaluate(() => window.__ttsProbe.plays.at(-1)), firstUrl, 'detached TTS must continue to the next five-segment batch')

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

async function testReaderEntrySemantics(browser) {
  const context = await browser.newContext(contextOptions({ viewport: { width: 390, height: 844 } }))
  const page = await context.newPage()
  const sessionRequests = []
  let sessionDeletes = 0
  page.on('request', request => {
    const url = new URL(request.url())
    if (url.pathname === '/api/v1/audiobook/sessions' && request.method() === 'POST') {
      sessionRequests.push(request.postDataJSON())
    }
    if (/^\/api\/v1\/audiobook\/sessions\/[0-9a-f]{32}$/.test(url.pathname)
        && request.method() === 'DELETE') sessionDeletes++
  })
  await page.addInitScript(fakeAudioScript)
  await page.route('**/api/v1/tts/speak?**', route => route.abort('failed'))
  await openReader(page)
  await startFromSettings(page)
  await page.waitForFunction(() => window.__ttsProbe.plays.length >= 1)
  assert.equal(sessionRequests.length, 1)
  assert.equal(sessionRequests[0].resume, false, 'ordinary listen must not resume an old server cursor')
  assert.equal(sessionRequests[0].start_paragraph_index, 0, 'ordinary listen must start at chapter paragraph zero')

  await page.evaluate(() => { window.__ttsProbe.instances[0].currentTime = 7.25 })
  const beforeOpen = await page.evaluate(() => ({
    src: window.__ttsProbe.instances[0].src,
    currentTime: window.__ttsProbe.instances[0].currentTime,
    plays: window.__ttsProbe.plays.length,
  }))
  await page.locator('#tts-player-close').click()
  await page.locator('.reader-tts-state').click()
  await page.locator('#tts-player').waitFor({ state: 'visible' })
  await page.waitForTimeout(100)
  const afterOpen = await page.evaluate(() => ({
    src: window.__ttsProbe.instances[0].src,
    currentTime: window.__ttsProbe.instances[0].currentTime,
    plays: window.__ttsProbe.plays.length,
  }))
  assert.equal(sessionRequests.length, 1, 'opening the player must not create a new audiobook session')
  assert.equal(sessionDeletes, 0, 'opening the player must not delete the active audiobook session')
  assert.deepEqual(afterOpen, beforeOpen, 'opening the player must preserve the active audio cursor')

  await page.locator('#tts-player-stop').click()
  const target = page.locator('.reader-paragraph').nth(12)
  await target.scrollIntoViewIfNeeded()
  const box = await target.boundingBox()
  assert.ok(box, 'coordinate target paragraph must be visible')
  await page.mouse.click(box.x + Math.min(30, box.width / 2), box.y + Math.min(20, box.height / 2), { button: 'right' })
  await page.getByRole('menuitem', { name: /从此处听书/ }).click()
  await page.waitForFunction(() => window.__ttsProbe.plays.length >= 2)
  assert.equal(sessionRequests.length, 2)
  assert.equal(sessionRequests[1].resume, false, 'from-here listen must use an explicit non-resume start')
  assert.equal(sessionRequests[1].start_paragraph_index, 12, 'from-here listen must use the paragraph under the pointer')
  await page.locator('#tts-player-stop').click()
  await context.close()
}

async function testAutomaticNextChapterKeepsAudioAuthorization(browser) {
  const context = await browser.newContext(contextOptions({ viewport: { width: 390, height: 844 } }))
  const page = await context.newPage()
  await page.route(/\/api\/v1\/audiobook\/sessions$/, async route => {
    const response = await route.fetch()
    const payload = await response.json()
    payload.current.segments = payload.current.segments.slice(0, 2)
    await route.fulfill({ response, json: payload })
  })
  await page.addInitScript(fakeAudioScript)
  await page.route('**/api/v1/tts/speak?**', route => route.abort('failed'))
  await openReader(page)
  await startFromSettings(page)
  const initialHash = await page.evaluate(() => location.hash)
  await page.evaluate(() => { window.__ttsProbe.invalidateAuthorizationOnLoad = true })

  for (let index = 0; index < 10; index++) {
    const playsBefore = await page.evaluate(() => window.__ttsProbe.plays.length)
    await page.evaluate(() => window.__ttsProbe.instances[0].finish())
    await page.waitForFunction(
      ({ hash, count }) => location.hash !== hash || window.__ttsProbe.plays.length > count,
      { hash: initialHash, count: playsBefore },
      { timeout: 30000 }
    )
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
    if (process.env.OOHSTORY_TTS_ENTRY_ONLY === '1') {
      await testReaderEntrySemantics(browser)
      console.log('iOS WebKit reader entry semantics checks passed')
      return
    }
    if (process.env.OOHSTORY_TTS_REPEAT_ONLY === '1') {
      await testRepeatedExitResumeAndNarratorContract(browser)
      console.log('iOS WebKit repeated exit/resume checks passed')
      return
    }
    await testHotSwitch(browser)
    if (process.env.OOHSTORY_TTS_UNLOCK_ONLY === '1') {
      console.log('iOS WebKit audio unlock checks passed')
      return
    }
    await testPolicyRejection(browser)
    await testDialogueAudioError(browser)
    await testStalledChapterStreamFallsBack(browser)
    await testRepeatedExitResumeAndNarratorContract(browser)
    await testReaderEntrySemantics(browser)
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
