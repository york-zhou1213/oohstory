// Account, profile, submission and notification UI.
// Loaded after app.js so it can reuse the shared state and DOM helpers.

async function accountApi(path, { method = 'GET', body = null, form = null } = {}) {
  const headers = new Headers({ Accept: 'application/json' })
  if (path === '/api/v1/auth/logout' && window.OOHStoryAudiobookClientId) {
    headers.set('X-Audiobook-Client', window.OOHStoryAudiobookClientId)
  }
  if (!['GET', 'HEAD'].includes(method) && state.csrfToken) headers.set('X-CSRF-Token', state.csrfToken)
  let requestBody = form
  if (body !== null) {
    headers.set('Content-Type', 'application/json')
    requestBody = JSON.stringify(body)
  }
  let response = await fetch(path, { method, headers, body: requestBody, credentials: 'same-origin' })
  if (response.status === 429 && ['GET', 'HEAD'].includes(method)) {
    await new Promise(resolve => window.setTimeout(resolve, 450))
    response = await fetch(path, { method, headers, body: requestBody, credentials: 'same-origin' })
  }
  const data = response.status === 204 ? {} : await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`)
  return data
}

function localUserContentIssue(value, { identity = false } = {}) {
  const visible = String(value || '').normalize('NFKC').toLocaleLowerCase()
    .replace(/[\u200b-\u200f\u202a-\u202e\u2060\ufeff]/g, '')
  const compact = visible.replace(/[^0-9a-z\u3400-\u9fff]+/g, '')
  const dotted = visible.replace(/(?:。|．|点|點|丶|句号|小数点|d\W*o\W*t|d\W*i\W*a\W*n)/gi, '.')
  const domainReady = dotted.replace(/[\s_+\-—·•,，/\\|:：;；'"`~!！?？()（）\[\]{}<>《》]+/g, '')
  const tld = '(?:com|cn|net|org|xyz|top|vip|io|cc|me|app|site|club|live|shop|online|link|bet|casino)'
  const separatedAscii = dotted.match(/(?<![a-z0-9])(?:[a-z0-9][\s_+\-·•.,，。．/\\|:：;；]+){5,}[a-z0-9](?![a-z0-9])/gi) || []
  const splitDomain = separatedAscii.some(item => new RegExp(`[a-z0-9]{3,}${tld}$`, 'i').test(item.replace(/[^a-z0-9]/gi, '')))
  if (/(?:h\W*[t7]\W*[t7]\W*p|h\W*x\W*x\W*p|ftp)\W*s?\W*[:：]?\W*\/?\W*\/?/i.test(visible) ||
      /w\W*w\W*w(?:\W|点|點)+/i.test(visible) ||
      new RegExp(`(?:^|[^a-z0-9])(?:[a-z0-9][a-z0-9-]{1,62}\\.)+${tld}(?:$|[^a-z0-9])`, 'i').test(domainReady) ||
      splitDomain) {
    return identity
      ? '这个昵称暂时无法使用。请去掉联系方式、广告引流或不合适的内容后，再试一个更纯粹的名字。'
      : '这条评论需要修改。评论里似乎包含网站、联系方式或推广内容，请删除相关内容后再发布，让「字里行间」只留下阅读交流。'
  }
  const contact = ['微信', '薇信', '威信', '维信', 'v信', 'vx', 'wx', 'weixin', 'qq', '扣扣', '电报', 'telegram', '飞机群', 'whatsapp', '二维码', '扫码', '群号', '加好友', '私聊我', '私信我', '联系我']
  const strongContact = ['二维码', '扫码', '群号', '加好友', '私聊我', '私信我', '联系我']
  const risk = ['傻逼', '脑残', '智障', '色情', '成人视频', '裸聊', '约炮', '刷单', '返利', '跑分', '杀猪盘', '博彩', '赌博', '下注', '玩球', '买球', '赌场', '毒品', '冰毒', '海洛因', '可卡因']
  const promo = ['赚钱', '网赚', '副业', '兼职', '高薪', '日结', '推广', '引流', '招代理', '开户', '带单', '稳赚', '躺赚']
  const phone = /(?<!\d)1[3-9](?:[\s_+\-·•()（）]*\d){9}(?!\d)/.test(visible)
  const contactHandle = contact.some(term => compact.includes(term)) && /[a-z0-9]{4,}/.test(compact)
  if (phone || strongContact.some(term => compact.includes(term)) || contactHandle || risk.some(term => compact.includes(term)) ||
      (identity && contact.some(term => compact.includes(term))) ||
      (identity && promo.some(term => compact.includes(term)))) {
    return identity
      ? '这个昵称暂时无法使用。请去掉联系方式、广告引流或不合适的内容后，再试一个更纯粹的名字。'
      : '这条评论暂时不能发布。内容可能不符合社区交流规范，请调整措辞、去掉不合适的内容后再发布。'
  }
  return ''
}

function isUserContentGuardIssue(message, { identity = false } = {}) {
  const text = String(message || '')
  return identity
    ? /(?:这个昵称暂时无法使用|昵称包含(?:违规|广告引流|联系方式))/.test(text)
    : /(?:这条评论(?:需要修改|暂时不能发布)|评论包含(?:链接|联系方式|违规|辱骂|涉黄|涉毒|涉诈|博彩))/.test(text)
}

function userContentNotice(issue, { identity = false } = {}) {
  if (identity) return {
    title: '换个昵称吧',
    message: '这个昵称暂时无法使用。请去掉联系方式、广告引流或不合适的内容后，再试一个更纯粹的名字。',
    action: '我来修改',
    kind: 'identity'
  }
  const text = String(issue || '')
  const promotion = /(?:需要修改|网站|链接|联系方式|推广|引流)/.test(text)
  return promotion ? {
    title: '这条评论需要修改',
    message: '评论里似乎包含网站、联系方式或推广内容。请删除相关内容后再发布，让「字里行间」只留下阅读交流。',
    action: '返回修改',
    kind: 'promotion'
  } : {
    title: '这条评论暂时不能发布',
    message: '内容可能不符合社区交流规范。请调整措辞、去掉不合适的内容后再发布。',
    action: '返回修改',
    kind: 'community'
  }
}

function openUserContentNotice(issue, { identity = false, returnFocus = null } = {}) {
  document.querySelector('.content-notice-overlay')?.remove()
  const copy = userContentNotice(issue, { identity })
  const previousFocus = returnFocus instanceof HTMLElement ? returnFocus : document.activeElement
  const overlay = node('div', { class: 'content-notice-overlay', role: 'presentation' })
  const titleId = `content-notice-title-${Date.now()}`
  const dialog = node('section', {
    class: `content-notice-dialog content-notice-${copy.kind}`,
    role: 'dialog',
    'aria-modal': 'true',
    'aria-labelledby': titleId
  })
  const close = () => {
    document.removeEventListener('keydown', onKeydown)
    overlay.remove()
    if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus({ preventScroll: true })
  }
  const onKeydown = event => { if (event.key === 'Escape') close() }
  const action = node('button', { class: 'content-notice-action', type: 'button', text: copy.action, onclick: close })
  dialog.append(
    node('div', { class: 'content-notice-mark', 'aria-hidden': 'true', text: '✦' }),
    node('p', { class: 'content-notice-kicker', text: identity ? '昵称提示' : '字里行间 · 友好提醒' }),
    node('h2', { id: titleId, text: copy.title }),
    node('p', { class: 'content-notice-message', text: copy.message }),
    action
  )
  overlay.append(dialog)
  overlay.addEventListener('click', event => { if (event.target === overlay) close() })
  document.addEventListener('keydown', onKeydown)
  document.body.append(overlay)
  queueMicrotask(() => action.focus({ preventScroll: true }))
}

function showAccountSuccessToast(message) {
  document.querySelector('.account-success-toast')?.remove()
  const toast = node('div', {
    class: 'account-success-toast',
    role: 'status',
    'aria-live': 'polite',
    'aria-atomic': 'true'
  }, [
    node('span', { class: 'account-success-toast-icon', 'aria-hidden': 'true', text: '✓' }),
    node('span', { class: 'account-success-toast-copy', text: message })
  ])
  let removed = false
  const remove = () => {
    if (removed) return
    removed = true
    toast.remove()
  }
  toast.addEventListener('animationend', remove, { once: true })
  document.body.append(toast)
  window.setTimeout(remove, 3400)
}

function openRecommendationDialog({
  title,
  message,
  primaryLabel,
  secondaryLabel = '',
  confirm = false
}) {
  return new Promise(resolve => {
    const overlay = node('div', { class: 'content-notice-overlay recommendation-donation-overlay', role: 'presentation' })
    const titleId = `recommendation-dialog-title-${Date.now()}`
    const dialog = node('section', {
      class: 'recommendation-donation-dialog',
      role: 'dialog',
      'aria-modal': 'true',
      'aria-labelledby': titleId
    })
    let settled = false
    const close = value => {
      if (settled) return
      settled = true
      document.removeEventListener('keydown', onKeydown)
      overlay.remove()
      resolve(Boolean(value))
    }
    const onKeydown = event => { if (event.key === 'Escape') close(false) }
    const primary = node('button', {
      class: 'recommendation-primary',
      type: 'button',
      text: primaryLabel,
      onclick: () => close(confirm)
    })
    const actions = node('div', { class: 'recommendation-dialog-actions' }, [
      secondaryLabel ? node('button', {
        class: 'recommendation-secondary',
        type: 'button',
        text: secondaryLabel,
        onclick: () => close(false)
      }) : null,
      primary
    ])
    dialog.append(
      node('div', { class: 'recommendation-donation-mark', 'aria-hidden': 'true', text: '✦' }),
      node('p', { class: 'content-notice-kicker', text: 'READING GIFT' }),
      node('h2', { id: titleId, text: title }),
      node('p', { class: 'recommendation-donation-message', text: message }),
      actions
    )
    overlay.append(dialog)
    overlay.addEventListener('click', event => { if (event.target === overlay) close(false) })
    document.addEventListener('keydown', onKeydown)
    document.body.append(overlay)
    queueMicrotask(() => primary.focus({ preventScroll: true }))
  })
}

function accountInitials(user = state.account) {
  const value = String(user?.display_name || user?.email || '人').trim()
  return [...value][0] || '人'
}

function readingRankTier(level) {
  const value = Number(level || 1)
  if (value >= 18) return 'mythic'
  if (value >= 13) return 'astral'
  if (value >= 7) return 'aurora'
  return 'silver'
}

function readingRankLevel(value) {
  return Math.min(18, Math.max(1, Number.parseInt(String(value || 1), 10) || 1))
}

function readingRankAsset(level) {
  return `/reading-level-icons/v13/level-${String(readingRankLevel(level)).padStart(2, '0')}.webp`
}

function readingRankIcon(reading, { decorative = false } = {}) {
  const roman = String(reading?.roman || 'Ⅰ')
  const name = String(reading?.name || '只如初见')
  const level = readingRankLevel(reading?.level)
  return node('span', {
    class: `reading-rank-icon rank-${readingRankTier(level)} rank-level-${String(level).padStart(2, '0')}`,
    title: `阅读等级 ${roman} · ${name}`,
    ...(decorative ? { 'aria-hidden': 'true' } : { role: 'img', 'aria-label': `阅读等级 ${roman}，${name}` })
  }, [node('img', {
    class: 'rank-art',
    src: readingRankAsset(level),
    alt: '',
    width: '256',
    height: '256',
    decoding: 'async'
  })])
}

function updateAccountButton() {
  const label = accountButton.querySelector('.account-label')
  const icon = accountButton.querySelector('.reading-rank-icon')
  const art = icon?.querySelector('.rank-art')
  label.textContent = state.account ? state.account.display_name : '登录'
  if (state.account && state.accountReading && icon && art) {
    const level = readingRankLevel(state.accountReading.level)
    art.src = readingRankAsset(level)
    icon.className = `reading-rank-icon rank-${readingRankTier(level)} rank-level-${String(level).padStart(2, '0')}`
    icon.hidden = false
    icon.title = `阅读等级 ${state.accountReading.roman} · ${state.accountReading.name}`
    accountButton.setAttribute('aria-label', `${state.account.display_name}，阅读等级 ${state.accountReading.roman} ${state.accountReading.name}，打开个人中心`)
  } else {
    if (icon) {
      icon.hidden = true
      icon.removeAttribute('title')
    }
    accountButton.setAttribute('aria-label', state.account ? `${state.account.display_name}，打开个人中心` : '登录或注册')
  }
}

async function loadAccountSession() {
  let data
  try {
    data = await accountApi('/api/v1/auth/session')
  } catch {
    // A temporary network/upstream failure is not proof that the session was
    // revoked. Preserve the current UI identity and retry on the next refresh.
    updateAccountButton()
    return
  }
  if (!data.user) {
    state.account = null
    state.accountProfile = null
    state.accountReading = null
    state.csrfToken = ''
    updateAccountButton()
    return
  }
  state.account = data.user
  state.csrfToken = data.csrf_token || ''
  try {
    const [, reading] = await Promise.all([
      refreshCloudState(),
      accountApi('/api/v1/me/reading-level')
    ])
    state.accountReading = reading
  } catch {
    // Profile enrichment may be independently throttled or unavailable; it
    // must not turn a valid authenticated session into a logged-out screen.
  }
  updateAccountButton()
}

async function refreshCloudState() {
  if (!state.account) return state.cloudState
  state.cloudState = await accountApi('/api/v1/me/state')
  return state.cloudState
}

function readingProgressPercent(value) {
  return Math.max(0, Math.min(100, Math.round(Number(value || 0) * 100)))
}

function readingHistoryPresentation(item, { local = false } = {}) {
  const chapterId = Math.max(1, Number(item?.chapter_id ?? item?.chapterId ?? 1) || 1)
  const chapterTitle = String(item?.current_chapter || item?.chapterTitle || `第 ${chapterId} 章`)
  const hasOverallProgress = !local && item?.overall_progress !== undefined && item?.overall_progress !== null
  const progress = readingProgressPercent(hasOverallProgress ? item.overall_progress : (item?.within ?? item?.progress))
  return {
    bookId: String(item?.book_id || item?.bookId || ''),
    chapterId,
    title: String(item?.title || '未命名作品'),
    chapterTitle,
    progress,
    context: `${chapterTitle} · ${hasOverallProgress ? '全书进度' : '本章进度'} ${progress}%`
  }
}

function latestHomeReading() {
  if (state.account) {
    const cloudHistory = Array.isArray(state.cloudState?.history) ? state.cloudState.history : []
    const latest = cloudHistory
      .filter(item => item?.book_id && item?.chapter_id)
      .slice()
      .sort((left, right) => new Date(right.updated_at || 0).getTime() - new Date(left.updated_at || 0).getTime())[0]
    return latest ? readingHistoryPresentation(latest) : null
  }
  const localEntries = Object.entries(readReadingProgressStore().books)
    .filter(([, entry]) => entry?.title && entry?.chapterTitle)
    .sort(([, left], [, right]) => Number(right.updatedAt || 0) - Number(left.updatedAt || 0))
  if (!localEntries.length) return null
  const [bookId, entry] = localEntries[0]
  return readingHistoryPresentation({ ...entry, bookId }, { local: true })
}

