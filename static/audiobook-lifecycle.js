(function (root) {
  'use strict'

  const STATES = Object.freeze({
    IDLE: 'idle', STARTING: 'starting', CONNECTING: 'connecting',
    PLAYING: 'playing', PAUSED: 'paused_by_user', BLOCKED: 'blocked',
    STOPPING: 'stopping'
  })
  const ALLOWED = Object.freeze({
    idle: new Set(['start']),
    starting: new Set(['connect', 'pause', 'block', 'stop', 'restart']),
    connecting: new Set(['connect', 'play', 'pause', 'block', 'stop', 'restart']),
    playing: new Set(['connect', 'pause', 'block', 'stop', 'restart']),
    paused_by_user: new Set(['resume', 'stop', 'restart']),
    blocked: new Set(['block', 'retry', 'stop', 'restart']),
    stopping: new Set(['finish'])
  })

  const create = () => {
    let value = Object.freeze({ state: STATES.IDLE, notice: '', generation: 0 })
    const commit = (event, nextState, notice = '', generationDelta = 0) => {
      if (!ALLOWED[value.state]?.has(event)) {
        throw new Error(`invalid audiobook lifecycle transition: ${value.state} -> ${event}`)
      }
      value = Object.freeze({
        state: nextState,
        notice: String(notice || ''),
        generation: value.generation + generationDelta
      })
      return value
    }
    return Object.freeze({
      snapshot: () => value,
      start: () => commit('start', STATES.STARTING, '', 1),
      restart: () => commit('restart', STATES.STARTING, '', 1),
      connect: () => commit('connect', STATES.CONNECTING),
      playing: () => commit('play', STATES.PLAYING),
      pause: () => commit('pause', STATES.PAUSED),
      resume: () => commit('resume', STATES.CONNECTING),
      block: notice => commit('block', STATES.BLOCKED, notice),
      retry: () => commit('retry', STATES.CONNECTING),
      stop: () => commit('stop', STATES.STOPPING, '', 1),
      finish: () => commit('finish', STATES.IDLE),
      isActive: () => value.state !== STATES.IDLE && value.state !== STATES.STOPPING,
      isBlocked: () => value.state === STATES.BLOCKED,
      isPausedByUser: () => value.state === STATES.PAUSED,
      isConnecting: () => value.state === STATES.STARTING || value.state === STATES.CONNECTING
    })
  }

  const responseError = async response => {
    let detail = ''
    try { detail = String((await response.json())?.detail || '').trim() } catch (_) {}
    const error = new Error(detail || `听书请求失败（${response.status}）`)
    error.status = Number(response.status)
    error.retryAfter = Math.max(0, Number(response.headers.get('Retry-After') || 0))
    return error
  }

  const failureNotice = error => {
    if (Number(error?.status) === 429) {
      const wait = Number(error?.retryAfter) > 0 ? `，请${Number(error.retryAfter)}秒后重试` : '，请稍后重试'
      return `${error?.message || '听书请求过于频繁'}${wait}`
    }
    if (Number(error?.status) === 401 && /登录状态已失效|请先登录/.test(String(error?.message || ''))) return '登录状态已失效，请重新登录后重试'
    if (Number(error?.status) === 403) return error?.message || '听书请求被拒绝，请稍后重试'
    return error?.message || '听书服务暂时不可用，请稍后重试'
  }

  const showFailure = (current, notice, retry) => {
    if (!notice) return current
    current?.remove()
    const toast = document.createElement('div')
    toast.className = 'tts-error-toast'
    toast.setAttribute('role', 'alert')
    toast.setAttribute('aria-live', 'assertive')
    toast.setAttribute('aria-atomic', 'true')
    toast.innerHTML = '<span class="tts-error-toast-icon" aria-hidden="true">!</span>'
      + `<span class="tts-error-toast-copy"></span><button class="tts-error-toast-retry" type="button">重试</button>`
    toast.querySelector('.tts-error-toast-copy').textContent = notice
    toast.querySelector('button').addEventListener('click', () => { toast.remove(); retry() })
    document.body.append(toast)
    return toast
  }

  root.OOHStoryAudiobookLifecycle = Object.freeze({ STATES, create, responseError, failureNotice, showFailure })
})(typeof window === 'undefined' ? globalThis : window)
