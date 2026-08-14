(function () {
  'use strict'

  const LEGACY_DBS = ['oohstory-audiobook-v1', 'oohstory-audiobook-v2']
  const LEGACY_CACHES = ['oohstory-audiobook-audio-v1', 'oohstory-audiobook-audio-v2']
  const SESSION_SEGMENT_LIMIT = 5
  const MP3_BYTES_PER_SECOND = 6000

  const responseDurationSeconds = (response, byteLength = 0) => {
    const durationMs = Number(response?.headers?.get?.('X-Audio-Duration-Ms') || 0)
    return durationMs > 0 ? durationMs / 1000 : Number(byteLength || 0) / MP3_BYTES_PER_SECOND
  }

  const digest = async bytes => {
    if (!globalThis.crypto?.subtle) return ''
    const hash = await crypto.subtle.digest('SHA-256', bytes)
    return Array.from(new Uint8Array(hash)).map(value => value.toString(16).padStart(2, '0')).join('')
  }

  const selectedSegments = (manifest, priorityStartIndex, priorityCount, maxSegments) => {
    const segments = Array.isArray(manifest?.segments) ? manifest.segments : []
    if (!segments.length) return []
    const start = Math.max(0, Math.min(Number(priorityStartIndex) || 0, segments.length - 1))
    const rotated = [...segments.slice(start), ...segments.slice(0, start)]
    const priority = Math.max(0, Number(priorityCount) || 0)
    const ordered = [...rotated.slice(0, priority), ...rotated.slice(priority)]
    const requested = Number(maxSegments) > 0 ? Number(maxSegments) : SESSION_SEGMENT_LIMIT
    return ordered.slice(0, Math.min(SESSION_SEGMENT_LIMIT, requested))
  }

  class VolatileAudiobookCache {
    constructor () {
      this.controllers = new Set()
      this.objectUrls = new Set()
      this.sessionSegments = new Map()
      this.capacity = null
      this.clearPersistentStorage().catch(() => {})
    }

    async clearPersistentStorage () {
      await Promise.all(LEGACY_CACHES.map(name => globalThis.caches?.delete?.(name).catch(() => false)))
      for (const name of LEGACY_DBS) {
        try { globalThis.indexedDB?.deleteDatabase?.(name) } catch (_) {}
      }
    }

    async shouldPrefetch () {
      const connection = navigator.connection
      if (connection?.saveData || /(^|-)2g$/.test(connection?.effectiveType || '')) return false
      try {
        const battery = await navigator.getBattery?.()
        if (battery && !battery.charging && Number(battery.level) < 0.2) return false
      } catch (_) {}
      try {
        const response = await fetch('/api/v1/audiobook/capacity', {
          method: 'GET', credentials: 'same-origin', cache: 'no-store'
        })
        if (!response.ok) return false
        this.capacity = { ...(await response.json()), checkedAt: Date.now() }
        return this.capacity.allow_prefetch === true
      } catch (_) {
        return false
      }
    }

    async prepare (manifest, { headers = {}, priorityCount = 3, priorityStartIndex = 0, maxSegments = SESSION_SEGMENT_LIMIT, onProgress = null, onSegment = null, exposeSegment = null, signal = null } = {}) {
      const controller = new AbortController()
      this.controllers.add(controller)
      if (signal) signal.addEventListener('abort', () => controller.abort(), { once: true })
      const ordered = selectedSegments(manifest, priorityStartIndex, priorityCount, maxSegments)
      const result = []
      let done = 0
      try {
        for (const segment of ordered) {
          if (controller.signal.aborted) throw new DOMException('cancelled', 'AbortError')
          const hash = String(segment.sha256 || '')
          let entry = this.sessionSegments.get(hash)
          if (!entry) {
            const response = await fetch(segment.audio_endpoint, {
              method: 'POST', headers, credentials: 'same-origin', cache: 'no-store', signal: controller.signal
            })
            if (!response.ok) throw new Error(`TTS ${response.status}`)
            const bytes = await response.arrayBuffer()
            const expected = response.headers.get('X-Content-SHA256') || ''
            if (expected) {
              const actual = await digest(bytes)
              if (actual && actual !== expected) throw new Error('TTS integrity mismatch')
            }
            const blob = new Blob([bytes], { type: 'audio/mpeg' })
            entry = {
              blob,
              durationSeconds: responseDurationSeconds(response, bytes.byteLength),
              durationExact: Number(response.headers.get('X-Audio-Duration-Ms') || 0) > 0
            }
            this.sessionSegments.set(hash, entry)
          }
          segment.duration_seconds = entry.durationSeconds
          segment.duration_exact = entry.durationExact
          if (!exposeSegment || exposeSegment(segment)) {
            const url = URL.createObjectURL(entry.blob)
            this.objectUrls.add(url)
            result.push({ ...segment, url, paraIdx: segment.paragraph_index })
            onSegment?.(segment, url)
          }
          onProgress?.(++done, ordered.length, segment)
        }
        return result
      } finally {
        this.controllers.delete(controller)
      }
    }

    async prepareVolatile (manifest, options = {}) {
      return this.prepare(manifest, options)
    }

    async urls (manifest) {
      const result = []
      for (const segment of selectedSegments(manifest, 0, SESSION_SEGMENT_LIMIT, SESSION_SEGMENT_LIMIT)) {
        const entry = this.sessionSegments.get(String(segment.sha256 || ''))
        if (!entry) throw new Error('incomplete session audiobook cache')
        const url = URL.createObjectURL(entry.blob)
        this.objectUrls.add(url)
        result.push({
          ...segment,
          duration_seconds: entry.durationSeconds,
          duration_exact: entry.durationExact,
          url,
          paraIdx: segment.paragraph_index
        })
      }
      return result
    }

    async evict () {
      this.releaseUrls()
      this.sessionSegments.clear()
      await this.clearPersistentStorage()
    }

    cancel () {
      for (const controller of this.controllers) controller.abort()
      this.controllers.clear()
      this.releaseUrls()
      this.sessionSegments.clear()
      this.clearPersistentStorage().catch(() => {})
    }

    releaseUrls () {
      for (const url of this.objectUrls) URL.revokeObjectURL(url)
      this.objectUrls.clear()
    }
  }

  window.OOHStoryAudiobookCache = new VolatileAudiobookCache()
  window.addEventListener('pagehide', () => window.OOHStoryAudiobookCache.cancel())
})()