function buildHomeContinueReading() {
  const recent = latestHomeReading()
  if (!recent) return null
  return node('section', { class: 'continue-reading-section' }, [
    node('a', {
      class: 'continue-reading-card',
      href: `/books/${recent.bookId}/chapters/${recent.chapterId}`
    }, [
      node('div', { class: 'continue-reading-info' }, [
        node('span', { class: 'continue-reading-kicker', text: '继续阅读' }),
        node('strong', { class: 'continue-reading-title', text: recent.title }),
        node('span', { class: 'continue-reading-chapter', text: recent.context }),
        node('div', { class: 'continue-reading-progress', role: 'progressbar', 'aria-label': recent.context, 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': String(recent.progress) }, [
          node('i', { style: `width:${recent.progress}%` })
        ])
      ]),
      node('span', { class: 'continue-reading-action', text: '继续阅读 →' })
    ])
  ])
}

function refreshHomeContinueReading() {
  const slot = document.querySelector('[data-home-continue-reading]')
  if (!slot) return
  const card = buildHomeContinueReading()
  slot.replaceChildren(...(card ? [card] : []))
  slot.hidden = !card
}

function scheduleReadingHistorySync(bookId, entry) {
  if (!state.account) return
  clearTimeout(state.cloudSyncTimer)
  state.cloudSyncTimer = setTimeout(async () => {
    try {
      state.cloudState = await accountApi('/api/v1/me/state', {
        method: 'PUT',
        body: {
          history: [{
            book_id: bookId,
            chapter_id: entry.chapterId,
            progress: entry.within,
            title: entry.title || '',
            updated_at: new Date(entry.updatedAt).toISOString()
          }],
          favorites: [],
          bookshelf: []
        }
      })
    } catch {
      // Local progress remains authoritative until the next successful sync.
    }
  }, 2500)
}

async function mergeLocalReadingHistory() {
  if (!state.account) return
  const local = readReadingProgressStore().books
  const history = Object.entries(local).map(([bookId, entry]) => ({
    book_id: bookId,
    chapter_id: entry.chapterId,
    progress: entry.within,
    title: entry.title || '',
    updated_at: new Date(entry.updatedAt).toISOString()
  }))
  if (!history.length) return refreshCloudState()
  state.cloudState = await accountApi('/api/v1/me/state', {
    method: 'PUT',
    body: { history, favorites: [], bookshelf: [] }
  })
}

function cloudHas(kind, bookId) {
  return (state.cloudState[kind] || []).some(item => item.book_id === bookId)
}

async function setCloudBook(kind, book, enabled) {
  if (!state.account) {
    openAuthDialog('login')
    return false
  }
  if (enabled) {
    const payload = { history: [], favorites: [], bookshelf: [] }
    payload[kind] = [{
      book_id: book.public_id,
      title: book.title || '',
      author: book.author || '',
      cover_url: book.cover_url || '',
      updated_at: new Date().toISOString()
    }]
    state.cloudState = await accountApi('/api/v1/me/state', { method: 'PUT', body: payload })
  } else {
    await accountApi(`/api/v1/me/state/${kind}/${book.public_id}`, { method: 'DELETE' })
    state.cloudState[kind] = (state.cloudState[kind] || []).filter(item => item.book_id !== book.public_id)
  }
  return true
}

function startReadingActivity(bookId) {
  state.readingActivity?.stop?.(true)
  if (!state.account || !/^[A-Za-z0-9_-]{22}$/.test(String(bookId || ''))) return
  let lastSample = performance.now()
  let lastInteraction = Date.now()
  let stopped = false
  let sending = false
  const markActive = () => { lastInteraction = Date.now() }
  const isActive = () => ttsSessionIsPlaying(bookId) || (document.visibilityState === 'visible'
    && (!document.hasFocus || document.hasFocus())
    && Date.now() - lastInteraction < 90_000)
  const send = async force => {
    if (stopped && !force) return
    const now = performance.now()
    const elapsed = Math.min(60, Math.floor((now - lastSample) / 1000))
    lastSample = now
    if (elapsed < 5 || !isActive() || sending || !state.account) return
    sending = true
    try {
      const reading = await accountApi('/api/v1/me/reading-heartbeat', {
        method: 'POST',
        body: {
          event_id: randomUuidV4(),
          book_id: bookId,
          active_seconds: elapsed
        }
      })
      state.accountReading = reading
      updateAccountButton()
    } catch {
      // Reading continues locally; totals only advance after a server acknowledgement.
    } finally {
      sending = false
    }
  }
  const timer = window.setInterval(() => { send(false) }, 30_000)
  ;['pointerdown', 'keydown', 'wheel', 'touchstart'].forEach(type => {
    window.addEventListener(type, markActive, { passive: true })
  })
  state.readingActivity = {
    bookId,
    stop(flush = false) {
      if (stopped) return
      if (flush) send(true)
      stopped = true
      window.clearInterval(timer)
      ;['pointerdown', 'keydown', 'wheel', 'touchstart'].forEach(type => {
        window.removeEventListener(type, markActive)
      })
      if (state.readingActivity === this) state.readingActivity = null
    }
  }
}

let googleScriptPromise = null
function loadGoogleIdentityScript() {
  if (window.google?.accounts?.id) return Promise.resolve()
  if (googleScriptPromise) return googleScriptPromise
  googleScriptPromise = new Promise((resolve, reject) => {
    const script = node('script', { src: 'https://accounts.google.com/gsi/client', async: '', defer: '' })
    script.addEventListener('load', resolve, { once: true })
    script.addEventListener('error', () => reject(new Error('Google 登录组件加载失败')), { once: true })
    document.head.append(script)
  })
  return googleScriptPromise
}

async function renderGoogleButton(slot, {
  mode = 'login',
  onSuccess = null,
  forceRedirect = false
} = {}) {
  if (!state.accountConfig) state.accountConfig = await api('/api/v1/auth/config')
  const googleConfig = state.accountConfig.google || {}
  if (!googleConfig.web_enabled) {
    slot.replaceChildren(node('div', { class: 'google-unavailable', text: 'Google 登录将在配置 OAuth 凭据后自动启用' }))
    return
  }
  try {
    await loadGoogleIdentityScript()
    const buttonHost = node('div', { class: 'google-popup-button' })
    const message = node('div', {
      class: 'google-login-message',
      role: 'status',
      'aria-live': 'polite'
    })
    if (forceRedirect) {
      window.google.accounts.id.initialize({
        client_id: googleConfig.web_client_id,
        auto_select: false,
        cancel_on_tap_outside: true,
        ux_mode: 'redirect',
        login_uri: location.origin
      })
      slot.replaceChildren(
        buttonHost,
        node('div', {
          class: 'google-login-message',
          text: '兼容模式会整页跳转；完成后将回到 OOH Story。'
        })
      )
      window.google.accounts.id.renderButton(buttonHost, {
        type: 'standard',
        theme: 'outline',
        size: 'large',
        shape: 'pill',
        text: 'continue_with',
        width: 310,
        state: mode === 'link' ? 'oohstory-web-link-v1' : 'oohstory-web-redirect-v1'
      })
      return
    }
    const fallback = mode === 'verify'
      ? node('span', { class: 'google-login-message', text: '请在当前窗口完成 Google 身份确认。' })
      : node('button', {
          class: 'google-login-fallback',
          type: 'button',
          text: '弹窗无法使用？切换兼容登录',
          onclick: async event => {
            event.currentTarget.disabled = true
            message.textContent = '正在切换兼容登录…'
            try {
              if (mode === 'link') {
                await accountApi('/api/v1/auth/google/link/start', { method: 'POST' })
              }
              await renderGoogleButton(slot, { mode, onSuccess, forceRedirect: true })
            } catch (error) {
              message.textContent = error.message
              event.currentTarget.disabled = false
            }
          }
        })
    const handleCredential = async response => {
      const credential = String(response?.credential || '')
      if (!credential) {
        message.textContent = 'Google 没有返回登录凭据，请重试或切换兼容登录。'
        return
      }
      fallback.disabled = true
      message.textContent = mode === 'link'
        ? '正在绑定 Google 账户…'
        : mode === 'verify'
          ? '正在确认 Google 身份…'
          : '正在完成 Google 登录…'
      try {
        const data = mode === 'verify'
          ? { id_token: credential }
          : await accountApi(
              mode === 'link' ? '/api/v1/auth/google/link' : '/api/v1/auth/google',
              {
                method: 'POST',
                body: { id_token: credential, client: 'web' }
              }
            )
        if (typeof onSuccess === 'function') await onSuccess(data)
      } catch (error) {
        message.textContent = error.message
        fallback.disabled = false
      }
    }
    window.google.accounts.id.initialize({
      client_id: googleConfig.web_client_id,
      auto_select: false,
      cancel_on_tap_outside: true,
      ux_mode: 'popup',
      callback: handleCredential
    })
    slot.replaceChildren(buttonHost, fallback, message)
    window.google.accounts.id.renderButton(buttonHost, {
      type: 'standard',
      theme: 'outline',
      size: 'large',
      shape: 'pill',
      text: 'continue_with',
      width: 310
    })
  } catch (error) {
    slot.replaceChildren(node('div', { class: 'google-unavailable', text: error.message }))
  }
}

function openAuthDialog(initialMode = 'login', initialError = '') {
  let mode = initialMode
  const overlay = node('div', { class: 'auth-overlay', role: 'presentation' })
  const dialog = node('section', { class: 'auth-dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': '登录或注册 OOH Story' })
  const close = () => overlay.remove()
  const art = node('aside', { class: 'auth-art' }, [
    node('div', { class: 'auth-art-visual' }, [
      node('span', { class: 'auth-art-halo auth-art-halo-one', 'aria-hidden': 'true' }, [
        node('span', { class: 'auth-art-orbit-dot' })
      ]),
      node('span', { class: 'auth-art-halo auth-art-halo-two', 'aria-hidden': 'true' }, [
        node('span', { class: 'auth-art-orbit-dot' })
      ]),
      node('img', { class: 'auth-art-mark', src: '/icon-192.png?v=20260730-icon1', alt: 'OOH Story 标志' })
    ]),
    node('h2', { text: '让每一次阅读，都在下一台设备接上。' }),
    node('p', { text: '同步阅读进度、收藏和私人书架。你的记录只属于你。' })
  ])
  const panel = node('div', { class: 'auth-panel' })
  const closeButton = node('button', { class: 'auth-close', type: 'button', text: '×', 'aria-label': '关闭', onclick: close })

  const render = () => {
    const isLogin = mode === 'login'
    const errorText = node('p', { class: 'auth-error', role: 'alert', text: initialError })
    initialError = ''
    const form = node('form', { class: 'auth-form' })
    if (!isLogin) form.append(node('label', { class: 'auth-field' }, [
      node('span', { text: '昵称' }), node('input', { name: 'display_name', autocomplete: 'nickname', maxlength: '40', placeholder: '怎么称呼你' })
    ]))
    if (!isLogin) form.append(node('label', { class: 'auth-field' }, [
      node('span', { text: '邀请码（选填）' }), node('input', { name: 'invite_code', autocomplete: 'off', minlength: '20', maxlength: '128', placeholder: '有邀请码可在这里填写' })
    ]))
    form.append(
      node('label', { class: 'auth-field' }, [
        node('span', { text: '邮箱' }), node('input', { name: 'email', type: 'email', autocomplete: 'email', maxlength: '254', required: '', placeholder: 'name@example.com' })
      ]),
      node('label', { class: 'auth-field' }, [
        node('span', { text: '密码' }), node('input', { name: 'password', type: 'password', autocomplete: isLogin ? 'current-password' : 'new-password', minlength: isLogin ? '1' : '12', maxlength: '128', required: '', placeholder: isLogin ? '输入密码' : '至少 12 位，包含三类字符' })
      ])
    )
    const submit = node('button', { class: 'auth-submit', type: 'submit', text: isLogin ? '安全登录' : '创建账户' })
    form.append(errorText, submit)
    form.addEventListener('submit', async event => {
      event.preventDefault()
      submit.disabled = true
      errorText.textContent = ''
      const values = new FormData(form)
      try {
        const body = {
          email: values.get('email'),
          password: values.get('password'),
          client: 'web'
        }
        if (!isLogin) {
          body.display_name = values.get('display_name') || ''
          body.invite_code = values.get('invite_code') || ''
          const identityIssue = localUserContentIssue(body.display_name, { identity: true })
          if (identityIssue) {
            openUserContentNotice(identityIssue, {
              identity: true,
              returnFocus: form.querySelector('input[name="display_name"]')
            })
            return
          }
        }
        const data = await accountApi(isLogin ? '/api/v1/auth/login' : '/api/v1/auth/register', {
          method: 'POST',
          body
        })
        state.account = data.user
        state.accountReading = null
        state.csrfToken = data.csrf_token
        updateAccountButton()
        await mergeLocalReadingHistory()
        close()
        location.hash = '#/account'
      } catch (error) {
        if (!isLogin && isUserContentGuardIssue(error.message, { identity: true })) {
          openUserContentNotice(error.message, {
            identity: true,
            returnFocus: form.querySelector('input[name="display_name"]')
          })
        } else {
          errorText.textContent = error.message
        }
      } finally {
        submit.disabled = false
      }
    })
    const tabs = node('div', { class: 'auth-tabs' }, [
      node('button', { class: isLogin ? 'active' : '', type: 'button', text: '登录', onclick: () => { mode = 'login'; render() } }),
      node('button', { class: isLogin ? '' : 'active', type: 'button', text: '注册', onclick: () => { mode = 'register'; render() } })
    ])
    const googleSlot = node('div', { class: 'google-login-slot' }, [
      node('div', {
        class: 'google-unavailable',
        text: '正在检查 Google 登录…'
      })
    ])
    panel.replaceChildren(
      node('p', { class: 'auth-kicker', text: 'OOH STORY ACCOUNT' }),
      node('h1', { text: isLogin ? '欢迎回来' : '建立你的阅读宇宙' }),
      node('p', {
        class: 'auth-subtitle',
        text: isLogin
          ? 'Google 首次登录即可直接进入，无需另外注册。'
          : '可直接使用 Google；邮箱密码账户也可单独创建。'
      }),
      tabs,
      googleSlot,
      node('p', { class: 'google-login-message', text: '首次 Google 登录会自动建立私人阅读档案；邮箱密码可稍后在个人中心按需启用。' }),
      node('div', { class: 'auth-divider', text: isLogin ? '或使用邮箱密码' : '或创建邮箱密码账户' }),
      form,
      node('p', { class: 'auth-safety', text: '🔒 Google 凭据仅用于验证身份；密码使用 Argon2id 加密，会话可随时撤销。' })
    )
    renderGoogleButton(googleSlot, {
      onSuccess: async data => {
        state.account = data.user
        state.accountReading = null
        state.csrfToken = data.csrf_token
        updateAccountButton()
        await mergeLocalReadingHistory()
        close()
        location.hash = '#/account'
      }
    }).catch(() => {})
  }
  render()
  dialog.append(art, panel, closeButton)
  overlay.append(dialog)
  overlay.addEventListener('click', event => { if (event.target === overlay) close() })
  overlay.addEventListener('keydown', event => { if (event.key === 'Escape') close() })
  document.body.append(overlay)
  dialog.tabIndex = -1
  dialog.focus()
}

