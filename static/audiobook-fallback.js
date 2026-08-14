(function (root) {
  'use strict'

  const BATCH_SIZE = 5

  const randomToken = () => {
    const bytes = new Uint8Array(16)
    if (root.crypto?.getRandomValues) root.crypto.getRandomValues(bytes)
    else for (let index = 0; index < bytes.length; index++) bytes[index] = Math.floor(Math.random() * 256)
    return `segment-${Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')}`
  }

  const create = options => {
    const prepared = new Map()
    const preparing = new Map()
    let activeUrl = ''
    let activeToken = ''
    let activeBatchStart = -1
    let watchdog = null

    const valid = (generation, token = activeToken) => Boolean(
      options.isActive() && options.generation() === generation && activeToken === token
    )

    const clearWatchdog = () => {
      if (!watchdog) return
      root.clearTimeout(watchdog)
      watchdog = null
    }

    const release = () => {
      clearWatchdog()
      activeToken = ''
      activeBatchStart = -1
      for (const url of new Set([activeUrl, ...prepared.values()])) {
        if (url) URL.revokeObjectURL(url)
      }
      activeUrl = ''
      prepared.clear()
      preparing.clear()
    }

    const prepare = (item, generation) => {
      const key = String(item?.sha256 || '')
      if (!key || !item?.audio_endpoint) return Promise.reject(new Error('segment fallback unavailable'))
      if (prepared.has(key)) return Promise.resolve(prepared.get(key))
      if (preparing.has(key)) return preparing.get(key)
      const promise = fetch(item.audio_endpoint, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'X-Audiobook-Client': options.clientId },
        signal: options.signal()
      }).then(async response => {
        if (!response.ok) throw await options.responseError(response)
        const blob = await response.blob()
        if (!blob.size) throw new Error('empty fallback audio segment')
        if (!options.isActive() || options.generation() !== generation) throw new DOMException('cancelled', 'AbortError')
        const durationMs = Number(response.headers.get('X-Audio-Duration-Ms') || 0)
        if (durationMs > 0) {
          item.durationSeconds = durationMs / 1000
          item.durationExact = true
        }
        const url = URL.createObjectURL(blob)
        prepared.set(key, url)
        return url
      }).finally(() => preparing.delete(key))
      preparing.set(key, promise)
      return promise
    }

    const retry = (index, retryCount, resumeOffset, generation) => {
      if (retryCount >= 3) return false
      root.setTimeout(() => {
        if (options.isActive() && options.generation() === generation) play(index, resumeOffset, retryCount + 1)
      }, Math.min(2400, 400 * (2 ** retryCount)))
      return true
    }

    const prepareBatchRemainder = async (start, end, generation) => {
      const items = options.items()
      for (let index = start; index < Math.min(end, items.length); index++) {
        if (!options.isActive() || options.generation() !== generation) return
        try { await prepare(items[index], generation) } catch (error) {
          if (error?.name === 'AbortError') return
        }
      }
    }

    const play = async (index, resumeOffset = 0, retryCount = 0) => {
      const items = options.items()
      if (!options.isActive() || index >= items.length) {
        if (index >= items.length) options.finish()
        return
      }
      if (options.isPaused()) return
      clearWatchdog()
      const generation = options.generation()
      if (activeBatchStart < 0 || index < activeBatchStart || index >= activeBatchStart + BATCH_SIZE) {
        activeBatchStart = index
      }
      const item = items[index]
      const token = randomToken()
      activeToken = token
      options.begin(index, token, resumeOffset)
      try {
        const url = await prepare(item, generation)
        if (!valid(generation, token)) return
        const audio = options.audio()
        const previousUrl = activeUrl
        activeUrl = url
        prepared.delete(String(item.sha256 || ''))
        audio.src = url
        audio.onloadedmetadata = () => {
          const offset = Math.max(0, Number(resumeOffset) || 0)
          if (offset > 0 && Number.isFinite(audio.duration)) audio.currentTime = Math.min(offset, Math.max(0, audio.duration - 0.05))
        }
        const playing = () => {
          if (!valid(generation, token)) return
          if (options.isPaused()) { audio.pause(); return }
          if (previousUrl && previousUrl !== url) URL.revokeObjectURL(previousUrl)
          options.playing(index)
          prepareBatchRemainder(index + 1, activeBatchStart + BATCH_SIZE, generation).catch(() => {})
          if (index === activeBatchStart + BATCH_SIZE - 1) {
            prepareBatchRemainder(activeBatchStart + BATCH_SIZE, activeBatchStart + 2 * BATCH_SIZE, generation).catch(() => {})
          }
        }
        audio.onplaying = playing
        audio.ontimeupdate = () => { if (valid(generation, token)) options.timeupdate(index) }
        audio.onended = () => {
          if (!valid(generation, token)) return
          options.progress(true)
          if (index + 1 < options.items().length) play(index + 1)
          else options.finish()
        }
        audio.onerror = () => {
          if (!valid(generation, token)) return
          if (!retry(index, retryCount, options.offset(), generation)) {
            options.fail(new Error('fallback audio element error'), '当前片段加载失败，点击重试')
          }
        }
        Promise.resolve(audio.play()).then(playing).catch(error => {
          if (options.isPolicyError(error)) options.fail(error, '')
          else audio.onerror?.()
        })
      } catch (error) {
        if (error?.name === 'AbortError' || !valid(generation, token)) return
        if (!retry(index, retryCount, resumeOffset, generation)) options.fail(error, options.failureNotice(error))
      }
    }

    const guard = (isStillConnecting, fallback) => {
      clearWatchdog()
      watchdog = root.setTimeout(() => {
        watchdog = null
        if (isStillConnecting()) fallback()
      }, Math.max(1000, Number(options.timeoutMs) || 8000))
    }

    return Object.freeze({ play, guard, clearWatchdog, release })
  }

  root.OOHStoryAudiobookFallback = Object.freeze({ create })
})(typeof window === 'undefined' ? globalThis : window)