function uploadStatusLabel(status) {
  return ({ quarantined: '等待后台检查', scanning: '后台安全检查中', clean_queued: '旧版归纳队列', ai_pending: '等待审核', reviewing: '审核中', approved: '已通过·等待入库', completed: '已入库', rejected: '已驳回' })[status] || status
}

const ACCOUNT_COLLECTIONS = {
  history: { title: '阅读记录', subtitle: '从上次停下的章节继续', empty: '还没有阅读记录，去书库遇见第一本故事。' },
  favorites: { title: '收藏记录', subtitle: '你认真标记过的作品', empty: '还没有收藏作品，看到喜欢的故事就点亮收藏。' },
  bookshelf: { title: '我的书架', subtitle: '跨设备同步的私人书架', empty: '书架还是空的，把正在追的作品加入这里吧。' }
}

function accountNavigation(active = 'overview') {
  const items = [
    ['overview', '#/account', '个人中心'],
    ['history', '#/account/history', '阅读记录'],
    ['favorites', '#/account/favorites', '收藏'],
    ['bookshelf', '#/account/bookshelf', '书架'],
    ['deconstruction-tasks', '#/account/deconstruction-tasks', '拆书任务'],
    ['submit', '#/account/submit', '投稿'],
    ['submissions', '#/account/submissions', '我的投稿'],
    ['notifications', '#/account/notifications', `消息${state.notificationsUnread ? ` · ${state.notificationsUnread}` : ''}`],
    ['profile', '#/account/profile', '资料与安全']
  ]
  return node('nav', { class: 'account-nav', 'aria-label': '用户中心导航' },
    items.map(([key, href, label]) => node('a', {
      class: key === active ? 'active' : '', href, text: label
    }))
  )
}

function deconstructionTaskStatus(status) {
  return ({ open: '待接取', claimed: '已接取', submitted: '审核中', completed: '已完成', expired: '已过期', cancelled: '已取消' })[status] || status
}

function deconstructionTaskExpiry(item) {
  if (item.status === 'expired') return '已过期，需要重新发布'
  if (['submitted', 'completed'].includes(item.status)) return item.status === 'completed' ? '档案已交付' : '档案审核与入库中'
  const seconds = Math.max(0, Number(item.remaining_seconds || 0))
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  return days > 0 ? `${days} 天 ${hours} 小时后过期` : `${Math.max(1, hours)} 小时内过期`
}

async function loadDeconstructionTasksPage() {
  if (!state.account) { openAuthDialog('login'); location.hash = '#/'; return }
  setSeo({ title: '拆书任务｜OOH Story', description: '发布、接取并交付拆书档案。', canonicalPath: '/', robots: 'noindex, nofollow' })
  const feedback = node('p', { class: 'profile-feedback', role: 'status' })
  let data
  let wallet
  try {
    ;[data, wallet] = await Promise.all([
      accountApi('/api/v1/deconstruction-tasks'),
      accountApi('/api/v1/me/wallet')
    ])
  } catch (error) {
    app.replaceChildren(node('div', { class: 'account-page' }, [accountNavigation('deconstruction-tasks'), node('p', { class: 'profile-feedback', text: error.message })]))
    return
  }

  const refresh = async message => {
    if (message) showAccountSuccessToast(message)
    await loadDeconstructionTasksPage()
  }
  const publishForm = node('form', { class: 'task-publish-form' }, [
    node('label', { class: 'profile-field' }, [node('span', { text: '书名' }), node('input', { name: 'book_title', maxlength: '160', required: '', placeholder: '需要拆解的作品' })]),
    node('label', { class: 'profile-field' }, [node('span', { text: '作者' }), node('input', { name: 'author', maxlength: '100', placeholder: '选填' })]),
    node('label', { class: 'profile-field profile-field-wide' }, [node('span', { text: '拆书要求' }), node('textarea', { name: 'request_note', maxlength: '2000', rows: '4', placeholder: '说明题材、重点章节或希望关注的拆解维度' })]),
    node('button', { class: 'primary-button', type: 'submit', text: '发布 7 天任务' })
  ])
  publishForm.addEventListener('submit', async event => {
    event.preventDefault()
    const button = publishForm.querySelector('button[type="submit"]')
    button.disabled = true
    try {
      const values = Object.fromEntries(new FormData(publishForm))
      await accountApi('/api/v1/deconstruction-tasks', { method: 'POST', body: values })
      await refresh('拆书任务已发布，7 天后自动过期')
    } catch (error) { feedback.textContent = error.message }
    finally { button.disabled = false }
  })

  const convertInput = node('input', { type: 'number', min: '1', max: String(Math.max(1, wallet.exchangeable_points || 1)), value: '1', 'aria-label': '兑换积分数量' })
  const convertButton = node('button', { class: 'ghost-button', type: 'button', text: '兑换积分' })
  convertButton.addEventListener('click', async () => {
    convertButton.disabled = true
    try {
      const points = Math.max(1, Number(convertInput.value || 1))
      await accountApi('/api/v1/me/wallet/convert-reading', { method: 'POST', body: { points, request_id: crypto.randomUUID() } })
      await refresh(`已用 ${points} 小时阅读时长兑换 ${points} 积分`)
    } catch (error) { feedback.textContent = error.message }
    finally { convertButton.disabled = false }
  })

  const renderTask = item => {
    const actions = node('div', { class: 'task-card-actions' })
    if (item.can_claim) {
      actions.append(node('button', { class: 'primary-button', type: 'button', text: '接取任务', onclick: async event => {
        event.currentTarget.disabled = true
        try { await accountApi(`/api/v1/deconstruction-tasks/${item.id}/claim`, { method: 'POST' }); await refresh('任务接取成功') }
        catch (error) { feedback.textContent = error.message; event.currentTarget.disabled = false }
      } }))
    } else if (item.viewer_is_claimer && item.status === 'claimed') {
      const file = node('input', { class: 'task-file-input', type: 'file', accept: '.zip,application/zip', required: '' })
      const fileName = node('span', { class: 'task-file-name', text: '尚未选择档案' })
      const filePicker = node('label', { class: 'task-file-picker' }, [
        file,
        node('strong', { text: '选择 ZIP 档案' }),
        fileName
      ])
      file.addEventListener('change', () => { fileName.textContent = file.files?.[0]?.name || '尚未选择档案' })
      const upload = node('button', { class: 'primary-button', type: 'button', text: '上传交付档案' })
      upload.addEventListener('click', async () => {
        if (!file.files?.[0]) { feedback.textContent = '请选择拆书 ZIP 档案'; return }
        upload.disabled = true
        const form = new FormData()
        form.append('file', file.files[0])
        form.append('task_id', item.id)
        try { await accountApi('/api/v1/me/uploads', { method: 'POST', form }); await refresh('档案已上传，审核通过后自动交付') }
        catch (error) { feedback.textContent = error.message; upload.disabled = false }
      })
      actions.append(node('div', { class: 'task-upload-controls' }, [
        filePicker,
        node('p', { class: 'automatic-pricing-note', text: '档案正式入库后，按原文/原文.txt 可见字数每 30 万字奖励 1 积分。' }),
        upload
      ]), node('button', { class: 'ghost-button', type: 'button', text: '放回任务池', onclick: async event => {
        event.currentTarget.disabled = true
        try { await accountApi(`/api/v1/deconstruction-tasks/${item.id}/claim`, { method: 'DELETE' }); await refresh('任务已放回任务池') }
        catch (error) { feedback.textContent = error.message; event.currentTarget.disabled = false }
      } }))
    } else if (item.viewer_is_creator && item.status === 'expired') {
      actions.append(node('button', { class: 'ghost-button', type: 'button', text: '重新发布', onclick: async event => {
        event.currentTarget.disabled = true
        try { await accountApi('/api/v1/deconstruction-tasks', { method: 'POST', body: { book_title: item.book_title, author: item.author || '', request_note: item.request_note || '' } }); await refresh('任务已重新发布，新有效期为 7 天') }
        catch (error) { feedback.textContent = error.message; event.currentTarget.disabled = false }
      } }))
    }
    return node('article', { class: `task-card task-status-${item.status}` }, [
      node('div', { class: 'task-card-heading' }, [node('span', { class: 'task-status', text: deconstructionTaskStatus(item.status) }), node('small', { text: deconstructionTaskExpiry(item) })]),
      node('h3', { text: item.book_title }),
      node('p', { class: 'task-author', text: item.author ? `作者：${item.author}` : '作者未填写' }),
      node('p', { class: 'task-note', text: item.request_note || '未填写额外拆解要求。' }),
      node('p', { class: 'task-owner', text: `发布者：${item.creator_display_name}${item.claimer_display_name ? ` · 接取者：${item.claimer_display_name}` : ''}` }),
      actions
    ])
  }
  const items = data.items || []
  const taskGrid = node('div', { class: 'task-grid' }, items.map(renderTask))
  if (!items.length) taskGrid.append(node('div', { class: 'task-empty' }, [node('h3', { text: '还没有拆书任务' }), node('p', { text: '发布第一条需求，邀请圈内读者协作完成。' })]))
  app.replaceChildren(node('div', { class: 'account-page' }, [
    accountNavigation('deconstruction-tasks'), feedback,
    node('header', { class: 'account-page-heading' }, [node('span', { class: 'eyebrow', text: 'DECONSTRUCTION EXCHANGE' }), node('h1', { text: '拆书任务' }), node('p', { text: '任务 7 天自动过期；接取、上传、审核与交付都在这里完成。' })]),
    node('section', { class: 'task-wallet' }, [
      node('div', {}, [node('span', { text: '可用积分' }), node('strong', { text: String(wallet.balance || 0) }), node('small', { text: `另有 ${wallet.exchangeable_points || 0} 小时可兑换，1 积分等于 1 小时阅读时长` })]),
      node('div', { class: 'task-wallet-actions' }, [convertInput, convertButton]),
      node('p', { text: '储值入口会在支付通道和兑换比例确定后启用，目前不会产生真实资金订单。' })
    ]),
    node('section', { class: 'account-section' }, [node('h2', { text: '发布拆书需求' }), node('p', { text: '发布后进入公共任务池。若 7 天内无人交付，任务自动过期，不占用后台资源。' }), publishForm]),
    node('section', { class: 'task-market' }, [node('div', { class: 'task-market-heading' }, [node('h2', { text: '全部用户任务' }), node('span', { text: `${items.length} 条` })]), taskGrid])
  ]))
}

function accountAvatar(profile, className = 'account-hero-avatar') {
  const wrap = node('div', { class: className })
  if (profile?.avatar_url) {
    const img = node('img', { src: profile.avatar_url, alt: `${profile.display_name || '用户'}的头像` })
    img.addEventListener('error', () => wrap.replaceChildren(node('span', { text: accountInitials() })))
    wrap.append(img)
  } else {
    wrap.append(node('span', { text: accountInitials() }))
  }
  return wrap
}

function readingIdentity(reading, compact = false) {
  const percent = Math.max(0, Math.min(100, Number(reading?.progress || 0) * 100))
  const next = reading?.is_max
    ? '已达最高等级'
    : `距下一级还需 ${formatReadingDuration(reading?.seconds_to_next, { remaining: true })}`
  return node('section', { class: `reading-identity${compact ? ' compact' : ''}` }, [
    node('div', { class: 'reading-level-seal' }, [readingRankIcon(reading)]),
    node('div', { class: 'reading-level-copy' }, [
      node('span', { class: 'eyebrow', text: 'READING IDENTITY' }),
      node('h2', { text: reading?.name || '只如初见' }),
      node('p', { text: `当前可用阅读时长 ${formatReadingDuration(reading?.active_seconds)} · ${next}` }),
      node('div', { class: 'reading-level-progress', role: 'progressbar', 'aria-valuenow': String(Math.round(percent)), 'aria-valuemin': '0', 'aria-valuemax': '100' }, [
        node('i', { style: `width:${percent}%` })
      ])
    ])
  ])
}

async function loadAccountCollection(kind) {
  const config = ACCOUNT_COLLECTIONS[kind]
  if (!state.account || !config) {
    if (!state.account) openAuthDialog('login')
    location.hash = '#/'
    return
  }
  setSeo({ title: `${config.title}｜OOH Story`, description: config.subtitle, canonicalPath: '/', robots: 'noindex, nofollow' })
  const cloud = await refreshCloudState()
  const items = cloud[kind] || []
  const pageSize = 10
  const query = new URLSearchParams((location.hash.split('?')[1] || ''))
  const requestedPage = Math.max(1, Number.parseInt(query.get('page') || '1', 10) || 1)
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize))
  const currentPage = Math.min(requestedPage, pageCount)
  const pageHref = page => `#/account/${kind}?page=${Math.min(Math.max(Number(page) || 1, 1), pageCount)}`
  if (requestedPage !== currentPage) window.history.replaceState(null, '', pageHref(currentPage))
  const visibleItems = items.slice((currentPage - 1) * pageSize, currentPage * pageSize)
  const list = node('div', { class: 'account-book-grid' })
  for (const item of visibleItems) {
    const historyPresentation = kind === 'history' ? readingHistoryPresentation(item) : null
    const cover = node('div', { class: 'account-book-cover record-cover' })
    if (item.cover_url) {
      const image = node('img', { alt: item.title || '作品封面' })
      coverLoader.observe(image, item.cover_url)
      cover.append(image)
    } else cover.append(node('span', { text: 'OOH' }))
    const destination = kind === 'history'
      ? `/books/${historyPresentation.bookId}/chapters/${historyPresentation.chapterId}`
      : `/books/${item.book_id}`
    const remove = node('button', { class: 'account-book-remove', type: 'button', text: '移除', onclick: async event => {
      event.preventDefault()
      const button = event.currentTarget
      button.disabled = true
      try {
        await accountApi(`/api/v1/me/state/${kind}/${item.book_id}`, { method: 'DELETE' })
        state.cloudState[kind] = (state.cloudState[kind] || []).filter(value => value.book_id !== item.book_id)
        await loadAccountCollection(kind)
      } catch (error) {
        button.disabled = false
        button.textContent = error.message
      }
    } })
    list.append(node('article', { class: 'account-book-card' }, [
      node('a', { class: 'account-book-link', href: destination }, [
        cover,
        node('div', { class: 'account-book-copy record-copy' }, [
          node('div', { class: 'account-book-title-row' }, [
            node('h2', { text: item.title || '未命名作品' }),
            node('span', { class: `account-history-status ${item.serialization_status || 'ongoing'}`, text: item.serialization_status === 'finished' ? '已完结' : '连载中' }),
            node('time', {
              class: 'account-reading-time',
              datetime: item.updated_at || '',
              title: item.updated_at ? new Date(item.updated_at).toLocaleString('zh-CN') : '',
              text: item.updated_at ? new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(item.updated_at)) : ''
            })
          ]),
          node('p', { text: item.author || '作者待补充' }),
          kind === 'history' && item.serialization_status !== 'finished'
            ? node('small', { class: 'account-latest-chapter', text: `当前最新：${item.latest_chapter || '等待目录同步'}` })
            : null,
          kind === 'history'
            ? node('span', { class: 'account-book-context', text: historyPresentation.context })
            : kind === 'bookshelf' && item.note
              ? node('span', { class: 'account-book-context', text: item.note })
              : kind === 'favorites'
                ? node('span', { class: 'account-book-context subtle', text: '已同步到云端收藏' })
                : kind === 'bookshelf'
                  ? node('span', { class: 'account-book-context subtle', text: '已同步到私人书架' })
              : null,
          kind === 'history'
            ? node('div', { class: 'account-read-progress' }, [node('i', { style: `width:${historyPresentation.progress}%` })])
            : null
        ])
      ].filter(Boolean)),
      remove
    ]))
  }
  if (!items.length) list.append(node('div', { class: 'account-empty' }, [
    node('span', { text: '✦' }), node('h2', { text: config.empty }), node('a', { class: 'primary-button', href: '/library', text: '去书库看看' })
  ]))
  let pagination = null
  if (items.length) {
    const firstVisible = Math.max(1, Math.min(currentPage - 2, pageCount - 4))
    const lastVisible = Math.min(pageCount, firstVisible + 4)
    const pageNumbers = []
    for (let page = firstVisible; page <= lastVisible; page += 1) {
      pageNumbers.push(node('a', {
        class: `page-number${page === currentPage ? ' active' : ''}`,
        href: pageHref(page),
        text: String(page),
        'aria-current': page === currentPage ? 'page' : null,
        'aria-label': `第 ${page} 页`
      }))
    }
    const action = (label, target, enabled, ariaLabel) => enabled
      ? node('a', { class: 'page-action', href: pageHref(target), text: label, 'aria-label': ariaLabel })
      : node('span', { class: 'page-action disabled', text: label, 'aria-hidden': 'true' })
    const jumpInput = node('input', {
      type: 'number', min: '1', max: String(pageCount), inputmode: 'numeric',
      placeholder: '自定义页数', 'aria-label': `输入 1 到 ${pageCount} 页`
    })
    const jump = () => {
      const page = Number.parseInt(jumpInput.value, 10)
      if (!Number.isFinite(page)) { jumpInput.focus(); return }
      location.hash = pageHref(page).slice(1)
    }
    jumpInput.addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); jump() }
    })
    const jumpControl = node('div', { class: 'page-jump' }, [
      jumpInput, node('button', { type: 'button', text: '跳转', onclick: jump })
    ])
    pagination = node('nav', { class: 'pagination account-pagination', 'aria-label': `${config.title}分页` }, [
      action('首页', 1, currentPage > 1, '返回首页'),
      action('<', currentPage - 1, currentPage > 1, '上一页'),
      node('div', { class: 'page-numbers' }, pageNumbers),
      action('>', currentPage + 1, currentPage < pageCount, '下一页'),
      jumpControl,
      action('尾页', pageCount, currentPage < pageCount, '前往尾页')
    ])
  }
  app.replaceChildren(node('div', { class: 'account-page' }, [
    accountNavigation(kind),
    node('header', { class: 'account-page-heading' }, [
      node('span', { class: 'eyebrow', text: 'MY OOH STORY' }), node('h1', { text: config.title }), node('p', { text: config.subtitle })
    ]),
    items.length ? node('p', { class: 'account-page-count', text: `共 ${items.length} 条记录 · 第 ${currentPage} / ${pageCount} 页` }) : null,
    list,
    pagination
  ].filter(Boolean)))
}

async function loadProfilePage() {
  if (!state.account) {
    openAuthDialog('login')
    location.hash = '#/'
    return
  }
  setSeo({ title: '资料与安全｜OOH Story', description: '管理个人资料、头像、密码和阅读身份。', canonicalPath: '/', robots: 'noindex, nofollow' })
  const data = await accountApi('/api/v1/me/profile')
  state.accountProfile = data.profile
  state.accountReading = data.reading
  updateAccountButton()
  const notice = node('p', { class: 'profile-feedback', role: 'status' })
  let avatarPreview = accountAvatar(data.profile, 'profile-avatar-preview')
  const avatarInput = node('input', { type: 'file', accept: 'image/jpeg,image/png,image/webp' })
  const uploadAvatar = node('button', { class: 'primary-button', type: 'button', text: '上传新头像', onclick: async event => {
    if (!avatarInput.files?.[0]) { notice.textContent = '请先选择图片'; return }
    const button = event.currentTarget
    button.disabled = true
    const form = new FormData(); form.append('file', avatarInput.files[0])
    try {
      const result = await accountApi('/api/v1/me/avatar', { method: 'POST', form })
      data.profile.avatar_url = result.avatar_url
      const nextPreview = accountAvatar(data.profile, 'profile-avatar-preview')
      avatarPreview.replaceWith(nextPreview)
      avatarPreview = nextPreview
      notice.textContent = result.message
    } catch (error) { notice.textContent = error.message }
    finally { button.disabled = false }
  } })
  const removeAvatar = node('button', { class: 'ghost-button', type: 'button', text: '移除头像', onclick: async event => {
    const button = event.currentTarget
    button.disabled = true
    try {
      await accountApi('/api/v1/me/avatar', { method: 'DELETE' })
      data.profile.avatar_url = null
      const nextPreview = accountAvatar(data.profile, 'profile-avatar-preview')
      avatarPreview.replaceWith(nextPreview)
      avatarPreview = nextPreview
      notice.textContent = '头像已移除'
    } catch (error) { notice.textContent = error.message }
    finally { button.disabled = false }
  } })
  const profileForm = node('form', { class: 'profile-form' })
  const gender = node('select', { name: 'gender' }, [
    node('option', { value: '', text: '不填写' }), node('option', { value: 'female', text: '女' }), node('option', { value: 'male', text: '男' }),
    node('option', { value: 'nonbinary', text: '非二元 / 其他' }), node('option', { value: 'prefer_not_say', text: '不愿透露' })
  ])
  gender.value = data.profile.gender || ''
  profileForm.append(
    node('label', { class: 'profile-field' }, [node('span', { text: '显示昵称' }), node('input', { name: 'display_name', value: data.profile.display_name || '', maxlength: '40', required: '', autocomplete: 'nickname' })]),
    node('label', { class: 'profile-field profile-field-wide' }, [node('span', { text: '个人简介' }), node('textarea', { name: 'bio', maxlength: '500', rows: '4', text: data.profile.bio || '', placeholder: '写下你喜欢的故事、作者或阅读偏好' })]),
    node('label', { class: 'profile-field' }, [node('span', { text: '性别（可选）' }), gender]),
    node('label', { class: 'profile-field' }, [node('span', { text: '生日（可选）' }), node('input', { name: 'birthday', type: 'date', value: data.profile.birthday || '', min: '1900-01-01', max: new Date().toISOString().slice(0, 10) })]),
    node('label', { class: 'profile-field profile-field-wide' }, [node('span', { text: '所在地（可选）' }), node('input', { name: 'location', value: data.profile.location || '', maxlength: '80', placeholder: '城市 / 地区' })]),
    node('button', { class: 'primary-button profile-submit', type: 'submit', text: '保存个人资料' })
  )
  profileForm.addEventListener('submit', async event => {
    event.preventDefault()
    const button = profileForm.querySelector('button[type="submit"]'); button.disabled = true
    const values = new FormData(profileForm)
    try {
      const profileBody = Object.fromEntries(values.entries())
      const identityIssue = localUserContentIssue(profileBody.display_name, { identity: true })
      if (identityIssue) {
        openUserContentNotice(identityIssue, {
          identity: true,
          returnFocus: profileForm.querySelector('input[name="display_name"]')
        })
        return
      }
      const result = await accountApi('/api/v1/me/profile', { method: 'PUT', body: profileBody })
      state.account.display_name = result.profile.display_name
      state.accountProfile = result.profile
      updateAccountButton()
      notice.textContent = ''
      showAccountSuccessToast('个人资料保存成功')
    } catch (error) {
      if (isUserContentGuardIssue(error.message, { identity: true })) {
        openUserContentNotice(error.message, {
          identity: true,
          returnFocus: profileForm.querySelector('input[name="display_name"]')
        })
      } else {
        notice.textContent = error.message
      }
    }
    finally { button.disabled = false }
  })
  const passwordLoginEnabled = Boolean(data.login_methods?.password)
  const passwordFields = []
  if (passwordLoginEnabled) passwordFields.push(
    node('label', { class: 'profile-field' }, [node('span', { text: '当前密码' }), node('input', { name: 'current_password', type: 'password', autocomplete: 'current-password', maxlength: '128', required: '' })])
  )
  passwordFields.push(
    node('label', { class: 'profile-field' }, [node('span', { text: passwordLoginEnabled ? '新密码' : '设置登录密码' }), node('input', { name: 'new_password', type: 'password', autocomplete: 'new-password', minlength: '12', maxlength: '128', required: '', placeholder: '至少 12 位，包含三类字符' })]),
    node('label', { class: 'profile-field' }, [node('span', { text: '确认新密码' }), node('input', { name: 'confirm_password', type: 'password', autocomplete: 'new-password', minlength: '12', maxlength: '128', required: '' })]),
    node('button', { class: 'primary-button', type: 'submit', text: passwordLoginEnabled ? '修改密码' : '启用邮箱密码登录' })
  )
  const passwordForm = node('form', { class: 'password-form' }, passwordFields)
  const passwordVerificationSlot = node('div', { class: 'google-login-slot' })
  passwordForm.addEventListener('submit', async event => {
    event.preventDefault()
    const values = new FormData(passwordForm)
    if (values.get('new_password') !== values.get('confirm_password')) { notice.textContent = '两次输入的新密码不一致'; return }
    const button = passwordForm.querySelector('button'); button.disabled = true
    try {
      if (!passwordLoginEnabled) {
        notice.textContent = '请在下方使用当前 Google 账户确认身份。'
        await renderGoogleButton(passwordVerificationSlot, {
          mode: 'verify',
          onSuccess: async credential => {
            const confirmedValues = new FormData(passwordForm)
            if (confirmedValues.get('new_password') !== confirmedValues.get('confirm_password')) {
              throw new Error('两次输入的新密码不一致')
            }
            const result = await accountApi('/api/v1/me/password/setup', {
              method: 'POST',
              body: {
                id_token: credential.id_token,
                new_password: confirmedValues.get('new_password'),
                client: 'web'
              }
            })
            passwordForm.reset()
            notice.textContent = ''
            if (state.account) state.account.password_login_enabled = true
            await loadProfilePage()
            showAccountSuccessToast(result.message)
          }
        })
        return
      }
      const result = await accountApi('/api/v1/me/password', { method: 'POST', body: { current_password: values.get('current_password'), new_password: values.get('new_password') } })
      passwordForm.reset()
      notice.textContent = ''
      await loadProfilePage()
      showAccountSuccessToast(result.message)
    } catch (error) { notice.textContent = error.message }
    finally { button.disabled = false }
  })
  const levels = [
    ['Ⅰ','只如初见','0'],['Ⅱ','此去经年','30'],['Ⅲ','素心相赠','100'],['Ⅳ','犹故人归','250'],['Ⅴ','踏歌寻醉','500'],['Ⅵ','冷暖自知','1,000'],
    ['Ⅶ','青青子衿','1,800'],['Ⅷ','似水流年','3,000'],['Ⅸ','不诉离殇','5,000'],['Ⅹ','近月侵衣','8,000'],['Ⅺ','对酒当歌','12,000'],['Ⅻ','长风万里','18,000'],
    ['ⅩⅢ','知与谁同','26,000'],['ⅩⅣ','扶摇九霄','36,000'],['ⅩⅤ','凌云绝顶','48,000'],['ⅩⅥ','摘星揽月','62,000'],['ⅩⅦ','天人合一','80,000'],['ⅩⅧ','水月镜花','100,000']
  ]
  app.replaceChildren(node('div', { class: 'account-page' }, [
    accountNavigation('profile'),
    notice,
    readingIdentity(data.reading),
    node('section', { class: 'account-section profile-avatar-section' }, [
      node('div', { class: 'profile-avatar-row' }, [avatarPreview, node('div', {}, [node('h2', { text: '个人头像' }), node('p', { text: '支持 JPEG、PNG、WebP；系统会安全解码、去除元数据并统一保存。' }), avatarInput, node('div', { class: 'profile-inline-actions' }, [uploadAvatar, removeAvatar])])])
    ]),
    node('section', { class: 'account-section' }, [node('h2', { text: '详细个人信息' }), node('p', { text: '这些资料仅用于你的个人中心，不公开邮箱或私密信息。' }), profileForm]),
    node('section', { class: 'account-section' }, [
      node('h2', { text: passwordLoginEnabled ? '账户安全' : '按需启用邮箱密码登录' }),
      node('p', {
        text: passwordLoginEnabled
          ? '修改成功后，其他设备会退出登录，当前设备保持在线。'
          : `当前仅使用 Google 登录。你可以继续保持现状，也可以为 ${state.account.email} 设置密码，以后任选一种方式登录。`
      }),
      passwordForm,
      ...(passwordLoginEnabled ? [] : [passwordVerificationSlot])
    ]),
    node('section', { class: 'account-section' }, [node('h2', { text: '阅读等级图鉴' }), node('p', { text: '仅在页面可见且有真实互动时积累；每次助力推荐会捐赠 1 小时阅读经验时长。' }), node('div', { class: 'reading-level-map' }, levels.map(([roman,name,hours], index) => node('div', { class: index + 1 === data.reading.level ? 'current' : '' }, [readingRankIcon({ level: index + 1, roman, name }, { decorative: true }), node('span', { text: name }), node('small', { text: `${hours} 小时` })])))])
  ]))
}

const SUBMISSION_DEFAULT_COVER = '/api/v1/assets/default-cover'

function submissionTitle(item, type) {
  if (type === 'novel') return item.title || '未命名小说'
  return String(item.output_slug || item.original_filename || '未命名拆文').replace(/\.zip$/i, '')
}

function submissionCover(item, type, className = '') {
  const title = submissionTitle(item, type)
  const image = node('img', { alt: `《${title}》封面`, loading: 'lazy' })
  image.addEventListener('error', () => {
    if (image.dataset.fallbackApplied) return
    image.dataset.fallbackApplied = '1'
    coverLoader.observe(image, SUBMISSION_DEFAULT_COVER)
  })
  coverLoader.observe(image, item.cover_url || SUBMISSION_DEFAULT_COVER)
  return node('figure', { class: `submission-cover ${className}`.trim() }, [image])
}

function submissionProgress(item) {
  const stageLabels = ['已提交', '内容审核', '正式入库']
  const stageStates = (() => {
    if (item.status === 'completed') return ['done', 'done', 'done']
    if (item.status === 'approved') return ['done', 'done', 'current']
    if (item.status === 'rejected') return ['done', 'error', 'pending']
    if (['ai_pending', 'reviewing'].includes(item.status)) return ['done', 'current', 'pending']
    return ['current', 'pending', 'pending']
  })()
  return node('div', { class: 'submission-progress', 'aria-label': `投稿进度：${uploadStatusLabel(item.status)}` }, stageLabels.map((label, index) =>
    node('span', { class: `submission-stage ${stageStates[index]}`, text: label })
  ))
}

function submissionRecord(item, type) {
  const structure = item.structure_report
  const missing = structure?.missing_files || item.review_result?.missing_files || []
  const title = submissionTitle(item, type)
  const structureLabel = structure?.profile === 'long'
    ? '长篇结构'
    : structure?.profile === 'short' ? '短篇结构' : '等待结构识别'
  const detail = item.author
    ? `${item.author} / ${item.category || '未分类'}`
    : `${structureLabel} / ${Number(structure?.file_count || 0)} 个文件`
  const reason = missing.length ? `缺少：${missing.join('、')}` : item.rejection_reason
  return node('article', { class: `submission-record${item.status === 'rejected' ? ' needs-attention' : ''}` }, [
    submissionCover(item, type, 'submission-history-cover'),
    node('div', { class: 'submission-record-body' }, [
      node('div', { class: 'submission-record-topline' }, [
        node('span', { class: 'submission-record-kind', text: type === 'novel' ? '小说投稿' : '拆书文' }),
        ...(item.created_at ? [node('time', { datetime: item.created_at, text: new Date(item.created_at).toLocaleDateString('zh-CN') })] : []),
        node('span', { class: `submission-status status-${item.status}`, text: uploadStatusLabel(item.status) })
      ]),
      node('h3', { text: title }),
      node('p', { class: 'submission-record-detail', text: detail }),
      submissionProgress(item),
      ...(reason ? [node('small', { class: 'submission-reason', text: reason })] : [])
    ])
  ])
}

function submissionDetailRecord(item, type, feedback, refresh) {
  const title = submissionTitle(item, type)
  const field = (label, value) => node('div', {}, [
    node('dt', { text: label }),
    node('dd', { text: value || '暂无' })
  ])
  const details = []
  const actions = node('div', { class: 'submission-detail-actions' })
  if (type === 'novel') {
    details.push(...[
      field('连载状态', item.serialization_status === 'finished' ? '已完结' : '连载中'),
      field('正文文件', item.manuscript_filename || '已安全保存'),
      field('文件大小', formatBytes(Number(item.bytes || 0))),
      field('作品来源', item.source || '未填写'),
      field('作品分类', item.category || '未分类'),
      field('正式入库', item.completed_at ? new Date(item.completed_at).toLocaleDateString('zh-CN') : '已完成')
    ])
  } else {
    const structure = item.structure_report || {}
    const originalCharacters = Number(structure.original_text_char_count)
    const downloadPoints = Number(item.download_points || 0)
    const rewardPoints = Number(item.reward_points || 0)
    const pointsEarned = Number(item.points_earned || 0)
    details.push(...[
      field('拆文类型', structure.profile === 'long' ? '长篇拆文' : structure.profile === 'short' ? '短篇拆文' : '等待结构识别'),
      field('档案文件', `${Number(structure.file_count || 0)} 个`),
      field('文件大小', formatBytes(Number(item.bytes || 0))),
      field('原文字数', Number.isFinite(originalCharacters) ? `${originalCharacters.toLocaleString('zh-CN')} 字` : '等待安全检查'),
      field('审核奖励', `${rewardPoints.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 积分`),
      field('下载方式', downloadPoints ? `${downloadPoints.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 积分下载` : '免费下载'),
      field('已售下载权', `${Number(item.purchase_count || 0)} 份`),
      field('销售获得', `${pointsEarned.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 积分`)
    ])
    if (item.status === 'completed' && item.output_slug && item.product_available) {
      const mode = node('select', { 'aria-label': `${item.original_filename || '拆文'}下载方式` }, [
        node('option', { value: 'free', text: '免费下载' }),
        node('option', { value: 'paid', text: '积分下载' })
      ])
      mode.value = downloadPoints > 0 ? 'paid' : 'free'
      const price = node('input', {
        type: 'number', min: '0.01', max: '999', step: '0.01',
        value: downloadPoints > 0 ? String(Math.round(downloadPoints * 100) / 100) : '0.01',
        disabled: downloadPoints > 0 ? null : '',
        'aria-label': `${item.original_filename || '拆文'}下载积分`
      })
      mode.addEventListener('change', () => { price.disabled = mode.value === 'free' })
      const save = node('button', { class: 'primary-button', type: 'button', text: '保存下载方式' })
      save.addEventListener('click', async () => {
        const rawPoints = Number(price.value)
        const points = mode.value === 'free' ? 0 : Math.round(rawPoints * 100) / 100
        if (mode.value === 'paid' && (!Number.isFinite(points) || points < 0.01 || points > 999)) {
          feedback.textContent = '积分下载价格必须在 0.01 至 999 之间，最多两位小数'
          return
        }
        save.disabled = true
        feedback.textContent = ''
        try {
          const result = await accountApi(`/api/v1/me/deconstructions/${encodeURIComponent(item.output_slug)}/price`, {
            method: 'PATCH', body: { download_points: points }
          })
          try {
            localStorage.setItem('oohstory-deconstruction-price-sync', JSON.stringify({
              slug: result.slug, download_points: result.download_points, updated_at: result.updated_at
            }))
          } catch {}
          showAccountSuccessToast(points ? `已设为 ${points} 积分下载` : '已设为免费下载')
          await refresh()
        } catch (error) {
          feedback.textContent = error.message
          save.disabled = false
        }
      })
      actions.append(node('div', { class: 'submission-price-editor' }, [
        node('div', { class: 'submission-price-copy' }, [node('strong', { text: '下载方式' }), node('small', { text: '随时切换免费或积分下载' })]),
        node('label', {}, [node('span', { text: '方式' }), mode]),
        node('label', {}, [node('span', { text: '所需积分' }), price]),
        save
      ]))
    }
    if (item.output_slug) {
      actions.append(node('a', {
        class: 'ghost-button',
        href: `/deconstructions/${encodeURIComponent(item.output_slug)}`,
        text: '查看拆文详情'
      }))
    }
    if (item.task_id) actions.append(node('a', { class: 'ghost-button', href: '#/account/deconstruction-tasks', text: '查看关联任务' }))
  }
  const coverInput = node('input', { type: 'file', accept: 'image/jpeg,image/png,image/webp' })
  const coverAction = node('label', { class: 'published-cover-action' }, [
    coverInput,
    node('span', { text: item.cover_source === 'custom' ? '再次更新封面' : '更新封面' })
  ])
  coverInput.addEventListener('change', async () => {
    if (!coverInput.files?.[0]) return
    feedback.textContent = '正在安全处理新封面…'
    coverAction.classList.add('is-loading')
    const form = new FormData()
    form.append('file', coverInput.files[0])
    try {
      const result = await accountApi(`/api/v1/me/submissions/${type}/${encodeURIComponent(item.id)}/cover`, { method: 'POST', form })
      feedback.textContent = ''
      showAccountSuccessToast(result.message || '封面已更新')
      await refresh()
    } catch (error) {
      feedback.textContent = error.message
      coverAction.classList.remove('is-loading')
    }
  })
  const sourceLabel = item.cover_source === 'custom'
    ? '自定义封面'
    : item.cover_source === 'library' ? '书库封面' : 'OOH Story 默认封面'
  const coverColumn = node('div', { class: 'published-cover-column' }, [
    submissionCover(item, type, 'published-work-cover'),
    node('small', { class: 'published-cover-source', text: sourceLabel }),
    coverAction
  ])
  const subtitle = type === 'novel'
    ? `${item.author || '佚名'} / ${item.category || '未分类'}`
    : `${item.structure_report?.profile === 'long' ? '长篇拆文' : '短篇拆文'} / 正式档案`
  const body = node('div', { class: 'published-work-body' }, [
    node('header', { class: 'published-work-heading' }, [
      node('div', {}, [
        node('span', { class: 'submission-record-kind', text: type === 'novel' ? '已过审书籍' : '已过审拆文' }),
        node('h3', { text: title }),
        node('p', { text: subtitle })
      ]),
      node('span', { class: 'submission-status status-completed', text: '已正式入库' })
    ]),
    node('dl', { class: 'submission-detail-grid', 'aria-label': `${title}档案详情` }, details),
    ...(item.summary ? [node('p', { class: 'submission-detail-summary', text: item.summary })] : []),
    ...(type === 'deconstruction' ? [node('p', { class: 'submission-pricing-note', text: '审核奖励按原文字数 ÷ 30 万一次性发放。下载方式和价格由你自行设置。' })] : []),
    ...(actions.childElementCount ? [actions] : [])
  ])
  return node('article', { class: `published-work-card published-work-${type}` }, [coverColumn, body])
}

async function loadSubmitPage() {
  if (!state.account) { openAuthDialog('login'); location.hash = '#/'; return }
  setSeo({ title: '投稿｜OOH Story', description: '安全上传拆书结构或小说正文。', canonicalPath: '/', robots: 'noindex, nofollow' })
  const [categoryData, deconstructions, novels] = await Promise.all([
    state.categories.length ? Promise.resolve({ items: state.categories }) : api('/api/v1/categories'),
    accountApi('/api/v1/me/uploads'),
    accountApi('/api/v1/me/novel-submissions')
  ])
  state.categories = (categoryData.items || []).filter(item => item?.name)
  const feedback = node('p', { class: 'profile-feedback', role: 'status' })
  const novelHistory = novels.items || []
  const deconstructionHistory = deconstructions.items || []
  const historyItems = [...novelHistory, ...deconstructionHistory]
  const historyCounts = historyItems.reduce((result, item) => {
    if (item.status === 'completed') result.completed += 1
    else if (item.status === 'rejected') result.rejected += 1
    else result.processing += 1
    return result
  }, { processing: 0, completed: 0, rejected: 0 })
  const novelHistoryGrid = node('div', { class: 'submission-records', role: 'list' },
    novelHistory.map(item => submissionRecord(item, 'novel')))
  if (!novelHistory.length) novelHistoryGrid.append(node('div', { class: 'submission-empty-state' }, [node('h3', { text: '暂无小说上传记录' })]))
  const deconstructionHistoryGrid = node('div', { class: 'submission-records', role: 'list' },
    deconstructionHistory.map(item => submissionRecord(item, 'deconstruction')))
  if (!deconstructionHistory.length) deconstructionHistoryGrid.append(node('div', { class: 'submission-empty-state' }, [node('h3', { text: '暂无拆文上传记录' })]))
  const reviewRules = () => node('details', { class: 'submission-policy' }, [
    node('summary', { text: '查看审核与入库规则' }),
    node('div', { class: 'submission-review-rules' }, [
      node('p', { text: '审核会覆盖完整正文与档案内容，不只检查标题、封面或开头。' }),
      node('ul', {}, [
        node('li', { text: '覆盖 TXT 全文、EPUB 内部章节及拆书结构内全部文本。' }),
        node('li', { text: '标题、简介、报告与正文主题必须一致；伪装成正常书籍的广告或违法内容会被驳回。' }),
        node('li', { text: '小说与拆解中的虚构赌局、下注、押注情节允许；禁止现实赌博推广、开户充值、诈骗、违法交易、广告引流、网址、邮箱、联系方式及二维码。' })
      ])
    ])
  ])

  const deconstructionInput = node('input', { class: 'task-file-input', type: 'file', accept: '.zip,application/zip', required: '' })
  const deconstructionFileName = node('span', { class: 'task-file-name', text: '尚未选择档案' })
  const deconstructionPicker = node('label', { class: 'task-file-picker submission-file-picker' }, [
    deconstructionInput,
    node('strong', { text: '选择 ZIP 档案' }),
    deconstructionFileName
  ])
  deconstructionInput.addEventListener('change', () => {
    deconstructionFileName.textContent = deconstructionInput.files?.[0]?.name || '尚未选择档案'
  })
  const deconstructionButton = node('button', { class: 'primary-button', type: 'submit', text: '上传并开始审核' })
  const deconstructionForm = node('form', { class: 'upload-box submission-upload-box' }, [
    deconstructionPicker,
    node('p', { class: 'automatic-pricing-note', text: '审核奖励：正式入库后按原文/原文.txt 可见字数计算，每 30 万字奖励 1 积分，不足部分按比例四舍五入至两位；下载方式在「我的投稿」中另行设置。' }),
    deconstructionButton
  ])
  deconstructionForm.addEventListener('submit', async event => {
    event.preventDefault()
    if (!deconstructionInput.files?.[0]) return
    deconstructionButton.disabled = true
    feedback.textContent = '正在上传文件…'
    const form = new FormData(); form.append('file', deconstructionInput.files[0])
    try {
      const result = await accountApi('/api/v1/me/uploads', { method: 'POST', form })
      feedback.textContent = ''
      deconstructionForm.reset()
      showAccountSuccessToast(result.message || '上传成功，正在等待审核')
      await loadSubmitPage()
    } catch (error) { feedback.textContent = error.message }
    finally { deconstructionButton.disabled = false }
  })

  const novelForm = node('form', { class: 'novel-submission-form' })
  const steps = [
    node('fieldset', { class: 'submission-step' }, [
      node('legend', { text: '作品资料' }),
      node('label', { class: 'profile-field' }, [node('span', { text: '书名' }), node('input', { name: 'title', maxlength: '160', required: '' })]),
      node('label', { class: 'profile-field' }, [node('span', { text: '作者' }), node('input', { name: 'author', maxlength: '100', required: '' })]),
      node('label', { class: 'profile-field' }, [
        node('span', { text: '分类' }),
        node('select', { name: 'category', required: '' }, [
          node('option', { value: '', text: '请选择系统分类', disabled: '', selected: '' }),
          ...state.categories.map(item => node('option', { value: item.name, text: item.name }))
        ])
      ]),
      node('label', { class: 'profile-field' }, [node('span', { text: '连载状态' }), node('select', { name: 'serialization_status' }, [node('option', { value: 'ongoing', text: '连载中' }), node('option', { value: 'finished', text: '已完结' })])]),
      node('label', { class: 'profile-field profile-field-wide' }, [node('span', { text: '作品简介' }), node('textarea', { name: 'summary', minlength: '20', maxlength: '4000', rows: '6', required: '' })])
    ]),
    node('fieldset', { class: 'submission-step', hidden: '' }, [
      node('legend', { text: '正文与封面' }),
      node('label', { class: 'profile-field profile-field-wide' }, [node('span', { text: '正文 TXT / EPUB' }), node('input', { name: 'manuscript', type: 'file', accept: '.txt,.epub,text/plain,application/epub+zip', required: '' })]),
      node('label', { class: 'profile-field profile-field-wide' }, [node('span', { text: '书籍封面 JPEG / PNG / WebP' }), node('input', { name: 'cover', type: 'file', accept: 'image/jpeg,image/png,image/webp', required: '' })])
    ]),
    node('fieldset', { class: 'submission-step', hidden: '' }, [
      node('legend', { text: '来源与授权' }),
      node('label', { class: 'profile-field profile-field-wide' }, [node('span', { text: '作品来源' }), node('input', { name: 'source', maxlength: '500', placeholder: '原创 / 开源地址 / 授权方', required: '' })]),
      node('label', { class: 'profile-field profile-field-wide' }, [node('span', { text: '版权或授权说明' }), node('textarea', { name: 'authorization', minlength: '10', maxlength: '2000', rows: '5', required: '', placeholder: '请说明你有权上传并允许我站展示的依据' })]),
      node('p', { class: 'page-subtitle', text: '提交后会先进入隔离沙箱、ClamAV 验毒与审核；未通过不会写入书库。' })
    ])
  ]
  let currentStep = 0
  const stepLabel = node('strong', { text: '1 / 3', 'aria-live': 'polite' })
  const previous = node('button', { class: 'ghost-button', type: 'button', text: '上一步', disabled: '' })
  const next = node('button', { class: 'primary-button', type: 'button', text: '下一步' })
  const submit = node('button', { class: 'primary-button', type: 'submit', text: '提交审核', hidden: '' })
  const showStep = index => {
    currentStep = Math.max(0, Math.min(2, index))
    steps.forEach((step, position) => { step.hidden = position !== currentStep })
    stepperItems.forEach((item, position) => {
      item.classList.toggle('is-current', position === currentStep)
      item.classList.toggle('is-complete', position < currentStep)
      if (position === currentStep) item.setAttribute('aria-current', 'step')
      else item.removeAttribute('aria-current')
    })
    previous.disabled = currentStep === 0; next.hidden = currentStep === 2; submit.hidden = currentStep !== 2
    stepLabel.textContent = `${currentStep + 1} / 3`
  }
  previous.addEventListener('click', () => showStep(currentStep - 1))
  next.addEventListener('click', () => {
    const controls = [...steps[currentStep].querySelectorAll('input,textarea,select')]
    if (controls.some(control => !control.reportValidity())) return
    showStep(currentStep + 1)
  })
  const stepperItems = ['作品资料', '正文与封面', '授权确认'].map(label => node('li', { text: label }))
  novelForm.append(node('ol', { class: 'submission-stepper', 'aria-label': '小说投稿步骤' }, stepperItems), ...steps,
    node('div', { class: 'submission-wizard-actions' }, [previous, stepLabel, next, submit]))
  showStep(0)
  novelForm.addEventListener('submit', async event => {
    event.preventDefault(); submit.disabled = true; feedback.textContent = '正在隔离沙箱扫描正文与封面…'
    const values = new FormData(novelForm)
    const metadata = Object.fromEntries(['title','author','category','serialization_status','summary','source','authorization'].map(key => [key, values.get(key)]))
    const form = new FormData(); form.append('metadata', JSON.stringify(metadata)); form.append('manuscript', values.get('manuscript')); form.append('cover', values.get('cover'))
    try {
      const result = await accountApi('/api/v1/me/novel-submissions', { method: 'POST', form })
      feedback.textContent = ''
      showAccountSuccessToast(result.message || '投稿成功，正在等待审核')
      await loadSubmitPage()
    } catch (error) { feedback.textContent = error.message }
    finally { submit.disabled = false }
  })

  const deconstructionPanel = node('section', { class: 'submission-workspace-panel submission-panel-deconstruction' }, [
    node('header', { class: 'submission-workspace-heading' }, [
      node('div', {}, [
        node('h2', { text: '上传我的拆书文' }),
        node('p', {}, [document.createTextNode('提交标准 ZIP 档案，系统会完成结构识别、内容复核和正式入库。'), node('a', { href: 'https://github.com/worldwonderer/oh-story-claudecode', target: '_blank', rel: 'noopener noreferrer', text: '查看标准结构' })])
      ]),
      node('span', { class: 'submission-format-label', text: 'ZIP 档案' })
    ]),
    deconstructionForm,
    reviewRules()
  ])
  const novelPanel = node('section', { class: 'submission-workspace-panel submission-panel-novel', hidden: '' }, [
    node('header', { class: 'submission-workspace-heading' }, [
      node('div', {}, [
        node('h2', { text: '上传小说' }),
        node('p', { text: '依次提交作品资料、正文封面与版权授权，审核完成后进入书库。' })
      ]),
      node('span', { class: 'submission-format-label', text: 'TXT / EPUB' })
    ]),
    novelForm,
    reviewRules()
  ])
  const uploadModeButtons = [
    node('button', { class: 'submission-mode-button is-active', type: 'button', role: 'tab', 'aria-selected': 'true' }, [
      node('strong', { text: '上传我的拆书文' }),
      node('span', { text: '标准结构 ZIP 档案' })
    ]),
    node('button', { class: 'submission-mode-button', type: 'button', role: 'tab', 'aria-selected': 'false' }, [
      node('strong', { text: '上传小说' }),
      node('span', { text: '正文、封面与授权' })
    ])
  ]
  const setUploadMode = mode => {
    const novelMode = mode === 'novel'
    deconstructionPanel.hidden = novelMode
    novelPanel.hidden = !novelMode
    uploadModeButtons.forEach((button, index) => {
      const active = novelMode ? index === 1 : index === 0
      button.classList.toggle('is-active', active)
      button.setAttribute('aria-selected', String(active))
    })
  }
  uploadModeButtons[0].addEventListener('click', () => setUploadMode('deconstruction'))
  uploadModeButtons[1].addEventListener('click', () => setUploadMode('novel'))

  const novelHistoryPanel = node('div', { class: 'submission-history-view', role: 'tabpanel' }, [novelHistoryGrid])
  const deconstructionHistoryPanel = node('div', { class: 'submission-history-view', role: 'tabpanel', hidden: '' }, [deconstructionHistoryGrid])
  const historyTabs = [
    node('button', { class: 'submission-history-tab is-active', type: 'button', role: 'tab', 'aria-selected': 'true' }, [
      node('strong', { text: '小说上传记录' }), node('span', { text: `${novelHistory.length} 条` })
    ]),
    node('button', { class: 'submission-history-tab', type: 'button', role: 'tab', 'aria-selected': 'false' }, [
      node('strong', { text: '拆文上传记录' }), node('span', { text: `${deconstructionHistory.length} 条` })
    ])
  ]
  const setHistoryView = type => {
    const deconstructionView = type === 'deconstruction'
    novelHistoryPanel.hidden = deconstructionView
    deconstructionHistoryPanel.hidden = !deconstructionView
    historyTabs.forEach((button, index) => {
      const active = deconstructionView ? index === 1 : index === 0
      button.classList.toggle('is-active', active)
      button.setAttribute('aria-selected', String(active))
    })
  }
  historyTabs[0].addEventListener('click', () => setHistoryView('novel'))
  historyTabs[1].addEventListener('click', () => setHistoryView('deconstruction'))

  app.replaceChildren(node('div', { class: 'account-page submission-studio-page' }, [
    accountNavigation('submit'), feedback,
    node('header', { class: 'submission-page-intro' }, [
      node('div', {}, [node('h1', { text: '投稿工作台' }), node('p', { text: '选择一种投稿方式，提交后可在下方查看完整审核与入库进度。' })]),
      node('a', { class: 'ghost-button', href: '#/account/submissions', text: '查看已入库作品' })
    ]),
    node('section', { class: 'submission-workspace', 'aria-label': '选择投稿类型并上传' }, [
      node('div', { class: 'submission-mode-switch', role: 'tablist', 'aria-label': '投稿类型' }, [
        node('p', { text: '选择投稿类型' }),
        ...uploadModeButtons,
        node('small', { text: '文件会先进入隔离扫描，审核通过后才会正式入库。' })
      ]),
      node('div', { class: 'submission-workspace-canvas' }, [deconstructionPanel, novelPanel])
    ]),
    node('section', { class: 'submission-history-panel submission-archive', 'aria-labelledby': 'submission-archive-heading' }, [
      node('header', { class: 'submission-archive-heading' }, [
        node('div', {}, [node('h2', { id: 'submission-archive-heading', text: '上传记录' }), node('p', { text: '保留每次上传、审核、驳回和正式入库的完整进度。' })]),
        node('div', { class: 'submission-archive-metrics', 'aria-label': '上传记录概览' }, [
          node('span', {}, [node('strong', { text: String(historyItems.length) }), node('small', { text: '全部' })]),
          node('span', {}, [node('strong', { text: String(historyCounts.processing) }), node('small', { text: '处理中' })]),
          node('span', {}, [node('strong', { text: String(historyCounts.completed) }), node('small', { text: '已完成' })]),
          node('span', {}, [node('strong', { text: String(historyCounts.rejected) }), node('small', { text: '未通过' })])
        ])
      ]),
      node('div', { class: 'submission-history-tabs', role: 'tablist', 'aria-label': '上传记录类型' }, historyTabs),
      novelHistoryPanel,
      deconstructionHistoryPanel
    ])
  ]))
}

async function loadMySubmissionsPage() {
  if (!state.account) { openAuthDialog('login'); location.hash = '#/'; return }
  setSeo({ title: '我的投稿｜OOH Story', description: '查看本人已过审并正式入库的书籍与拆文。', canonicalPath: '/', robots: 'noindex, nofollow' })
  const [published, notificationData] = await Promise.all([
    accountApi('/api/v1/me/published-submissions'),
    accountApi('/api/v1/me/notifications?limit=1')
  ])
  state.notificationsUnread = Number(notificationData.unread_count || 0)
  const feedback = node('p', { class: 'profile-feedback', role: 'status' })
  const refresh = async () => loadMySubmissionsPage()
  const novelItems = published.novels || []
  const deconstructionItems = published.deconstructions || []
  const freeDeconstructions = deconstructionItems.filter(item => Number(item.download_points || 0) === 0).length
  const paidDeconstructions = deconstructionItems.length - freeDeconstructions
  const novelGrid = node('div', { class: 'published-work-list', role: 'list' },
    novelItems.map(item => submissionDetailRecord(item, 'novel', feedback, refresh)))
  if (!novelItems.length) novelGrid.append(node('div', { class: 'submission-empty-state' }, [node('h3', { text: '暂无已过审书籍' }), node('p', { text: '审核和正式入库完成后，书籍会出现在这里。' }), node('a', { href: '#/account/submit', text: '查看上传记录 →' })]))
  const deconstructionGrid = node('div', { class: 'published-work-list', role: 'list' },
    deconstructionItems.map(item => submissionDetailRecord(item, 'deconstruction', feedback, refresh)))
  if (!deconstructionItems.length) deconstructionGrid.append(node('div', { class: 'submission-empty-state' }, [node('h3', { text: '暂无已过审拆文' }), node('p', { text: '审核和正式入库完成后，拆文及下载方式会出现在这里。' }), node('a', { href: '#/account/submit', text: '查看上传记录 →' })]))
  app.replaceChildren(node('div', { class: 'account-page my-submissions-page' }, [
    accountNavigation('submissions'), feedback,
    node('header', { class: 'account-page-heading my-submissions-heading' }, [
      node('div', {}, [node('span', { class: 'eyebrow', text: 'MY CONTRIBUTIONS' }), node('h1', { text: '我的投稿' }), node('p', { text: '这里只展示已经审核通过并完成正式入库的书籍与拆文。上传及审核记录请到「投稿」查看。' })]),
      node('a', { class: 'primary-button', href: '#/account/submit', text: '投稿与上传记录' })
    ]),
    node('div', { class: 'submission-history-summary my-submissions-summary', 'aria-label': '已过审投稿概览' }, [
      node('span', {}, [node('strong', { text: String(novelItems.length) }), node('small', { text: '已过审书籍' })]),
      node('span', {}, [node('strong', { text: String(deconstructionItems.length) }), node('small', { text: '已过审拆文' })]),
      node('span', {}, [node('strong', { text: String(freeDeconstructions) }), node('small', { text: '免费下载' })]),
      node('span', {}, [node('strong', { text: String(paidDeconstructions) }), node('small', { text: '积分下载' })])
    ]),
    node('section', { class: 'submission-history-panel submission-library-panel', 'aria-labelledby': 'my-novel-submissions' }, [
      node('header', { class: 'submission-history-header' }, [node('div', {}, [node('h2', { id: 'my-novel-submissions', text: '已过审书籍' }), node('p', { text: '已经完成审核并正式入库的作品' })]), node('span', { text: `${novelItems.length} 本` })]),
      novelGrid
    ]),
    node('section', { class: 'submission-history-panel submission-library-panel', 'aria-labelledby': 'my-deconstruction-submissions' }, [
      node('header', { class: 'submission-history-header' }, [node('div', {}, [node('h2', { id: 'my-deconstruction-submissions', text: '已过审拆文' }), node('p', { text: '已经正式入库的拆文及其当前下载方式' })]), node('span', { text: `${deconstructionItems.length} 份` })]),
      deconstructionGrid
    ]),
    node('p', { class: 'submission-live-sync-note', text: '拆文正式入库后会按原文字数一次性发放审核奖励；你可将档案设为免费下载或 0.01–999 积分下载。购买与下载实时读取最新价格，已购下载权永久保留。' })
  ]))
}

const NOTIFICATIONS_PER_PAGE = 10

async function loadNotificationsPage(requestedPage = 1) {
  if (!state.account) { openAuthDialog('login'); location.hash = '#/'; return }
  setSeo({ title: '消息中心｜OOH Story', description: '查看投稿审核与入库通知。', canonicalPath: '/', robots: 'noindex, nofollow' })
  const targetPage = Math.max(1, Number(requestedPage) || 1)
  const data = await accountApi(`/api/v1/me/notifications?limit=${NOTIFICATIONS_PER_PAGE}&page=${targetPage}`)
  state.notificationsUnread = Number(data.unread_count || 0)
  const totalCount = Number(data.total_count || 0)
  const pageCount = Math.max(1, Number(data.page_count || 1))
  const currentPage = Math.min(targetPage, pageCount)
  const notificationView = item => {
    const searchable = `${item.title || ''} ${item.message || ''}`
    const rejected = /驳回|未通过|失败|缺少/.test(searchable)
    if (rejected) return { tone: 'danger', icon: '补', label: '需要处理' }
    if (item.kind === 'submission_ingestion') return { tone: 'success', icon: '入', label: '入库进度' }
    return { tone: 'review', icon: '审', label: '审核动态' }
  }
  const list = node('div', { class: 'notification-list', role: 'list' }, (data.items || []).map(item => {
    const view = notificationView(item)
    const status = item.resource_status ? uploadStatusLabel(item.resource_status) : ''
    const resourceTitle = String(item.resource_title || '').replace(/\.zip$/i, '')
    const actions = node('div', { class: 'notification-actions' }, [
      ...(item.action_url ? [node('a', { class: 'notification-action-link', href: item.action_url, text: '查看投稿 →' })] : []),
      ...(!item.read_at ? [node('button', { class: 'notification-read-button', type: 'button', text: '标记已读', onclick: async () => { await accountApi(`/api/v1/me/notifications/${item.id}/read`, { method: 'POST' }); await loadNotificationsPage(currentPage) } })] : [])
    ])
    return node('article', { class: `notification-row tone-${view.tone}${item.read_at ? '' : ' unread'}`, role: 'listitem' }, [
      node('span', { class: 'notification-icon', 'aria-hidden': 'true', text: view.icon }),
      node('div', { class: 'notification-content' }, [
        node('div', { class: 'notification-card-topline' }, [
          node('span', { class: `notification-kind tone-${view.tone}`, text: view.label }),
          ...(resourceTitle && !String(item.title || '').includes(resourceTitle) ? [node('span', { class: 'notification-resource-title', text: resourceTitle })] : []),
          ...(!item.read_at ? [node('span', { class: 'notification-unread', role: 'status', text: '未读' })] : [])
        ]),
        node('h2', { text: item.title }),
        node('p', { class: 'notification-message', text: item.message })
      ]),
      node('aside', { class: 'notification-row-aside' }, [
        ...(status ? [node('span', { class: `notification-resource-status status-${item.resource_status}`, role: 'status', text: status })] : []),
        node('time', { datetime: item.created_at, text: new Date(item.created_at).toLocaleDateString('zh-CN') }),
        actions
      ])
    ])
  }))
  if (!list.childElementCount) list.append(node('div', { class: 'account-empty notification-empty' }, [
    node('span', { class: 'notification-empty-icon', text: '◇' }),
    node('h2', { text: '收件箱很安静' }),
    node('p', { text: '投稿审核、缺失文件和正式入库结果会第一时间出现在这里。' }),
    node('a', { class: 'primary-button', href: '#/account/submit', text: '前往投稿' })
  ]))
  const pagination = node('nav', { class: 'notification-pagination', 'aria-label': '消息分页' }, [
    node('button', { class: 'notification-page-button', type: 'button', text: '上一页', disabled: currentPage <= 1 ? '' : null, onclick: async () => { await loadNotificationsPage(currentPage - 1) } }),
    node('span', { class: 'notification-page-status', role: 'status', 'aria-live': 'polite', text: `第 ${currentPage} / ${pageCount} 页` }),
    node('button', { class: 'notification-page-button', type: 'button', text: '下一页', disabled: currentPage >= pageCount ? '' : null, onclick: async () => { await loadNotificationsPage(currentPage + 1) } })
  ])
  const stream = node('section', { class: 'notification-stream', 'aria-labelledby': 'notification-stream-title' }, [
    node('header', { class: 'notification-stream-header' }, [
      node('div', {}, [
        node('h2', { id: 'notification-stream-title', text: '审核动态' }),
        node('p', { text: `共 ${totalCount} 条通知，每页 ${NOTIFICATIONS_PER_PAGE} 条` })
      ]),
      node('span', { class: 'notification-stream-count', text: `${state.notificationsUnread} 条未读` })
    ]),
    list,
    pagination
  ])
  const markAll = node('button', { class: 'notification-mark-all', type: 'button', text: '全部标记已读', disabled: state.notificationsUnread ? null : '', onclick: async () => { await accountApi('/api/v1/me/notifications/read', { method: 'POST' }); await loadNotificationsPage(currentPage) } })
  app.replaceChildren(node('div', { class: 'account-page notification-page' }, [
    accountNavigation('notifications'),
    node('header', { class: 'notification-page-heading' }, [
      node('div', {}, [
        node('span', { class: 'eyebrow', text: 'MESSAGE CENTER' }),
        node('h1', { text: '消息中心' }),
        node('p', { text: '审核进展、入库结果与需要补充的资料，都集中在这里。' })
      ]),
      node('div', { class: 'notification-overview' }, [
        node('span', { text: `未读 ${state.notificationsUnread}` }),
        markAll
      ])
    ]),
    stream
  ]))
}

async function loadAccountPage() {
  if (!state.account) {
    openAuthDialog('login')
    location.hash = '#/'
    return
  }
  setSeo({ title: '个人中心｜OOH Story', description: '管理私人阅读记录、收藏、书架与上传历史。', canonicalPath: '/', robots: 'noindex, nofollow' })
  if (!state.accountConfig) state.accountConfig = await api('/api/v1/auth/config')
  const [cloud, uploads, novelSubmissions, userInfo, notifications] = await Promise.all([
    refreshCloudState(),
    accountApi('/api/v1/me/uploads'),
    accountApi('/api/v1/me/novel-submissions'),
    accountApi('/api/v1/me/profile'),
    accountApi('/api/v1/me/notifications?limit=1')
  ])
  state.notificationsUnread = Number(notifications.unread_count || 0)
  state.accountProfile = userInfo.profile
  state.accountReading = userInfo.reading
  const loginMethods = userInfo.login_methods || {
    google: Boolean(state.account.google_linked),
    password: Boolean(state.account.password_login_enabled)
  }
  updateAccountButton()
  const contributionCount = Number((uploads.items || []).length) + Number((novelSubmissions.items || []).length)
  const logout = node('button', { class: 'ghost-button', type: 'button', text: '退出登录', onclick: async () => {
    state.ttsController?.stop?.()
    window.OOHStoryAudiobookCache?.cancel?.()
    await accountApi('/api/v1/auth/logout', { method: 'POST' })
    state.account = null
    state.accountProfile = null
    state.accountReading = null
    state.csrfToken = ''
    state.cloudState = { history: [], favorites: [], bookshelf: [] }
    updateAccountButton()
    location.hash = '#/'
  } })
  const resend = !state.account.email_verified && state.accountConfig.email_verification_delivery
    ? node('button', { class: 'ghost-button', type: 'button', text: '重发验证邮件', onclick: async event => {
        event.currentTarget.disabled = true
        try {
          const result = await accountApi('/api/v1/auth/resend-verification', { method: 'POST' })
          state.accountNotice = result.message
        } catch (error) {
          state.accountNotice = error.message
        }
        await loadAccountPage()
      } })
    : null
  const googleSlot = node('div', { class: 'google-login-slot' }, state.account.google_linked ? [] : [
    node('button', { class: 'ghost-button', type: 'button', text: '绑定 Google 账户', onclick: async event => {
      event.currentTarget.disabled = true
      try {
        await renderGoogleButton(googleSlot, {
          mode: 'link',
          onSuccess: async data => {
            state.account = data.user
            state.accountNotice = 'Google 账户绑定成功，今后可直接使用 Google 登录。'
            showAccountSuccessToast('Google 账户绑定成功')
            await loadAccountPage()
          }
        })
      } catch (error) {
        googleSlot.replaceChildren(node('div', { class: 'google-unavailable', text: error.message }))
      }
    } })
  ])
  const googleMessage = node('p', {
    class: 'google-link-message',
    text: loginMethods.google
      ? (loginMethods.password
          ? 'Google 与邮箱密码登录均已启用，你可以任选一种方式。'
          : '当前直接使用 Google 登录；无需创建密码，也可稍后在资料与安全中按需启用。')
      : '当前使用邮箱密码登录；如需绑定 Google，请选择与注册邮箱一致的 Google 账户。'
  })
  app.replaceChildren(node('div', { class: 'account-page' }, [
    accountNavigation('overview'),
    ...(state.accountNotice ? [node('p', { class: 'account-notice', text: state.accountNotice, role: 'status' })] : []),
    node('section', { class: 'account-hero' }, [
      accountAvatar(userInfo.profile),
      node('div', {}, [
        node('div', { class: 'account-name-row' }, [
          node('h1', { text: state.account.display_name }),
          readingRankIcon(userInfo.reading)
        ]),
        node('p', { text: `${state.account.email}${state.account.email_verified ? ' · 已验证' : ' · 待验证，上传功能暂不可用'}` }),
        node('span', { class: 'account-level-badge', text: `${userInfo.reading.roman} · ${userInfo.reading.name}` })
      ]),
      ...(resend ? [node('div', { class: 'account-hero-actions' }, [resend])] : [])
    ]),
    node('div', { class: 'account-grid' }, [
      node('a', { class: 'account-stat', href: '#/account/history' }, [node('strong', { text: String(cloud.history.length) }), node('span', { text: '阅读记录' })]),
      node('a', { class: 'account-stat', href: '#/account/favorites' }, [node('strong', { text: String(cloud.favorites.length) }), node('span', { text: '收藏作品' })]),
      node('a', { class: 'account-stat', href: '#/account/bookshelf' }, [node('strong', { text: String(cloud.bookshelf.length) }), node('span', { text: '私人书架' })]),
      node('a', { class: 'account-stat', href: '#/account/notifications' }, [node('strong', { text: String(state.notificationsUnread) }), node('span', { text: '未读消息' })])
    ]),
    readingIdentity(userInfo.reading, true),
    node('section', { class: 'account-section account-profile-shortcut' }, [
      node('div', {}, [node('h2', { text: '资料与账户安全' }), node('p', { text: loginMethods.password ? '上传头像、完善个人信息或修改密码。' : '上传头像、完善个人信息；邮箱密码登录可按需启用。' })]),
      node('a', { class: 'ghost-button', href: '#/account/profile', text: '打开设置 →' })
    ]),
    node('section', { class: 'account-section google-link-card' }, [
      node('div', { class: 'google-link-copy' }, [
        node('h2', { text: '登录方式' }),
        googleMessage
      ]),
      state.account.google_linked
        ? node('span', { class: 'google-link-status', text: loginMethods.password ? 'Google · 密码' : 'Google' })
        : googleSlot
    ]),
    node('section', { class: 'account-section account-contribution-shortcut' }, [
      node('div', {}, [node('h2', { text: '投稿与作品管理' }), node('p', { text: `已上传 ${contributionCount} 项内容。上传入口与本人作品档案已经分开管理。` })]),
      node('div', { class: 'profile-inline-actions' }, [
        node('a', { class: 'primary-button', href: '#/account/submit', text: '投稿' }),
        node('a', { class: 'ghost-button', href: '#/account/submissions', text: '我的投稿' })
      ])
    ]),
    node('div', { class: 'account-logout-zone' }, [logout])
  ]))
}

// ---------------------------------------------------------------------------
// Admin panel
// ---------------------------------------------------------------------------

function adminNavigation(active = 'dashboard') {
  const items = [
    ['dashboard', '#/admin', '概览'],
    ['users', '#/admin/users', '用户管理'],
    ['invites', '#/admin/invites', '邀请码'],
    ['categories', '#/admin/categories', '分类管理'],
    ['novels', '#/admin/novels', '内容管理']
  ]
  return node('nav', { class: 'admin-nav', 'aria-label': '管理后台导航' }, [
    node('a', { class: 'admin-nav-back', href: '#/account', text: '← 个人中心' }),
    ...items.map(([key, href, label]) => node('a', {
      class: key === active ? 'active' : '', href, text: label
    }))
  ])
}

function adminStatusBadge(status) {
  const labels = { active: '正常', disabled: '已禁用', expired: '已过期', completed: '已入库', rejected: '已驳回', ai_pending: '等待审核', reviewing: '审核中' }
  return node('span', { class: `admin-status ${status || ''}`, text: labels[status] || status || '未知' })
}

function adminTimeLabel(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

async function loadAdminPage(path) {
  if (!state.account) {
    openAuthDialog('login')
    location.hash = '#/'
    return
  }
  setSeo({ title: '管理后台｜OOH Story', description: '站点管理面板。', canonicalPath: '/', robots: 'noindex, nofollow' })
  const section = (path || '/admin').replace('/admin', '').replace(/^\//, '') || 'dashboard'
  try {
    if (section === 'dashboard') await renderAdminDashboard()
    else if (section === 'users') await renderAdminUsers()
    else if (section === 'invites') await renderAdminInvites()
    else if (section === 'categories') await renderAdminCategories()
    else if (section === 'novels') await renderAdminNovels()
    else await renderAdminDashboard()
  } catch (error) {
    if (error.message && /权限|登录/.test(error.message)) {
      location.hash = '#/account'
      return
    }
    app.replaceChildren(node('div', { class: 'admin-page' }, [
      adminNavigation(),
      node('p', { class: 'admin-feedback', text: error.message || '加载失败' })
    ]))
  }
}

async function renderAdminDashboard() {
  const data = await accountApi('/api/v1/admin/summary')
  const s = data.summary || {}
  const roleBadge = node('span', { class: `admin-role${data.role === 'owner' ? ' role-owner' : ''}`, text: data.role === 'owner' ? 'OWNER' : 'ADMIN' })
  app.replaceChildren(node('div', { class: 'admin-page' }, [
    adminNavigation('dashboard'),
    node('section', { class: 'admin-hero' }, [
      node('div', {}, [
        roleBadge,
        node('h1', { text: '管理后台' }),
        node('p', { text: '管理用户、邀请码、分类和内容投稿。' })
      ])
    ]),
    node('div', { class: 'admin-metric-grid' }, [
      node('a', { class: 'admin-metric', href: '#/admin/users' }, [
        node('span', { text: '注册用户' }),
        node('strong', { text: String(s.users || 0) }),
        node('small', { text: `${s.active_users || 0} 位活跃` })
      ]),
      node('a', { class: 'admin-metric', href: '#/admin/invites' }, [
        node('span', { text: '邀请码' }),
        node('strong', { text: String(s.invites || 0) }),
        node('small', { text: `${s.active_invites || 0} 个有效` })
      ]),
      node('a', { class: 'admin-metric', href: '#/admin/categories' }, [
        node('span', { text: '分类' }),
        node('strong', { text: String(data.categories || 0) }),
        node('small', { text: '全部分类' })
      ]),
      node('a', { class: 'admin-metric', href: '#/admin/novels' }, [
        node('span', { text: '内容投稿' }),
        node('strong', { text: String(s.novel_uploads || 0) }),
        node('small', { text: `${s.published_uploads || 0} 已发布 · ${s.pending_uploads || 0} 待审` })
      ])
    ]),
    node('section', { class: 'admin-guide' }, [
      node('div', {}, [
        node('span', { class: 'admin-role', text: 'QUICK START' }),
        node('h2', { text: '上架流程' })
      ]),
      node('ol', {}, [
        node('li', {}, [node('strong', { text: '创建分类' }), node('span', { text: '在分类管理中添加书库分类' })]),
        node('li', {}, [node('strong', { text: '上传小说' }), node('span', { text: '在内容管理中上传正文与封面' })]),
        node('li', {}, [node('strong', { text: '等待入库' }), node('span', { text: '系统审核通过后自动发布' })])
      ])
    ])
  ]))
}

async function renderAdminUsers() {
  const searchInput = node('input', { type: 'search', placeholder: '搜索邮箱或昵称…' })
  const listContainer = node('div', { class: 'admin-user-list' })
  const paginationContainer = node('div', { style: 'display:flex;gap:8px;justify-content:center;margin-top:16px' })
  let currentPage = 1
  let currentQuery = ''

  async function loadUsers(page, query) {
    currentPage = page
    currentQuery = query
    const params = new URLSearchParams({ page: String(page), page_size: '20' })
    if (query) params.set('q', query)
    const data = await accountApi(`/api/v1/admin/users?${params}`)
    listContainer.replaceChildren()
    if (!(data.items || []).length) {
      listContainer.append(node('p', { class: 'admin-empty', text: '没有找到匹配的用户。' }))
    }
    for (const user of data.items || []) {
      const initials = String(user.display_name || user.email || '?').trim()[0] || '?'
      const avatarClass = `admin-user-avatar${user.role === 'owner' ? ' role-owner' : user.role === 'admin' ? ' role-admin' : ''}`
      const statusSelect = node('select', { value: user.status }, [
        node('option', { value: 'active', text: '正常', ...(user.status === 'active' ? { selected: '' } : {}) }),
        node('option', { value: 'disabled', text: '禁用', ...(user.status === 'disabled' ? { selected: '' } : {}) })
      ])
      statusSelect.addEventListener('change', async () => {
        try {
          await accountApi(`/api/v1/admin/users/${user.id}`, { method: 'PATCH', body: { status: statusSelect.value } })
          showAccountSuccessToast('用户状态已更新')
        } catch (err) {
          statusSelect.value = user.status
          showAccountSuccessToast(err.message)
        }
      })
      const sessions = user.active_sessions ? `${user.active_sessions} 个会话` : ''
      const lastLogin = user.last_login_at ? `最近登录 ${adminTimeLabel(user.last_login_at)}` : '从未登录'
      listContainer.append(node('div', { class: 'admin-user-card' }, [
        node('div', { class: 'admin-user-identity' }, [
          node('div', { class: avatarClass, text: initials }),
          node('div', {}, [
            node('strong', { text: user.display_name || '未设置昵称' }),
            node('span', { text: user.email }),
            node('small', { text: [user.role, user.email_verified ? '已验证' : '未验证', lastLogin, sessions].filter(Boolean).join(' · ') })
          ])
        ]),
        node('div', { class: 'admin-user-actions' }, [
          adminStatusBadge(user.status),
          user.role !== 'owner' ? statusSelect : null
        ].filter(Boolean))
      ]))
    }
    // Pagination
    paginationContainer.replaceChildren()
    if ((data.pages || 1) > 1) {
      if (currentPage > 1) paginationContainer.append(node('button', { class: 'ghost-button', type: 'button', text: '← 上一页', onclick: () => loadUsers(currentPage - 1, currentQuery) }))
      paginationContainer.append(node('span', { text: `${currentPage} / ${data.pages}`, style: 'align-self:center;font-size:12px;color:var(--muted)' }))
      if (currentPage < data.pages) paginationContainer.append(node('button', { class: 'ghost-button', type: 'button', text: '下一页 →', onclick: () => loadUsers(currentPage + 1, currentQuery) }))
    }
  }

  let searchTimer = null
  searchInput.addEventListener('input', () => {
    clearTimeout(searchTimer)
    searchTimer = setTimeout(() => loadUsers(1, searchInput.value.trim()), 350)
  })

  app.replaceChildren(node('div', { class: 'admin-page' }, [
    adminNavigation('users'),
    node('section', { class: 'admin-panel' }, [
      node('h2', { text: '用户管理' }),
      node('div', { class: 'admin-search' }, [searchInput]),
      listContainer,
      paginationContainer
    ])
  ]))
  await loadUsers(1, '')
}

async function renderAdminInvites() {
  const feedback = node('p', { class: 'admin-feedback' })
  const codeReveal = node('div', { class: 'admin-code-reveal', hidden: '' })
  const listContainer = node('div', { class: 'admin-record-list' })

  async function refreshInvites() {
    const data = await accountApi('/api/v1/admin/invites')
    listContainer.replaceChildren()
    if (!(data.items || []).length) {
      listContainer.append(node('p', { class: 'admin-empty', text: '还没有创建过邀请码。' }))
      return
    }
    for (const inv of data.items || []) {
      const isActive = !inv.disabled_at && (!inv.expires_at || new Date(inv.expires_at) > new Date()) && inv.used_count < inv.max_uses
      const statusText = inv.disabled_at ? 'disabled' : (!inv.expires_at || new Date(inv.expires_at) > new Date()) ? (inv.used_count >= inv.max_uses ? 'expired' : 'active') : 'expired'
      const revokeBtn = node('button', { class: 'admin-danger-button', type: 'button', text: '作废', disabled: !isActive ? '' : null })
      revokeBtn.addEventListener('click', async () => {
        revokeBtn.disabled = true
        try {
          await accountApi(`/api/v1/admin/invites/${inv.id}`, { method: 'DELETE' })
          showAccountSuccessToast('邀请码已作废')
          await refreshInvites()
        } catch (err) {
          feedback.textContent = err.message
          revokeBtn.disabled = false
        }
      })
      listContainer.append(node('div', { class: 'admin-record' }, [
        node('div', {}, [
          node('strong', { text: inv.label || inv.code_prefix || '未命名' }),
          node('small', { text: `已用 ${inv.used_count}/${inv.max_uses} · 创建于 ${adminTimeLabel(inv.created_at)}${inv.expires_at ? ` · 过期 ${adminTimeLabel(inv.expires_at)}` : ''}` })
        ]),
        adminStatusBadge(statusText),
        revokeBtn
      ]))
    }
  }

  const labelInput = node('input', { type: 'text', placeholder: '用途备注（可选）' })
  const maxUsesInput = node('input', { type: 'number', value: '10', min: '1', max: '100000' })
  const expiresInput = node('input', { type: 'number', value: '30', min: '1', max: '365' })
  const createBtn = node('button', { class: 'primary-button', type: 'button', text: '生成' })
  createBtn.addEventListener('click', async () => {
    createBtn.disabled = true
    feedback.textContent = ''
    codeReveal.hidden = true
    try {
      const result = await accountApi('/api/v1/admin/invites', {
        method: 'POST',
        body: {
          label: labelInput.value.trim(),
          max_uses: parseInt(maxUsesInput.value, 10) || 10,
          expires_in_days: parseInt(expiresInput.value, 10) || 30
        }
      })
      codeReveal.replaceChildren(
        node('span', { text: '邀请码（仅显示一次）' }),
        node('code', { text: result.code })
      )
      codeReveal.hidden = false
      labelInput.value = ''
      await refreshInvites()
    } catch (err) {
      feedback.textContent = err.message
    } finally {
      createBtn.disabled = false
    }
  })

  app.replaceChildren(node('div', { class: 'admin-page' }, [
    adminNavigation('invites'),
    node('section', { class: 'admin-panel' }, [
      node('h2', { text: '邀请码管理' }),
      node('div', { class: 'admin-form' }, [
        node('label', {}, [document.createTextNode('用途备注'), labelInput]),
        node('label', {}, [document.createTextNode('可用次数'), maxUsesInput]),
        node('label', {}, [document.createTextNode('有效天数'), expiresInput]),
        createBtn
      ]),
      feedback,
      codeReveal,
      node('h2', { text: '已有邀请码', style: 'margin-top:24px' }),
      listContainer
    ])
  ]))
  await refreshInvites()
}

async function renderAdminCategories() {
  const feedback = node('p', { class: 'admin-feedback' })
  const listContainer = node('div', { class: 'admin-category-list' })

  async function refreshCategories() {
    const data = await accountApi('/api/v1/admin/categories')
    listContainer.replaceChildren()
    if (!(data.items || []).length) {
      listContainer.append(node('p', { class: 'admin-empty', text: '还没有创建分类。' }))
      return
    }
    for (const cat of data.items || []) {
      const nameInput = node('input', { type: 'text', value: cat.display_name || cat.source_name })
      const enabledCheckbox = node('input', { type: 'checkbox', ...(cat.enabled ? { checked: '' } : {}) })
      const sortInput = node('input', { type: 'number', value: String(cat.sort_order || 100), min: '0', max: '10000', style: 'width:70px' })
      const saveBtn = node('button', { class: 'ghost-button', type: 'button', text: '保存' })
      const deleteBtn = node('button', { class: 'admin-danger-button', type: 'button', text: '删除' })

      saveBtn.addEventListener('click', async () => {
        saveBtn.disabled = true
        try {
          await accountApi(`/api/v1/admin/categories/${cat.id}`, {
            method: 'PUT',
            body: {
              display_name: nameInput.value.trim(),
              description: cat.description || '',
              enabled: enabledCheckbox.checked,
              sort_order: parseInt(sortInput.value, 10) || 100
            }
          })
          showAccountSuccessToast('分类已保存')
          await refreshCategories()
        } catch (err) {
          feedback.textContent = err.message
        } finally {
          saveBtn.disabled = false
        }
      })

      deleteBtn.addEventListener('click', async () => {
        if (!confirm(`确定删除分类「${cat.display_name || cat.source_name}」？`)) return
        deleteBtn.disabled = true
        try {
          await accountApi(`/api/v1/admin/categories/${cat.id}`, { method: 'DELETE' })
          showAccountSuccessToast('分类已删除')
          await refreshCategories()
        } catch (err) {
          feedback.textContent = err.message
          deleteBtn.disabled = false
        }
      })

      listContainer.append(node('div', { class: `admin-category-card${cat.enabled ? '' : ' disabled'}` }, [
        node('div', { class: 'admin-category-title' }, [
          node('strong', { text: cat.display_name || cat.source_name }),
          node('span', { text: `${cat.book_count || 0} 本书` })
        ]),
        cat.description ? node('p', { style: 'margin:0;color:var(--muted);font-size:11px' , text: cat.description }) : null,
        node('div', { class: 'admin-category-actions' }, [
          nameInput,
          sortInput,
          node('label', {}, [enabledCheckbox, document.createTextNode(' 启用')]),
          saveBtn
        ]),
        node('div', { style: 'display:flex;justify-content:space-between;align-items:center' }, [
          node('small', { text: `源名称: ${cat.source_name}` }),
          deleteBtn
        ])
      ].filter(Boolean)))
    }
  }

  const nameInput = node('input', { type: 'text', placeholder: '分类名称' })
  const descInput = node('input', { type: 'text', placeholder: '分类描述（可选）' })
  const sortInput = node('input', { type: 'number', value: '100', min: '0', max: '10000' })
  const createBtn = node('button', { class: 'primary-button', type: 'button', text: '创建' })
  createBtn.addEventListener('click', async () => {
    const name = nameInput.value.trim()
    if (!name) { feedback.textContent = '请填写分类名称'; return }
    createBtn.disabled = true
    feedback.textContent = ''
    try {
      await accountApi('/api/v1/admin/categories', {
        method: 'POST',
        body: { name, description: descInput.value.trim(), sort_order: parseInt(sortInput.value, 10) || 100 }
      })
      nameInput.value = ''
      descInput.value = ''
      showAccountSuccessToast('分类已创建')
      await refreshCategories()
    } catch (err) {
      feedback.textContent = err.message
    } finally {
      createBtn.disabled = false
    }
  })

  app.replaceChildren(node('div', { class: 'admin-page' }, [
    adminNavigation('categories'),
    node('section', { class: 'admin-panel' }, [
      node('h2', { text: '分类管理' }),
      node('div', { class: 'admin-form admin-category-create' }, [
        node('label', {}, [document.createTextNode('名称'), nameInput]),
        node('label', {}, [document.createTextNode('描述'), descInput]),
        node('label', {}, [document.createTextNode('排序'), sortInput]),
        createBtn
      ]),
      feedback,
      node('h2', { text: '现有分类', style: 'margin-top:24px' }),
      listContainer
    ])
  ]))
  await refreshCategories()
}

async function renderAdminNovels() {
  const data = await accountApi('/api/v1/admin/novels')
  const items = data.items || []
  const listContainer = node('div', { class: 'admin-record-list' })
  if (!items.length) {
    listContainer.append(node('p', { class: 'admin-empty', text: '还没有内容投稿。' }))
  }
  for (const item of items) {
    listContainer.append(node('div', { class: 'admin-record' }, [
      node('div', {}, [
        node('strong', { text: `${item.title || '未命名'} — ${item.author || '未知作者'}` }),
        node('small', { text: `${item.category || '未分类'} · ${formatBytes(item.bytes || 0)} · ${adminTimeLabel(item.created_at)}` })
      ]),
      adminStatusBadge(item.status)
    ]))
  }
  app.replaceChildren(node('div', { class: 'admin-page' }, [
    adminNavigation('novels'),
    node('section', { class: 'admin-panel' }, [
      node('h2', { text: '内容管理' }),
      listContainer
    ])
  ]))
}

async function bootstrapAccount() {
  const query = new URLSearchParams(location.search)
  const googleError = query.get('google_error')
  const googleLinked = query.get('google_linked')
  const verification = query.get('verify')
  if (verification) {
    try {
      await accountApi('/api/v1/auth/verify-email', {
        method: 'POST',
        body: { token: verification }
      })
      state.accountNotice = '邮箱验证成功，你现在可以安全上传作品。'
    } catch (error) {
      state.accountNotice = error.message
    }
    history.replaceState(null, '', '/account#/account')
  }
  await loadAccountSession()
  if (googleLinked) {
    state.accountNotice = 'Google 账户绑定成功，今后可以直接使用 Google 登录。'
    history.replaceState(null, '', '/#/account')
  }
  if (googleError) {
    history.replaceState(null, '', '/#/')
    openAuthDialog('login', googleError)
  }
}

const bootstrapQuery = new URLSearchParams(location.search)
const hasAccountCallback = ['verify', 'google_linked', 'google_error']
  .some(key => bootstrapQuery.has(key))
if (pathFromLocation() === '/' && !hasAccountCallback) {
  // The public home snapshot and anonymous session check are independent.
  // Start both immediately so authentication latency never gates first paint.
  const accountBootstrapPromise = bootstrapAccount()
  route()
  accountBootstrapPromise.then(refreshHomeContinueReading).catch(() => {})
} else {
  bootstrapAccount().finally(route)
}
