const app = document.querySelector('#app')
const themeToggle = document.querySelector('#theme-toggle')
const accountButton = document.querySelector('#account-button')
const globalTtsReturn = document.querySelector('#global-tts-return')
const ttsPlayer = document.querySelector('#tts-player')
const ttsPlayerClose = document.querySelector('#tts-player-close')
const ttsPlayerReturn = document.querySelector('#tts-player-return')
const ttsPlayerHeading = document.querySelector('#tts-player-heading')
const ttsPlayerBook = document.querySelector('#tts-player-book')
const ttsPlayerChapterIndex = document.querySelector('#tts-player-chapter-index')
const ttsPlayerChapter = document.querySelector('#tts-player-chapter')
const ttsPlayerLine = document.querySelector('#tts-player-line')
const ttsPlayerTranscript = document.querySelector('#tts-player-transcript')
const ttsPlayerCover = document.querySelector('#tts-player-cover')
const ttsPlayerCoverFallback = document.querySelector('#tts-player-cover-fallback')
const ttsPlayerProgressFill = document.querySelector('#tts-player-progress-fill')
const ttsPlayerProgressCopy = document.querySelector('#tts-player-progress-copy')
const ttsPlayerModeCopy = document.querySelector('#tts-player-mode-copy')
const ttsPlayerPrevious = document.querySelector('#tts-player-previous')
const ttsPlayerToggle = document.querySelector('#tts-player-toggle')
const ttsPlayerNext = document.querySelector('#tts-player-next')
const ttsPlayerRate = document.querySelector('#tts-player-rate')
const ttsPlayerMode = document.querySelector('#tts-player-mode')
const ttsPlayerEmotion = document.querySelector('#tts-player-emotion')
const ttsPlayerText = document.querySelector('#tts-player-text')
const ttsPlayerStop = document.querySelector('#tts-player-stop')
const ttsEmotionSheet = document.querySelector('#tts-emotion-sheet')
const ttsEmotionClose = document.querySelector('#tts-emotion-close')
const ttsEmotionOptions = document.querySelector('#tts-emotion-options')
const READING_PROGRESS_STORAGE_KEY = 'oohstory-reading-progress'
const READING_PROGRESS_SCHEMA = 'oohstory-reading-progress'
const READING_PROGRESS_VERSION = 1
const READING_PROGRESS_BOOK_LIMIT = 100
const READING_PROGRESS_SAVE_DELAY = 750
const PUBLIC_METRIC_VISITOR_KEY = 'oohstory-public-metric-visitor-id'
const SITE_ORIGIN = window.location.origin
const SITE_NAME = 'OOH Story'
const SITE_DEFAULT_IMAGE = '/icon-512.png'
const SITE_DESCRIPTION = 'OOH Story 是免费、开源、自托管的中文小说阅读站，提供全本小说在线阅读、书库检索与深度拆书档案。'
const SITE_KEYWORDS = 'OOH Story, 免费小说阅读, 免费小说下载, 中文小说, 全本小说, TXT电子书, 深度拆书'
const HERO_SUMMARY_MAX_CHARS = 180

function cleanSeoText(value, limit = 180) {
  const text = String(value || '').replace(/[\u0000-\u001f\u007f]+/g, ' ').replace(/\s+/g, ' ').trim()
  return text.length > limit ? `${text.slice(0, Math.max(1, limit - 1)).trim()}…` : text
}

function compactHeroSummary(value) {
  const fallback = '打开作品详情，立即开始阅读。'
  const text = String(value || fallback)
    .replace(/[\u0000-\u001f\u007f]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim() || fallback
  if (text.length <= HERO_SUMMARY_MAX_CHARS) return text
  return `${text.slice(0, HERO_SUMMARY_MAX_CHARS - 2).trimEnd()}……`
}

function publicUrl(value, fallback = '/') {
  try {
    const url = new URL(String(value || fallback), SITE_ORIGIN)
    if (url.origin !== SITE_ORIGIN) return new URL(fallback, SITE_ORIGIN).href
    url.hash = ''
    return url.href
  } catch {
    return new URL(fallback, SITE_ORIGIN).href
  }
}

function setMetaContent(selector, value) {
  const element = document.querySelector(selector)
  if (element) element.setAttribute('content', String(value))
}

function setSeo({
  title,
  description,
  keywords = SITE_KEYWORDS,
  canonicalPath,
  type = 'website',
  image = SITE_DEFAULT_IMAGE,
  imageAlt = 'OOH Story 品牌图标',
  author = SITE_NAME,
  robots = 'index, follow, max-image-preview:large, max-snippet:-1',
  entity = null
}) {
  const safeTitle = cleanSeoText(title, 100) || SITE_NAME
  const safeDescription = cleanSeoText(description, 180) || SITE_DESCRIPTION
  const canonical = publicUrl(canonicalPath)
  const imageUrl = publicUrl(image, SITE_DEFAULT_IMAGE)
  const safeImageAlt = cleanSeoText(imageAlt, 120) || 'OOH Story 品牌图标'
  document.title = safeTitle
  setMetaContent('meta[name="description"]', safeDescription)
  setMetaContent('meta[name="keywords"]', cleanSeoText(keywords, 500) || SITE_KEYWORDS)
  setMetaContent('meta[name="author"]', cleanSeoText(author, 100) || SITE_NAME)
  setMetaContent('meta[name="robots"]', robots)
  setMetaContent('meta[property="og:type"]', type)
  setMetaContent('meta[property="og:title"]', safeTitle)
  setMetaContent('meta[property="og:description"]', safeDescription)
  setMetaContent('meta[property="og:url"]', canonical)
  setMetaContent('meta[property="og:image"]', imageUrl)
  setMetaContent('meta[property="og:image:alt"]', safeImageAlt)
  setMetaContent('meta[name="twitter:title"]', safeTitle)
  setMetaContent('meta[name="twitter:description"]', safeDescription)
  setMetaContent('meta[name="twitter:image"]', imageUrl)
  setMetaContent('meta[name="twitter:image:alt"]', safeImageAlt)
  const canonicalLink = document.querySelector('link[rel="canonical"]')
  if (canonicalLink) canonicalLink.setAttribute('href', canonical)

  const graph = [
    {
      '@type': 'Organization',
      '@id': `${SITE_ORIGIN}/#organization`,
      url: `${SITE_ORIGIN}/`,
      name: SITE_NAME,
      logo: {
        '@type': 'ImageObject',
        url: publicUrl(SITE_DEFAULT_IMAGE),
        width: 512,
        height: 512
      }
    },
    {
      '@type': 'WebSite',
      '@id': `${SITE_ORIGIN}/#website`,
      url: `${SITE_ORIGIN}/`,
      name: SITE_NAME,
      alternateName: 'OOH STORY',
      description: SITE_DESCRIPTION,
      inLanguage: 'zh-CN',
      image: publicUrl(SITE_DEFAULT_IMAGE),
      publisher: { '@id': `${SITE_ORIGIN}/#organization` }
    },
    {
      '@type': 'WebPage',
      '@id': `${canonical}#webpage`,
      url: canonical,
      name: safeTitle,
      description: safeDescription,
      isPartOf: { '@id': `${SITE_ORIGIN}/#website` },
      inLanguage: 'zh-CN'
    }
  ]
  if (entity && typeof entity === 'object') graph.push(entity)
  const structuredData = document.querySelector('#structured-data')
  if (structuredData) structuredData.textContent = JSON.stringify({ '@context': 'https://schema.org', '@graph': graph })
}

function bookSeoTitle(book) {
  const title = cleanSeoText(book?.title, 70) || '未命名作品'
  const author = cleanSeoText(book?.author, 30) || '佚名'
  return cleanSeoText(`${title}全文在线阅读_免费TXT下载_${author}｜${SITE_NAME}`, 100)
}

function bookSeoKeywords(book) {
  const title = cleanSeoText(book?.title, 70) || '未命名作品'
  const author = cleanSeoText(book?.author, 30) || '佚名'
  const category = cleanSeoText(book?.category, 30) || '中文小说'
  return [...new Set([
    title,
    `${title}在线阅读`,
    `${title}全文阅读`,
    `${title}TXT下载`,
    author,
    `${author}小说`,
    category,
    '免费小说阅读',
    '免费小说下载',
    'TXT电子书',
    SITE_NAME
  ])].join(', ')
}

function bookSeoEntity(book, canonical, description, imageUrl) {
  const genres = [book.category, ...(book.genre_tags || [])]
    .map(value => cleanSeoText(value, 40))
    .filter(Boolean)
  return {
    '@type': 'Book',
    '@id': `${canonical}#book`,
    url: canonical,
    name: cleanSeoText(book.title, 160),
    author: { '@type': 'Person', name: cleanSeoText(book.author, 100) || '佚名' },
    description,
    genre: [...new Set(genres)],
    image: imageUrl,
    inLanguage: 'zh-CN'
  }
}

function localStorageGet(key) {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function localStorageSet(key, value) {
  try {
    window.localStorage.setItem(key, value)
    return true
  } catch {
    return false
  }
}

function localStorageRemove(key) {
  try {
    window.localStorage.removeItem(key)
  } catch {
    // Private browsing and locked-down browsers may deny storage entirely.
  }
}

;[['ohhstory-reader', 'oohstory-reader'], ['ohhstory-reading-progress', 'oohstory-reading-progress'], ['ohhstory-theme', 'oohstory-theme']].forEach(([oldKey, newKey]) => {
  const old = localStorageGet(oldKey)
  if (old !== null && localStorageGet(newKey) === null) {
    localStorageSet(newKey, old)
    localStorageRemove(oldKey)
  }
})

let savedReaderSettings = {}
try {
  savedReaderSettings = JSON.parse(localStorageGet('oohstory-reader') || '{}')
} catch {
  localStorageRemove('oohstory-reader')
}
savedReaderSettings.autoReading = false
savedReaderSettings.ttsActive = false
const state = {
  home: null,
  homeSecondary: null,
  homeSecondaryPromise: null,
  categories: [],
  reader: savedReaderSettings,
  readerNavigation: null,
  readerAutoContinue: false,
  ttsContinueOnLoad: false,
  readerCatalogs: new Map(),
  readerChapters: new Map(),
  readerInflight: new Map(),
  account: null,
  accountNotice: '',
  accountConfig: null,
  accountProfile: null,
  accountReading: null,
  csrfToken: '',
  cloudState: { history: [], favorites: [], bookshelf: [] },
  notificationsUnread: 0,
  cloudSyncTimer: null,
  readingActivity: null,
  ttsController: null,
  ttsSession: null
}
// Keep one media element for the lifetime of the page. Safari/iOS grants
// autoplay permission to the element that received the user's initial tap;
// replacing it while routing to the next chapter loses that permission.
let ttsAudioEl = null
const TTS_CHECKPOINT_STORAGE_KEY = 'oohstory-tts-checkpoint'

function readTtsCheckpoint(bookId, chapterId) {
  try {
    const checkpoint = JSON.parse(localStorageGet(TTS_CHECKPOINT_STORAGE_KEY) || 'null')
    if (!checkpoint || String(checkpoint.bookId) !== String(bookId) || String(checkpoint.chapterId) !== String(chapterId)) return null
    if (Date.now() - Number(checkpoint.updatedAt || 0) > 7 * 24 * 60 * 60 * 1000) return null
    return checkpoint
  } catch {
    return null
  }
}

function saveTtsCheckpoint(session) {
  if (!session?.bookId || !session?.chapterId) return
  localStorageSet(TTS_CHECKPOINT_STORAGE_KEY, JSON.stringify({
    bookId: String(session.bookId),
    chapterId: String(session.chapterId),
    paragraphIndex: Math.max(0, Number(session.paragraphIndex) || 0),
    itemIndex: Math.max(0, Number(session.itemIndex) || 0),
    returnPath: String(session.returnPath || ''),
    updatedAt: Date.now()
  }))
}
const READER_CHAPTER_CACHE_LIMIT = 8

const ttsPlayerModeLabels = {
  normal: ['普通', '普通演绎'],
  smart: ['智能', '多角色智能演绎'],
  cantonese: ['粤语', '粤语演绎'],
  hokkien: ['闽南语', '闽南语演绎']
}
const ttsEmotionModes = {
  auto: { label: '自动', desc: '逐段识别情绪并动态演绎' },
  neutral: { label: '平稳', desc: '自然克制的标准叙述' },
  gentle: { label: '温柔', desc: '柔和亲近、低压舒缓' },
  joyful: { label: '喜悦', desc: '明快轻盈、笑意充沛' },
  excited: { label: '激昂', desc: '高能振奋、节奏强烈' },
  angry: { label: '愤怒', desc: '强硬紧迫、压迫感突出' },
  sad: { label: '悲伤', desc: '低沉迟缓、情绪下坠' },
  fearful: { label: '惊惧', desc: '呼吸急促、音线绷紧' },
  tense: { label: '紧张', desc: '节奏收紧、悬念增强' },
  mysterious: { label: '神秘', desc: '低声慢述、幽微莫测' },
  solemn: { label: '庄重', desc: '沉稳肃穆、字句清晰' },
  affectionate: { label: '深情', desc: '细腻温暖、情感绵长' },
  humorous: { label: '诙谐', desc: '灵动俏皮、节拍跳跃' },
  weary: { label: '疲惫', desc: '缓慢虚弱、气息低落' }
}

function closeTtsEmotionSheet() {
  if (!ttsEmotionSheet) return
  ttsEmotionSheet.hidden = true
  ttsEmotionSheet.setAttribute('aria-hidden', 'true')
}

function renderTtsEmotionOptions() {
  if (!ttsEmotionOptions || ttsEmotionOptions.childElementCount) return
  Object.entries(ttsEmotionModes).forEach(([key, item]) => {
    const button = document.createElement('button')
    button.type = 'button'
    button.dataset.emotion = key
    const label = document.createElement('strong')
    label.textContent = item.label
    const description = document.createElement('span')
    description.textContent = item.desc
    button.append(label, description)
    button.addEventListener('click', () => {
      state.ttsController?.setEmotion?.(key)
      closeTtsEmotionSheet()
    })
    ttsEmotionOptions.append(button)
  })
}

function openTtsEmotionSheet() {
  if (!ttsEmotionSheet || !state.ttsSession?.active) return
  renderTtsEmotionOptions()
  ttsEmotionSheet.hidden = false
  ttsEmotionSheet.setAttribute('aria-hidden', 'false')
  updateTtsPlayer()
  ttsEmotionSheet.querySelector(`[data-emotion="${state.reader.ttsEmotion}"]`)?.focus({ preventScroll: true })
}

function ttsPlayerIsOpen() {
  return Boolean(ttsPlayer && !ttsPlayer.hidden)
}

function closeTtsPlayer() {
  if (!ttsPlayer) return
  closeTtsEmotionSheet()
  ttsPlayer.hidden = true
  ttsPlayer.setAttribute('aria-hidden', 'true')
  document.body.classList.remove('tts-player-open')
  if (state.ttsSession) state.ttsSession.playerOpen = false
  updateGlobalTtsReturn()
}

function updateTtsPlayer() {
  if (!ttsPlayer) return
  const session = state.ttsSession
  if (!session?.active) {
    closeTtsPlayer()
    return
  }
  const itemIndex = Math.max(0, Number(session.itemIndex) || 0)
  const itemCount = Math.max(1, Number(session.itemCount) || 1)
  const progress = Math.min(100, ((itemIndex + 1) / itemCount) * 100)
  const mode = ttsPlayerModeLabels[state.reader.ttsMode] || ttsPlayerModeLabels.normal
  const selectedEmotion = ttsEmotionModes[state.reader.ttsEmotion] || ttsEmotionModes.auto
  const activeEmotion = ttsEmotionModes[session.currentEmotion] || ttsEmotionModes.neutral
  const isPlaying = ttsSessionIsPlaying()
  const isBlocked = Boolean(session.playbackBlocked)
  ttsPlayer.classList.toggle('is-playing', isPlaying)
  ttsPlayer.classList.toggle('is-paused', !isPlaying)
  ttsPlayer.classList.toggle('is-blocked', isBlocked)
  if (ttsPlayerHeading) ttsPlayerHeading.textContent = isBlocked ? '等待继续播放' : isPlaying ? '正在播放' : '已暂停'
  if (ttsPlayerBook) ttsPlayerBook.textContent = session.bookTitle || 'OOH Story'
  if (ttsPlayerChapterIndex) {
    const chapterNumber = Math.max(1, Number(session.chapterNumber) || 1)
    const chapterCount = Math.max(chapterNumber, Number(session.chapterCount) || chapterNumber)
    ttsPlayerChapterIndex.textContent = `第 ${chapterNumber} / ${chapterCount} 章`
  }
  if (ttsPlayerChapter) ttsPlayerChapter.textContent = session.chapterTitle || '正在准备本章音频'
  const contextItems = Array.isArray(session.contextItems) && session.contextItems.length
    ? session.contextItems
    : [session.currentText || '轻触播放，故事即刻开始。']
  const contextIndex = Math.min(contextItems.length - 1, Math.max(0, itemIndex))
  const contextKey = `${session.chapterId}:${contextIndex}:${contextItems.length}`
  if (ttsPlayerTranscript && ttsPlayerTranscript.dataset.contextKey !== contextKey) {
    const start = Math.max(0, contextIndex - 2)
    const end = Math.min(contextItems.length, contextIndex + 3)
    const fragment = document.createDocumentFragment()
    for (let index = start; index < end; index++) {
      const line = document.createElement('p')
      line.textContent = String(contextItems[index] || '')
      line.className = index < contextIndex ? 'is-past' : index === contextIndex ? 'is-current' : 'is-future'
      line.dataset.itemIndex = String(index)
      fragment.append(line)
    }
    ttsPlayerTranscript.replaceChildren(fragment)
    ttsPlayerTranscript.dataset.contextKey = contextKey
    if (ttsPlayerIsOpen()) window.requestAnimationFrame(() => {
      ttsPlayerTranscript.querySelector('.is-current')?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    })
  } else if (ttsPlayerLine) {
    ttsPlayerLine.textContent = session.currentText || '轻触播放，故事即刻开始。'
  }
  if (ttsPlayerProgressFill) ttsPlayerProgressFill.style.width = `${progress}%`
  if (ttsPlayerProgressCopy) ttsPlayerProgressCopy.textContent = `第 ${Math.min(itemCount, itemIndex + 1)} / ${itemCount} 段`
  if (ttsPlayerModeCopy) ttsPlayerModeCopy.textContent = `${mode[1]} · ${activeEmotion.label}`
  if (ttsPlayerRate) ttsPlayerRate.querySelector('strong').textContent = `${Number(state.reader.ttsRate || 1).toFixed(1)}×`
  if (ttsPlayerMode) ttsPlayerMode.querySelector('strong').textContent = mode[0]
  if (ttsPlayerEmotion) ttsPlayerEmotion.querySelector('strong').textContent = selectedEmotion.label
  ttsEmotionOptions?.querySelectorAll('button').forEach(button => {
    const active = button.dataset.emotion === state.reader.ttsEmotion
    button.classList.toggle('active', active)
    button.setAttribute('aria-pressed', String(active))
  })
  if (ttsPlayerToggle) ttsPlayerToggle.setAttribute('aria-label', isPlaying ? '暂停听书' : '继续听书')
  if (ttsPlayerPrevious) ttsPlayerPrevious.disabled = itemIndex <= 0
  if (ttsPlayerNext) ttsPlayerNext.disabled = itemIndex >= itemCount - 1
  if (ttsPlayerCover && ttsPlayerCoverFallback) {
    const coverUrl = String(session.coverUrl || '')
    if (coverUrl && ttsPlayerCover.dataset.source !== coverUrl) {
      ttsPlayerCover.dataset.source = coverUrl
      ttsPlayerCover.hidden = false
      ttsPlayerCoverFallback.hidden = true
      ttsPlayerCover.src = freshCoverUrl(coverUrl)
      ttsPlayerCover.onerror = () => {
        ttsPlayerCover.hidden = true
        ttsPlayerCoverFallback.hidden = false
      }
    } else if (!coverUrl) {
      ttsPlayerCover.hidden = true
      ttsPlayerCoverFallback.hidden = false
    }
  }
}

function openTtsPlayer() {
  if (!ttsPlayer || !state.ttsSession?.active) return
  ttsPlayer.hidden = false
  ttsPlayer.setAttribute('aria-hidden', 'false')
  document.body.classList.add('tts-player-open')
  state.ttsSession.playerOpen = true
  updateTtsPlayer()
  updateGlobalTtsReturn()
  window.setTimeout(() => ttsPlayerClose?.focus({ preventScroll: true }), 0)
}

function returnTtsToReader() {
  const session = state.ttsSession
  if (!session?.active) return
  closeTtsPlayer()
  session.returning = true
  navigateInApp(session.returnPath || `/books/${session.bookId}/chapters/${session.chapterId}`)
}

function ttsSessionIsPlaying(bookId = '') {
  const session = state.ttsSession
  return Boolean(session?.active
    && (!bookId || String(session.bookId) === String(bookId))
    && ttsAudioEl
    && !ttsAudioEl.paused
    && !ttsAudioEl.ended)
}

function updateGlobalTtsReturn() {
  if (!globalTtsReturn) return
  const session = state.ttsSession
  const path = pathFromLocation()
  const listeningPath = session?.active
    ? `/read/${session.bookId}/${session.chapterId}`
    : ''
  const show = Boolean(session?.active && !ttsPlayerIsOpen() && (session.detached || path !== listeningPath))
  globalTtsReturn.hidden = !show
  if (show) {
    const detail = [session.bookTitle, session.chapterTitle].filter(Boolean).join(' · ')
    const label = detail ? `打开${detail}的听书播放页` : '打开听书播放页'
    globalTtsReturn.setAttribute('aria-label', label)
    globalTtsReturn.setAttribute('title', label)
  }
}

function navigateInApp(target, { replace = false } = {}) {
  const url = new URL(String(target || '/'), location.origin)
  if (url.origin !== location.origin) return false
  const href = `${url.pathname}${url.search}${url.hash}`
  if (replace) history.replaceState(null, '', href)
  else history.pushState(null, '', href)
  route()
  return true
}

function emptyReadingProgressStore() {
  return {
    schema: READING_PROGRESS_SCHEMA,
    version: READING_PROGRESS_VERSION,
    books: {}
  }
}

function validReadingProgressEntry(value) {
  return value
    && Number.isInteger(Number(value.chapterId))
    && Number(value.chapterId) > 0
    && Number.isFinite(Number(value.within))
    && Number(value.within) >= 0
    && Number(value.within) <= 1
    && Number.isFinite(Number(value.updatedAt))
    && Number(value.updatedAt) > 0
}

function readReadingProgressStore() {
  const raw = localStorageGet(READING_PROGRESS_STORAGE_KEY)
  if (!raw) return emptyReadingProgressStore()
  try {
    const parsed = JSON.parse(raw)
    if (parsed?.schema !== READING_PROGRESS_SCHEMA
      || parsed?.version !== READING_PROGRESS_VERSION
      || !parsed.books
      || typeof parsed.books !== 'object'
      || Array.isArray(parsed.books)) {
      return emptyReadingProgressStore()
    }
    const books = {}
    Object.entries(parsed.books).forEach(([publicId, entry]) => {
      if (!/^[A-Za-z0-9_-]{22}$/.test(publicId) || !validReadingProgressEntry(entry)) return
      const restored = {
        chapterId: Number(entry.chapterId),
        within: Number(entry.within),
        mode: ['slide', 'cover', 'simulation', 'vertical'].includes(entry.mode) ? entry.mode : 'vertical',
        updatedAt: Number(entry.updatedAt)
      }
      if (typeof entry.title === 'string' && entry.title) restored.title = entry.title.slice(0, 200)
      if (typeof entry.chapterTitle === 'string' && entry.chapterTitle) restored.chapterTitle = entry.chapterTitle.slice(0, 200)
      books[publicId] = restored
    })
    return { ...emptyReadingProgressStore(), books }
  } catch {
    return emptyReadingProgressStore()
  }
}

function getReadingProgress(bookId) {
  return readReadingProgressStore().books[String(bookId)] || null
}

function saveReadingProgress(bookId, chapterId, within, mode, title, chapterTitle) {
  const publicId = String(bookId)
  const normalizedChapterId = Number(chapterId)
  if (!/^[A-Za-z0-9_-]{22}$/.test(publicId)
    || !Number.isInteger(normalizedChapterId) || normalizedChapterId <= 0) return
  const store = readReadingProgressStore()
  const entry = {
    chapterId: normalizedChapterId,
    within: Math.min(1, Math.max(0, Number(within) || 0)),
    mode: ['slide', 'cover', 'simulation', 'vertical'].includes(mode) ? mode : 'vertical',
    updatedAt: Date.now()
  }
  if (title) entry.title = String(title).slice(0, 200)
  else if (store.books[publicId]?.title) entry.title = store.books[publicId].title
  if (chapterTitle) entry.chapterTitle = String(chapterTitle).slice(0, 200)
  else if (store.books[publicId]?.chapterTitle) entry.chapterTitle = store.books[publicId].chapterTitle
  store.books[publicId] = entry
  store.books = Object.fromEntries(
    Object.entries(store.books)
      .sort(([, left], [, right]) => right.updatedAt - left.updatedAt)
      .slice(0, READING_PROGRESS_BOOK_LIMIT)
  )
  localStorageSet(READING_PROGRESS_STORAGE_KEY, JSON.stringify(store))
  scheduleReadingHistorySync(publicId, entry)
}

const palettes = [
  ['#234e70', '#14283e'], ['#783f5a', '#321b32'], ['#87542f', '#332112'],
  ['#276151', '#102e29'], ['#435ab4', '#182145'], ['#766325', '#302812']
]

function node(tag, options = {}, children = []) {
  const element = document.createElement(tag)
  Object.entries(options).forEach(([key, value]) => {
    if (value === null || value === undefined) return
    if (key === 'class') element.className = value
    else if (key === 'text') element.textContent = value
    else if (key === 'html') throw new Error('Unsafe HTML is disabled')
    else if (key.startsWith('on') && typeof value === 'function') {
      element.addEventListener(key.slice(2).toLowerCase(), value)
    } else if (key === 'dataset') Object.assign(element.dataset, value)
    else element.setAttribute(key, value)
  })
  ;(Array.isArray(children) ? children : [children]).forEach(child => {
    if (child === null || child === undefined) return
    element.append(child instanceof Node ? child : document.createTextNode(String(child)))
  })
  return element
}

function freshCoverUrl(url) {
  try {
    const parsed = new URL(String(url || ''), window.location.origin)
    if (parsed.origin === window.location.origin) return `${parsed.pathname}${parsed.search}${parsed.hash}`
  } catch (_) {}
  return url
}

const coverLoader = (() => {
  const MAX_CONCURRENT = 6
  const MAX_BLOB_CACHE_BYTES = 12 * 1024 * 1024
  const queue = []
  const inFlight = new Map()
  const blobCache = new Map()
  let active = 0
  let blobCacheBytes = 0
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return
      const img = entry.target
      observer.unobserve(img)
      const url = img.dataset.coverSrc
      if (url) enqueue(img, url)
    })
  }, { rootMargin: '200px' })

  function enqueue(img, url) {
    const job = { img, url: freshCoverUrl(url) }
    queueMicrotask(() => {
      if (!img.isConnected) return
      queue.push(job)
      flush()
    })
  }

  function flush() {
    while (active < MAX_CONCURRENT && queue.length) {
      const job = queue.shift()
      active++
      load(job)
    }
  }

  function load(job) {
    const { img, url } = job
    if (!img.isConnected) { active--; flush(); return }
    coverBlob(url).then(blob => {
      if (!img.isConnected) { active--; flush(); return }
      const objectUrl = URL.createObjectURL(blob)
      img.addEventListener('load', () => {
        img.style.removeProperty('visibility')
        URL.revokeObjectURL(objectUrl)
      }, { once: true })
      img.addEventListener('error', () => URL.revokeObjectURL(objectUrl), { once: true })
      img.src = objectUrl
      active--
      flush()
    }).catch(() => {
      img.dispatchEvent(new Event('error'))
      active--
      flush()
    })
  }

  function rememberBlob(url, blob) {
    if (blob.size > MAX_BLOB_CACHE_BYTES) return
    while (blobCache.size && blobCacheBytes + blob.size > MAX_BLOB_CACHE_BYTES) {
      const [oldestUrl, oldestBlob] = blobCache.entries().next().value
      blobCache.delete(oldestUrl)
      blobCacheBytes -= oldestBlob.size
    }
    blobCache.set(url, blob)
    blobCacheBytes += blob.size
  }

  async function requestBlob(url, attempt = 0) {
    const response = await fetch(url, { cache: 'default' })
    if (response.status === 429 && attempt < 3) {
      await new Promise(resolve => setTimeout(resolve, 1000 * (attempt + 1)))
      return requestBlob(url, attempt + 1)
    }
    if (!response.ok) throw new Error(`封面读取失败（${response.status}）`)
    return response.blob()
  }

  function coverBlob(url) {
    const cached = blobCache.get(url)
    if (cached) {
      blobCache.delete(url)
      blobCache.set(url, cached)
      return Promise.resolve(cached)
    }
    const existing = inFlight.get(url)
    if (existing) return existing
    const request = requestBlob(url).then(blob => {
      rememberBlob(url, blob)
      return blob
    }).finally(() => inFlight.delete(url))
    inFlight.set(url, request)
    return request
  }

  return {
    observe(img, url) {
      img.style.visibility = 'hidden'
      img.dataset.coverSrc = url
      observer.observe(img)
    },
    loadNow(img, url) {
      img.style.visibility = 'hidden'
      enqueue(img, url)
    }
  }
})()

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {})
  headers.set('Accept', 'application/json')
  const response = await fetch(path, { ...options, headers })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`)
  return data
}

async function accountApi(path, { method = 'GET', body = null, form = null } = {}) {
  const headers = new Headers({ Accept: 'application/json' })
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
  try {
    const data = await accountApi('/api/v1/auth/session')
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
    const [, reading] = await Promise.all([
      refreshCloudState(),
      accountApi('/api/v1/me/reading-level')
    ])
    state.accountReading = reading
  } catch {
    state.account = null
    state.accountProfile = null
    state.accountReading = null
    state.csrfToken = ''
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

async function renderGoogleButton(slot, { mode = 'login' } = {}) {
  if (!state.accountConfig) state.accountConfig = await api('/api/v1/auth/config')
  const googleConfig = state.accountConfig.google || {}
  if (!googleConfig.web_enabled) {
    slot.replaceChildren(node('div', { class: 'google-unavailable', text: 'Google 登录将在配置 OAuth 凭据后自动启用' }))
    return
  }
  try {
    await loadGoogleIdentityScript()
    window.google.accounts.id.initialize({
      client_id: googleConfig.web_client_id,
      auto_select: false,
      cancel_on_tap_outside: true,
      ux_mode: 'redirect',
      login_uri: location.origin
    })
    slot.replaceChildren()
    window.google.accounts.id.renderButton(slot, {
      type: 'standard',
      theme: 'outline',
      size: 'large',
      shape: 'pill',
      text: 'continue_with',
      width: 310,
      state: mode === 'link' ? 'oohstory-web-link-v1' : 'oohstory-web-redirect-v1'
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
    node('img', { class: 'auth-art-mark', src: '/icon-192.png?v=20260730-icon1', alt: 'OOH Story 标志' }),
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
        text: isLogin ? '正在检查 Google 登录…' : '注册后请在个人中心绑定 Google 账户'
      })
    ])
    panel.replaceChildren(
      node('p', { class: 'auth-kicker', text: 'OOH STORY ACCOUNT' }),
      node('h1', { text: isLogin ? '欢迎回来' : '建立你的阅读宇宙' }),
      node('p', { class: 'auth-subtitle', text: isLogin ? '登录后继续上次的故事。' : '现在开放注册；邀请码为选填项，一个账户同步三端。' }),
      tabs, form,
      ...(isLogin ? [node('div', { class: 'auth-divider', text: '或' })] : []),
      googleSlot,
      node('p', { class: 'auth-safety', text: '🔒 密码使用 Argon2id 加密；会话可随时撤销；我们不会保存 Google 密码。' })
    )
    if (isLogin) renderGoogleButton(googleSlot).catch(() => {})
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
  return ({ quarantined: '隔离扫描中', clean_queued: '旧版归纳队列', ai_pending: '等待审核', reviewing: '审核中', approved: '已通过·等待入库', completed: '已入库', rejected: '已驳回' })[status] || status
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
  const passwordForm = node('form', { class: 'password-form' }, [
    node('label', { class: 'profile-field' }, [node('span', { text: '当前密码' }), node('input', { name: 'current_password', type: 'password', autocomplete: 'current-password', maxlength: '128', required: '' })]),
    node('label', { class: 'profile-field' }, [node('span', { text: '新密码' }), node('input', { name: 'new_password', type: 'password', autocomplete: 'new-password', minlength: '12', maxlength: '128', required: '', placeholder: '至少 12 位，包含三类字符' })]),
    node('label', { class: 'profile-field' }, [node('span', { text: '确认新密码' }), node('input', { name: 'confirm_password', type: 'password', autocomplete: 'new-password', minlength: '12', maxlength: '128', required: '' })]),
    node('button', { class: 'primary-button', type: 'submit', text: '修改密码' })
  ])
  passwordForm.addEventListener('submit', async event => {
    event.preventDefault()
    const values = new FormData(passwordForm)
    if (values.get('new_password') !== values.get('confirm_password')) { notice.textContent = '两次输入的新密码不一致'; return }
    const button = passwordForm.querySelector('button'); button.disabled = true
    try {
      const result = await accountApi('/api/v1/me/password', { method: 'POST', body: { current_password: values.get('current_password'), new_password: values.get('new_password') } })
      passwordForm.reset(); notice.textContent = ''; showAccountSuccessToast('密码修改成功')
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
    node('section', { class: 'account-section' }, [node('h2', { text: '账户安全' }), node('p', { text: '修改成功后，其他设备会退出登录，当前设备保持在线。' }), passwordForm]),
    node('section', { class: 'account-section' }, [node('h2', { text: '阅读等级图鉴' }), node('p', { text: '仅在页面可见且有真实互动时积累；每次助力推荐会捐赠 1 小时阅读经验时长。' }), node('div', { class: 'reading-level-map' }, levels.map(([roman,name,hours], index) => node('div', { class: index + 1 === data.reading.level ? 'current' : '' }, [readingRankIcon({ level: index + 1, roman, name }, { decorative: true }), node('span', { text: name }), node('small', { text: `${hours} 小时` })])))])
  ]))
}

function submissionRecord(item, type) {
  const structure = item.structure_report
  const missing = structure?.missing_files || item.review_result?.missing_files || []
  return node('article', { class: 'submission-record' }, [
    node('div', {}, [
      node('span', { class: 'eyebrow', text: type === 'novel' ? '小说投稿' : '拆书文' }),
      node('h3', { text: item.title || item.original_filename || '未命名投稿' }),
      node('p', { text: item.author ? `${item.author} · ${item.category || '未分类'}` : `${structure?.profile === 'long' ? '长篇' : '短篇'}结构 · ${Number(structure?.file_count || 0)} 个文件` }),
      ...(missing.length ? [node('small', { class: 'submission-reason', text: `缺少：${missing.join('、')}` })] : []),
      ...(item.rejection_reason ? [node('small', { class: 'submission-reason', text: item.rejection_reason })] : [])
    ]),
    node('span', { class: `submission-status status-${item.status}`, text: uploadStatusLabel(item.status) })
  ])
}

async function loadSubmissionPage() {
  if (!state.account) { openAuthDialog('login'); location.hash = '#/'; return }
  setSeo({ title: '我的投稿｜OOH Story', description: '安全上传拆书结构或小说正文并查看审核结果。', canonicalPath: '/', robots: 'noindex, nofollow' })
  const [deconstructions, novels, categoryData] = await Promise.all([
    accountApi('/api/v1/me/uploads'),
    accountApi('/api/v1/me/novel-submissions'),
    state.categories.length ? Promise.resolve({ items: state.categories }) : api('/api/v1/categories')
  ])
  state.categories = (categoryData.items || []).filter(item => item?.name)
  const feedback = node('p', { class: 'profile-feedback', role: 'status' })
  const reviewRules = () => node('aside', { class: 'submission-review-rules' }, [
    node('strong', { text: '审核范围' }),
    node('ul', {}, [
      node('li', { text: '覆盖 TXT 全文、EPUB 内部章节及拆书结构内全部文本，不只检查标题、封面或开头。' }),
      node('li', { text: '标题、简介、报告与正文主题必须一致；伪装成正常书籍的广告或违法内容会被驳回。' }),
      node('li', { text: '禁止涉黄、涉毒、涉赌、诈骗、违法交易、广告引流、网址、邮箱、联系方式及二维码。' })
    ])
  ])

  const deconstructionInput = node('input', { type: 'file', accept: '.zip,application/zip', required: '' })
  const deconstructionButton = node('button', { class: 'primary-button', type: 'submit', text: '上传并开始审核' })
  const deconstructionForm = node('form', { class: 'upload-box submission-upload-box' }, [deconstructionInput, deconstructionButton])
  deconstructionForm.addEventListener('submit', async event => {
    event.preventDefault()
    if (!deconstructionInput.files?.[0]) return
    deconstructionButton.disabled = true
    feedback.textContent = '正在安全解压、验毒并识别长/短篇结构…'
    const form = new FormData(); form.append('file', deconstructionInput.files[0])
    try {
      const result = await accountApi('/api/v1/me/uploads', { method: 'POST', form })
      feedback.textContent = result.message
      await loadSubmissionPage()
    } catch (error) { feedback.textContent = error.message }
    finally { deconstructionButton.disabled = false }
  })

  const novelForm = node('form', { class: 'novel-submission-form' })
  const steps = [
    node('fieldset', { class: 'submission-step' }, [
      node('legend', { text: '01 · 作品资料' }),
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
      node('legend', { text: '02 · 文件与封面' }),
      node('label', { class: 'profile-field profile-field-wide' }, [node('span', { text: '正文 TXT / EPUB' }), node('input', { name: 'manuscript', type: 'file', accept: '.txt,.epub,text/plain,application/epub+zip', required: '' })]),
      node('label', { class: 'profile-field profile-field-wide' }, [node('span', { text: '书籍封面 JPEG / PNG / WebP' }), node('input', { name: 'cover', type: 'file', accept: 'image/jpeg,image/png,image/webp', required: '' })])
    ]),
    node('fieldset', { class: 'submission-step', hidden: '' }, [
      node('legend', { text: '03 · 来源与授权' }),
      node('label', { class: 'profile-field profile-field-wide' }, [node('span', { text: '作品来源' }), node('input', { name: 'source', maxlength: '500', placeholder: '原创 / 开源地址 / 授权方', required: '' })]),
      node('label', { class: 'profile-field profile-field-wide' }, [node('span', { text: '版权或授权说明' }), node('textarea', { name: 'authorization', minlength: '10', maxlength: '2000', rows: '5', required: '', placeholder: '请说明你有权上传并允许我站展示的依据' })]),
      node('p', { class: 'page-subtitle', text: '提交后会先进入隔离沙箱、ClamAV 验毒与审核；未通过不会写入书库。' })
    ])
  ]
  let currentStep = 0
  const stepLabel = node('strong', { text: '1 / 3' })
  const previous = node('button', { class: 'ghost-button', type: 'button', text: '上一步', disabled: '' })
  const next = node('button', { class: 'primary-button', type: 'button', text: '下一步' })
  const submit = node('button', { class: 'primary-button', type: 'submit', text: '提交审核', hidden: '' })
  const showStep = index => {
    currentStep = Math.max(0, Math.min(2, index))
    steps.forEach((step, position) => { step.hidden = position !== currentStep })
    previous.disabled = currentStep === 0; next.hidden = currentStep === 2; submit.hidden = currentStep !== 2
    stepLabel.textContent = `${currentStep + 1} / 3`
  }
  previous.addEventListener('click', () => showStep(currentStep - 1))
  next.addEventListener('click', () => {
    const controls = [...steps[currentStep].querySelectorAll('input,textarea,select')]
    if (controls.some(control => !control.reportValidity())) return
    showStep(currentStep + 1)
  })
  novelForm.append(node('ol', { class: 'submission-stepper' }, [node('li', { text: '作品资料' }), node('li', { text: '正文与封面' }), node('li', { text: '授权确认' })]), ...steps,
    node('div', { class: 'submission-wizard-actions' }, [previous, stepLabel, next, submit]))
  novelForm.addEventListener('submit', async event => {
    event.preventDefault(); submit.disabled = true; feedback.textContent = '正在隔离沙箱扫描正文与封面…'
    const values = new FormData(novelForm)
    const metadata = Object.fromEntries(['title','author','category','serialization_status','summary','source','authorization'].map(key => [key, values.get(key)]))
    const form = new FormData(); form.append('metadata', JSON.stringify(metadata)); form.append('manuscript', values.get('manuscript')); form.append('cover', values.get('cover'))
    try {
      const result = await accountApi('/api/v1/me/novel-submissions', { method: 'POST', form })
      feedback.textContent = result.message; await loadSubmissionPage()
    } catch (error) { feedback.textContent = error.message }
    finally { submit.disabled = false }
  })

  const records = node('div', { class: 'submission-records' }, [
    ...(novels.items || []).map(item => submissionRecord(item, 'novel')),
    ...(deconstructions.items || []).map(item => submissionRecord(item, 'deconstruction'))
  ])
  if (!records.childElementCount) records.append(node('p', { class: 'page-subtitle', text: '还没有投稿记录。' }))
  app.replaceChildren(node('div', { class: 'account-page' }, [
    accountNavigation('submissions'), feedback,
    node('header', { class: 'account-page-heading' }, [node('span', { class: 'eyebrow', text: 'CONTRIBUTOR STUDIO' }), node('h1', { text: '我的投稿' }), node('p', { text: '每一份文件都会安全隔离、识别结构并完成审核，通过后才交给入库流程。' })]),
    node('section', { class: 'account-section submission-panel' }, [node('h2', { text: '上传我的拆书文' }), node('p', {}, [document.createTextNode('注意：我站目前仅接受上传来自开源项目《oh-story-claudecode》的拆书结构。'), node('a', { href: 'https://github.com/worldwonderer/oh-story-claudecode', target: '_blank', rel: 'noopener noreferrer', text: '查看开源项目 ↗' })]), reviewRules(), deconstructionForm]),
    node('section', { class: 'account-section submission-panel' }, [node('h2', { text: '上传小说' }), node('p', { text: '按作品资料、正文封面、版权授权三步提交。' }), reviewRules(), novelForm]),
    node('section', { class: 'account-section' }, [node('h2', { text: '审核与入库记录' }), records])
  ]))
}

async function loadNotificationsPage() {
  if (!state.account) { openAuthDialog('login'); location.hash = '#/'; return }
  setSeo({ title: '消息中心｜OOH Story', description: '查看投稿审核与入库通知。', canonicalPath: '/', robots: 'noindex, nofollow' })
  const data = await accountApi('/api/v1/me/notifications')
  state.notificationsUnread = Number(data.unread_count || 0)
  const notificationView = item => {
    const searchable = `${item.title || ''} ${item.message || ''}`
    const rejected = /驳回|未通过|失败|缺少/.test(searchable)
    if (rejected) return { tone: 'danger', icon: '!', label: '需要处理' }
    if (item.kind === 'submission_ingestion') return { tone: 'success', icon: '✓', label: '入库进度' }
    return { tone: 'review', icon: '◇', label: '审核动态' }
  }
  const list = node('div', { class: 'notification-list', role: 'list' }, (data.items || []).map(item => {
    const view = notificationView(item)
    const actions = node('div', { class: 'notification-actions' }, [
      ...(item.action_url ? [node('a', { class: 'notification-action-link', href: item.action_url, text: '查看投稿 →' })] : []),
      ...(!item.read_at ? [node('button', { class: 'notification-read-button', type: 'button', text: '标记已读', onclick: async () => { await accountApi(`/api/v1/me/notifications/${item.id}/read`, { method: 'POST' }); await loadNotificationsPage() } })] : [])
    ])
    return node('article', { class: `notification-card tone-${view.tone}${item.read_at ? '' : ' unread'}`, role: 'listitem' }, [
      node('div', { class: 'notification-icon', 'aria-hidden': 'true' }, [node('span', { text: view.icon })]),
      node('div', { class: 'notification-content' }, [
        node('div', { class: 'notification-card-topline' }, [
          node('span', { class: `notification-kind tone-${view.tone}`, text: view.label }),
          ...(!item.read_at ? [node('span', { class: 'notification-unread', role: 'status', text: '未读' })] : [node('span', { class: 'notification-read', text: '已读' })])
        ]),
        node('h2', { text: item.title }),
        node('p', { text: item.message }),
        node('footer', { class: 'notification-footer' }, [
          node('time', { datetime: item.created_at, text: new Date(item.created_at).toLocaleString('zh-CN') }),
          actions
        ])
      ])
    ])
  }))
  if (!list.childElementCount) list.append(node('div', { class: 'account-empty notification-empty' }, [
    node('span', { class: 'notification-empty-icon', text: '◇' }),
    node('h2', { text: '收件箱很安静' }),
    node('p', { text: '投稿审核、缺失文件和正式入库结果会第一时间出现在这里。' }),
    node('a', { class: 'primary-button', href: '#/account/submissions', text: '前往投稿中心' })
  ]))
  const markAll = node('button', { class: 'notification-mark-all', type: 'button', text: '全部标记已读', disabled: state.notificationsUnread ? null : '', onclick: async () => { await accountApi('/api/v1/me/notifications/read', { method: 'POST' }); await loadNotificationsPage() } })
  app.replaceChildren(node('div', { class: 'account-page notification-page' }, [
    accountNavigation('notifications'),
    node('header', { class: 'notification-hero' }, [
      node('div', { class: 'notification-hero-copy' }, [
        node('span', { class: 'eyebrow', text: 'MESSAGE CENTER' }),
        node('h1', { text: '消息中心' }),
        node('p', { text: '审核进展、入库结果与需要补充的资料，都集中在这里。' })
      ]),
      node('div', { class: 'notification-summary' }, [
        node('span', { text: '未读消息' }),
        node('strong', { text: String(state.notificationsUnread) }),
        markAll
      ])
    ]),
    list
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
  const [cloud, uploads, userInfo, notifications] = await Promise.all([
    refreshCloudState(),
    accountApi('/api/v1/me/uploads'),
    accountApi('/api/v1/me/profile'),
    accountApi('/api/v1/me/notifications?limit=1')
  ])
  state.notificationsUnread = Number(notifications.unread_count || 0)
  state.accountProfile = userInfo.profile
  state.accountReading = userInfo.reading
  updateAccountButton()
  const uploadList = node('div', { class: 'upload-list' },
    (uploads.items || []).map(item => node('div', { class: 'upload-item' }, [
      node('div', {}, [
        node('strong', { text: item.original_filename }),
        node('small', { text: `${formatBytes(item.bytes)} · ${new Date(item.created_at).toLocaleString('zh-CN')}${item.rejection_reason ? ` · ${item.rejection_reason}` : ''}` })
      ]),
      node('span', { class: 'upload-status', text: uploadStatusLabel(item.status) })
    ]))
  )
  if (!(uploads.items || []).length) uploadList.append(node('p', { class: 'page-subtitle', text: '还没有上传记录。' }))
  const fileInput = node('input', { type: 'file', accept: '.zip,application/zip', required: '' })
  const uploadButton = node('button', { class: 'primary-button', type: 'submit', text: '隔离扫描并上传' })
  const uploadMessage = node('p', { class: 'auth-error', role: 'status' })
  const uploadForm = node('form', { class: 'upload-box' }, [fileInput, uploadButton])
  uploadForm.addEventListener('submit', async event => {
    event.preventDefault()
    if (!fileInput.files?.[0]) return
    uploadButton.disabled = true
    uploadMessage.textContent = '正在隔离、检查文件结构并执行病毒扫描…'
    const form = new FormData()
    form.append('file', fileInput.files[0])
    try {
      const result = await accountApi('/api/v1/me/uploads', { method: 'POST', form })
      uploadMessage.textContent = result.message
      await loadAccountPage()
    } catch (error) {
      uploadMessage.textContent = error.message
    } finally {
      uploadButton.disabled = false
    }
  })
  const logout = node('button', { class: 'ghost-button', type: 'button', text: '退出登录', onclick: async () => {
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
        await accountApi('/api/v1/auth/google/link/start', { method: 'POST' })
        await renderGoogleButton(googleSlot, { mode: 'link' })
      } catch (error) {
        googleSlot.replaceChildren(node('div', { class: 'google-unavailable', text: error.message }))
      }
    } })
  ])
  const googleMessage = node('p', {
    class: 'google-link-message',
    text: state.account.google_linked
      ? '已绑定 Google。今后 Web、Android 和 iOS 均可直接使用此 Google 账户登录。'
      : '绑定时请选择与注册邮箱一致的 Google 账户。绑定完成后，无需再次输入密码。'
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
      node('div', {}, [node('h2', { text: '资料与账户安全' }), node('p', { text: '上传头像、完善个人信息或修改密码。' })]),
      node('a', { class: 'ghost-button', href: '#/account/profile', text: '打开设置 →' })
    ]),
    node('section', { class: 'account-section google-link-card' }, [
      node('div', { class: 'google-link-copy' }, [
        node('h2', { text: 'Google 账户绑定' }),
        googleMessage
      ]),
      state.account.google_linked
        ? node('span', { class: 'google-link-status', text: '✓ 已绑定' })
        : googleSlot
    ]),
    node('section', { class: 'account-section' }, [
      node('h2', { text: '上传我的拆书文' }),
      node('p', {}, [
        document.createTextNode('注意：我站目前仅接受上传来自开源项目《oh-story-claudecode》的拆书结构。'),
        node('a', { href: 'https://github.com/worldwonderer/oh-story-claudecode', target: '_blank', rel: 'noopener noreferrer', text: '查看项目结构 ↗' })
      ]),
      node('p', { text: '请上传 ZIP。我们会长/短篇结构审核与内容复核完后，并通过消息中心告知您上传结果。' }),
      uploadForm, uploadMessage, uploadList,
      node('a', { class: 'ghost-button', href: '#/account/submissions', text: '打开完整投稿中心 →' })
    ]),
    node('div', { class: 'account-logout-zone' }, [logout])
  ]))
}

let ephemeralMetricVisitorId = null

function isUuidV4(value) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(String(value || ''))
}

function randomUuidV4() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

function publicMetricVisitorId() {
  const stored = localStorageGet(PUBLIC_METRIC_VISITOR_KEY)
  if (isUuidV4(stored)) return stored
  if (stored) localStorageRemove(PUBLIC_METRIC_VISITOR_KEY)
  if (!ephemeralMetricVisitorId) ephemeralMetricVisitorId = randomUuidV4()
  if (localStorageSet(PUBLIC_METRIC_VISITOR_KEY, ephemeralMetricVisitorId)) {
    return ephemeralMetricVisitorId
  }
  return ephemeralMetricVisitorId
}

async function trackBookMetric(bookId, event, { keepalive = false } = {}) {
  if (!['read', 'download'].includes(event)) return null
  const visitorId = publicMetricVisitorId()
  const response = await fetch(`/api/v1/books/${encodeURIComponent(String(bookId))}/metrics/${event}`, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ visitor_id: visitorId }),
    keepalive
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || `统计请求失败（${response.status}）`)
  return data
}

function queueBookMetricBeacon(bookId, event) {
  if (!['read', 'download'].includes(event)) return false
  if (typeof navigator.sendBeacon !== 'function') return false
  try {
    const visitorId = publicMetricVisitorId()
    const body = new Blob(
      [JSON.stringify({ visitor_id: visitorId })],
      { type: 'application/json' }
    )
    return navigator.sendBeacon(
      `/api/v1/books/${encodeURIComponent(String(bookId))}/metrics/${event}`,
      body
    )
  } catch {
    return false
  }
}

function validReaderCatalog(value, bookId) {
  return value
    && String(value.book?.public_id || '') === String(bookId)
    && Array.isArray(value.chapters)
    && value.chapters.every(item => Number.isInteger(Number(item.id)) && Number(item.id) > 0)
}

function readerCatalogCacheKey(bookId) {
  return `oohstory-reader-catalog:${String(bookId)}`
}

async function getReaderCatalog(bookId) {
  const key = `catalog:${String(bookId)}`
  if (state.readerCatalogs.has(key)) return state.readerCatalogs.get(key)
  if (state.readerInflight.has(key)) return state.readerInflight.get(key)
  let stored = null
  try {
    stored = JSON.parse(sessionStorage.getItem(readerCatalogCacheKey(bookId)) || 'null')
  } catch {
    sessionStorage.removeItem(readerCatalogCacheKey(bookId))
  }
  const savedAt = Number(sessionStorage.getItem(`${readerCatalogCacheKey(bookId)}:saved-at`) || 0)
  if (Date.now() - savedAt < 5 * 60 * 1000 && validReaderCatalog(stored, bookId)) {
    state.readerCatalogs.set(key, stored)
    return stored
  }
  const request = api(`/api/v1/books/${bookId}/chapters`).then(data => {
    if (!validReaderCatalog(data, bookId)) throw new Error('章节目录响应无效')
    state.readerCatalogs.set(key, data)
    try {
      sessionStorage.setItem(readerCatalogCacheKey(bookId), JSON.stringify(data))
      sessionStorage.setItem(`${readerCatalogCacheKey(bookId)}:saved-at`, String(Date.now()))
    } catch {
      // A very large catalog may exceed session storage. Memory cache still works.
    }
    return data
  }).finally(() => state.readerInflight.delete(key))
  state.readerInflight.set(key, request)
  return request
}

function rememberReaderChapter(key, chapter) {
  state.readerChapters.delete(key)
  state.readerChapters.set(key, chapter)
  while (state.readerChapters.size > READER_CHAPTER_CACHE_LIMIT) {
    state.readerChapters.delete(state.readerChapters.keys().next().value)
  }
}

async function getReaderChapter(bookId, chapterId) {
  const key = `chapter:${String(bookId)}:${Number(chapterId)}`
  if (state.readerChapters.has(key)) {
    const cached = state.readerChapters.get(key)
    rememberReaderChapter(key, cached)
    return cached
  }
  if (state.readerInflight.has(key)) return state.readerInflight.get(key)
  const request = api(`/api/v1/books/${bookId}/chapters/${chapterId}`).then(chapter => {
    if (Number(chapter.id) !== Number(chapterId) || String(chapter.book?.public_id || '') !== String(bookId)) {
      throw new Error('章节响应与地址不匹配')
    }
    rememberReaderChapter(key, chapter)
    return chapter
  }).finally(() => state.readerInflight.delete(key))
  state.readerInflight.set(key, request)
  return request
}

function formatNumber(value) {
  return new Intl.NumberFormat('zh-CN', { notation: Number(value) > 9999 ? 'compact' : 'standard' }).format(Number(value || 0))
}

function formatReadingDuration(seconds, { remaining = false } = {}) {
  const raw = Math.max(0, Number(seconds) || 0)
  const totalMinutes = remaining ? Math.ceil(raw / 60) : Math.floor(raw / 60)
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  if (!hours) return `${minutes} 分钟`
  if (!minutes) return `${hours} 小时`
  return `${hours} 小时 ${minutes} 分钟`
}

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (!bytes) return '待统计'
  const units = ['B', 'KB', 'MB', 'GB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / Math.pow(1024, index)).toFixed(index > 1 ? 1 : 0)} ${units[index]}`
}

function formatWordCount(value) {
  const count = Number(value || 0)
  if (count >= 10000) return `${(count / 10000).toFixed(count >= 100000 ? 0 : 1)} 万字`
  return `${formatNumber(count)} 字`
}

function formatChineseChapterNumber(value) {
  const number = Math.max(1, Math.trunc(Number(value) || 1))
  const digits = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九']
  if (number < 10) return digits[number]
  if (number > 9999) return String(number)

  const convert = current => {
    if (current < 10) return digits[current]
    for (const [unit, label] of [[1000, '千'], [100, '百'], [10, '十']]) {
      if (current < unit) continue
      const leading = Math.floor(current / unit)
      const remainder = current % unit
      const prefix = unit === 10 && leading === 1 ? '' : convert(leading)
      if (!remainder) return `${prefix}${label}`
      const zero = remainder < unit / 10 ? '零' : ''
      return `${prefix}${label}${zero}${convert(remainder)}`
    }
    return ''
  }

  return convert(number)
}

function chapterPresentation(chapter, position = null) {
  const label = String(chapter?.label || '').trim()
  const title = String(chapter?.title || '').trim()
  const numericPosition = Number(position)
  const sequence = Number.isInteger(numericPosition) && numericPosition >= 0
    ? numericPosition + 1
    : Math.max(1, Math.trunc(Number(chapter?.id) || 1))
  const fallback = `第${formatChineseChapterNumber(sequence)}章`
  const isMissingTitle = value => !value
    || value === '原文未标注章名'
    || /^正文片段\s*\d+$/u.test(value)
  const isGenericLabel = value => !value
    || value === '正文'
    || /^第?\s*[零〇一二三四五六七八九十百千万两\d]+\s*[章节回卷篇部集]$/u.test(value)

  if (!isMissingTitle(title) && title !== label) {
    return { label: label || fallback, title }
  }
  if (!isMissingTitle(label) && !isGenericLabel(label)) {
    return { label: '', title: label }
  }
  return { label: '', title: fallback }
}

function chapterDisplayTitle(chapter, position = null) {
  return chapterPresentation(chapter, position).title
}

function chapterRunningTitle(chapter, position = null) {
  const presentation = chapterPresentation(chapter, position)
  return presentation.label
    ? `${presentation.label} · ${presentation.title}`
    : presentation.title
}

function coverStyle(book) {
  const seed = Array.from(`${book.title || ''}${book.category || ''}`)
    .reduce((sum, char) => sum + char.charCodeAt(0), 0)
  const palette = palettes[seed % palettes.length]
  return `--cover-a:${palette[0]};--cover-b:${palette[1]}`
}

function cover(book, extraClass = '') {
  const shell = node('div', { class: `book-cover ${extraClass}`, style: coverStyle(book) })
  if (book.cover_url) {
    const image = node('img', { alt: `${book.title}封面` })
    image.addEventListener('load', () => shell.classList.add('has-image'))
    image.addEventListener('error', () => {
      shell.classList.remove('has-image')
      image.remove()
    })
    shell.append(image)
    coverLoader.observe(image, book.cover_url)
  }
  shell.append(node('div', { class: 'cover-fallback' }, [
    node('span', { text: `OOH STORY · ${book.category || '小说'}` }),
    node('strong', { text: book.title || '未命名作品' }),
    node('small', { text: book.author || '佚名' })
  ]))
  if (book.has_deconstruction) shell.append(node('span', { class: 'book-badge', text: '深读' }))
  return shell
}

function safeLibraryReturnPath(value) {
  const fallback = '/library'
  if (!value) return fallback
  try {
    const url = new URL(String(value), location.origin)
    if (url.origin !== location.origin || url.pathname !== '/library') return fallback
    return `${url.pathname}${url.search}`
  } catch {
    return fallback
  }
}

function withLibraryReturn(path, returnTo) {
  const libraryPath = safeLibraryReturnPath(returnTo)
  if (libraryPath === '/library') return path
  const url = new URL(path, location.origin)
  url.searchParams.set('from', libraryPath)
  return `${url.pathname}${url.search}`
}

function bookCard(book, { returnTo = '' } = {}) {
  const bookCover = cover(book)
  bookCover.append(node('span', { class: 'book-read-badge', text: '阅读', 'aria-hidden': 'true' }))
  return node('a', { class: 'book-card', href: withLibraryReturn(`/books/${book.public_id}`, returnTo) }, [
    bookCover,
    node('h3', { text: book.title }),
    node('p', { text: `${book.author} · ${book.category}` }),
    node('div', { class: 'book-card-meta' }, [
      node('span', { text: formatWordCount(book.approx_word_count) }),
      node('span', {
        class: `serialization-badge ${book.serialization_status || 'ongoing'}`,
        text: book.serialization_status === 'finished' ? '已完本' : '连载中'
      })
    ])
  ])
}

function homeDeconstructionPriority(item) {
  const percentage = Number(item.progress_percent || 0)
  const active = percentage > 0 && percentage < 100
  const completed = percentage >= 100
  const hasFullReport = item.documents.some(document => document.label === '完整拆文报告')
  return [
    active ? 3 : completed ? 2 : 1,
    hasFullReport ? 1 : 0,
    item.documents.length,
    percentage
  ]
}

function orderHomeDeconstructions(items) {
  return [...items].sort((left, right) => {
    const leftPriority = homeDeconstructionPriority(left)
    const rightPriority = homeDeconstructionPriority(right)
    for (let index = 0; index < leftPriority.length; index += 1) {
      if (leftPriority[index] !== rightPriority[index]) {
        return rightPriority[index] - leftPriority[index]
      }
    }
    return String(left.title).localeCompare(String(right.title), 'zh-CN')
  })
}

function deconstructionState(item) {
  const percentage = Math.min(100, Math.max(0, Number(item.progress_percent || 0)))
  const completed = percentage >= 100
  const active = percentage > 0 && percentage < 100
  return {
    percentage,
    completed,
    active,
    status: active ? '拆解中' : completed ? '已完成' : '档案已建立'
  }
}

function deconstructionBackdrop(item) {
  if (!item.cover_url) return null
  const image = node('img', {
    class: 'deconstruction-cover-backdrop',
    alt: '',
    decoding: 'async',
    'aria-hidden': 'true'
  })
  image.addEventListener('load', () => image.parentElement?.classList.add('has-cover'))
  image.addEventListener('error', () => image.remove())
  coverLoader.observe(image, item.cover_url)
  return image
}

function homeDeconstructionCard(item, featured = false) {
  const { percentage, completed, active, status } = deconstructionState(item)
  const hasProgress = Number(item.total_chapters || 0) > 0
  const progressCopy = hasProgress
    ? `已拆解 ${formatNumber(item.completed_chapters)} / ${formatNumber(item.total_chapters)} 章`
    : item.documents.length
      ? `已收录 ${formatNumber(item.documents.length)} 份深读文档`
      : '公开文档整理中'
  const card = node('a', {
    class: `home-deconstruction-card${featured ? ' featured' : ''}`,
    href: `/deconstructions/${encodeURIComponent(item.slug)}`,
    'data-featured': featured ? 'true' : null,
    'aria-label': `打开《${item.title}》拆书档案`
  }, [
    deconstructionBackdrop(item),
    node('div', { class: 'home-deconstruction-card-head' }, [
      node('span', { class: `home-deconstruction-status ${active ? 'active' : completed ? 'complete' : 'archive'}`, text: status }),
      node('span', { class: 'home-deconstruction-index', text: featured ? 'FEATURED FILE' : 'DEEP READING' })
    ]),
    node('h3', { text: item.title }),
    node('div', { class: 'home-deconstruction-documents', 'aria-label': '档案文档' },
      item.documents.map(document => node('span', { text: document.label }))
    ),
    node('div', { class: 'home-deconstruction-card-foot' }, [
      node('div', { class: 'home-deconstruction-progress-copy' }, [
        node('span', { text: progressCopy }),
        hasProgress ? node('strong', { text: `${percentage.toFixed(percentage % 1 ? 1 : 0)}%` }) : null
      ]),
      hasProgress ? node('div', {
        class: 'home-deconstruction-progress',
        role: 'progressbar',
        'aria-label': `${item.title}拆解进度`,
        'aria-valuemin': '0',
        'aria-valuemax': '100',
        'aria-valuenow': String(percentage)
      }, node('span', { style: `width:${percentage}%` })) : null,
      node('span', { class: 'home-deconstruction-open', text: '打开档案 →' })
    ])
  ])
  return card
}

function loading(message = '正在读取故事…') {
  const skeletonCards = []
  for (let i = 0; i < 7; i++) {
    skeletonCards.push(node('div', { class: 'skeleton-card' }, [
      node('div', { class: 'skeleton-cover skeleton-shimmer' }),
      node('div', { class: 'skeleton-line skeleton-shimmer', style: 'width:80%;margin-top:11px' }),
      node('div', { class: 'skeleton-line skeleton-shimmer skeleton-line-short', style: 'width:50%;margin-top:6px' })
    ]))
  }
  app.replaceChildren(node('section', { class: 'skeleton-container' }, [
    node('div', { class: 'skeleton-hero skeleton-shimmer' }),
    node('div', { class: 'skeleton-section' }, [
      node('div', { class: 'skeleton-heading skeleton-shimmer' }),
      node('div', { class: 'skeleton-grid' }, skeletonCards)
    ]),
    node('p', { class: 'skeleton-message', text: message })
  ]))
}

function errorView(error) {
  app.replaceChildren(node('section', { class: 'page-state' }, [
    node('div', { class: 'error-box' }, [
      node('h2', { text: '这一页暂时翻不过去' }),
      node('p', { text: error.message || '未知错误' }),
      node('button', { class: 'primary-button', text: '重新加载', onclick: () => route() })
    ])
  ]))
}

function pageHeading(kicker, title, copy) {
  return node('header', { class: 'page-heading' }, [
    node('span', { class: 'eyebrow', text: kicker }),
    node('h1', { text: title }),
    node('p', { text: copy })
  ])
}

function buildStaggerCarousel(books) {
  if (!books.length || window.innerWidth <= 720) return null
  let activeIndex = 0
  let timer = null
  const stageEl = node('div', { class: 'stagger-stage' })
  const infoEl = node('div', { class: 'stagger-info' })
  const dotsEl = node('div', { class: 'stagger-dots' })
  const items = books.map((book, i) => {
    const el = node('div', { class: 'stagger-item pos-hidden', style: coverStyle(book) })
    if (book.cover_url) {
      const img = node('img', { alt: book.title })
      img.addEventListener('error', () => {
        img.remove()
        el.append(node('div', { style: 'width:100%;height:100%;background:linear-gradient(145deg,var(--cover-a,#3f6f95),var(--cover-b,#20384f))' }))
      })
      el.append(img)
      coverLoader.loadNow(img, book.cover_url)
    } else {
      el.append(node('div', { style: 'width:100%;height:100%;background:linear-gradient(145deg,var(--cover-a,#3f6f95),var(--cover-b,#20384f))' }))
    }
    el.addEventListener('click', () => { goTo(i) })
    stageEl.append(el)
    return el
  })
  const dots = books.map((_, i) => {
    const d = node('button', { class: 'stagger-dot', 'aria-label': `第${i+1}本` })
    d.addEventListener('click', () => goTo(i))
    dotsEl.append(d)
    return d
  })
  function updateInfo(book) {
    infoEl.replaceChildren(
      node('h3', { text: book.title }),
      node('div', { class: 'stagger-info-meta' }, [
        node('span', { class: 'tag-cat', text: book.category || '小说' }),
        node('span', { text: book.author }),
        node('span', { text: formatWordCount(book.approx_word_count) })
      ]),
      node('p', { class: 'stagger-info-desc', text: book.summary || '打开作品详情，立即开始阅读。' }),
      node('a', { class: 'stagger-read-btn', href: `/books/${book.public_id}`, text: '立即阅读' })
    )
  }
  function layout() {
    const n = books.length
    items.forEach((el, i) => {
      el.classList.remove('pos-center', 'pos-left', 'pos-right', 'pos-hidden')
      if (i === activeIndex) el.classList.add('pos-center')
      else if (i === (activeIndex - 1 + n) % n) el.classList.add('pos-left')
      else if (i === (activeIndex + 1) % n) el.classList.add('pos-right')
      else el.classList.add('pos-hidden')
    })
    dots.forEach((d, i) => d.classList.toggle('active', i === activeIndex))
    updateInfo(books[activeIndex])
  }
  function goTo(i) {
    activeIndex = i
    layout()
    resetTimer()
  }
  function resetTimer() {
    if (timer) clearInterval(timer)
    timer = setInterval(() => {
      activeIndex = (activeIndex + 1) % books.length
      layout()
    }, 4000)
  }
  layout()
  resetTimer()
  return node('div', { class: 'stagger-wrap' }, [stageEl, infoEl, dotsEl])
}

function buildLongShowcase(books, categories, categoryBooks) {
  if (!books.length) return null
  if (window.innerWidth <= 720) {
    const listEl = node('div', { class: 'long-mobile-list' })
    books.slice(0, 6).forEach(book => {
      const coverEl = node('div', { class: 'long-mobile-cover', style: coverStyle(book) })
      if (book.cover_url) {
        const img = node('img', { alt: book.title })
        img.addEventListener('error', () => img.remove())
        coverEl.append(img)
        coverLoader.observe(img, book.cover_url)
      }
      listEl.append(node('a', { class: 'long-mobile-item', href: `/books/${book.public_id}` }, [
        coverEl,
        node('div', { class: 'long-mobile-info' }, [
          node('h4', { class: 'long-mobile-title', text: book.title }),
          node('p', { class: 'long-mobile-summary', text: book.summary || '打开作品详情，立即开始阅读。' }),
          node('div', { class: 'long-mobile-meta' }, [
            node('span', { class: 'tag-cat', text: book.category || '小说' }),
            node('span', { text: book.author || '佚名' }),
            node('span', { class: `serialization-badge ${book.serialization_status || 'ongoing'}`, text: book.serialization_status === 'finished' ? '已完本' : '连载中' }),
            node('span', { text: formatWordCount(book.approx_word_count) })
          ])
        ])
      ]))
    })
    return listEl
  }
  const left = buildStaggerCarousel(books)
  const gridEl = node('div', { class: 'cate-grid' })
  categories.slice(0, 8).forEach(cat => {
    const catBooks = categoryBooks[cat.name] || []
    const listEl = node('ul', { class: 'cate-cell-list' })
    catBooks.forEach(b => {
      listEl.append(node('li', {}, [
        node('a', { href: `/books/${b.public_id}` }, [
          node('span', { class: 'cate-book-title', text: b.title }),
          b.author ? node('span', { class: 'cate-book-author', text: b.author }) : null
        ].filter(Boolean))
      ]))
    })
    gridEl.append(node('div', { class: 'cate-cell' }, [
      node('div', { class: 'cate-cell-head' }, [
        node('span', { class: 'cate-name', text: cat.name }),
        node('a', { class: 'cate-more', href: `/library?category=${encodeURIComponent(cat.name)}`, text: '更多' })
      ]),
      listEl
    ]))
  })
  return node('div', { class: 'showcase' }, [left, gridEl])
}

function buildShortShowcase(books, categoryBooks) {
  if (!books.length) return null
  if (window.innerWidth <= 720) {
    const catNames = Object.keys(categoryBooks || {}).slice(0, 4)
    if (!catNames.length) return null
    const wrap = node('div', { class: 'cat-rec-mobile' })
    catNames.forEach(catName => {
      const catBooks = (categoryBooks[catName] || []).slice(0, 10)
      if (!catBooks.length) return
      const scrollEl = node('div', { class: 'cat-rec-scroll' })
      catBooks.forEach(b => {
        const coverEl = node('div', { class: 'cat-rec-cover', style: coverStyle(b) })
        if (b.cover_url) {
          const img = node('img', { alt: b.title })
          img.addEventListener('error', () => img.remove())
          coverEl.append(img)
          coverLoader.observe(img, b.cover_url)
        }
        scrollEl.append(node('a', { class: 'cat-rec-item', href: `/books/${b.public_id}` }, [
          coverEl,
          node('span', { class: 'cat-rec-title', text: b.title }),
          b.author ? node('span', { class: 'cat-rec-author', text: b.author }) : null
        ].filter(Boolean)))
      })
      wrap.append(node('div', { class: 'cat-rec-row' }, [
        node('div', { class: 'cat-rec-head' }, [
          node('span', { class: 'cat-rec-name', text: catName }),
          node('a', { class: 'cat-rec-more', href: `/library?category=${encodeURIComponent(catName)}`, text: '更多→' })
        ]),
        scrollEl
      ]))
    })
    return wrap
  }
  const left = buildStaggerCarousel(books)
  const featuredBooks = books.slice(0, 6)
  const compactBooks = books.slice(6, 30)
  const featuredEl = node('div', { class: 'complete-featured' })
  featuredBooks.forEach(book => {
    const coverEl = node('div', { class: 'complete-book-cover', style: coverStyle(book) })
    if (book.cover_url) {
      const cimg = node('img', { alt: book.title })
      cimg.addEventListener('error', () => cimg.remove())
      coverEl.append(cimg)
      coverLoader.observe(cimg, book.cover_url)
    }
    featuredEl.append(node('a', { class: 'complete-book', href: `/books/${book.public_id}` }, [
      coverEl,
      node('div', { class: 'complete-book-info' }, [
        node('h4', { text: book.title }),
        node('div', { class: 'complete-meta' }, [
          node('span', { class: 'tag-cat', text: book.category || '小说' }),
          node('span', { text: book.author })
        ]),
        node('p', { class: 'complete-desc', text: book.summary || '打开作品详情，立即开始阅读。' })
      ])
    ]))
  })
  const cardsEl = node('div', { class: 'short-cards' })
  const cardCount = 3
  const perCard = 8
  for (let c = 0; c < cardCount; c++) {
    const cardBooks = compactBooks.slice(c * perCard, (c + 1) * perCard)
    if (!cardBooks.length) break
    const listEl = node('div', { class: 'short-card-list' })
    cardBooks.forEach(book => {
      listEl.append(node('a', { class: 'short-card-item', href: `/books/${book.public_id}` }, [
        node('span', { class: 'compact-cat', text: book.category || '小说' }),
        node('span', { class: 'compact-title', text: book.title }),
        book.author ? node('span', { class: 'compact-author', text: book.author }) : null
      ].filter(Boolean)))
    })
    cardsEl.append(node('div', { class: 'short-card' }, [listEl]))
  }
  const rightEl = node('div', { class: 'complete-right' }, [featuredEl, cardsEl])
  return node('div', { class: 'showcase' }, [left, rightEl])
}

function buildRankingSection(rankings) {
  const boards = [
    { key: 'weekly_clicks', label: '周点击榜', unit: '点击' },
    { key: 'monthly_clicks', label: '月点击榜', unit: '点击' },
    { key: 'monthly_recommends', label: '月推荐榜', unit: '推荐' },
    { key: 'new_books', label: '新书榜', unit: '字数' },
    { key: 'favorites', label: '收藏榜', unit: '收藏' },
    { key: 'completed', label: '完本榜', unit: '阅读' },
  ]
  const cards = boards.map(board => {
    const items = rankings[board.key] || []
    const moreLink = board.key === 'new_books' ? '/library?sort=recent'
      : board.key === 'completed' ? '/library?serialization=finished'
      : '/library'
    const headerEl = node('div', { class: 'rank-card-header' }, [
      node('h3', { class: 'rank-card-title', text: board.label }),
      node('a', { class: 'rank-card-more', href: moreLink, text: '更多 →' })
    ])
    if (!items.length) {
      return node('div', { class: 'rank-card' }, [
        headerEl,
        node('div', { class: 'ranking-empty', text: '暂无数据' })
      ])
    }
    const top = items[0]
    const coverEl = node('div', { class: 'rank-top-cover', style: coverStyle(top) })
    if (top.cover_url) {
      const img = node('img', { alt: top.title })
      img.addEventListener('error', () => img.remove())
      coverEl.append(img)
      coverLoader.observe(img, top.cover_url)
    }
    const valueText = board.key === 'new_books' ? formatWordCount(top.value) : formatNumber(top.value)
    const topEl = node('a', { class: 'rank-top-item', href: `/books/${top.public_id}` }, [
      coverEl,
      node('div', { class: 'rank-top-info' }, [
        node('span', { class: 'ranking-rank top1', text: '1' }),
        node('h4', { class: 'rank-top-title', text: top.title }),
        node('div', { class: 'rank-top-meta' }, [
          node('span', { class: 'tag-cat', text: top.category || '小说' }),
          node('span', { text: top.author })
        ]),
        node('span', { class: 'rank-top-value', text: `${valueText} ${board.unit}` })
      ])
    ])
    const listEl = node('div', { class: 'rank-card-list' })
    items.slice(1).forEach((item, i) => {
      const rankClass = i < 2 ? `ranking-rank top${i + 2}` : 'ranking-rank'
      const val = board.key === 'new_books' ? formatWordCount(item.value) : formatNumber(item.value)
      listEl.append(node('a', { class: 'ranking-item', href: `/books/${item.public_id}` }, [
        node('span', { class: rankClass, text: String(i + 2) }),
        node('span', { class: 'ranking-title', text: item.title }),
        node('span', { class: 'ranking-author', text: item.author }),
        node('span', { class: 'ranking-value', text: `${val} ${board.unit}` })
      ]))
    })
    return node('div', { class: 'rank-card' }, [headerEl, topEl, listEl])
  })
  return node('div', { class: 'ranking-grid' }, cards)
}

async function loadHome() {
  setSeo({
    title: 'OOH Story｜免费中文小说阅读与深度拆书',
    description: SITE_DESCRIPTION,
    canonicalPath: '/'
  })
  if (!state.home) state.home = await api('/api/v1/home/primary', { cache: 'no-store' })
  state.categories = state.home.categories
  const search = node('form', { class: 'search-bar' }, [
    node('input', { type: 'search', name: 'q', maxlength: '80', placeholder: '搜索书名或作者…', 'aria-label': '搜索书名或作者' }),
    node('button', { class: 'primary-button', type: 'submit', text: '开始阅读' })
  ])
  search.addEventListener('submit', event => {
    event.preventDefault()
    const query = new FormData(search).get('q').trim()
    location.assign(`/library?q=${encodeURIComponent(query)}`)
  })
  const heroBooks = state.home.featured.slice(0, 6)
  const hero = (() => {
    if (!heroBooks.length) return node('section', { class: 'hero' })
    let activeIdx = 0, heroTimer = null
    const bookInfoEl = node('div', { class: 'hero-book-info' })
    const slidesEl = node('div', { class: 'hero-carousel-slides' })
    const tabsEl = node('div', { class: 'hero-carousel-tabs' })
    const slides = heroBooks.map((book, i) => {
      const slideEl = node('a', { class: `hero-carousel-slide${i === 0 ? ' active' : ''}`, href: `/books/${book.public_id}`, style: coverStyle(book) })
      if (book.cover_url) {
        const img = node('img', { alt: book.title })
        img.addEventListener('error', () => {
          img.remove()
          slideEl.append(node('div', { class: 'hero-slide-fallback' }, [
            node('span', { text: book.category || '小说' }),
            node('strong', { text: book.title }),
            node('small', { text: book.author })
          ]))
        })
        slideEl.append(img)
        coverLoader.loadNow(img, book.cover_url)
      } else {
        slideEl.append(node('div', { class: 'hero-slide-fallback' }, [
          node('span', { text: book.category || '小说' }),
          node('strong', { text: book.title }),
          node('small', { text: book.author })
        ]))
      }
      slidesEl.append(slideEl)
      return slideEl
    })
    const tabs = heroBooks.map((book, i) => {
      const tabEl = node('div', { class: `hero-carousel-tab${i === 0 ? ' active' : ''}`, style: coverStyle(book) })
      if (book.cover_url) {
        const tabImg = node('img', { alt: book.title })
        tabEl.append(tabImg)
        coverLoader.loadNow(tabImg, book.cover_url)
      }
      tabEl.addEventListener('click', () => heroGoTo(i))
      tabsEl.append(tabEl)
      return tabEl
    })
    function updateBookInfo(book) {
      const statusText = book.serialization_status === 'finished' ? '已完结' : '连载中'
      const statusClass = book.serialization_status === 'finished' ? 'finished' : 'ongoing'
      const exactChapterCount = Number(book.chapter_count)
      const approximateChapterCount = Number(book.approx_chapter_count)
      const chapterCount = exactChapterCount > 0
        ? exactChapterCount
        : (approximateChapterCount > 0 ? approximateChapterCount : '?')
      const tags = []
      if (book.genre_tags) tags.push(...book.genre_tags.slice(0, 2))
      if (book.tone_tags) tags.push(...book.tone_tags.slice(0, 2))
      const tagsEl = tags.length ? node('div', { class: 'hero-book-tags' },
        tags.slice(0, 4).map(t => node('span', { class: 'hero-book-tag', text: t }))
      ) : null
      bookInfoEl.replaceChildren(
        node('div', { class: 'hero-book-cat' }, [
          node('span', { class: 'hero-cat-label', text: book.category || '小说' }),
          node('span', { class: `hero-status ${statusClass}`, text: statusText })
        ]),
        node('h2', { class: 'hero-book-title', text: book.title }),
        node('div', { class: 'hero-book-meta' }, [
          node('span', { text: book.author }),
          node('span', { text: formatWordCount(book.approx_word_count) }),
          node('span', { text: `${chapterCount}章` })
        ]),
        ...(tagsEl ? [tagsEl] : []),
        node('p', { class: 'hero-book-summary', text: compactHeroSummary(book.summary) })
      )
    }
    function heroGoTo(i) {
      slides[activeIdx].classList.remove('active')
      tabs[activeIdx].classList.remove('active')
      activeIdx = i
      slides[activeIdx].classList.add('active')
      tabs[activeIdx].classList.add('active')
      updateBookInfo(heroBooks[activeIdx])
      heroResetTimer()
    }
    function heroResetTimer() {
      if (heroTimer) clearInterval(heroTimer)
      heroTimer = setInterval(() => heroGoTo((activeIdx + 1) % heroBooks.length), 5000)
    }
    updateBookInfo(heroBooks[0])
    heroResetTimer()
    const coverEl = node('div', { class: 'hero-cover' }, [slidesEl])
    const rightEl = node('div', { class: 'hero-right' }, [
      bookInfoEl,
      tabsEl,
      node('div', { class: 'hero-bottom' }, [
        node('span', { class: 'hero-kicker', text: 'OOH STORY · 免费小说阅读' }),
        search
      ])
    ])
    return node('section', { class: 'hero' }, [coverEl, rightEl])
  })()
  const categoryStrip = node('div', { class: 'category-strip' })
  state.home.categories.forEach(item => categoryStrip.append(
    node('a', { class: 'category-chip', href: `/library?category=${encodeURIComponent(item.name)}` }, [
      item.name, node('small', { text: formatNumber(item.count) })
    ])
  ))
  const recommendations = Array.isArray(state.home.recommendations) ? state.home.recommendations : []
  const recGrid = node('div', { class: 'book-grid' })
  recommendations.forEach(book => recGrid.append(bookCard(book)))
  const books = node('div', { class: 'book-grid' })
  state.home.featured.forEach(book => books.append(bookCard(book)))
  const continueReadingSlot = node('div', { class: 'home-continue-reading-slot', 'data-home-continue-reading': '' })
  const initialContinueReading = buildHomeContinueReading()
  if (initialContinueReading) continueReadingSlot.append(initialContinueReading)
  else continueReadingSlot.hidden = true
  const deferredHome = node('div', { class: 'home-deferred', 'aria-live': 'polite' }, [
    node('section', { class: 'skeleton-section', 'aria-label': '正在加载更多推荐' }, [
      node('div', { class: 'skeleton-heading skeleton-shimmer' })
    ])
  ])
  const sections = [
    hero,
    continueReadingSlot,
    node('section', { class: 'section' }, [
      node('div', { class: 'section-heading' }, [
        node('div', {}, [node('span', { class: 'section-kicker', text: '热门分类' }), node('h2', { text: '按题材找书' })]),
        node('a', { class: 'section-more', href: '/library', text: '进入书库 →' })
      ]),
      categoryStrip
    ]),
    node('section', { class: 'section' }, [
      node('div', { class: 'section-heading' }, [
        node('div', {}, [node('span', { class: 'section-kicker', text: '每日精选' }), node('h2', { text: '人气推荐' })]),
        node('a', { class: 'section-more', href: '/library', text: '查看全部 →' })
      ]),
      recGrid
    ]),
    deferredHome,
    node('section', { class: 'section' }, [
      node('div', { class: 'section-heading' }, [
        node('div', {}, [node('span', { class: 'section-kicker', text: '持续更新' }), node('h2', { text: '新书入库' })]),
        node('a', { class: 'section-more', href: '/library', text: '查看全部 →' })
      ]),
      books
    ])
  ].filter(Boolean)
  app.replaceChildren(...sections)

  const loadDeferredHome = async () => {
    if (!state.homeSecondaryPromise) {
      state.homeSecondaryPromise = api('/api/v1/home/secondary', { cache: 'no-store' })
        .then(data => (state.homeSecondary = data))
        .catch(error => {
          state.homeSecondaryPromise = null
          throw error
        })
    }
    try {
      const secondary = state.homeSecondary || await state.homeSecondaryPromise
      if (!deferredHome.isConnected || pathFromLocation() !== '/') return
      const categoryBooks = secondary.category_books || {}
      const longNovels = Array.isArray(secondary.long_novels) ? secondary.long_novels : []
      const shortNovels = Array.isArray(secondary.short_novels) ? secondary.short_novels : []
      const longShowcase = longNovels.length
        ? buildLongShowcase(longNovels, state.home.categories, categoryBooks)
        : null
      const shortShowcase = shortNovels.length
        ? buildShortShowcase(shortNovels, categoryBooks)
        : null
      deferredHome.replaceChildren(...[
        longShowcase ? node('section', { class: 'section' }, [
          node('div', { class: 'section-heading' }, [
            node('div', {}, [node('span', { class: 'section-kicker', text: '百万字巨著' }), node('h2', { text: '经典长篇' })]),
            node('a', { class: 'section-more', href: '/library?words=over_1m', text: '查看全部 →' })
          ]),
          longShowcase
        ]) : null,
        shortShowcase ? node('section', { class: 'section' }, [
          node('div', { class: 'section-heading' }, [
            node('div', {}, [
              node('span', { class: 'section-kicker', text: window.innerWidth <= 720 ? '分类推荐' : '轻松一口气读完' }),
              node('h2', { text: window.innerWidth <= 720 ? '热门分类' : '精彩短篇' })
            ]),
            node('a', { class: 'section-more', href: window.innerWidth <= 720 ? '/library' : '/library?words=under_100k', text: '查看全部 →' })
          ]),
          shortShowcase
        ]) : null
      ].filter(Boolean))
    } catch (error) {
      if (!deferredHome.isConnected || pathFromLocation() !== '/') return
      deferredHome.replaceChildren(node('button', {
        class: 'ghost-button home-deferred-retry',
        type: 'button',
        text: '更多推荐加载失败，点击重试',
        onclick: loadDeferredHome
      }))
    }
  }
  if (state.homeSecondary) {
    loadDeferredHome()
  } else if ('IntersectionObserver' in window) {
    const deferredObserver = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return
      deferredObserver.disconnect()
      loadDeferredHome()
    }, { rootMargin: '200px 0px' })
    deferredObserver.observe(deferredHome)
  } else {
    loadDeferredHome()
  }
}

function paramsFromHash() {
  const raw = location.hash
    ? (location.hash.split('?')[1] || '')
    : location.search.replace(/^\?/, '')
  return new URLSearchParams(raw)
}

async function loadLibrary() {
  const params = paramsFromHash()
  const query = params.get('q') || ''
  const category = params.get('category') || ''
  const words = params.get('words') || ''
  const serialization = params.get('serialization') || ''
  const page = Math.max(Number(params.get('page') || 1), 1)
  const sort = params.get('sort') || 'recent'
  if (!state.categories.length) {
    const data = await api('/api/v1/categories')
    state.categories = data.items
  }
  const data = await api(`/api/v1/books?${new URLSearchParams({
    q: query,
    category,
    words,
    serialization,
    page: String(page),
    page_size: '24',
    sort
  })}`)
  const libraryContext = [
    query ? `“${query}”搜索结果` : '',
    category,
    page > 1 ? `第 ${page} 页` : ''
  ].filter(Boolean).join(' · ')
  const libraryTitle = libraryContext
    ? `${libraryContext}｜中文小说书库 - OOH Story`
    : '中文小说书库｜分类检索与在线阅读 - OOH Story'
  const libraryDescription = cleanSeoText([
    libraryContext,
    `OOH Story 书库当前找到 ${formatNumber(data.total)} 本可读作品。`,
    '可按书名、作者、分类、字数和连载状态筛选，并直接在线阅读。'
  ].filter(Boolean).join(' '))
  const hasFilteredVariant = Boolean(query || category || words || serialization || page > 1 || sort !== 'recent')
  setSeo({
    title: libraryTitle,
    description: libraryDescription,
    canonicalPath: '/library',
    robots: hasFilteredVariant
      ? 'noindex, follow, max-image-preview:large'
      : 'index, follow, max-image-preview:large, max-snippet:-1'
  })
  const searchInput = node('input', { type: 'search', value: query, maxlength: '80', placeholder: '书名 / 作者' })
  const sortSelect = node('select', { 'aria-label': '排序' }, [
    node('option', { value: 'recent', text: '新书入库' }),
    node('option', { value: 'title', text: '按书名' }),
    node('option', { value: 'long', text: '长篇优先' })
  ])
  sortSelect.value = sort
  const submit = () => {
    const next = new URLSearchParams()
    if (searchInput.value.trim()) next.set('q', searchInput.value.trim())
    if (category) next.set('category', category)
    if (words) next.set('words', words)
    if (serialization) next.set('serialization', serialization)
    if (sortSelect.value !== 'recent') next.set('sort', sortSelect.value)
    location.assign(`/library${next.toString() ? `?${next}` : ''}`)
  }
  const filterHref = (key, value) => {
    const next = new URLSearchParams(params)
    next.delete('page')
    if (value) next.set(key, value)
    else next.delete(key)
    return `/library${next.toString() ? `?${next}` : ''}`
  }
  const categoryList = node('div', { class: 'category-strip library-category-list', 'aria-label': '小说分类' }, [
    node('a', {
      class: `category-chip${category ? '' : ' active'}`,
      href: filterHref('category', '')
    }, ['全部', node('small', { text: formatNumber(data.total) })]),
    ...state.categories.map(item => node('a', {
      class: `category-chip${category === item.name ? ' active' : ''}`,
      href: filterHref('category', item.name)
    }, [item.name, node('small', { text: formatNumber(item.count) })]))
  ])
  const facetOptions = (key, active, options) => node('div', { class: 'facet-options' },
    options.map(([value, label]) => node('a', {
      class: `facet-option${active === value ? ' active' : ''}`,
      href: filterHref(key, value),
      text: label
    }))
  )
  const filterPanel = node('section', { class: 'library-filter-panel', 'aria-label': '书库筛选' }, [
    node('div', { class: 'filter-row' }, [
      node('strong', { text: '分类' }),
      categoryList
    ]),
    node('div', { class: 'filter-row' }, [
      node('strong', { text: '字数' }),
      facetOptions('words', words, [
        ['', '全部'],
        ['under_100k', '10 万以下'],
        ['over_100k', '10 万以上'],
        ['over_200k', '20 万以上'],
        ['over_300k', '30 万以上'],
        ['over_500k', '50 万以上'],
        ['over_1m', '100 万以上'],
        ['over_2m', '200 万以上']
      ])
    ]),
    node('div', { class: 'filter-row' }, [
      node('strong', { text: '状态' }),
      facetOptions('serialization', serialization, [
        ['', '全部'],
        ['finished', '已完本'],
        ['ongoing', '连载中']
      ])
    ])
  ])
  const toolbar = node('form', { class: 'library-toolbar' }, [
    node('label', { class: 'field' }, [node('span', { text: '⌕' }), searchInput]),
    node('div', { class: 'filters' }, [
      node('label', { class: 'field' }, sortSelect)
    ])
  ])
  toolbar.addEventListener('submit', event => { event.preventDefault(); submit() })
  sortSelect.addEventListener('change', submit)
  const grid = node('div', { class: 'book-grid' })
  const libraryReturnPath = `/library${params.toString() ? `?${params}` : ''}`
  data.items.forEach(book => grid.append(bookCard(book, { returnTo: libraryReturnPath })))
  const withPage = target => {
    const next = new URLSearchParams(params)
    next.set('page', String(target))
    return `/library?${next}`
  }
  const currentPage = Math.min(Math.max(Number(data.page), 1), data.page_count)
  const pageGroupSize = window.innerWidth <= 720 ? 5 : 10
  const groupStart = Math.floor((currentPage - 1) / pageGroupSize) * pageGroupSize + 1
  const groupEnd = Math.min(groupStart + pageGroupSize - 1, data.page_count)
  const pageNumbers = []
  for (let number = groupStart; number <= groupEnd; number += 1) {
    pageNumbers.push(node('a', {
      class: `page-number${number === currentPage ? ' active' : ''}`,
      href: withPage(number),
      'aria-current': number === currentPage ? 'page' : null,
      text: String(number)
    }))
  }
  const jumpInput = node('input', {
    type: 'number',
    min: '1',
    max: String(data.page_count),
    placeholder: `${currentPage}/${data.page_count}`,
    inputmode: 'numeric',
    autocomplete: 'off',
    required: '',
    'aria-label': '跳转页码'
  })
  const jumpForm = node('form', { class: 'page-jump' }, [
    jumpInput,
    node('button', { type: 'submit', text: '跳转' })
  ])
  jumpForm.addEventListener('submit', event => {
    event.preventDefault()
    if (!jumpInput.value.trim()) {
      jumpInput.focus()
      return
    }
    const target = Math.min(Math.max(Number(jumpInput.value), 1), data.page_count)
    location.assign(withPage(target))
  })
  const pageAction = (label, target, enabled, ariaLabel) => enabled
    ? node('a', { class: 'page-action', href: withPage(target), text: label, 'aria-label': ariaLabel })
    : node('span', { class: 'page-action disabled', text: label, 'aria-hidden': 'true' })
  const pagination = node('nav', { class: 'pagination', 'aria-label': '书库分页' }, [
    pageAction('首页', 1, currentPage > 1, '返回首页'),
    pageAction('‹‹', currentPage - 1, currentPage > 1, '上一页'),
    node('div', { class: 'page-numbers' }, pageNumbers),
    pageAction('››', currentPage + 1, currentPage < data.page_count, '下一页'),
    jumpForm,
    pageAction('尾页', data.page_count, currentPage < data.page_count, '前往尾页')
  ])
  app.replaceChildren(
    pageHeading('THE LIBRARY', '全局书库', '海量正版小说免费阅读，输入书名或作者即可搜索。'),
    filterPanel,
    toolbar,
    node('p', { class: 'result-meta', text: `找到 ${formatNumber(data.total)} 本可读作品` }),
    grid,
    pagination,
    node('div', { style: 'height:80px' })
  )
}

async function loadBook(bookId) {
  const libraryReturnPath = safeLibraryReturnPath(paramsFromHash().get('from'))
  const contextualHref = path => withLibraryReturn(path, libraryReturnPath)
  const [book, catalog, metrics, recommendationState] = await Promise.all([
    api(`/api/v1/books/${bookId}`),
    api(`/api/v1/books/${bookId}/chapters`),
    api(`/api/v1/books/${bookId}/metrics`, { cache: 'no-store' }),
    state.account
      ? accountApi(`/api/v1/books/${bookId}/recommendation`).catch(() => null)
      : Promise.resolve(null)
  ])
  const tags = [...new Set([
    book.category,
    book.serialization_status === 'finished' ? '已完本' : '连载中',
    formatWordCount(book.approx_word_count),
    ...(book.genre_tags || []),
    ...(book.tone_tags || [])
  ])]
  const chapterList = node('div', { class: 'chapter-list' })
  const hasVolumes = Boolean(catalog.volumes && catalog.volumes.length > 0)
  if (hasVolumes) {
    const volGrid = node('div', { class: 'vol-cover-grid' })
    catalog.volumes.forEach(vol => {
      const coverEl = node('div', { class: 'vol-cover-wrap' })
      if (vol.cover_path) {
        const img = node('img', { alt: vol.title })
        img.addEventListener('error', () => img.remove())
        coverLoader.observe(img, `/api/v1/books/${bookId}/illustrations/${encodeURI(vol.cover_path)}`)
        coverEl.append(img)
      } else if (Number(vol.id) === 1 && book.cover_url && !book.cover_is_default) {
        const img = node('img', { alt: `${book.title} 封面` })
        img.addEventListener('error', () => img.remove())
        coverLoader.observe(img, book.cover_url)
        coverEl.append(img)
      } else {
        coverEl.append(node('span', { class: 'vol-cover-placeholder' }, [
          node('span', { class: 'vol-placeholder-mark', text: 'LIGHT NOVEL' }),
          node('strong', { text: `第 ${vol.id} 卷` }),
          node('small', { text: vol.title })
        ]))
      }
      const card = node('a', { class: 'vol-cover-card', href: contextualHref(`/books/${bookId}/volumes/${vol.id}`) }, [
        coverEl,
        node('p', { class: 'vol-cover-title', text: vol.title }),
        node('span', { class: 'vol-cover-meta', text: `${vol.chapter_ids.length}章${vol.illustration_count ? ' · ' + vol.illustration_count + '插画' : ''}` })
      ])
      volGrid.append(card)
    })
    chapterList.append(volGrid)
  } else {
    catalog.chapters.forEach((chapter, index) => {
      const presentation = chapterPresentation(chapter, index)
      chapterList.append(node('a', { class: 'chapter-link', href: contextualHref(`/books/${bookId}/chapters/${chapter.id}`) }, [
        presentation.label ? node('span', { text: presentation.label }) : null,
        node('strong', { text: presentation.title })
      ]))
    })
  }
  const summary = book.summary || '暂无结构化简介，可以从章节目录直接进入故事。'
  const bookCanonical = publicUrl(`/books/${encodeURIComponent(book.public_id || bookId)}`)
  const bookImage = publicUrl(book.cover_url || SITE_DEFAULT_IMAGE, SITE_DEFAULT_IMAGE)
  const bookDescription = cleanSeoText(
    `《${book.title}》，作者：${book.author || '佚名'}。${book.category || '中文小说'}，${formatNumber(catalog.chapter_count)} 个可读章节，${book.serialization_status === 'finished' ? '已完本' : '连载中'}。${summary}`
  )
  setSeo({
    title: bookSeoTitle(book),
    description: bookDescription,
    keywords: bookSeoKeywords(book),
    canonicalPath: bookCanonical,
    type: 'book',
    image: bookImage,
    imageAlt: `《${book.title}》封面`,
    author: book.author || SITE_NAME,
    entity: bookSeoEntity(book, bookCanonical, bookDescription, bookImage)
  })
  const savedProgress = getReadingProgress(book.public_id || bookId)
  const resumeChapter = savedProgress
    ? catalog.chapters.find(chapter => Number(chapter.id) === Number(savedProgress.chapterId))
    : null
  const firstChapter = catalog.chapters[0]
  const readingChapter = resumeChapter || firstChapter
  const recommendCountEl = node('span', { text: formatNumber(metrics.recommend_count || 0) })
  const favoriteCountEl = node('span', { text: formatNumber(metrics.favorite_count || 0) })
  const metricCounts = node('strong', {
    text: `${formatNumber(metrics.read_count)} 人已阅读 / ${formatNumber(metrics.download_count)} 人已下载`
  })
  const metricSummary = node('div', { class: 'book-public-metrics' }, metricCounts)
  let recommended = Boolean(recommendationState?.recommended)
  const recommendBtn = node('button', {
    class: `ghost-button detail-interact-btn${recommended ? ' active' : ''}`,
    type: 'button',
    onclick: async () => {
      if (!state.account) {
        openAuthDialog('login', '登录后才能捐赠阅读经验时长，为好书助力推荐。')
        return
      }
      const confirmed = await openRecommendationDialog({
        title: '为这本好书助力？',
        message: '捐赠 1 小时阅读经验时长，将好书推荐给更多人。',
        primaryLabel: '助力推荐',
        secondaryLabel: '再想想',
        confirm: true
      })
      if (!confirmed) return
      recommendBtn.disabled = true
      try {
        const result = await accountApi(`/api/v1/books/${bookId}/recommend`, {
          method: 'POST',
          body: { event_id: randomUuidV4() }
        })
        if (result) {
          recommendCountEl.textContent = formatNumber(result.recommend_count)
          recommended = true
          recommendBtn.classList.add('active')
          if (result.reading) {
            state.accountReading = result.reading
            updateAccountButton()
          }
          state.home = null
          state.homeSecondary = null
          await openRecommendationDialog({
            title: result.sync_pending ? '心意正在送达' : '心意已送达',
            message: result.message || '已捐赠 1 小时阅读经验时长，为这本好书完成一次助力推荐。',
            primaryLabel: '知道了'
          })
        }
      } catch (error) {
        const insufficient = String(error.message || '').includes('不足 1 小时')
        await openRecommendationDialog({
          title: insufficient ? '阅读时长还差一点' : '暂时无法推荐',
          message: insufficient
            ? '每次助力推荐需要捐赠 1 小时阅读经验时长。继续阅读，累计满 1 小时后再来为好书助力吧。'
            : (error.message || '网络暂时不可用，请稍后再试。'),
          primaryLabel: insufficient ? '继续阅读' : '知道了'
        })
      } finally {
        recommendBtn.disabled = false
      }
    }
  }, [node('span', { text: '👍' }), node('span', { text: '推荐 ' }), recommendCountEl])
  let favoriteActive = cloudHas('favorites', book.public_id || bookId)
  let shelfActive = cloudHas('bookshelf', book.public_id || bookId)
  const favoriteLabel = node('span', { text: favoriteActive ? '已收藏' : '收藏' })
  const favoriteBtn = node('button', {
    class: `ghost-button detail-interact-btn${favoriteActive ? ' active' : ''}`,
    type: 'button',
    onclick: async () => {
      try {
        const changed = await setCloudBook('favorites', book, !favoriteActive)
        if (!changed) return
        favoriteActive = !favoriteActive
        favoriteBtn.classList.toggle('active', favoriteActive)
        favoriteLabel.textContent = favoriteActive ? '已收藏' : '收藏'
        const updatedMetrics = await api(`/api/v1/books/${bookId}/metrics`, { cache: 'no-store' })
        favoriteCountEl.textContent = formatNumber(updatedMetrics.favorite_count || 0)
        state.home = null
        state.homeSecondary = null
      } catch {}
    }
  }, [node('span', { text: '⭐' }), favoriteLabel, favoriteCountEl])
  const shelfLabel = node('span', { text: shelfActive ? '已在书架' : '加入书架' })
  const shelfBtn = node('button', {
    class: `ghost-button detail-interact-btn${shelfActive ? ' active' : ''}`,
    type: 'button',
    onclick: async () => {
      try {
        const changed = await setCloudBook('bookshelf', book, !shelfActive)
        if (!changed) return
        shelfActive = !shelfActive
        shelfBtn.classList.toggle('active', shelfActive)
        shelfLabel.textContent = shelfActive ? '已在书架' : '加入书架'
      } catch {}
    }
  }, [node('span', { text: '▣' }), shelfLabel])
  const actionRow = node('div', { class: 'detail-actions' }, [
    readingChapter ? node('a', {
      class: 'primary-button',
      href: contextualHref(`/books/${bookId}/chapters/${readingChapter.id}`),
      text: resumeChapter ? '📖 继续阅读' : '📖 开始阅读'
    }) : null,
    recommendBtn,
    favoriteBtn,
    shelfBtn,
    node('a', {
      class: 'ghost-button download-button',
      href: `/api/v1/books/${bookId}/download`,
      download: '',
      text: '⇩ 下载 TXT',
      'aria-label': `下载《${book.title}》TXT`,
      onclick: event => {
        if (!event.isTrusted) return
        const destination = event.currentTarget.href
        const refreshMetricCounts = async () => {
          try {
            const result = await api(`/api/v1/books/${bookId}/metrics`, { cache: 'no-store' })
            metricCounts.textContent = `${formatNumber(result.read_count)} 人已阅读 / ${formatNumber(result.download_count)} 人已下载`
          } catch {}
        }
        if (queueBookMetricBeacon(bookId, 'download')) {
          window.setTimeout(refreshMetricCounts, 600)
          return
        }
        event.preventDefault()
        trackBookMetric(bookId, 'download', { keepalive: true })
          .then(result => {
            if (result) {
              metricCounts.textContent = `${formatNumber(result.read_count)} 人已阅读 / ${formatNumber(result.download_count)} 人已下载`
            }
          })
          .catch(() => {})
          .finally(() => {
            window.location.assign(destination)
          })
      }
    }),
    book.has_deconstruction ? node('a', { class: 'ghost-button', href: '/deconstructions', text: '查看拆书档案' }) : null
  ])
  app.replaceChildren(node('div', { class: 'detail-page' }, [
    node('div', { class: 'detail-backbar' }, [
      node('a', { class: 'detail-back', href: libraryReturnPath, text: '← 返回书库' })
    ]),
    node('section', { class: 'detail-layout' }, [
      node('aside', { class: 'detail-cover' }, cover(book)),
      node('article', { class: 'detail-main' }, [
        node('span', { class: 'eyebrow', text: 'BOOK PROFILE' }),
        node('h1', { text: book.title }),
        node('p', { class: 'detail-author', text: `作者 · ${book.author}` }),
        node('div', { class: 'tag-row' }, tags.map(tag => node('span', { class: 'tag', text: tag }))),
        actionRow,
        metricSummary,
        node('div', { class: 'fact-grid' }, [
          node('div', { class: 'fact' }, [node('strong', { text: formatNumber(book.approx_word_count) }), node('span', { text: '估算字数' })]),
          node('div', { class: 'fact' }, [node('strong', { text: formatNumber(catalog.chapter_count) }), node('span', { text: '可读章节' })]),
          node('div', { class: 'fact' }, [node('strong', { text: formatBytes(book.source_bytes) }), node('span', { text: '正文体积' })])
        ]),
        node('h2', { text: '内容简介' }),
        (() => {
          const summaryEl = node('p', { class: 'detail-summary detail-summary-clamped', text: summary })
          const toggleBtn = node('button', {
            class: 'detail-summary-toggle',
            type: 'button',
            text: '展开',
            onclick: () => {
              const clamped = summaryEl.classList.toggle('detail-summary-clamped')
              toggleBtn.textContent = clamped ? '展开' : '收起'
            }
          })
          requestAnimationFrame(() => {
            if (summaryEl.scrollHeight <= summaryEl.clientHeight + 2) toggleBtn.style.display = 'none'
          })
          return node('div', { class: 'detail-summary-wrapper' }, [summaryEl, toggleBtn])
        })(),
        node('section', { class: 'chapter-panel' }, [
          node('div', { class: 'chapter-panel-head' }, [
            node('h2', { text: hasVolumes ? '分卷目录' : '章节目录' }),
            node('span', { class: 'tag', text: hasVolumes
              ? `共 ${catalog.volumes.length} 卷 · ${formatNumber(catalog.chapter_count)} 章`
              : `${formatNumber(catalog.chapter_count)} 章`
            })
          ]),
          chapterList
        ])
      ])
    ])
  ]))
}

function saveReaderSettings() {
  localStorageSet('oohstory-reader', JSON.stringify(state.reader))
  applyReaderSettings()
}

function applyReaderSettings() {
  state.reader = {
    colorScheme: state.reader.colorScheme === 'night' ? 'night' : 'day',
    background: ['paper', 'white', 'warm', 'green', 'gray'].includes(state.reader.background) ? state.reader.background : 'paper',
    brightness: Math.min(100, Math.max(35, Number(state.reader.brightness || 100))),
    size: Math.min(36, Math.max(14, Number(state.reader.size || 20))),
    leading: Math.min(2.4, Math.max(1.6, Number(state.reader.leading || 2))),
    width: Math.min(1200, Math.max(620, Number(state.reader.width || 900))),
    mode: ['slide', 'cover', 'simulation', 'vertical'].includes(state.reader.mode) ? state.reader.mode : 'vertical',
    eyeCare: Boolean(state.reader.eyeCare),
    autoSpeed: Math.min(9, Math.max(1, Number(state.reader.autoSpeed || 5))),
    autoReading: Boolean(state.reader.autoReading),
    ttsRate: Math.min(3, Math.max(0.5, Number(state.reader.ttsRate || 1))),
    ttsVoice: state.reader.ttsVoice || 'nuanxi',
    ttsMode: ['normal', 'smart', 'cantonese', 'hokkien'].includes(state.reader.ttsMode) ? state.reader.ttsMode : 'normal',
    ttsNarrator: state.reader.ttsNarrator || 'mocheng',
    ttsEmotion: Object.prototype.hasOwnProperty.call(ttsEmotionModes, state.reader.ttsEmotion) ? state.reader.ttsEmotion : 'auto',
    ttsActive: Boolean(state.reader.ttsActive)
  }
  document.documentElement.style.setProperty('--reader-size', `${state.reader.size}px`)
  document.documentElement.style.setProperty('--reader-leading', String(state.reader.leading))
  document.documentElement.style.setProperty('--reader-width', `${state.reader.width}px`)
  document.documentElement.style.setProperty('--reader-brightness', String(state.reader.brightness / 100))
  document.documentElement.dataset.readerBackground = state.reader.colorScheme === 'night'
    ? 'dark'
    : (state.reader.eyeCare ? 'green' : state.reader.background)
  document.documentElement.dataset.readerMode = state.reader.mode
}

function readerUsesPageScroll(mode = state.reader.mode) {
  return mode === 'vertical' && window.matchMedia('(max-width: 720px)').matches
}

function readerPageScroller() {
  return document.scrollingElement || document.documentElement
}

function readerScrollMetrics(stage, mode = state.reader.mode) {
  if (readerUsesPageScroll(mode)) {
    const scroller = readerPageScroller()
    return {
      scrollTop: scroller.scrollTop,
      clientHeight: window.visualViewport?.height || window.innerHeight || scroller.clientHeight,
      scrollHeight: scroller.scrollHeight
    }
  }
  return {
    scrollTop: stage?.scrollTop || 0,
    clientHeight: stage?.clientHeight || 0,
    scrollHeight: stage?.scrollHeight || 0
  }
}

function setReaderScrollTop(stage, value, mode = state.reader.mode) {
  const top = Math.max(0, Number(value) || 0)
  if (readerUsesPageScroll(mode)) readerPageScroller().scrollTop = top
  else if (stage) stage.scrollTop = top
}

function scrollReaderBy(stage, top, behavior = 'auto', mode = state.reader.mode) {
  if (readerUsesPageScroll(mode)) window.scrollBy({ top, behavior })
  else stage?.scrollBy({ top, behavior })
}

function setReaderScrollBehavior(stage, behavior, mode = state.reader.mode) {
  const target = readerUsesPageScroll(mode) ? readerPageScroller() : stage
  if (!target) return
  if (behavior) target.style.setProperty('scroll-behavior', behavior)
  else target.style.removeProperty('scroll-behavior')
}

function requestReaderFullscreen() {
  if (!window.matchMedia('(max-width: 720px)').matches) return Promise.resolve(false)
  if (window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true) {
    return Promise.resolve(true)
  }
  if (document.fullscreenElement || !document.fullscreenEnabled || !document.documentElement.requestFullscreen) {
    return Promise.resolve(false)
  }
  return document.documentElement.requestFullscreen({ navigationUI: 'hide' })
    .then(() => true)
    .catch(() => false)
}

function bindMobileReaderGestures({
  stage,
  sidebar,
  backdrop,
  mobileNav,
  chapterList,
  ensureCatalog,
  settingsPanel,
  setSettingsVisible,
  previousId,
  nextId,
  goToChapter,
  changePage,
  stopAuto
}) {
  let singleTapTimer = null
  let lastTapAt = 0
  let suppressClickUntil = 0
  let touchStartX = 0
  let touchStartY = 0
  let touchStartScrollTop = 0
  let touchMoved = false
  let touchActive = false
  let gestureLocked = false

  const isMobile = () => window.matchMedia('(max-width: 720px)').matches
  const locateCurrentChapter = (behavior = 'smooth') => {
    ensureCatalog()
    chapterList.querySelector('.active')?.scrollIntoView({ block: 'center', behavior })
  }
  const setCatalogVisible = visible => {
    stopAuto()
    setSettingsVisible(false)
    if (visible) ensureCatalog()
    sidebar.classList.toggle('mobile-visible', visible)
    backdrop.classList.toggle('visible', visible)
    backdrop.setAttribute('aria-hidden', String(!visible))
    mobileNav.classList.remove('visible')
    if (visible) requestAnimationFrame(() => locateCurrentChapter('auto'))
  }
  const toggleCatalog = () => setCatalogVisible(!sidebar.classList.contains('mobile-visible'))

  stage.addEventListener('touchstart', event => {
    const touch = event.touches?.[0]
    if (!touch) return
    touchStartX = touch.clientX
    touchStartY = touch.clientY
    touchStartScrollTop = readerScrollMetrics(stage).scrollTop
    touchMoved = false
    touchActive = true
    gestureLocked = false
  }, { passive: true })
  stage.addEventListener('touchmove', event => {
    const touch = event.touches?.[0]
    if (!touch) return
    if (Math.abs(touch.clientX - touchStartX) > 10 || Math.abs(touch.clientY - touchStartY) > 10) {
      touchMoved = true
    }
  }, { passive: true })
  const handleReaderScroll = () => {
    if (touchActive && Math.abs(readerScrollMetrics(stage).scrollTop - touchStartScrollTop) > 4) {
      touchMoved = true
      suppressClickUntil = Date.now() + 450
    }
  }
  stage.addEventListener('scroll', handleReaderScroll, { passive: true })
  window.addEventListener('scroll', handleReaderScroll, { passive: true })
  stage.addEventListener('touchend', event => {
    const touch = event.changedTouches?.[0]
    const deltaX = touch ? touch.clientX - touchStartX : 0
    const deltaY = touch ? touch.clientY - touchStartY : 0
    const metrics = readerScrollMetrics(stage)
    const moved = !touch
      || touchMoved
      || Math.abs(deltaX) > 12
      || Math.abs(deltaY) > 12
      || Math.abs(metrics.scrollTop - touchStartScrollTop) > 4
    touchActive = false
    if (moved) suppressClickUntil = Date.now() + 450
    if (!touch || !moved || gestureLocked) return
    stopAuto()
    if (state.reader.mode !== 'vertical' && Math.abs(deltaX) > 62 && Math.abs(deltaX) > Math.abs(deltaY)) {
      gestureLocked = changePage(deltaX < 0 ? 1 : -1)
      return
    }
    if (state.reader.mode === 'vertical' && Math.abs(deltaY) > 72 && Math.abs(deltaY) > Math.abs(deltaX)) {
      const atTop = touchStartScrollTop <= 3 && metrics.scrollTop <= 3
      const bottomAtStart = touchStartScrollTop + metrics.clientHeight >= metrics.scrollHeight - 3
      const bottomAtEnd = metrics.scrollTop + metrics.clientHeight >= metrics.scrollHeight - 3
      if (deltaY > 0 && atTop && previousId) {
        gestureLocked = true
        goToChapter(previousId)
      } else if (deltaY < 0 && bottomAtStart && bottomAtEnd && nextId) {
        gestureLocked = true
        goToChapter(nextId)
      }
    }
  }, { passive: true })
  stage.addEventListener('touchcancel', () => {
    touchActive = false
    suppressClickUntil = Date.now() + 450
  }, { passive: true })
  stage.addEventListener('click', event => {
    if (!isMobile()) return
    if (event.target instanceof Element && event.target.closest('a, button, input, select, textarea')) return
    requestReaderFullscreen()
    const now = Date.now()
    if (now < suppressClickUntil) return
    if (lastTapAt && now - lastTapAt <= 320) {
      if (singleTapTimer) window.clearTimeout(singleTapTimer)
      singleTapTimer = null
      lastTapAt = 0
      stopAuto()
      toggleCatalog()
      return
    }
    lastTapAt = now
    singleTapTimer = window.setTimeout(() => {
      singleTapTimer = null
      lastTapAt = 0
      const willShow = !mobileNav.classList.contains('visible')
      setCatalogVisible(false)
      setSettingsVisible(false)
      mobileNav.classList.toggle('visible', willShow)
    }, 320)
  })
  stage.addEventListener('dblclick', event => {
    if (isMobile()) event.preventDefault()
  })
  backdrop.addEventListener('click', () => {
    setCatalogVisible(false)
    setSettingsVisible(false)
  })
  settingsPanel.addEventListener('click', event => event.stopPropagation())
  settingsPanel.addEventListener('touchend', event => event.stopPropagation())

  return {
    locateCurrentChapter,
    toggleCatalog,
    closeCatalog: () => setCatalogVisible(false),
    cancelTap: preserveAuto => {
      if (singleTapTimer) window.clearTimeout(singleTapTimer)
      singleTapTimer = null
      if (!preserveAuto) stopAuto()
    },
    dispose: () => window.removeEventListener('scroll', handleReaderScroll)
  }
}

async function loadReader(bookId, chapterId) {
  const requestedBookId = String(bookId)
  const requestedChapterId = Number(chapterId)
  const libraryReturnPath = safeLibraryReturnPath(paramsFromHash().get('from'))
  const contextualHref = path => withLibraryReturn(path, libraryReturnPath)
  const [chapter, catalog, initialChapterComments] = await Promise.all([
    getReaderChapter(requestedBookId, requestedChapterId),
    getReaderCatalog(requestedBookId),
    api(`/api/v1/books/${requestedBookId}/chapters/${requestedChapterId}/comments`)
      .catch(() => ({ paragraphs: {}, comment_count: 0 }))
  ])
  const chapterPosition = catalog.chapters.findIndex(item => Number(item.id) === requestedChapterId)
  if (chapterPosition < 0) throw new Error('当前章节不在本书目录中')
  trackBookMetric(requestedBookId, 'read').catch(() => {})
  const seoChapterHeading = chapterRunningTitle(chapter, chapterPosition)
  const chapterCanonical = publicUrl(`/books/${encodeURIComponent(requestedBookId)}/chapters/${requestedChapterId}`)
  const parentBookCanonical = publicUrl(`/books/${encodeURIComponent(requestedBookId)}`)
  const chapterDescription = cleanSeoText(
    `《${chapter.book.title}》${seoChapterHeading}在线阅读。作者：${chapter.book.author || '佚名'}。OOH Story 提供本章纯文本阅读与章节导航。`
  )
  setSeo({
    title: `${seoChapterHeading} - 《${chapter.book.title}》在线阅读｜OOH Story`,
    description: chapterDescription,
    canonicalPath: chapterCanonical,
    type: 'article',
    author: chapter.book.author || SITE_NAME,
    entity: {
      '@type': 'Chapter',
      '@id': `${chapterCanonical}#chapter`,
      url: chapterCanonical,
      name: seoChapterHeading,
      position: chapterPosition + 1,
      isPartOf: {
        '@type': 'Book',
        '@id': `${parentBookCanonical}#book`,
        url: parentBookCanonical,
        name: cleanSeoText(chapter.book.title, 160),
        author: { '@type': 'Person', name: cleanSeoText(chapter.book.author, 100) || '佚名' }
      },
      inLanguage: 'zh-CN'
    }
  })
  const previousId = chapter.previous_id ?? catalog.chapters[chapterPosition - 1]?.id ?? null
  const nextId = chapter.next_id ?? catalog.chapters[chapterPosition + 1]?.id ?? null
  ;[previousId, nextId].forEach(id => {
    if (id) getReaderChapter(requestedBookId, id).catch(() => {})
  })

  let mobileReaderControls = null
  let catalogRendered = false
  let settingsVisible = false
  let pageIndex = 0
  let pageCount = 1
  let layoutMode = state.reader.mode
  let resizeTimer = null
  let pageAnimationTimer = null
  let progressSaveTimer = null
  let autoFrame = null
  let autoLastTime = 0
  let progressFrame = null
  let visibilityListener = null
  let stage = null
  let readerContent = null
  let progressFill = null
  let progressCopy = null
  let autoState = null
  let autoButton = null
  let ttsButton = null
  let ttsStateBar = null
  let ttsParagraphIndex = -1
  let currentParagraphHint = -1
  let interlineAction = null
  let chapterComments = initialChapterComments
  let paragraphCommentsByIndex = new Map()
  let settingsPanel = null
  let mobileNav = null
  let desktopProgressFill = null
  let desktopProgressText = null
  let topProgressFill = null
  let fontSizeDisplay = null
  let fontSizeInput = null
  let fontSizeOutput = null

  const ingestChapterComments = data => {
    chapterComments = data && typeof data === 'object' ? data : { paragraphs: {}, comment_count: 0 }
    paragraphCommentsByIndex = new Map()
    Object.values(chapterComments.paragraphs || {}).forEach(thread => {
      const index = Number(thread?.paragraph_index)
      if (Number.isInteger(index) && index >= 0) paragraphCommentsByIndex.set(index, thread)
    })
    readerContent?.querySelectorAll('.reader-paragraph').forEach(paragraph => {
      const index = Number(paragraph.dataset.paragraphIndex)
      const count = Number(paragraphCommentsByIndex.get(index)?.count || 0)
      const bubble = paragraph.querySelector('.interline-bubble')
      if (!bubble) return
      bubble.hidden = count <= 0
      bubble.textContent = `🫧 ${count}`
      bubble.setAttribute('aria-label', `查看这段文字的 ${count} 条评论`)
    })
  }
  const savedProgress = getReadingProgress(requestedBookId)
  const restoreWithin = Number(savedProgress?.chapterId) === requestedChapterId
    ? Math.min(1, Math.max(0, Number(savedProgress.within) || 0))
    : null

  const readingWithin = () => {
    if (!stage) return 0
    if (layoutMode !== 'vertical') return pageCount <= 1 ? 0 : pageIndex / (pageCount - 1)
    const metrics = readerScrollMetrics(stage, layoutMode)
    return Math.min(1, Math.max(0, metrics.scrollTop / Math.max(1, metrics.scrollHeight - metrics.clientHeight)))
  }

  const flushReadingProgress = () => {
    window.clearTimeout(progressSaveTimer)
    progressSaveTimer = null
    if (!stage) return
    saveReadingProgress(requestedBookId, requestedChapterId, readingWithin(), layoutMode, chapter.book.title, seoChapterHeading)
  }

  const scheduleReadingProgressSave = () => {
    if (progressSaveTimer) return
    progressSaveTimer = window.setTimeout(flushReadingProgress, READING_PROGRESS_SAVE_DELAY)
  }

  const goToChapter = (id, automatic = false) => {
    if (!id) return false
    if (!automatic) stopAutoReading()
    flushReadingProgress()
    state.readerAutoContinue = Boolean(automatic)
    navigateInApp(contextualHref(`/books/${requestedBookId}/chapters/${id}`))
    return true
  }

  const updateProgress = () => {
    if (!stage) return
    const within = readingWithin()
    const percentage = Math.min(100, Math.max(0, ((chapterPosition + within) / Math.max(1, catalog.chapters.length)) * 100))
    const text = `${percentage.toFixed(1)}% · 第 ${chapterPosition + 1}/${catalog.chapters.length} 章`
    if (progressFill) progressFill.style.width = `${percentage}%`
    if (progressCopy) progressCopy.textContent = text
    if (desktopProgressFill) desktopProgressFill.style.width = `${percentage}%`
    if (desktopProgressText) desktopProgressText.textContent = text
    if (topProgressFill) topProgressFill.style.width = `${percentage}%`
  }

  const applyPageTransform = direction => {
    if (!readerContent || state.reader.mode === 'vertical') return
    window.clearTimeout(pageAnimationTimer)
    readerContent.dataset.direction = direction || 'none'
    const x = -pageIndex * (stage?.clientWidth || 0)
    readerContent.style.setProperty('--reader-page-x', `${x}px`)
    if (state.reader.mode === 'simulation' && direction !== 'none') {
      readerContent.style.transformOrigin = direction === 'next' ? 'right center' : 'left center'
      readerContent.style.transform = `translate3d(${x}px,0,0) rotateY(${direction === 'next' ? '-4deg' : '4deg'})`
      pageAnimationTimer = window.setTimeout(() => {
        readerContent.style.transform = `translate3d(${x}px,0,0) rotateY(0deg)`
      }, 430)
    } else {
      readerContent.style.transform = `translate3d(${x}px,0,0)`
    }
  }

  const recomputePagination = reset => {
    if (!stage || !readerContent) return
    const previousWithin = reset ? 0 : readingWithin()
    stage.className = `reader-stage reader-mode-${state.reader.mode}`
    if (state.reader.mode === 'vertical') {
      pageIndex = 0
      pageCount = 1
      readerContent.style.removeProperty('transform')
      layoutMode = 'vertical'
      const metrics = readerScrollMetrics(stage, layoutMode)
      setReaderScrollTop(stage, previousWithin * Math.max(0, metrics.scrollHeight - metrics.clientHeight), layoutMode)
      updateProgress()
      return
    }
    pageCount = Math.max(1, Math.ceil(readerContent.scrollWidth / Math.max(1, stage.clientWidth)))
    pageIndex = reset ? 0 : Math.round(previousWithin * Math.max(0, pageCount - 1))
    pageIndex = Math.min(Math.max(0, pageIndex), pageCount - 1)
    layoutMode = state.reader.mode
    setReaderScrollTop(stage, 0, 'vertical')
    stage.scrollTop = 0
    applyPageTransform('none')
    updateProgress()
  }

  const queuePagination = reset => {
    window.clearTimeout(resizeTimer)
    resizeTimer = window.setTimeout(() => recomputePagination(reset), 80)
  }

  const changePage = (delta, automatic = false) => {
    if (state.reader.mode === 'vertical') return false
    const target = pageIndex + delta
    if (target >= 0 && target < pageCount) {
      pageIndex = target
      applyPageTransform(delta > 0 ? 'next' : 'previous')
      updateProgress()
      scheduleReadingProgressSave()
      return true
    }
    return goToChapter(delta > 0 ? nextId : previousId, automatic)
  }

  function stopAutoReading() {
    if (autoFrame) cancelAnimationFrame(autoFrame)
    autoFrame = null
    autoLastTime = 0
    if (stage) setReaderScrollBehavior(stage, null, layoutMode)
    state.reader.autoReading = false
    saveReaderSettings()
    if (autoState) autoState.hidden = true
    if (autoButton) {
      autoButton.classList.remove('active')
      autoButton.textContent = '自动阅读'
    }
    mobileNav?.classList.remove('auto-active')
  }

  const startAutoReading = () => {
    if (autoFrame) cancelAnimationFrame(autoFrame)
    if (state.reader.mode !== 'vertical') {
      state.reader.mode = 'vertical'
      modeOptions?.querySelectorAll('button').forEach(item => {
        item.classList.toggle('active', item.dataset.readerMode === 'vertical')
      })
      recomputePagination(false)
    }
    state.reader.autoReading = true
    saveReaderSettings()
    if (autoState) {
      autoState.hidden = false
      autoState.textContent = `停止自动阅读 · ${state.reader.autoSpeed}档`
    }
    if (autoButton) {
      autoButton.classList.add('active')
      autoButton.textContent = '停止自动阅读'
    }
    mobileNav?.classList.add('auto-active')
    if (stage) setReaderScrollBehavior(stage, 'auto', 'vertical')
    const speed = Number(state.reader.autoSpeed || 5)
    const pixelsPerSecond = 14 + speed * 10
    const tick = timestamp => {
      if (!state.reader.autoReading || !stage) return
      if (!autoLastTime) autoLastTime = timestamp
      const elapsed = Math.min(64, timestamp - autoLastTime)
      autoLastTime = timestamp
      const metrics = readerScrollMetrics(stage, 'vertical')
      setReaderScrollTop(stage, metrics.scrollTop + pixelsPerSecond * elapsed / 1000, 'vertical')
      updateProgress()
      scheduleReadingProgressSave()
      const updatedMetrics = readerScrollMetrics(stage, 'vertical')
      if (updatedMetrics.scrollTop + updatedMetrics.clientHeight >= updatedMetrics.scrollHeight - 2) {
        if (!goToChapter(nextId, true)) stopAutoReading()
        return
      }
      autoFrame = requestAnimationFrame(tick)
    }
    autoLastTime = 0
    autoFrame = requestAnimationFrame(tick)
  }

  const toggleAutoReading = () => state.reader.autoReading ? stopAutoReading() : startAutoReading()

  const ttsMandarinPool = ['nuanxi', 'lingxian', 'shuanger', 'yanzhi', 'kuangyun', 'qingyan', 'tongzhen', 'mocheng']
  const ttsFemalePool = ['nuanxi', 'lingxian', 'shuanger', 'yanzhi']
  const ttsMalePool = ['kuangyun', 'qingyan', 'tongzhen', 'mocheng']
  const ttsCantonesePool = ['wanqing', 'muyao', 'yueming']
  const ttsHokkienPool = ['qianyu', 'ruoxi', 'hanfeng']
  const ttsCache = new Map()
  const ttsCachePromises = new Map()
  let ttsChapterPlan = []
  let ttsPlanIndex = 0
  let ttsNextChapterPlan = []
  let ttsNextChapterSignature = ''
  let ttsNextChapterId = null
  let ttsNextChapterFollowingId = null
  let ttsNextChapterTitle = ''
  let ttsFollowingChapterId = nextId
  let ttsNextChapterCached = false
  let ttsPrevUrls = new Set()
  let ttsHeartbeat = null
  let ttsPlanGeneration = 0
  let ttsRebuildTimer = null
  let ttsRebuildRequested = false
  let ttsPlaybackBlocked = false
  let ttsPlaybackNotice = ''
  let ttsCacheEpoch = 0
  const ttsOwner = {}

  const ttsIllustLine = /^\[illustration:.+\]$/
  const ttsParagraphs = () => {
    if (!readerContent) return []
    return Array.from(readerContent.querySelectorAll('.reader-paragraph'))
      .map(paragraph => paragraph.dataset.paragraphText || '')
      .filter(Boolean)
  }

  const ttsHighlight = index => {
    if (!readerContent) return
    ttsClearHighlight()
    const paragraph = readerContent.querySelector(`.reader-paragraph[data-tts-index="${Number(index)}"]`)
    if (!paragraph) return
    paragraph.classList.add('tts-active-line')
    paragraph.scrollIntoView({
      behavior: 'smooth',
      block: window.matchMedia('(max-width: 720px)').matches ? 'start' : 'center'
    })
  }

  const ttsClearHighlight = () => {
    if (!readerContent) return
    readerContent.querySelectorAll('.tts-active-line').forEach(el => el.classList.remove('tts-active-line'))
  }

  const ttsDetectEmotion = (text) => {
    const E = (pitch, rate) => {
      if (/亲爱|宝贝|甜蜜|深情|挂念|想念|思念|牵挂|柔情|眷恋|缠绵|爱你|喜欢你|在乎你|舍不得/.test(text)) return 'affectionate'
      if (/温柔|亲切|和蔼|慈祥|和善|慈爱|柔和|暖意|关切|心疼|怜惜|疼爱|宠溺|轻声|低语|耳语|呢喃/.test(text)) return 'gentle'
      if (/冷笑|嗤|鄙夷|不屑|嘲讽|讥|阴阳怪气|挖苦|讽刺|揶揄|滑稽|逗趣/.test(text)) return 'humorous'
      if (/神秘|诡异|幽暗|迷雾|悄悄|压低.*声|别出声|阴冷|未知|谜/.test(text)) return 'mysterious'
      if (/严肃|认真|郑重|正色|凝重|肃穆|庄严|命令|必须|记住/.test(text)) return 'solemn'
      if (/希望|期待|期盼|盼望|憧憬|太好了|好极了|开心|高兴|欢喜|欣喜/.test(text)) return 'joyful'
      if (/疲惫|疲倦|累|乏|困|没力气|精疲力竭|筋疲力尽|有气无力|虚弱/.test(text)) return 'weary'
      if (/紧张|忐忑|催促|赶紧|来不及|快跑|屏住呼吸|手心出汗/.test(text)) return 'tense'
      const profiles = {
        '+8Hz:15': 'excited', '+6Hz:15': 'excited', '+4Hz:18': 'tense',
        '+6Hz:10': 'angry', '+4Hz:12': 'fearful', '+3Hz:8': 'fearful',
        '-5Hz:-18': 'sad', '+4Hz:10': 'joyful', '+3Hz:5': 'humorous',
        '-2Hz:-5': 'mysterious', '-4Hz:-15': 'weary', '-3Hz:-10': 'sad',
        '-2Hz:-8': 'gentle', '-5Hz:-20': 'mysterious', '-2Hz:-10': 'affectionate',
        '+1Hz:-5': 'solemn', '-1Hz:-8': 'neutral', '+3Hz:-5': 'solemn',
        '-3Hz:-15': 'weary', '+2Hz:8': 'tense', '+2Hz:5': 'excited',
        '+3Hz:3': 'tense', '+0Hz:0': 'neutral'
      }
      return profiles[`${pitch}:${rate}`] || 'neutral'
    }
    if (/大喊|大叫|嘶吼|嘶喊|声嘶力竭|扯着嗓子|放声|吼叫|嚎叫|尖叫|撕心裂肺.*喊|拼命.*喊|冲.*吼/.test(text))
      return E('+8Hz', 15)
    if (/吼|怒|骂|咆哮|暴怒|斥|喝道|呵斥|怒吼|怒骂|怒喝|大骂|厉声|咬牙切齿|气急败坏|火冒三丈|暴跳如雷|拍桌|摔|怒视|怒斥|恼怒|震怒|盛怒|狂怒|激怒|愤怒|愤然|忿忿|恨恨|咬牙|握拳|一拳|砸|踢|踹|掀|怒目|瞪|暴起|炸了|妈的|他妈|混蛋|王八|滚|去死|找死|狗东西|畜生/.test(text) || /[！!]{2,}/.test(text))
      return E('+6Hz', 10)
    if (/魂飞魄散|吓死|死定了|完了完了|救命|不要过来|求你|饶命|跪|恐惧到.*说不出|瘫倒|瘫软|石化|呆住|魂不附体|三魂七魄/.test(text))
      return E('+4Hz', 12)
    if (/害怕|恐惧|颤抖|发抖|哆嗦|战栗|惊恐|吓|心惊|胆寒|毛骨悚然|不寒而栗|瑟缩|骇|慌张|慌乱|惊慌|心虚|提心吊胆|惶恐|汗毛竖|头皮发麻|脊背发凉|冷汗|浑身僵|腿软|血液凝固|窒息|喘不上|不敢动|不敢看|不敢说/.test(text))
      return E('+3Hz', 8)
    if (/哭|泪|悲伤|痛哭|呜咽|抽泣|啜泣|哽咽|泣不成声|潸然泪下|眼眶.*红|红了眼|鼻酸|酸楚|凄凉|心碎|难过|伤心|悲痛|含泪|流泪|泪水|眼泪|湿润|模糊.*眼|哭泣|恸|痛心|心痛|揪心|撕心裂肺|肝肠寸断|悲恸/.test(text))
      return E('-5Hz', -18)
    if (/哈哈|笑|嘻嘻|呵呵|开心|高兴|乐|欢喜|兴奋|激动|欣喜|得意|畅快|愉悦|雀跃|喜出望外|眉开眼笑|笑逐颜开|太好了|好极了|真棒|哇|帅|厉害|牛|爽|痛快|过瘾|漂亮|精彩|妙/.test(text) && !/冷笑|苦笑|惨笑|嘲笑|讪笑|皮笑肉不笑|假笑|干笑/.test(text))
      return E('+4Hz', 10)
    if (/嫉妒|眼红|羡慕|酸|凭什么|为什么是.*不是|不公平|偏心|吃醋|红了眼/.test(text) && !/嗤|鄙夷/.test(text))
      return E('+2Hz', 5)
    if (/冷笑|嗤|鄙夷|不屑|嘲讽|讥|嘲笑|讪笑|皮笑肉不笑|阴阳怪气|撇嘴|哼|嗤之以鼻|挖苦|讽刺|奚落|揶揄|轻蔑|看不起|呸|切|哟|可笑|笑话|蠢|白痴|废物|垃圾|也配/.test(text))
      return E('+3Hz', 5)
    if (/冷冷|冰冷|寒声|阴沉|阴冷|冷淡|疏离|漠然|淡漠|无情|绝情|狠心|心如铁石|别碍事|少管|与你无关|关你什么事|懒得|不稀罕|爱谁谁/.test(text))
      return E('-2Hz', -5)
    if (/尴尬|羞|窘|脸红|不好意思|难为情|局促|手足无措|面红耳赤|赧然|忸怩|臊|羞赧|涨红|害臊|别看我|别说了|讨厌|人家|哎呀/.test(text))
      return E('+2Hz', 5)
    if (/叹息|无奈|沮丧|颓然|失落|叹气|苦笑|惆怅|萧索|落寞|意兴阑珊|垂头丧气|心灰意冷|黯然|怅然|郁郁|算了|罢了|认命|没办法|也只能|有什么办法|能怎样|唉|哎/.test(text))
      return E('-4Hz', -15)
    if (/抱歉|对不起|惭愧|内疚|愧疚|过意不去|自责|悔恨|懊悔|歉意|亏欠|都怪我|是我的错|我不该|后悔|早知道/.test(text))
      return E('-3Hz', -10)
    if (/理解|懂你|辛苦了|不容易|受苦了|心疼你|感同身受|能体会|可以理解|别难过|会好的|在这里|陪着你|有我在/.test(text))
      return E('-2Hz', -8)
    if (/轻声|低语|悄悄|耳语|私语|喃喃|呢喃|嘟囔|咕哝|小声|附耳|贴着耳朵|凑近|压低.*声|嘘|别出声|安静/.test(text) || /[轻低柔温]声/.test(text))
      return E('-5Hz', -20)
    if (/温柔|亲切|和蔼|慈祥|和善|和气|慈爱|柔和|暖意|关切|心疼|怜惜|疼爱|宠溺|摸.*头|拍.*肩|搂|拥|抱|牵.*手|握.*手|替.*擦/.test(text))
      return E('-2Hz', -10)
    if (/严肃|认真|郑重|正色|凝重|肃穆|庄严|一本正经|严厉|端正|正经|肃然|不准|禁止|不许|不要|住手|够了|闭嘴|给我|命令|必须|听清楚|记住/.test(text))
      return E('+1Hz', -5)
    if (/平静|冷静|从容|淡定|镇定|不动声色|波澜不惊|若无其事|泰然|沉着|坦然|安然|无所谓|随便|都行|没关系|不要紧/.test(text))
      return E('-1Hz', -8)
    if (/亲爱|宝贝|甜蜜|深情|挂念|想念|思念|牵挂|恋恋不舍|柔情|情深|眷恋|缠绵|爱你|喜欢你|在乎你|等你|陪你|舍不得|不要走|别离开/.test(text))
      return E('-2Hz', -10)
    if (/希望|期待|期盼|盼望|憧憬|指望|终于.*了|就快|马上就|有希望|有救|有机会|来得及|还不晚|一定能|一定会|相信|我们能/.test(text))
      return E('+3Hz', 5)
    if (/惊讶|诧异|愕然|目瞪口呆|瞠目|吃惊|震惊|瞪大|难以置信|不敢相信|万万没想到|怎么可能|竟然|不会吧|你说什么|真的假的|什么|啊[？?!！]/.test(text))
      return E('+6Hz', 15)
    if (/催促|快点|赶紧|急|来不及|快跑|快走|抓紧|紧迫|火急|十万火急|跑|逃|快|冲|别磨蹭|再不.*就/.test(text))
      return E('+4Hz', 18)
    if (/得意|嘚瑟|傲|不可一世|高高在上|居高临下|洋洋|沾沾自喜|自信|胸有成竹|十拿九稳|笃定|把握|拿下/.test(text))
      return E('+3Hz', -5)
    if (/疲惫|疲倦|累|乏|困|没力气|精疲力竭|筋疲力尽|有气无力|虚弱|勉强|撑.*不住/.test(text))
      return E('-3Hz', -15)
    if (/紧张|忐忑|七上八下|心跳加速|手心出汗|屏住呼吸|大气不敢出|绷|攥|捏|死死/.test(text))
      return E('+2Hz', 8)
    if (/[！!]/.test(text))
      return E('+2Hz', 5)
    if (/[？?]/.test(text))
      return E('+3Hz', 3)
    return E('+0Hz', 0)
  }

  const ttsEmotionForText = text => state.reader.ttsEmotion === 'auto'
    ? ttsDetectEmotion(text)
    : state.reader.ttsEmotion

  const ttsBuildUrl = (text, voice, emotion = ttsEmotionForText(text)) => {
    const cleaned = text.replace(/[——]+/g, '，').replace(/[“”"「」『』【】\[\]［］]/g, '')
    const baseRatePct = Math.round((state.reader.ttsRate - 1) * 100)
    const rate = baseRatePct >= 0 ? `+${baseRatePct}%` : `${baseRatePct}%`
    const params = new URLSearchParams({ text: cleaned, voice, rate, emotion })
    return `/api/v1/tts/speak?${params}`
  }

  const ttsEnsureAudio = () => {
    if (!ttsAudioEl) {
      ttsAudioEl = new Audio()
      ttsAudioEl.preload = 'auto'
    }
    return ttsAudioEl
  }

  const ttsCachePrefetch = url => {
    if (ttsCache.has(url) || ttsCachePromises.has(url)) return
    const epoch = ttsCacheEpoch
    const p = fetch(url, { cache: 'force-cache' })
      .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.arrayBuffer() })
      .then(() => {
        if (epoch !== ttsCacheEpoch || ttsCachePromises.get(url) !== p) return null
        ttsCache.set(url, true)
        ttsCachePromises.delete(url)
        return true
      })
      .catch(e => {
        if (epoch === ttsCacheEpoch && ttsCachePromises.get(url) === p) {
          console.error('[TTS] cache fail:', url.substring(0, 80), e)
          ttsCachePromises.delete(url)
          ttsCache.set(url, null)
        }
        return null
      })
    ttsCachePromises.set(url, p)
  }

  const ttsClearCache = urlSet => {
    ttsCacheEpoch++
    ttsCachePromises.clear()
    if (urlSet) {
      for (const url of urlSet) ttsCache.delete(url)
    } else {
      ttsCache.clear()
    }
  }

  const ttsAllQuoteRe = /“[^”]*”|"[^"]*"|「[^」]*」|『[^』]*』|【[^】]*】|\[[^\]]*\]|［[^］]*］/g

  const ttsRoleColonIndex = line => {
    const match = line.match(/^\s*[^，。！？；：:\[\]［］【】“”「」『』]{1,15}[：:]\s*\S{2,}/)
    if (!match) return -1
    const chinese = line.indexOf('：')
    const ascii = line.indexOf(':')
    if (chinese < 0) return ascii
    if (ascii < 0) return chinese
    return Math.min(chinese, ascii)
  }

  const ttsIsDialogueLine = line => {
    if (new RegExp(ttsAllQuoteRe.source).test(line)) return true
    return ttsRoleColonIndex(line) > 0
  }

  const ttsNonNameWords = new Set([
    '不过', '但是', '然而', '可是', '只是', '于是', '因此', '所以', '如果', '虽然',
    '忽然', '突然', '接着', '随后', '此时', '这时', '那时', '一时', '顿时', '刹那',
    '其实', '果然', '毕竟', '几乎', '似乎', '仿佛', '大概', '或许', '也许', '同时',
    '他们', '她们', '我们', '你们', '大家', '众人', '所有', '一切', '这些', '那些',
    '一个', '两个', '一些', '一声', '一句', '一段', '什么', '怎么', '哪里', '那边',
    '说道', '笑道', '喊道', '叫道', '吼道', '骂道', '问道', '答道', '叹道', '嚷道',
    '哼道', '喝道', '嗤道', '怒道', '冷道', '淡道', '轻道', '急道', '忙道', '苦道',
    '低声', '高声', '冷笑', '苦笑', '大声', '轻声', '转头', '回头', '抬头', '低头',
    '看到', '听到', '想到', '见到', '看见', '不禁', '只见', '赫然', '猛然', '忽而',
    '对方', '那人', '这人', '此人', '旁边', '身边', '前方', '后方', '眼前', '身后',
    '已经', '正在', '依然', '仍然', '居然', '竟然', '终于', '马上', '立刻', '当即',
    '开始', '继续', '停止', '结束', '完成', '准备', '发现', '感觉', '知道', '明白',
    '一边', '另一', '每个', '没有', '只有', '就是', '不是', '还是', '而是', '正是',
    '心想', '暗想', '心道', '暗道', '自言', '自语', '不由', '不免', '不觉',
    '摇头', '点头', '皱眉', '挑眉', '眯眼', '瞪眼', '张嘴', '闭嘴', '咬牙',
    '当时', '今日', '此刻', '那天', '昨天', '今天', '明天', '方才', '刚才', '先前',
    '面对', '看着', '望着', '盯着', '瞥见', '觑见', '听见', '遇见', '碰见', '撞见',
    '清寒', '最美', '活脱', '自嘲', '近前', '近旁', '颔首', '勾唇', '并肩', '上辈',
    '笑吟', '瓮声', '进饭', '口中', '以指', '虽说', '后头', '都得', '见他', '这六',
    '说起', '脱口', '可此', '祖母', '今日', '但不', '对此', '小吏', '会这', '她忍',
    '这一', '则是', '两人', '三年', '哪怕', '尽管', '无论', '不论', '凡是', '除了',
    '如今', '后来', '至少', '因为', '关于', '有些', '走到', '旋即', '语毕', '明明',
    '不知', '不见', '不想', '不能', '不会', '不行', '不去', '不来', '不让', '不敢',
    '余光', '余下', '见状', '正好', '恰好', '来到', '说完', '想来', '看来', '起来',
    '本来', '原来', '出来', '过来', '下来', '上来', '回来', '进来', '起身', '出去',
    '原本', '果真', '难怪', '难道', '莫非', '可见', '竟是', '倒是', '显然', '若非',
    '随即', '继而', '进而', '尔后', '之后', '以后', '之前', '以前', '之间', '期间',
  ])
  const ttsNonNameEndChars = new Set('递恭的了着过得地么吗呢吧啊呀哦嗯噢唉哎嘛喽呗罢将而且及或又也都还再就便即却仍已曾于自从向往对为与和跟同比像按城州院府驿庠县镇村寺殿宫楼阁堂山河湖海道路街巷门窗桥亭')
  const ttsNonNameStartChars = new Set('当那面会此可她他这我你谁哪每又也都就还却便乃若既虽且更最仍但而已将要把被让给从向往在到过经由以为与和跟同比像按如至因走余见有后关旋语原不明岂难莫')

  const ttsExtractCandidates = line => {
    const candidates = new Set()
    const narration = line.replace(ttsAllQuoteRe, '')
    const segments = []
    const re = new RegExp(ttsAllQuoteRe.source, 'g')
    let lastIdx = 0, m
    while ((m = re.exec(line)) !== null) {
      if (m.index > lastIdx) segments.push(line.substring(lastIdx, m.index))
      lastIdx = re.lastIndex
    }
    if (lastIdx < line.length) segments.push(line.substring(lastIdx))
    for (const seg of segments) {
      const trimmed = seg.replace(/^[，。！？、；：\s…—·“”"「」『』【】\[\]［］]+/, '')
      if (!trimmed) continue
      const m2 = trimmed.match(/^([一-鿿]{2})/)
      const m3 = trimmed.match(/^([一-鿿]{3})/)
      if (m2 && !ttsNonNameWords.has(m2[1]) && !ttsNonNameStartChars.has(m2[1][0]) && !ttsNonNameEndChars.has(m2[1][1])) candidates.add(m2[1])
      if (m3 && !ttsNonNameWords.has(m3[1]) && !ttsNonNameWords.has(m3[1].substring(0, 2))
          && !ttsNonNameEndChars.has(m3[1][2]) && !ttsNonNameStartChars.has(m3[1][0])) candidates.add(m3[1])
    }
    const verbPat = /([一-鿿]{2,8})[说道喊叫嚷吼骂笑哼嗤叹问答斥喝呵嘟囔呢喃嘀咕嘶嚎吟冷轻苦怒惊感嘲叱]+[道说]?/g
    let vm
    while ((vm = verbPat.exec(narration)) !== null) {
      const raw = vm[1]
      const c2 = raw.substring(0, 2)
      if (!ttsNonNameWords.has(c2) && !ttsNonNameStartChars.has(c2[0]) && !ttsNonNameEndChars.has(c2[1])) candidates.add(c2)
      if (raw.length >= 3) {
        const c3 = raw.substring(0, 3)
        if (!ttsNonNameWords.has(c3) && !ttsNonNameWords.has(c3.substring(0, 2))
            && !ttsNonNameEndChars.has(c3[2]) && !ttsNonNameStartChars.has(c3[0])) candidates.add(c3)
      }
    }
    return [...candidates]
  }

  const ttsFindSpeaker = (line, speakerMap, nameAliases) => {
    const narration = line.replace(ttsAllQuoteRe, '')
    const names = Object.keys(speakerMap).filter(k => k !== '_aliases').sort((a, b) => b.length - a.length)
    for (const name of names) {
      if (narration.includes(name)) return name
    }
    if (nameAliases) {
      for (const [short, long] of nameAliases) {
        if (narration.includes(short) && speakerMap[long]) return long
      }
    }
    return null
  }

  const ttsFemaleNameChars = '芳兰梅莲莉花玉凤雪月云霞丽美婷娟娜燕清静秀琴琳瑶薇颖慧蝶蓉萍雯珊妮媛莹冰虹蕊珍柔漪婉姝妍茹菲灵纤黛绮韵语欣悠萱瑾璇思依怡晴彤馨曦嫣儿蕾珂雅岚姗琪瑗瑜璐蓓苒珺琬蓁嫚姿妤婕瑄彩翠巧素贞惠淑媚荷苓茉蔓葵棠樱桃杏梨棉绣缨缇绫罗绢纱锦鹃莺燕鸳鸯蝴蝶凰鸾鹂'
  const ttsMaleNameChars = '强伟刚军明龙虎飞志勇杰磊鹏波超涛辰翔宇峰浩亮华昊天锋剑武威壮坤鸿博轩逸远哲铭泽阳诚毅恒煜旭霆骏凯斌彬松柏森权桐栋梁钧钢锐钦铮磐石岩崇琛卓晟烨熠焱麒麟霖瀚渊澈潇骁驰征战戍戎猛彪狄豹鲲鹰隼雄烽铁铜钟锤镇雷霄奉卿怀廷仁义德信礼谦耀荣盛裕邦坚昂尧禹舜'

  const ttsFemaleNameWeight = {
    '婷': 3, '娟': 3, '燕': 3, '莲': 3, '芳': 3, '梅': 3, '凤': 3, '娜': 3, '妍': 3, '姝': 3,
    '嫣': 3, '媛': 3, '妤': 3, '婕': 3, '姗': 3, '蕊': 3, '珺': 3, '瑗': 3, '蓓': 3,
    '莺': 3, '鸾': 3, '凰': 3, '绣': 3, '缨': 3, '鹃': 3, '嫚': 3, '苓': 3,
    '雪': 2, '霞': 2, '丽': 2, '秀': 2, '琴': 2, '瑶': 2, '薇': 2, '颖': 2,
    '漪': 2, '婉': 2, '茹': 2, '菲': 2, '黛': 2, '韵': 2, '馨': 2, '曦': 2,
    '萱': 2, '瑾': 2, '璇': 2, '蕾': 2, '琪': 2, '璐': 2, '荷': 2, '茉': 2, '棠': 2,
    '樱': 2, '锦': 2, '绫': 2, '缇': 2, '纱': 2, '蝶': 2, '莹': 2, '珊': 2,
    '清': 1, '静': 1, '冰': 1, '柔': 1, '欣': 1, '怡': 1, '晴': 1, '彤': 1, '语': 1,
    '悠': 1, '思': 1, '依': 1, '月': 1, '云': 1, '灵': 1, '岚': 1, '雅': 1, '珂': 1
  }
  const ttsMaleNameWeight = {
    '刚': 3, '军': 3, '虎': 3, '龙': 3, '武': 3, '壮': 3, '钢': 3, '铮': 3, '骁': 3, '麟': 3,
    '猛': 3, '彪': 3, '豹': 3, '雄': 3, '铁': 3, '征': 3, '戎': 3, '鹰': 3, '隼': 3,
    '强': 2, '伟': 2, '勇': 2, '磊': 2, '鹏': 2, '峰': 2, '锋': 2, '剑': 2, '威': 2, '霆': 2, '骏': 2,
    '飞': 2, '超': 2, '涛': 2, '翔': 2, '浩': 2, '博': 2, '毅': 2, '煜': 2, '烨': 2,
    '雷': 2, '霄': 2, '鲲': 2, '烽': 2, '锤': 2, '镇': 2, '战': 2, '戍': 2, '狄': 2,
    '宇': 1, '辰': 1, '轩': 1, '逸': 1, '远': 1, '哲': 1, '铭': 1, '泽': 1, '阳': 1,
    '晟': 1, '明': 1, '天': 1, '华': 1, '昊': 1, '恒': 1, '凯': 1, '斌': 1, '彬': 1,
    '奉': 1, '卿': 1, '怀': 1, '廷': 1, '仁': 1, '义': 1, '德': 1, '信': 1, '礼': 1,
    '谦': 1, '耀': 1, '荣': 1, '盛': 1, '裕': 1, '邦': 1, '坚': 1, '尧': 1, '禹': 1
  }

  const ttsGuessGender = name => {
    let f = 0, m = 0
    for (const ch of name) {
      f += ttsFemaleNameWeight[ch] || (ttsFemaleNameChars.includes(ch) ? 1 : 0)
      m += ttsMaleNameWeight[ch] || (ttsMaleNameChars.includes(ch) ? 1 : 0)
    }
    if (f > m) return 'female'
    if (m > f) return 'male'
    return null
  }

  const ttsInferContextGender = line => {
    const narration = String(line || '').replace(ttsAllQuoteRe, '')
    if (/她|女孩|女生|女子|女人|少女|姑娘|母亲|妈妈|姐姐|妹妹|妻子|夫人|奶奶|阿姨|女声|女儿|公主|王后|皇后|妃子/.test(narration)) return 'female'
    if (/他|男孩|男生|男子|男人|少年|青年|大汉|父亲|爸爸|哥哥|弟弟|丈夫|先生|爷爷|叔叔|男声|儿子|王子|皇帝/.test(narration)) return 'male'
    return null
  }

  const ttsGenderScore = name => {
    let f = 0, m = 0
    for (const ch of name) {
      f += ttsFemaleNameWeight[ch] || (ttsFemaleNameChars.includes(ch) ? 1 : 0)
      m += ttsMaleNameWeight[ch] || (ttsMaleNameChars.includes(ch) ? 1 : 0)
    }
    return Math.abs(f - m)
  }

  const ttsBuildSpeakerMap = paragraphs => {
    const narrator = state.reader.ttsNarrator || 'mocheng'
    const nameCounts = new Map()
    for (const line of paragraphs) {
      if (!ttsIsDialogueLine(line)) continue
      for (const name of ttsExtractCandidates(line)) {
        nameCounts.set(name, (nameCounts.get(name) || 0) + 1)
      }
    }
    const confirmed = new Set()
    for (const [name, count] of nameCounts) {
      if (count >= 2) confirmed.add(name)
      else if (ttsGuessGender(name) !== null && ttsGenderScore(name) >= 2) confirmed.add(name)
    }
    const nameAliases = new Map()
    const toRemove = new Set()
    const sorted = [...confirmed].sort((a, b) => b.length - a.length)
    for (const long of sorted) {
      if (long.length !== 3) continue
      for (const short of sorted) {
        if (short.length !== 2 || toRemove.has(short)) continue
        if (long.startsWith(short) || long.endsWith(short)) {
          nameAliases.set(short, long)
          toRemove.add(short)
        }
      }
    }
    for (const name of toRemove) confirmed.delete(name)
    const map = {}
    let fIdx = 0, mIdx = 0, uIdx = 0
    const fPool = ttsFemalePool.filter(v => v !== narrator)
    const mPool = ttsMalePool.filter(v => v !== narrator)
    for (const name of confirmed) {
      const gender = ttsGuessGender(name)
      if (gender === 'female') map[name] = fPool[fIdx++ % fPool.length]
      else if (gender === 'male') map[name] = mPool[mIdx++ % mPool.length]
      else {
        map[name] = (uIdx % 2 === 0 ? mPool : fPool)[Math.floor(uIdx / 2) % (uIdx % 2 === 0 ? mPool : fPool).length]
        uIdx++
      }
    }
    map._aliases = nameAliases
    return map
  }

  const TTS_REQUEST_CHAR_LIMIT = 900
  const ttsSplitForRequest = text => {
    let remaining = String(text || '').trim()
    const chunks = []
    while (remaining.length > TTS_REQUEST_CHAR_LIMIT) {
      const windowText = remaining.slice(0, TTS_REQUEST_CHAR_LIMIT)
      let cut = Math.max(
        windowText.lastIndexOf('。'), windowText.lastIndexOf('！'), windowText.lastIndexOf('？'),
        windowText.lastIndexOf('；'), windowText.lastIndexOf('，'), windowText.lastIndexOf(','),
        windowText.lastIndexOf('!'), windowText.lastIndexOf('?')
      ) + 1
      if (cut < Math.floor(TTS_REQUEST_CHAR_LIMIT * .42)) {
        cut = Math.max(windowText.lastIndexOf(' '), windowText.lastIndexOf('　'))
      }
      if (cut < Math.floor(TTS_REQUEST_CHAR_LIMIT * .42)) cut = TTS_REQUEST_CHAR_LIMIT
      chunks.push(remaining.slice(0, cut).trim())
      remaining = remaining.slice(cut).trim()
    }
    if (remaining) chunks.push(remaining)
    return chunks.filter(Boolean)
  }

  const ttsAppendPlan = (plan, text, voice, paraIdx) => {
    for (const chunk of ttsSplitForRequest(text)) {
      const emotion = ttsEmotionForText(chunk)
      plan.push({ url: ttsBuildUrl(chunk, voice, emotion), paraIdx, text: chunk, voice, emotion })
    }
  }

  const ttsBuildChapterPlan = (paragraphs, startIdx = 0) => {
    const mode = state.reader.ttsMode
    const isSmart = mode === 'smart'
    const narrator = state.reader.ttsNarrator || 'mocheng'
    const speakerMap = isSmart ? ttsBuildSpeakerMap(paragraphs) : {}
    const dialoguePool = ttsMandarinPool.filter(v => v !== narrator)
    let unknownIdx = 0
    const plan = []
    for (let i = startIdx; i < paragraphs.length; i++) {
      const line = paragraphs[i]
      if (!isSmart) {
        ttsAppendPlan(plan, line, state.reader.ttsVoice, i)
        continue
      }
      const isDialogue = ttsIsDialogueLine(line)
      let speakerVoice = narrator
      if (isDialogue) {
        const speaker = ttsFindSpeaker(line, speakerMap, speakerMap._aliases)
        if (speaker && speakerMap[speaker]) {
          speakerVoice = speakerMap[speaker]
        } else {
          const gender = ttsInferContextGender(line)
          const pool = gender === 'female' ? ttsFemalePool.filter(v => v !== narrator)
            : gender === 'male' ? ttsMalePool.filter(v => v !== narrator)
              : dialoguePool
          speakerVoice = pool[unknownIdx++ % pool.length]
        }
      }
      const cleanLine = line.replace(/[\s　]+/g, '').replace(/[——…、，。！？；：“”"「」『』【】\[\]［］（）\-]+/g, '')
      if (!cleanLine) continue
      if (!isDialogue) {
        ttsAppendPlan(plan, line, narrator, i)
        continue
      }
      const hasQuotes = new RegExp(ttsAllQuoteRe.source).test(line)
      const segs = []
      if (hasQuotes) {
        const qre = new RegExp('(' + ttsAllQuoteRe.source + ')', 'g')
        let last = 0, match
        while ((match = qre.exec(line)) !== null) {
          if (match.index > last) {
            const before = line.substring(last, match.index).trim()
            if (before && before.replace(/[，。！？、；：\s…—·“”"「」『』【】\[\]［］]+/g, '')) segs.push({ text: before, voice: narrator })
          }
          const inside = match[1].slice(1, -1)
          if (inside.trim() && inside.replace(/[，。！？、；：\s…—·“”"「」『』【】\[\]［］]+/g, '')) segs.push({ text: inside, voice: speakerVoice })
          last = qre.lastIndex
        }
        if (last < line.length) {
          const after = line.substring(last).trim()
          if (after && after.replace(/[，。！？、；：\s…—·“”"「」『』【】\[\]［］]+/g, '')) segs.push({ text: after, voice: narrator })
        }
      } else {
        const colonIdx = ttsRoleColonIndex(line)
        if (colonIdx > 0) {
          const before = line.substring(0, colonIdx).trim()
          const after = line.substring(colonIdx + 1).trim()
          if (before) segs.push({ text: before, voice: narrator })
          if (after) segs.push({ text: after, voice: speakerVoice })
        }
      }
      if (!segs.length) segs.push({ text: line, voice: speakerVoice })
      for (const seg of segs) {
        ttsAppendPlan(plan, seg.text, seg.voice, i)
      }
    }
    const voices = {}
    for (const p of plan) { const v = new URLSearchParams(p.url.split('?')[1]).get('voice'); voices[v] = (voices[v] || 0) + 1 }
    console.log('[TTS] plan built:', plan.length, 'items. speakerMap:', JSON.stringify(speakerMap), 'voices:', JSON.stringify(voices))
    return plan
  }

  const TTS_PREFETCH_AHEAD = 10

  const ttsSettingsSignature = () => JSON.stringify({
    mode: state.reader.ttsMode,
    voice: state.reader.ttsVoice,
    narrator: state.reader.ttsNarrator,
    rate: Number(state.reader.ttsRate).toFixed(1),
    emotion: state.reader.ttsEmotion
  })

  const ttsCacheWindow = fromIdx => {
    const windowItems = ttsChapterPlan.slice(fromIdx, fromIdx + TTS_PREFETCH_AHEAD)
    if (windowItems.length < TTS_PREFETCH_AHEAD && ttsNextChapterPlan.length) {
      windowItems.push(...ttsNextChapterPlan.slice(0, TTS_PREFETCH_AHEAD - windowItems.length))
    }
    const keepUrls = new Set(windowItems.map(item => item.url))
    for (const url of ttsCache.keys()) {
      if (!keepUrls.has(url)) ttsCache.delete(url)
    }
    for (const url of ttsCachePromises.keys()) {
      if (!keepUrls.has(url)) ttsCachePromises.delete(url)
    }
    for (const item of windowItems) {
      ttsCachePrefetch(item.url)
    }
  }

  const ttsStopPlayback = () => {
    if (ttsAudioEl) {
      ttsAudioEl.onended = null
      ttsAudioEl.onerror = null
      ttsAudioEl.pause()
      ttsAudioEl.removeAttribute('src')
      ttsAudioEl.load()
    }
  }

  const ttsModeLabel = () => {
    if (ttsPlaybackBlocked) return ttsPlaybackNotice || (state.reader.ttsMode === 'smart' ? '点击继续智能听书' : '点击继续听书')
    const labels = { smart: '停止智能听书', cantonese: '停止粤语听书', hokkien: '停止闽南语听书' }
    return labels[state.reader.ttsMode] || `停止听书 · ${state.reader.ttsRate}x`
  }

  const ttsUpdateControls = () => {
    if (ttsButton) {
      ttsButton.classList.toggle('active', state.reader.ttsActive)
      ttsButton.textContent = !state.reader.ttsActive ? '听书' : (ttsPlaybackBlocked ? '继续听书' : '听书播放页')
    }
    if (ttsStateBar) {
      ttsStateBar.hidden = !state.reader.ttsActive
      if (state.reader.ttsActive) ttsStateBar.textContent = ttsPlaybackBlocked
        ? ttsModeLabel()
        : `正在听 · ${Number(state.reader.ttsRate || 1).toFixed(1)}x · 打开播放页`
    }
    mobileNav?.classList.toggle('tts-active', state.reader.ttsActive)
    if (state.ttsSession?.active) state.ttsSession.playbackBlocked = ttsPlaybackBlocked
    updateTtsPlayer()
  }

  const ttsIsPlaybackPolicyError = error => ['NotAllowedError', 'AbortError'].includes(String(error?.name || ''))

  const ttsMarkPlaybackBlocked = (error, notice = '') => {
    ttsPlaybackBlocked = true
    ttsPlaybackNotice = notice
    ttsUpdateControls()
    console.warn('[TTS] playback paused for retry:', error)
  }

  const ttsPendingPlanForCurrentSettings = () => {
    const pending = state.ttsPendingPlan
    if (!pending || pending.signature !== ttsSettingsSignature()) return null
    if (String(pending.chapterId) !== String(requestedChapterId)) return null
    if (!Array.isArray(pending.items) || !pending.items.length) return null
    return pending
  }

  const ttsChapterEnd = async () => {
    if (state.reader.ttsActive && ttsFollowingChapterId) {
      const keepReaderInSync = Boolean(state.ttsSession?.active && !state.ttsSession.detached)
      if (!ttsNextChapterPlan.length || String(ttsNextChapterId) !== String(ttsFollowingChapterId)) {
        await ttsPrefetchNextChapter()
      }
      if (!state.reader.ttsActive || !ttsNextChapterPlan.length) {
        ttsMarkPlaybackBlocked(new Error('next chapter unavailable'), '下一章加载失败，点击重试')
        return
      }
      const enteringChapterId = String(ttsNextChapterId)
      ttsPrevUrls = new Set(ttsChapterPlan.map(item => item.url))
      ttsChapterPlan = ttsNextChapterPlan
      ttsPlanIndex = 0
      ttsFollowingChapterId = ttsNextChapterFollowingId
      state.ttsSession.chapterId = enteringChapterId
      state.ttsSession.chapterTitle = ttsNextChapterTitle
      const nextPosition = catalog.chapters.findIndex(item => String(item.id) === enteringChapterId)
      state.ttsSession.chapterNumber = nextPosition >= 0 ? nextPosition + 1 : state.ttsSession.chapterNumber + 1
      state.ttsSession.chapterCount = catalog.chapters.length
      state.ttsSession.contextItems = ttsChapterPlan.map(item => item.text || '')
      state.ttsSession.paragraphIndex = 0
      state.ttsSession.itemIndex = 0
      state.ttsSession.returnPath = contextualHref(`/books/${requestedBookId}/chapters/${enteringChapterId}`)
      ttsNextChapterPlan = []
      ttsNextChapterSignature = ''
      ttsNextChapterId = null
      ttsNextChapterFollowingId = null
      ttsNextChapterTitle = ''
      ttsNextChapterCached = false
      state.ttsPendingPlan = null
      state.ttsContinueOnLoad = false
      saveTtsCheckpoint(state.ttsSession)
      updateGlobalTtsReturn()
      ttsCacheWindow(1)
      ttsPlayItem(0)
      ttsPrefetchNextChapter()
      if (keepReaderInSync) {
        requestAnimationFrame(() => navigateInApp(contextualHref(`/books/${requestedBookId}/chapters/${enteringChapterId}`)))
      }
    } else {
      stopTTS()
    }
  }

  const ttsUpdateMediaSession = (paraIdx) => {
    if (!('mediaSession' in navigator)) return
    const title = state.ttsSession?.chapterTitle || chapter?.title || '听书'
    const bookTitle = state.ttsSession?.bookTitle || chapter?.book?.title || ''
    navigator.mediaSession.metadata = new MediaMetadata({
      title: title,
      artist: bookTitle,
      album: 'OOHStory 听书',
    })
    navigator.mediaSession.setActionHandler('play', () => ttsResumePlayback())
    navigator.mediaSession.setActionHandler('pause', () => {
      if (ttsAudioEl) ttsAudioEl.pause()
      ttsUpdateControls()
    })
    navigator.mediaSession.setActionHandler('stop', () => stopTTS())
    navigator.mediaSession.setActionHandler('previoustrack', () => {
      if (ttsPlanIndex > 0) ttsPlayItem(ttsPlanIndex - 1)
    })
    navigator.mediaSession.setActionHandler('nexttrack', () => {
      if (ttsPlanIndex < ttsChapterPlan.length - 1) ttsPlayItem(ttsPlanIndex + 1)
    })
  }

  const ttsPlayItem = (idx, retryCount = 0) => {
    if (!state.reader.ttsActive || idx >= ttsChapterPlan.length) {
      if (idx >= ttsChapterPlan.length) ttsChapterEnd()
      return
    }
    const generation = ttsPlanGeneration
    const item = ttsChapterPlan[idx]
    const audio = ttsEnsureAudio()
    ttsCacheWindow(idx + 1)
    if (!ttsNextChapterCached && idx > ttsChapterPlan.length * 0.5 && ttsFollowingChapterId) {
      ttsNextChapterCached = true
      ttsPrefetchNextChapter()
    }
    ttsParagraphIndex = item.paraIdx
    ttsHighlight(item.paraIdx)
    ttsPlanIndex = idx
    if (state.ttsSession?.active) {
      state.ttsSession.paragraphIndex = item.paraIdx
      state.ttsSession.itemIndex = idx
      state.ttsSession.itemCount = ttsChapterPlan.length
      state.ttsSession.currentText = item.text || ''
      state.ttsSession.contextItems = ttsChapterPlan.map(planItem => planItem.text || '')
      state.ttsSession.currentEmotion = item.emotion || 'neutral'
      state.ttsSession.playbackBlocked = false
      state.ttsSession.returnPath = contextualHref(`/books/${requestedBookId}/chapters/${state.ttsSession.chapterId}`)
      state.ttsSession.onParagraph?.(item.paraIdx)
      saveTtsCheckpoint(state.ttsSession)
      updateGlobalTtsReturn()
      updateTtsPlayer()
    }
    ttsUpdateMediaSession(item.paraIdx)
    // Keep media on a same-origin URL. Safari PWA may reject object URLs for
    // sequential audio, while this endpoint is already HTTP-cached for one hour.
    audio.src = item.url
    let failed = false
    const advanceAfterFailure = error => {
      if (failed || !state.reader.ttsActive || generation !== ttsPlanGeneration) return
      failed = true
      if (ttsIsPlaybackPolicyError(error)) {
        ttsMarkPlaybackBlocked(error)
        return
      }
      if (retryCount < 6) {
        console.warn('[TTS] transient audio failure, retrying same item', idx, retryCount + 1)
        window.setTimeout(() => {
          if (state.reader.ttsActive && generation === ttsPlanGeneration) ttsPlayItem(idx, retryCount + 1)
        }, Math.min(5000, 400 * (2 ** retryCount)))
        return
      }
      console.warn('[TTS] audio failed at', idx, error)
      ttsMarkPlaybackBlocked(error, '音频加载失败，点击重试')
    }
    audio.onended = () => {
      if (!state.reader.ttsActive || generation !== ttsPlanGeneration) return
      if (ttsRebuildRequested) {
        const following = ttsChapterPlan[idx + 1]
        const resumeIdx = following?.paraIdx === item.paraIdx ? item.paraIdx : item.paraIdx + 1
        ttsRebuildActivePlan(resumeIdx)
        return
      }
      const nextIdx = idx + 1
      if (nextIdx >= ttsChapterPlan.length) { ttsChapterEnd(); return }
      ttsPlayItem(nextIdx)
    }
    audio.onerror = () => advanceAfterFailure(new Error('audio element error'))
    try {
      const playPromise = audio.play()
      Promise.resolve(playPromise).then(() => {
        if (!state.reader.ttsActive || generation !== ttsPlanGeneration) return
        ttsPlaybackBlocked = false
        ttsPlaybackNotice = ''
        ttsUpdateControls()
      }).catch(advanceAfterFailure)
    } catch (error) {
      advanceAfterFailure(error)
    }
    console.log('[TTS] playing', idx, '/', ttsChapterPlan.length, 'para=' + item.paraIdx)
  }

  const ttsPrefetchNextChapter = async () => {
    const chapterId = ttsFollowingChapterId
    if (!chapterId) return
    const signature = ttsSettingsSignature()
    try {
      const data = await api(`/api/v1/books/${requestedBookId}/chapters/${chapterId}`)
      if (!data?.content || !state.reader.ttsActive || signature !== ttsSettingsSignature()) return
      const text = (data.content || '').replace(/\r\n/g, '\n')
      const ilRe = /^\[illustration:.+\]$/
      const paras = text.split('\n').filter(l => l.trim().length > 0 && !ilRe.test(l))
      ttsNextChapterPlan = ttsBuildChapterPlan(paras, 0)
      ttsNextChapterSignature = signature
      ttsNextChapterId = chapterId
      ttsNextChapterFollowingId = data.next_id ?? null
      ttsNextChapterTitle = data.title || data.display_title || '下一章'
      ttsCacheWindow(ttsPlanIndex + 1)
    } catch { /* ignore */ }
  }

  const stopTTS = ({ preservePending = false } = {}) => {
    if (ttsHeartbeat) { clearInterval(ttsHeartbeat); ttsHeartbeat = null }
    if (ttsRebuildTimer) { clearTimeout(ttsRebuildTimer); ttsRebuildTimer = null }
    ttsRebuildRequested = false
    ttsPlaybackBlocked = false
    ttsPlaybackNotice = ''
    ttsPlanGeneration++
    if (preservePending) {
      // The current item has ended already. Detach the old chapter callbacks,
      // but preserve the unlocked Audio element for seamless next-chapter play.
      if (ttsAudioEl) {
        ttsAudioEl.onended = null
        ttsAudioEl.onerror = null
      }
    } else {
      ttsStopPlayback()
    }
    const pending = preservePending
      && state.ttsPendingPlan?.signature === ttsSettingsSignature()
      && Array.isArray(state.ttsPendingPlan?.items)
      && state.ttsPendingPlan.items.length
      ? state.ttsPendingPlan
      : null
    if (!preservePending) {
      state.ttsPendingPlan = null
      state.ttsContinueOnLoad = false
    } else if (!pending) {
      state.ttsPendingPlan = null
    }
    const keepUrls = pending ? new Set(pending.items.map(item => item.url)) : null
    if (keepUrls) {
      for (const url of ttsCache.keys()) {
        if (!keepUrls.has(url)) ttsCache.delete(url)
      }
      for (const url of ttsCachePromises.keys()) {
        if (!keepUrls.has(url)) ttsCachePromises.delete(url)
      }
    } else {
      ttsClearCache()
    }
    ttsChapterPlan = []
    ttsPlanIndex = 0
    ttsNextChapterPlan = []
    ttsNextChapterSignature = ''
    ttsNextChapterId = null
    ttsNextChapterFollowingId = null
    ttsNextChapterTitle = ''
    ttsNextChapterCached = false
    ttsPrevUrls = new Set()
    state.reader.ttsActive = false
    ttsParagraphIndex = -1
    ttsClearHighlight()
    saveReaderSettings()
    ttsUpdateControls()
    if (state.ttsController?.owner === ttsOwner) state.ttsController = null
    if (!preservePending) {
      const wasDetached = Boolean(state.ttsSession?.detached)
      state.ttsSession = null
      closeTtsPlayer()
      updateGlobalTtsReturn()
      if (wasDetached) state.readingActivity?.stop?.(true)
    }
    if ('mediaSession' in navigator) {
      navigator.mediaSession.metadata = null
      navigator.mediaSession.setActionHandler('play', null)
      navigator.mediaSession.setActionHandler('pause', null)
      navigator.mediaSession.setActionHandler('stop', null)
      navigator.mediaSession.setActionHandler('previoustrack', null)
      navigator.mediaSession.setActionHandler('nexttrack', null)
    }
  }

  const ttsFirstVisibleParagraph = () => {
    if (!readerContent) return 0
    const stageRect = stage?.getBoundingClientRect() || { top: 0, bottom: innerHeight, left: 0, right: innerWidth }
    const visible = Array.from(readerContent.querySelectorAll('.reader-paragraph'))
      .map(paragraph => ({ paragraph, rect: paragraph.getBoundingClientRect() }))
      .filter(({ rect }) => rect.bottom > stageRect.top + 4 && rect.top < stageRect.bottom
        && rect.right > stageRect.left && rect.left < stageRect.right)
      .sort((a, b) => a.rect.top - b.rect.top)
    if (visible.length) {
      return Math.max(0, Number(visible[0].paragraph.dataset.ttsIndex) || 0)
    }
    const top = Math.max(0, stageRect.top) + 8
    const left = Math.max(0, stageRect.left) + Math.min(72, Math.max(24, stageRect.width * .12))
    const points = [[left, top], [innerWidth / 2, top], [innerWidth / 2, innerHeight / 2]]
    for (const [x, y] of points) {
      const paragraph = document.elementFromPoint(x, y)?.closest?.('.reader-paragraph')
      if (paragraph && readerContent.contains(paragraph)) {
        return Math.max(0, Number(paragraph.dataset.ttsIndex) || 0)
      }
    }
    if (currentParagraphHint >= 0) {
      const hinted = readerContent.querySelector(`.reader-paragraph[data-tts-index="${currentParagraphHint}"]`)
      const rect = hinted?.getBoundingClientRect()
      if (rect && rect.bottom > stageRect.top && rect.top < stageRect.bottom && rect.right > stageRect.left && rect.left < stageRect.right) {
        return currentParagraphHint
      }
    }
    for (const paragraph of readerContent.querySelectorAll('.reader-paragraph')) {
      const rect = paragraph.getBoundingClientRect()
      if (rect.bottom > stageRect.top + 8 && rect.top < stageRect.bottom && rect.right > stageRect.left && rect.left < stageRect.right) {
        return Math.max(0, Number(paragraph.dataset.ttsIndex) || 0)
      }
    }
    return 0
  }

  const ttsRebuildActivePlan = startParagraph => {
    if (!state.reader.ttsActive) return
    const startIdx = Math.max(0, Number.isInteger(startParagraph)
      ? startParagraph
      : (ttsParagraphIndex >= 0 ? ttsParagraphIndex : ttsFirstVisibleParagraph()))
    const audio = ttsEnsureAudio()
    audio.onended = null
    audio.onerror = null
    ttsRebuildRequested = false
    ttsPlaybackBlocked = false
    ttsPlaybackNotice = ''
    ttsPlanGeneration++
    ttsClearCache()
    state.ttsPendingPlan = null
    state.ttsContinueOnLoad = false
    ttsChapterPlan = ttsBuildChapterPlan(ttsParagraphs(), startIdx)
    ttsPlanIndex = 0
    ttsNextChapterPlan = []
    ttsNextChapterSignature = ''
    ttsNextChapterCached = false
    ttsCacheWindow(1)
    if (ttsChapterPlan.length) ttsPlayItem(0)
  }

  const ttsScheduleRebuild = () => {
    if (state.ttsController?.active && state.ttsController.owner !== ttsOwner) {
      state.ttsController.rebuild?.()
      return
    }
    if (!state.reader.ttsActive) return
    if (ttsRebuildTimer) clearTimeout(ttsRebuildTimer)
    ttsRebuildTimer = null
    ttsRebuildRequested = true
    const audio = ttsEnsureAudio()
    if (audio.paused || audio.ended || !audio.src || ttsPlaybackBlocked) {
      ttsRebuildActivePlan()
    }
  }

  const startTTS = (startParagraph = null) => {
    stopAutoReading()
    const explicitStart = Number.isInteger(startParagraph)
    const pending = explicitStart ? null : ttsPendingPlanForCurrentSettings()
    if (state.ttsController && state.ttsController.owner !== ttsOwner) {
      state.ttsController.stop({ preservePending: Boolean(pending) })
    } else {
      stopTTS({ preservePending: Boolean(pending) })
    }
    state.reader.ttsActive = true
    ttsPlaybackBlocked = false
    ttsRebuildRequested = false
    ttsPlaybackNotice = ''
    saveReaderSettings()
    ttsEnsureAudio()
    state.ttsSession = {
      active: true,
      detached: false,
      bookId: String(requestedBookId),
      chapterId: String(requestedChapterId),
      paragraphIndex: explicitStart ? Math.max(0, startParagraph) : 0,
      itemIndex: 0,
      itemCount: 1,
      currentText: '',
      currentEmotion: 'neutral',
      bookTitle: chapter?.book?.title || '',
      chapterTitle: chapter?.title || '',
      chapterNumber: chapterPosition + 1,
      chapterCount: catalog.chapters.length,
      contextItems: [],
      coverUrl: chapter?.book?.cover_url || `/api/v1/books/${requestedBookId}/cover`,
      playbackBlocked: false,
      playerOpen: false,
      returnPath: contextualHref(`/books/${requestedBookId}/chapters/${requestedChapterId}`),
      onParagraph: null
    }
    state.ttsController = {
      owner: ttsOwner,
      get active() { return state.reader.ttsActive },
      stop: options => stopTTS(options),
      resume: () => ttsResumePlayback(),
      pause: () => {
        if (ttsAudioEl && !ttsAudioEl.paused) ttsAudioEl.pause()
        ttsUpdateControls()
      },
      previous: () => { if (ttsPlanIndex > 0) ttsPlayItem(ttsPlanIndex - 1) },
      next: () => { if (ttsPlanIndex < ttsChapterPlan.length - 1) ttsPlayItem(ttsPlanIndex + 1) },
      setRate: value => {
        state.reader.ttsRate = Number(value)
        saveReaderSettings()
        ttsScheduleRebuild()
        ttsUpdateControls()
      },
      setMode: value => {
        state.reader.ttsMode = value
        saveReaderSettings()
        ttsScheduleRebuild()
        ttsUpdateControls()
      },
      setEmotion: value => {
        if (!Object.prototype.hasOwnProperty.call(ttsEmotionModes, value)) return
        state.reader.ttsEmotion = value
        saveReaderSettings()
        ttsScheduleRebuild()
        ttsUpdateControls()
      },
      rebuild: () => ttsScheduleRebuild(),
      detach() {
        if (!state.ttsSession?.active) return
        state.ttsSession.detached = true
        state.ttsSession.onParagraph = null
        updateGlobalTtsReturn()
      },
      attach(onParagraph) {
        if (!state.ttsSession?.active) return
        state.ttsSession.detached = false
        state.ttsSession.onParagraph = onParagraph
        onParagraph?.(state.ttsSession.paragraphIndex)
        updateGlobalTtsReturn()
      }
    }
    updateGlobalTtsReturn()
    ttsUpdateControls()
    if (pending) {
      ttsChapterPlan = pending.items
      state.ttsPendingPlan = null
    } else {
      const paragraphs = ttsParagraphs()
      const checkpoint = explicitStart ? null : readTtsCheckpoint(requestedBookId, requestedChapterId)
      const startIdx = explicitStart
        ? Math.max(0, startParagraph)
        : Math.max(0, Number(checkpoint?.paragraphIndex ?? ttsFirstVisibleParagraph()) || 0)
      console.log('[TTS] mode:', state.reader.ttsMode, 'narrator:', state.reader.ttsNarrator, 'paragraphs:', paragraphs.length, 'startIdx:', startIdx)
      ttsChapterPlan = ttsBuildChapterPlan(paragraphs, startIdx)
      state.ttsSession.paragraphIndex = startIdx
    }
    state.ttsSession.contextItems = ttsChapterPlan.map(item => item.text || '')
    ttsClearCache(ttsPrevUrls)
    ttsPrevUrls = new Set()
    ttsNextChapterCached = false
    ttsNextChapterSignature = ''
    if (ttsHeartbeat) clearInterval(ttsHeartbeat)
    ttsHeartbeat = setInterval(() => {
      if (!state.reader.ttsActive) { clearInterval(ttsHeartbeat); ttsHeartbeat = null }
    }, 5000)
    ttsCacheWindow(1)
    if (state.reader.ttsActive) ttsPlayItem(0)
  }

  const ttsResumePlayback = () => {
    if (!state.reader.ttsActive || !ttsChapterPlan.length) return
    const retryCurrentItem = ttsPlaybackBlocked
    ttsPlaybackBlocked = false
    ttsPlaybackNotice = ''
    ttsUpdateControls()
    const audio = ttsEnsureAudio()
    if (!retryCurrentItem && audio.src && audio.paused && !audio.ended) {
      Promise.resolve(audio.play()).then(() => {
        ttsPlaybackBlocked = false
        ttsUpdateControls()
      }).catch(error => ttsMarkPlaybackBlocked(error))
      return
    }
    ttsPlayItem(Math.max(0, ttsPlanIndex))
  }

  const openTTS = () => {
    if (state.ttsController?.active && state.ttsController.owner !== ttsOwner) {
      openTtsPlayer()
      return
    }
    if (!state.reader.ttsActive) startTTS()
    else if (ttsPlaybackBlocked) ttsResumePlayback()
    openTtsPlayer()
  }

  desktopProgressFill = node('i')
  desktopProgressText = node('span', { class: 'reader-toolbar-progress-text', text: '0.0%' })
  fontSizeDisplay = node('span', { class: 'reader-toolbar-font-size', text: String(state.reader.size) })
  const desktopDayNightBtn = node('button', {
    class: 'reader-toolbar-btn', type: 'button',
    text: state.reader.colorScheme === 'night' ? '☀' : '☽',
    title: state.reader.colorScheme === 'night' ? '日间模式' : '夜间模式',
    onclick: () => {
      state.reader.colorScheme = state.reader.colorScheme === 'night' ? 'day' : 'night'
      desktopDayNightBtn.textContent = state.reader.colorScheme === 'night' ? '☀' : '☽'
      desktopDayNightBtn.title = state.reader.colorScheme === 'night' ? '日间模式' : '夜间模式'
      dayNightButton.textContent = state.reader.colorScheme === 'night' ? '日间' : '夜间'
      saveReaderSettings()
    }
  })
  const sidebarToggle = node('button', {
    class: 'reader-toolbar-btn', type: 'button', title: '收起/展开目录', text: '☰',
    onclick: () => {
      const shell = app.querySelector('.reader-shell')
      if (!shell) return
      shell.classList.toggle('sidebar-collapsed')
      sidebarToggle.classList.toggle('active', shell.classList.contains('sidebar-collapsed'))
    }
  })
  const toolbarSep = () => node('div', { class: 'reader-toolbar-separator' })
  const desktopToolbar = node('div', { class: 'reader-desktop-toolbar' }, [
    sidebarToggle,
    toolbarSep(),
    node('button', {
      class: 'reader-toolbar-btn', type: 'button', text: '←', title: '上一章',
      disabled: !previousId ? '' : null, onclick: () => goToChapter(previousId)
    }),
    node('button', {
      class: 'reader-toolbar-btn', type: 'button', text: '→', title: '下一章',
      disabled: !nextId ? '' : null, onclick: () => goToChapter(nextId)
    }),
    toolbarSep(),
    node('div', { class: 'reader-toolbar-progress' }, [
      node('div', { class: 'reader-toolbar-progress-track' }, desktopProgressFill),
      desktopProgressText
    ]),
    toolbarSep(),
    node('button', {
      class: 'reader-toolbar-btn', type: 'button', text: 'A-', title: '减小字号',
      onclick: () => {
        state.reader.size = Math.max(14, state.reader.size - 2)
        fontSizeDisplay.textContent = String(state.reader.size)
        if (fontSizeInput) { fontSizeInput.value = String(state.reader.size); fontSizeOutput.textContent = `${state.reader.size}px` }
        saveReaderSettings()
        queuePagination(false)
      }
    }),
    fontSizeDisplay,
    node('button', {
      class: 'reader-toolbar-btn', type: 'button', text: 'A+', title: '增大字号',
      onclick: () => {
        state.reader.size = Math.min(36, state.reader.size + 2)
        fontSizeDisplay.textContent = String(state.reader.size)
        if (fontSizeInput) { fontSizeInput.value = String(state.reader.size); fontSizeOutput.textContent = `${state.reader.size}px` }
        saveReaderSettings()
        queuePagination(false)
      }
    }),
    toolbarSep(),
    desktopDayNightBtn,
    node('button', {
      class: `reader-toolbar-btn${state.reader.ttsActive ? ' active' : ''}`, type: 'button', text: '🔊', title: '听书',
      onclick: openTTS
    }),
    node('button', {
      class: 'reader-toolbar-btn', type: 'button', text: '⚙', title: '阅读设置',
      onclick: () => setSettingsVisible(!settingsVisible)
    })
  ].filter(Boolean))

  const desktopNav = node('div', { class: 'reader-desktop-nav' }, [
    previousId ? node('a', { class: 'ghost-button', href: contextualHref(`/books/${requestedBookId}/chapters/${previousId}`), text: '← 上一章' }) : node('span'),
    node('a', { class: 'ghost-button', href: contextualHref(`/books/${requestedBookId}`), text: '返回目录' }),
    nextId ? node('a', { class: 'primary-button', href: contextualHref(`/books/${requestedBookId}/chapters/${nextId}`), text: '下一章 →' }) : node('span')
  ])
  const chapterList = node('div', { class: 'reader-catalog-list' })
  const ensureCatalog = () => {
    if (catalogRendered) return
    const fragment = document.createDocumentFragment()
    catalog.chapters.forEach((item, index) => {
      const presentation = chapterPresentation(item, index)
      fragment.append(node('a', {
        class: `reader-chapter-item${Number(item.id) === requestedChapterId ? ' active' : ''}`,
        href: contextualHref(`/books/${requestedBookId}/chapters/${item.id}`),
        'aria-current': Number(item.id) === requestedChapterId ? 'page' : null
      }, [
        presentation.label ? node('span', { text: presentation.label }) : null,
        node('strong', { text: presentation.title })
      ]))
    })
    chapterList.append(fragment)
    catalogRendered = true
  }
  const locateButton = node('button', {
    class: 'reader-locate-button',
    type: 'button',
    text: '定位当前',
    onclick: () => mobileReaderControls?.locateCurrentChapter()
  })
  const closeCatalogButton = node('button', {
    class: 'reader-catalog-close',
    type: 'button',
    text: '×',
    'aria-label': '关闭章节目录',
    onclick: () => mobileReaderControls?.closeCatalog()
  })
  const sidebar = node('aside', { class: 'reader-sidebar', 'aria-label': '章节目录' }, [
    node('div', { class: 'reader-book' }, [
      closeCatalogButton,
      node('a', { class: 'reader-back', href: contextualHref(`/books/${requestedBookId}`), text: '← 返回作品' }),
      node('span', { text: catalog.book.category }),
      node('h2', { text: catalog.book.title }),
      node('p', { text: `${catalog.book.author} · ${formatNumber(catalog.chapter_count)} 章` })
    ]),
    node('div', { class: 'reader-keyboard-help', 'aria-label': '键盘阅读快捷键' }, [
      node('span', { text: '键盘阅读' }),
      node('div', {}, [
        node('kbd', { text: '↑' }),
        node('kbd', { text: '↓' }),
        node('small', { text: '滚动正文' })
      ]),
      node('div', {}, [
        node('kbd', { text: '←' }),
        node('kbd', { text: '→' }),
        node('small', { text: '切换章节' })
      ])
    ]),
    node('div', { class: 'reader-catalog-heading' }, [
      node('strong', { text: '章节目录' }),
      node('div', { class: 'reader-catalog-actions' }, [
        node('span', { text: `${formatNumber(catalog.chapter_count)} 章` }),
        locateButton
      ])
    ]),
    chapterList
  ])
  const backdrop = node('button', {
    class: 'reader-catalog-backdrop',
    type: 'button',
    'aria-label': '关闭章节目录',
    'aria-hidden': 'true'
  })
  progressFill = node('i')
  progressCopy = node('b', { text: '0.0%' })
  const progressBar = node('div', { class: 'reader-progress' }, [
    node('div', { class: 'reader-progress-copy' }, [
      node('span', { text: '全书进度' }),
      progressCopy
    ]),
    node('div', { class: 'reader-progress-track' }, progressFill)
  ])
  const action = (text, onClick, disabled = false) => node('button', {
    type: 'button',
    text,
    disabled: disabled ? '' : null,
    onclick: onClick
  })
  const settingsButton = action('设置', () => {
    if (!settingsVisible) currentParagraphHint = ttsFirstVisibleParagraph()
    setSettingsVisible(!settingsVisible)
  })
  const dayNightButton = action(state.reader.colorScheme === 'night' ? '日间' : '夜间', () => {
    state.reader.colorScheme = state.reader.colorScheme === 'night' ? 'day' : 'night'
    dayNightButton.textContent = state.reader.colorScheme === 'night' ? '日间' : '夜间'
    saveReaderSettings()
  })
  mobileNav = node('div', { class: 'reader-nav' }, [
    progressBar,
    node('div', { class: 'reader-nav-actions' }, [
      action('上一章', () => goToChapter(previousId), !previousId),
      action('目录', () => mobileReaderControls?.toggleCatalog()),
      action('首页', () => { stopAutoReading(); navigateInApp(`/books/${requestedBookId}`) }),
      dayNightButton,
      settingsButton,
      action('下一章', () => goToChapter(nextId), !nextId)
    ]),
    (autoState = node('button', {
      class: 'reader-auto-state',
      type: 'button',
      text: `停止自动阅读 · ${state.reader.autoSpeed}档`,
      hidden: '',
      onclick: stopAutoReading
    })),
    (ttsStateBar = node('button', {
      class: 'reader-auto-state reader-tts-state',
      type: 'button',
      text: `停止听书 · ${state.reader.ttsRate}x`,
      hidden: '',
      onclick: openTTS
    }))
  ].filter(Boolean))

  const rangeSetting = (label, valueText, min, max, value, onInput, step = 1) => {
    const output = node('b', { text: valueText(value) })
    const input = node('input', {
      type: 'range',
      min: String(min),
      max: String(max),
      step: String(step),
      value: String(value),
      oninput: event => {
        const number = Number(event.target.value)
        output.textContent = valueText(number)
        onInput(number)
      }
    })
    return node('label', {}, [node('span', { text: label }), output, input])
  }
  const backgroundOptions = node('div', { class: 'reader-color-options' })
  ;[
    ['paper', '米白', '#fffaf0'], ['white', '纯白', '#fff'], ['warm', '暖黄', '#f7ead7'],
    ['green', '护眼绿', '#e7f0df'], ['gray', '雾灰', '#e9ecef']
  ].forEach(([value, label, color]) => {
    const button = node('button', {
      type: 'button',
      class: state.reader.background === value ? 'active' : '',
      style: `background:${color}`,
      'aria-label': label,
      onclick: () => {
        state.reader.background = value
        state.reader.colorScheme = 'day'
        backgroundOptions.querySelectorAll('button').forEach(item => item.classList.toggle('active', item === button))
        dayNightButton.textContent = '夜间'
        saveReaderSettings()
      }
    })
    backgroundOptions.append(button)
  })
  const modeOptions = node('div', { class: 'reader-mode-options' })
  ;[
    ['slide', '平移翻页'], ['cover', '覆盖翻页'], ['simulation', '仿真翻页'], ['vertical', '上下翻页']
  ].forEach(([value, label]) => {
    const button = node('button', {
      type: 'button',
      class: state.reader.mode === value ? 'active' : '',
      'data-reader-mode': value,
      text: label,
      onclick: () => {
        stopAutoReading()
        state.reader.mode = value
        modeOptions.querySelectorAll('button').forEach(item => item.classList.toggle('active', item === button))
        saveReaderSettings()
        queuePagination(false)
      }
    })
    modeOptions.append(button)
  })
  const eyeCareButton = node('button', {
    type: 'button',
    class: state.reader.eyeCare ? 'active' : '',
    text: '护眼模式',
    onclick: () => {
      state.reader.eyeCare = !state.reader.eyeCare
      eyeCareButton.classList.toggle('active', state.reader.eyeCare)
      saveReaderSettings()
    }
  })
  autoButton = node('button', {
    type: 'button',
    text: '自动阅读',
    onclick: () => {
      toggleAutoReading()
      autoButton.classList.toggle('active', state.reader.autoReading)
      autoButton.textContent = state.reader.autoReading ? '停止自动阅读' : '自动阅读'
      if (state.reader.autoReading) setSettingsVisible(false)
    }
  })
  ttsButton = node('button', {
    type: 'button',
    text: '听书',
    onclick: () => {
      openTTS()
      if (state.reader.ttsActive) setSettingsVisible(false)
    }
  })
  fontSizeOutput = node('b', { text: `${state.reader.size}px` })
  fontSizeInput = node('input', {
    type: 'range', min: '14', max: '36', step: '1', value: String(state.reader.size),
    oninput: event => {
      const value = Number(event.target.value)
      fontSizeOutput.textContent = `${value}px`
      state.reader.size = value
      if (fontSizeDisplay) fontSizeDisplay.textContent = String(value)
      saveReaderSettings()
      queuePagination(false)
    }
  })
  const fontSizeLabel = node('label', {}, [node('span', { text: '阅读字号' }), fontSizeOutput, fontSizeInput])
  settingsPanel = node('section', { class: 'reader-settings-panel', 'aria-hidden': 'true' }, [
    node('div', { class: 'reader-settings-title' }, [
      node('strong', { text: '阅读设置' }),
      node('button', { type: 'button', text: '×', 'aria-label': '关闭阅读设置', onclick: () => setSettingsVisible(false) })
    ]),
    rangeSetting('页面亮度（不修改系统）', value => `${value}%`, 35, 100, state.reader.brightness, value => {
      state.reader.brightness = value
      saveReaderSettings()
    }),
    fontSizeLabel,
    rangeSetting('阅读行距', value => Number(value).toFixed(2), 1.6, 2.4, state.reader.leading, value => {
      state.reader.leading = Number(value.toFixed(2))
      saveReaderSettings()
      queuePagination(false)
    }, 0.05),
    rangeSetting('页面宽度', value => `${value}px`, 620, 1200, state.reader.width, value => {
      state.reader.width = value
      saveReaderSettings()
    }, 20),
    node('div', { class: 'reader-setting-group' }, [node('span', { text: '阅读背景色' }), backgroundOptions]),
    node('div', { class: 'reader-setting-group' }, [node('span', { text: '阅读模式' }), modeOptions]),
    node('div', { class: 'reader-setting-toggles' }, [eyeCareButton, autoButton, ttsButton].filter(Boolean)),
    rangeSetting('自动阅读速度', value => String(value), 1, 9, state.reader.autoSpeed, value => {
      state.reader.autoSpeed = value
      saveReaderSettings()
      if (state.reader.autoReading) startAutoReading()
    }),
    rangeSetting('听书语速', value => `${value}x`, 0.5, 3, state.reader.ttsRate, value => {
      state.reader.ttsRate = Number(value.toFixed(1))
      saveReaderSettings()
      ttsScheduleRebuild()
      ttsUpdateControls()
    }, 0.1),
    (() => {
      const voices = [
        { key: 'nuanxi', label: '暖溪 · 温婉知性', gender: 'female', lang: 'zh-CN' },
        { key: 'lingxian', label: '灵弦 · 灵动俏皮', gender: 'female', lang: 'zh-CN' },
        { key: 'shuanger', label: '霜儿 · 爽朗飒然', gender: 'female', lang: 'zh-CN' },
        { key: 'yanzhi', label: '燕知 · 清亮质朴', gender: 'female', lang: 'zh-CN' },
        { key: 'wanqing', label: '晚晴 · 柔婉细腻', gender: 'female', lang: 'zh-HK' },
        { key: 'muyao', label: '沐瑶 · 端庄优雅', gender: 'female', lang: 'zh-HK' },
        { key: 'qianyu', label: '浅语 · 温润恬静', gender: 'female', lang: 'zh-TW' },
        { key: 'ruoxi', label: '若汐 · 甜美亲和', gender: 'female', lang: 'zh-TW' },
        { key: 'kuangyun', label: '旷云 · 热血豪迈', gender: 'male', lang: 'zh-CN' },
        { key: 'qingyan', label: '清砚 · 少年朗逸', gender: 'male', lang: 'zh-CN' },
        { key: 'tongzhen', label: '童真 · 稚气天真', gender: 'male', lang: 'zh-CN' },
        { key: 'mocheng', label: '墨澄 · 沉稳儒雅', gender: 'male', lang: 'zh-CN' },
        { key: 'yueming', label: '岳鸣 · 浑厚磁性', gender: 'male', lang: 'zh-HK' },
        { key: 'hanfeng', label: '寒枫 · 清冷内敛', gender: 'male', lang: 'zh-TW' }
      ]
      const modeVoiceFilter = { cantonese: 'zh-HK', hokkien: 'zh-TW' }
      const modeDefaults = { cantonese: 'wanqing', hokkien: 'qianyu' }
      const buildVoiceOptions = (select, filterLang, current) => {
        select.replaceChildren()
        const femaleGroup = document.createElement('optgroup')
        femaleGroup.label = '♀ 女声'
        const maleGroup = document.createElement('optgroup')
        maleGroup.label = '♂ 男声'
        const filtered = filterLang ? voices.filter(v => v.lang === filterLang) : voices
        filtered.forEach(v => {
          const opt = document.createElement('option')
          opt.value = v.key
          opt.textContent = v.label
          if (v.key === current) opt.selected = true
          ;(v.gender === 'female' ? femaleGroup : maleGroup).append(opt)
        })
        if (femaleGroup.children.length) select.append(femaleGroup)
        if (maleGroup.children.length) select.append(maleGroup)
      }
      const voiceSelectEl = node('select', { class: 'reader-tts-voice-select' })
      const voiceGroup = node('div', { class: 'reader-setting-group' }, [node('span', { text: '听书音色' }), voiceSelectEl])
      const narratorSelectEl = node('select', { class: 'reader-tts-voice-select' })
      const narratorGroup = node('div', { class: 'reader-setting-group' }, [node('span', { text: '旁白音色' }), narratorSelectEl])
      const refreshVoiceSelects = () => {
        const mode = state.reader.ttsMode
        const filterLang = modeVoiceFilter[mode] || null
        if (filterLang && !voices.some(v => v.lang === filterLang && v.key === state.reader.ttsVoice)) {
          state.reader.ttsVoice = modeDefaults[mode]
        }
        buildVoiceOptions(voiceSelectEl, filterLang, state.reader.ttsVoice)
        buildVoiceOptions(narratorSelectEl, null, state.reader.ttsNarrator)
        voiceGroup.style.display = mode === 'smart' ? 'none' : ''
        narratorGroup.style.display = mode === 'smart' ? '' : 'none'
      }
      voiceSelectEl.onchange = () => {
        state.reader.ttsVoice = voiceSelectEl.value
        saveReaderSettings()
        ttsScheduleRebuild()
      }
      narratorSelectEl.onchange = () => {
        state.reader.ttsNarrator = narratorSelectEl.value
        saveReaderSettings()
        ttsScheduleRebuild()
      }
      const modeSelect = node('select', { class: 'reader-tts-voice-select' })
      ;[
        { key: 'normal', label: '普通模式 · 单音色朗读' },
        { key: 'smart', label: '智能模式 · 多角色演绎' },
        { key: 'cantonese', label: '粤语模式 · 粤语朗读' },
        { key: 'hokkien', label: '闽南语模式 · 闽南语朗读' }
      ].forEach(m => {
        const opt = document.createElement('option')
        opt.value = m.key
        opt.textContent = m.label
        if (m.key === state.reader.ttsMode) opt.selected = true
        modeSelect.append(opt)
      })
      modeSelect.onchange = () => {
        state.reader.ttsMode = modeSelect.value
        refreshVoiceSelects()
        saveReaderSettings()
        ttsUpdateControls()
        ttsScheduleRebuild()
      }
      const emotionSelect = node('select', { class: 'reader-tts-voice-select' })
      Object.entries(ttsEmotionModes).forEach(([key, item]) => {
        const opt = document.createElement('option')
        opt.value = key
        opt.textContent = `${item.label} · ${item.desc}`
        if (key === state.reader.ttsEmotion) opt.selected = true
        emotionSelect.append(opt)
      })
      emotionSelect.onchange = () => {
        state.reader.ttsEmotion = emotionSelect.value
        saveReaderSettings()
        ttsUpdateControls()
        ttsScheduleRebuild()
      }
      refreshVoiceSelects()
      return [
        node('div', { class: 'reader-setting-group' }, [node('span', { text: '听书模式' }), modeSelect]),
        node('div', { class: 'reader-setting-group' }, [node('span', { text: '情感阅读' }), emotionSelect]),
        voiceGroup,
        narratorGroup
      ]
    })()
  ].flat())
  const setSettingsVisible = visible => {
    settingsVisible = Boolean(visible)
    settingsPanel.classList.toggle('visible', settingsVisible)
    settingsPanel.setAttribute('aria-hidden', String(!settingsVisible))
    backdrop.classList.toggle('visible', settingsVisible)
    backdrop.setAttribute('aria-hidden', String(!settingsVisible))
    sidebar.classList.remove('mobile-visible')
    mobileNav.classList.remove('visible')
  }

  const refreshChapterComments = async () => {
    const data = await accountApi(`/api/v1/books/${requestedBookId}/chapters/${requestedChapterId}/comments`)
    ingestChapterComments(data)
    return data
  }

  const formatInterlineTime = value => {
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return ''
    return date.toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
    })
  }

  const interlineAuthorAvatar = author => {
    const avatar = node('span', { class: 'interline-avatar', 'aria-hidden': 'true' })
    avatar.append(node('span', {
      class: 'interline-avatar-fallback', text: accountInitials(author)
    }))
    if (author?.avatar_url) {
      const image = node('img', {
        src: author.avatar_url, alt: '', loading: 'lazy', decoding: 'async'
      })
      image.addEventListener('error', () => image.remove(), { once: true })
      avatar.append(image)
    }
    return avatar
  }

  const openInterlineDialog = paragraph => {
    interlineAction?.remove()
    interlineAction = null
    const paragraphIndex = Number(paragraph.dataset.paragraphIndex)
    currentParagraphHint = paragraphIndex
    const overlay = node('div', { class: 'interline-overlay', role: 'presentation' })
    const dialog = node('section', {
      class: 'interline-dialog', role: 'dialog', 'aria-modal': 'true',
      'aria-label': '字里行间段落评论'
    })
    const closeButton = node('button', {
      class: 'interline-close', type: 'button', text: '×', 'aria-label': '关闭评论窗口'
    })
    const countTitle = node('h2', { text: '这一段的 0 条评论' })
    const threadList = node('div', { class: 'interline-thread-list', 'aria-live': 'polite' })
    const composer = node('div', { class: 'interline-composer' })
    const close = () => {
      document.removeEventListener('keydown', onKeydown)
      overlay.remove()
    }
    const onKeydown = event => { if (event.key === 'Escape') close() }
    closeButton.onclick = close
    overlay.addEventListener('click', event => { if (event.target === overlay) close() })
    document.addEventListener('keydown', onKeydown)

    const render = () => {
      const thread = paragraphCommentsByIndex.get(paragraphIndex) || { count: 0, comments: [] }
      const comments = Array.isArray(thread.comments) ? thread.comments : []
      countTitle.textContent = `这一段的 ${Number(thread.count || 0)} 条评论`
      threadList.replaceChildren()
      if (!comments.length) {
        threadList.append(node('div', { class: 'interline-empty' }, [
          node('span', { text: '🫧' }),
          node('strong', { text: '这一段还没有评论' }),
          node('p', { text: '留下第一个字里行间的想法。' })
        ]))
      } else {
        comments.forEach(comment => {
          const author = comment.author || {}
          const reading = author.reading || {}
          const viewerLikes = Math.max(0, Math.min(3, Number(comment.viewer_like_count || 0)))
          const totalLikes = Number(comment.like_count ?? comment.thanks_count ?? 0)
          const likeMaxed = viewerLikes >= 3
          const likeLabel = comment.is_own
            ? `收到点赞 · ${totalLikes}`
            : likeMaxed
              ? `已点满 3/3 · ${totalLikes}`
              : viewerLikes > 0
                ? `再赞一次 ${viewerLikes}/3 · ${totalLikes}`
                : `点赞 · ${totalLikes}`
          const likeButton = node('button', {
            class: `interline-like${viewerLikes > 0 ? ' active' : ''}${likeMaxed ? ' maxed' : ''}`,
            type: 'button',
            disabled: comment.is_own || likeMaxed ? '' : null,
            'aria-label': likeLabel
          }, [
            node('span', { class: 'interline-heart-icon', 'aria-hidden': 'true', text: '♡' }),
            node('span', { text: likeLabel })
          ])
          if (!comment.is_own && !likeMaxed) likeButton.onclick = async () => {
            if (!state.account) { close(); openAuthDialog('login'); return }
            likeButton.disabled = true
            try {
              await accountApi(`/api/v1/paragraph-comments/${comment.id}/likes`, { method: 'POST' })
              await refreshChapterComments()
              render()
            } catch (error) {
              window.alert(error.message || '暂时无法点赞这条评论')
              likeButton.disabled = false
            }
          }
          threadList.append(node('article', { class: 'interline-comment' }, [
            node('header', {}, [
              node('div', { class: 'interline-author' }, [
                interlineAuthorAvatar(author),
                node('div', { class: 'interline-author-copy' }, [
                  node('strong', { text: author.display_name || '读者' }),
                  node('span', { class: 'interline-author-rank' }, [
                    readingRankIcon(reading, { decorative: true }),
                    node('span', { text: `${reading.roman || 'Ⅰ'} · ${reading.name || '只如初见'}` })
                  ])
                ])
              ]),
              node('time', { text: formatInterlineTime(comment.created_at), datetime: comment.created_at || '' })
            ]),
            node('p', { text: comment.content || '' }),
            node('footer', {}, [likeButton])
          ]))
        })
      }

      composer.replaceChildren()
      if (!state.account) {
        const login = node('button', { class: 'interline-login', type: 'button', text: '登录后参与字里行间' })
        login.onclick = () => { close(); openAuthDialog('login') }
        composer.append(login)
        return
      }
      const textarea = node('textarea', {
        maxlength: '500', rows: '3', placeholder: '说说你对这一段的理解…',
        'aria-label': '评论内容'
      })
      const count = node('span', { text: '0 / 500' })
      const submit = node('button', { type: 'button', text: '发布评论' })
      textarea.addEventListener('input', () => { count.textContent = `${[...textarea.value].length} / 500` })
      submit.onclick = async () => {
        const content = textarea.value.trim()
        if (!content) { window.alert('评论不能为空'); return }
        const contentIssue = localUserContentIssue(content)
        if (contentIssue) { openUserContentNotice(contentIssue, { returnFocus: textarea }); return }
        submit.disabled = true
        try {
          const data = await accountApi(
            `/api/v1/books/${requestedBookId}/chapters/${requestedChapterId}/comments`,
            { method: 'POST', body: { paragraph_index: paragraphIndex, content } }
          )
          ingestChapterComments(data)
          render()
        } catch (error) {
          if (isUserContentGuardIssue(error.message)) openUserContentNotice(error.message, { returnFocus: textarea })
          else window.alert(error.message || '评论无法发布')
          submit.disabled = false
        }
      }
      composer.append(textarea, node('div', { class: 'interline-compose-actions' }, [count, submit]))
      queueMicrotask(() => textarea.focus({ preventScroll: true }))
    }

    dialog.append(
      node('header', { class: 'interline-dialog-head' }, [
        node('div', {}, [node('span', { text: '字里行间' }), countTitle]),
        closeButton
      ]),
      node('blockquote', { text: paragraph.dataset.paragraphText || '' }),
      threadList,
      composer
    )
    overlay.append(dialog)
    document.body.append(overlay)
    render()
  }

  const showInterlineAction = (paragraph, clientX, clientY) => {
    interlineAction?.remove()
    currentParagraphHint = Number(paragraph.dataset.paragraphIndex)
    const paragraphIndex = Math.max(0, Number(paragraph.dataset.ttsIndex) || 0)
    const startFromHere = event => {
      event.stopPropagation()
      interlineAction?.remove()
      interlineAction = null
      if (state.ttsController?.active && state.ttsController.owner !== ttsOwner) {
        state.ttsController.stop()
      }
      if (state.reader.ttsActive && state.ttsController?.owner === ttsOwner) {
        ttsRebuildActivePlan(paragraphIndex)
      } else {
        startTTS(paragraphIndex)
      }
      openTtsPlayer()
    }
    const action = node('div', {
      class: 'interline-action-menu',
      role: 'menu',
      'aria-label': '段落操作'
    }, [
      node('span', { class: 'interline-action-label', text: '选中这一段' }),
      node('div', { class: 'interline-action-buttons' }, [
        node('button', { class: 'interline-action tts', type: 'button', role: 'menuitem', onclick: startFromHere }, [
          node('span', { class: 'interline-action-icon', text: '◉', 'aria-hidden': 'true' }),
          node('span', { text: '从此处听书' })
        ]),
        node('button', {
          class: 'interline-action comment', type: 'button', role: 'menuitem',
          onclick: event => { event.stopPropagation(); openInterlineDialog(paragraph) }
        }, [
          node('span', { class: 'interline-action-icon', text: '🫧', 'aria-hidden': 'true' }),
          node('span', { text: '字里行间' })
        ])
      ])
    ])
    action.style.left = `${Math.min(innerWidth - 310, Math.max(12, Number(clientX) - 112))}px`
    action.style.top = `${Math.min(innerHeight - 108, Math.max(12, Number(clientY) - 102))}px`
    action.addEventListener('click', event => event.stopPropagation())
    document.body.append(action)
    interlineAction = action
    window.setTimeout(() => { if (interlineAction === action) { action.remove(); interlineAction = null } }, 6000)
  }

  const bindInterlineParagraph = paragraph => {
    let timer = null
    let originX = 0
    let originY = 0
    const cancel = () => { window.clearTimeout(timer); timer = null }
    paragraph.addEventListener('pointerdown', event => {
      if (event.button !== 0) return
      currentParagraphHint = Number(paragraph.dataset.paragraphIndex)
      originX = event.clientX
      originY = event.clientY
      cancel()
      timer = window.setTimeout(() => showInterlineAction(paragraph, originX, originY), 520)
    })
    paragraph.addEventListener('pointermove', event => {
      if (Math.hypot(event.clientX - originX, event.clientY - originY) > 12) cancel()
    })
    ;['pointerup', 'pointercancel', 'pointerleave'].forEach(type => paragraph.addEventListener(type, cancel))
    paragraph.addEventListener('contextmenu', event => {
      event.preventDefault()
      cancel()
      showInterlineAction(paragraph, event.clientX, event.clientY)
    })
  }

  readerContent = node('div', { class: 'reader-content' })
  const illustrationPattern = /^\[illustration:(.+)\]$/
  let paragraphIndex = 0
  ;(chapter.content || '').split('\n').forEach(line => {
    const m = line.match(illustrationPattern)
    if (m) {
      const imgUrl = `/api/v1/books/${requestedBookId}/illustrations/${encodeURI(m[1])}`
      const img = node('img', { src: imgUrl, alt: '插画', loading: 'lazy', class: 'reader-illustration' })
      img.addEventListener('error', () => img.style.display = 'none')
      readerContent.append(img)
    } else if (!line.trim()) {
      readerContent.append(node('div', { class: 'reader-paragraph-gap', 'aria-hidden': 'true' }))
    } else {
      const bubble = node('button', {
        class: 'interline-bubble', type: 'button', text: '🫧 0', hidden: '',
        'aria-label': '查看这段文字的评论'
      })
      const paragraph = node('p', {
        class: 'reader-paragraph',
        dataset: {
          paragraphIndex: String(paragraphIndex),
          ttsIndex: String(paragraphIndex),
          paragraphText: line
        }
      }, [node('span', { class: 'reader-paragraph-copy', text: line }), bubble])
      bubble.onclick = event => { event.stopPropagation(); openInterlineDialog(paragraph) }
      bindInterlineParagraph(paragraph)
      readerContent.append(paragraph)
      paragraphIndex++
    }
  })
  ingestChapterComments(chapterComments)
  const chapterHeading = chapterPresentation(chapter, chapterPosition)
  stage = node('div', { class: `reader-stage reader-mode-${state.reader.mode}`, tabindex: '0' }, [
    desktopToolbar,
    node('article', { class: 'reader-paper' }, [
      node('header', {}, [
        chapterHeading.label ? node('span', { text: chapterHeading.label }) : null,
        node('h1', { text: chapterHeading.title }),
        node('p', { text: `${chapter.book.title} · ${formatNumber(chapter.word_count)} 字` })
      ]),
      readerContent
    ]),
    desktopNav
  ])
  topProgressFill = node('i')
  const topProgressBar = node('div', { class: 'reader-top-progress' }, topProgressFill)
  app.replaceChildren(node('section', { class: 'reader-shell' }, [topProgressBar, sidebar, backdrop, stage, settingsPanel, mobileNav]))
  applyReaderSettings()
  mobileReaderControls = bindMobileReaderGestures({
    stage,
    sidebar,
    backdrop,
    mobileNav,
    chapterList,
    ensureCatalog,
    settingsPanel,
    setSettingsVisible,
    previousId,
    nextId,
    goToChapter,
    changePage,
    stopAuto: stopAutoReading
  })
  if (!window.matchMedia('(max-width: 720px)').matches) {
    ensureCatalog()
    mobileNav.classList.add('visible')
  }
  const handleProgressScroll = () => {
    scheduleReadingProgressSave()
    if (progressFrame) return
    progressFrame = requestAnimationFrame(() => {
      progressFrame = null
      updateProgress()
    })
  }
  stage.addEventListener('scroll', handleProgressScroll, { passive: true })
  window.addEventListener('scroll', handleProgressScroll, { passive: true })
  const resizeListener = () => queuePagination(false)
  window.addEventListener('resize', resizeListener)
  visibilityListener = () => {
    if (document.hidden) {
      flushReadingProgress()
      if (!state.reader.ttsActive) stopAutoReading()
    } else {
      if (state.reader.ttsActive && ttsAudioEl && ttsAudioEl.paused && ttsPlanIndex >= 0) {
        ttsResumePlayback()
      }
    }
  }
  document.addEventListener('visibilitychange', visibilityListener)
  const pageShowListener = () => {
    if (state.reader.ttsActive && ttsAudioEl?.paused && ttsPlanIndex >= 0) ttsResumePlayback()
  }
  window.addEventListener('pageshow', pageShowListener)
  const pageHideListener = () => flushReadingProgress()
  window.addEventListener('pagehide', pageHideListener)
  state.readerNavigation = {
    bookId: String(requestedBookId),
    previousId,
    nextId,
    scrollBy: options => scrollReaderBy(stage, options.top, options.behavior, layoutMode),
    changePage,
    mode: () => state.reader.mode,
    cancelTap: () => {
      flushReadingProgress()
      if (state.ttsContinueOnLoad) {
        state.readingActivity?.stop?.(true)
        if (state.ttsController?.active) state.ttsController.stop({ preservePending: true })
        else stopTTS({ preservePending: true })
      } else if (state.ttsController?.active) {
        state.ttsController.detach()
      } else {
        state.readingActivity?.stop?.(true)
        stopTTS()
      }
      const preserveAuto = state.readerAutoContinue
      mobileReaderControls.cancelTap(preserveAuto)
      mobileReaderControls.dispose?.()
      if (autoFrame) cancelAnimationFrame(autoFrame)
      autoFrame = null
      window.clearTimeout(resizeTimer)
      window.clearTimeout(pageAnimationTimer)
      if (progressFrame) cancelAnimationFrame(progressFrame)
      window.removeEventListener('scroll', handleProgressScroll)
      window.removeEventListener('resize', resizeListener)
      document.removeEventListener('visibilitychange', visibilityListener)
      window.removeEventListener('pageshow', pageShowListener)
      window.removeEventListener('pagehide', pageHideListener)
    }
  }
  startReadingActivity(String(requestedBookId))
  requestAnimationFrame(() => {
    if (catalogRendered) chapterList.querySelector('.active')?.scrollIntoView({ block: 'center' })
    stage.focus({ preventScroll: true })
    recomputePagination(true)
    const afterLayout = () => {
      if (restoreWithin !== null) {
        if (layoutMode === 'vertical') {
          const metrics = readerScrollMetrics(stage, layoutMode)
          setReaderScrollTop(stage, restoreWithin * Math.max(0, metrics.scrollHeight - metrics.clientHeight), layoutMode)
        } else {
          pageIndex = Math.round(restoreWithin * Math.max(0, pageCount - 1))
          applyPageTransform('none')
        }
      }
      updateProgress()
      if (state.readerAutoContinue) {
        state.readerAutoContinue = false
        if (state.ttsContinueOnLoad) {
          state.ttsContinueOnLoad = false
          startTTS()
        } else {
          startAutoReading()
        }
      } else if (state.ttsSession?.active
        && String(state.ttsSession.bookId) === requestedBookId
        && String(state.ttsSession.chapterId) === String(requestedChapterId)) {
        state.ttsController?.attach?.(index => {
          ttsParagraphIndex = Number(index)
          ttsHighlight(ttsParagraphIndex)
          ttsUpdateControls()
        })
        state.reader.ttsActive = true
        ttsUpdateControls()
      }
    }
    if (document.hidden) {
      setTimeout(afterLayout, 50)
    } else {
      requestAnimationFrame(afterLayout)
    }
  })
}

async function loadDeconstructions() {
  const data = await api('/api/v1/deconstructions')
  const ordered = orderHomeDeconstructions(data.items)
  const completeCount = ordered.filter(item => Number(item.progress_percent || 0) >= 100).length
  const activeCount = ordered.filter(item => {
    const progress = Number(item.progress_percent || 0)
    return progress > 0 && progress < 100
  }).length
  setSeo({
    title: '深度拆书档案｜小说结构、节奏与文风分析 - OOH Story',
    description: `浏览 OOH Story 收录的 ${formatNumber(ordered.length)} 份小说拆书档案，了解作品的结构、叙事节奏、人物关系与文风技法。`,
    canonicalPath: '/deconstructions'
  })
  const grid = node('div', { class: 'deconstruction-grid' })
  ordered.forEach((item, index) => {
    const { percentage, completed, active, status } = deconstructionState(item)
    const hasProgress = Number(item.total_chapters || 0) > 0
    grid.append(node('a', {
      class: `deconstruction-card${index === 0 ? ' featured' : ''}`,
      href: `/deconstructions/${encodeURIComponent(item.slug)}`,
      'aria-label': `打开《${item.title}》拆书档案`
    }, [
      deconstructionBackdrop(item),
      node('div', { class: 'deconstruction-card-top' }, [
        node('span', { class: `home-deconstruction-status ${active ? 'active' : completed ? 'complete' : 'archive'}`, text: status }),
        node('span', { class: 'deconstruction-sequence', text: String(index + 1).padStart(2, '0') })
      ]),
      node('div', { class: 'deconstruction-card-copy' }, [
        node('span', { class: 'eyebrow', text: index === 0 ? 'FEATURED READING FILE' : 'DEEP READING FILE' }),
        node('h2', { text: item.title }),
        node('p', { class: 'deconstruction-document-summary', text: item.documents.map(doc => doc.label).join(' · ') || '拆解资料整理中' })
      ]),
      node('div', { class: 'deconstruction-card-bottom' }, [
        hasProgress ? node('div', { class: 'progress' }, node('span', { style: `width:${percentage}%` })) : null,
        node('div', { class: 'deconstruction-card-meta' }, [
          node('span', {
            text: hasProgress
              ? `逐章拆解 ${item.progress} · ${percentage.toFixed(percentage % 1 ? 1 : 0)}%`
              : `已收录 ${formatNumber(item.documents.length)} 份深读文档`
          }),
          node('strong', { text: '进入档案 →' })
        ])
      ])
    ]))
  })
  app.replaceChildren(
    node('section', { class: 'deconstruction-archive-head' }, [
      node('div', { class: 'deconstruction-archive-copy' }, [
        node('span', { class: 'section-kicker', text: 'DECONSTRUCTION ARCHIVE' }),
        node('h1', { text: '全局拆书档案' }),
        node('p', { text: '从表层剧情进入结构：黄金三章、叙事节拍、人物关系与文风技法，都在这里被重新拆开。' })
      ]),
      node('div', { class: 'deconstruction-archive-stats' }, [
        node('div', {}, [node('strong', { text: formatNumber(ordered.length) }), node('span', { text: '全部档案' })]),
        node('div', {}, [node('strong', { text: formatNumber(activeCount) }), node('span', { text: '正在拆解' })]),
        node('div', {}, [node('strong', { text: formatNumber(completeCount) }), node('span', { text: '完整归档' })])
      ])
    ]),
    grid,
    node('div', { style: 'height:90px' })
  )
}

function renderMarkdownText(text) {
  const container = node('div', { class: 'report-document' })
  let list = null
  text.split(/\r?\n/).forEach(raw => {
    const line = raw.trimEnd()
    if (!line.trim()) { list = null; return }
    const heading = line.match(/^(#{1,3})\s+(.+)/)
    if (heading) {
      list = null
      container.append(node(`h${heading[1].length}`, { text: heading[2] }))
      return
    }
    const item = line.match(/^\s*[-*]\s+(.+)/)
    if (item) {
      if (!list) { list = node('ul'); container.append(list) }
      list.append(node('li', { text: item[1] }))
      return
    }
    list = null
    container.append(node('p', { text: line.replace(/^\|?[-:|\s]+\|?$/, '') }))
  })
  return container
}

async function loadDeconstruction(slug) {
  const data = await api(`/api/v1/deconstructions/${encodeURIComponent(slug)}`)
  const deconstructionCanonical = publicUrl(`/deconstructions/${encodeURIComponent(data.slug || slug)}`)
  const documentLabels = data.documents.map(document => cleanSeoText(document.label, 40)).filter(Boolean)
  const subdirs = Array.isArray(data.subdirectories) ? data.subdirectories : []
  const deconstructionDescription = cleanSeoText([
    `《${data.title}》深度拆书档案。`,
    data.progress ? `逐章拆解进度 ${data.progress}，完成度 ${data.progress_percent}%。` : '',
    documentLabels.length ? `档案包含：${documentLabels.join('、')}。` : '拆解资料整理中。'
  ].filter(Boolean).join(' '))
  setSeo({
    title: `《${data.title}》拆书档案｜结构、节奏与文风分析 - OOH Story`,
    description: deconstructionDescription,
    canonicalPath: deconstructionCanonical,
    type: 'article',
    image: data.cover_url || SITE_DEFAULT_IMAGE,
    imageAlt: data.cover_url ? `《${data.title}》封面` : 'OOH Story 品牌图标'
  })
  const allTabs = [
    ...data.documents.map(doc => ({ type: 'doc', label: doc.label, content: doc.content })),
    ...subdirs.map(sd => ({ type: 'subdir', label: sd.label, name: sd.name, items: sd.items }))
  ]
  let active = 0
  const tabs = node('div', { class: 'report-tabs' })
  const body = node('div')
  const fileCache = {}

  async function loadSubdirFile(subdirName, filePath) {
    const cacheKey = `${subdirName}/${filePath}`
    if (fileCache[cacheKey]) return fileCache[cacheKey]
    const result = await api(`/api/v1/deconstructions/${encodeURIComponent(slug)}/file/${encodeURIComponent(subdirName)}/${encodeURIComponent(filePath)}`)
    fileCache[cacheKey] = result.content || ''
    return fileCache[cacheKey]
  }

  function renderSubdirList(tab) {
    const container = node('div', { class: 'report-document subdir-list' })
    function renderEntries(entries, parentPath) {
      entries.forEach(entry => {
        if (entry.type === 'directory') {
          container.append(node('div', { class: 'subdir-folder-header' }, [
            node('span', { class: 'subdir-folder-icon', text: '📁' }),
            node('strong', { text: entry.label }),
            node('span', { class: 'subdir-folder-count', text: `${entry.items.length} 个文件` })
          ]))
          renderEntries(entry.items, `${parentPath}${entry.name}/`)
        } else {
          const btn = node('button', {
            class: 'subdir-file-item',
            type: 'button',
            onclick: async () => {
              btn.textContent = '加载中…'
              try {
                const content = await loadSubdirFile(tab.name, `${parentPath}${entry.filename}`)
                body.replaceChildren(
                  node('div', { class: 'subdir-file-viewer' }, [
                    node('button', {
                      class: 'subdir-back-btn',
                      type: 'button',
                      text: '← 返回列表',
                      onclick: () => render()
                    }),
                    node('h2', { class: 'subdir-file-title', text: entry.label }),
                    renderMarkdownText(content)
                  ])
                )
              } catch (e) {
                btn.textContent = entry.label
              }
            }
          }, [
            node('span', { class: 'subdir-file-icon', text: fileIcon(tab.name, parentPath) }),
            node('span', { class: 'subdir-file-label', text: entry.label }),
            node('span', { class: 'subdir-file-arrow', text: '→' })
          ])
          container.append(btn)
        }
      })
    }
    renderEntries(tab.items, '')
    return container
  }

  function fileIcon(subdirName, path) {
    if (subdirName === '剧情') return '🎬'
    if (subdirName === '角色') return '👤'
    if (path.includes('世界观')) return '🌍'
    if (path.includes('势力')) return '⚔️'
    if (subdirName === '设定') return '⚙️'
    if (subdirName === '章节') return '📄'
    return '📝'
  }

  const render = () => {
    tabs.replaceChildren()
    allTabs.forEach((tab, index) => tabs.append(
      node('button', {
        class: index === active ? 'active' : '',
        type: 'button',
        text: tab.label,
        onclick: () => { active = index; render() }
      })
    ))
    const current = allTabs[active]
    if (!current) {
      body.replaceChildren(node('div', { class: 'report-document', text: '档案文档仍在整理中。' }))
    } else if (current.type === 'doc') {
      body.replaceChildren(
        current.content ? renderMarkdownText(current.content) : node('div', { class: 'report-document', text: '文档内容生成中…' })
      )
    } else {
      body.replaceChildren(renderSubdirList(current))
    }
  }
  render()
  app.replaceChildren(node('article', { class: 'report' }, [
    node('header', { class: 'report-head' }, [
      deconstructionBackdrop(data),
      node('span', { class: 'eyebrow', text: 'DEEP READING FILE' }),
      node('h1', { text: data.title }),
      node('p', { text: data.progress ? `逐章拆解 ${data.progress} · 完成度 ${data.progress_percent}%` : '全局拆书档案' }),
      node('div', { class: 'report-head-actions' }, [
        data.public_id ? node('a', { class: 'primary-button', href: `/books/${data.public_id}`, text: '打开原作' }) : null,
        state.account
          ? node('a', {
              class: 'ghost-button',
              href: `/api/v1/me/deconstructions/${encodeURIComponent(slug)}/download`,
              download: '',
              text: '下载完整档案 ZIP'
            })
          : node('button', {
              class: 'ghost-button',
              type: 'button',
              text: '登录后下载档案',
              onclick: () => openAuthDialog('login')
            })
      ])
    ]),
    tabs,
    body
  ]))
}

function pathFromLocation() {
  if (location.hash) {
    return (location.hash.slice(1).split('?')[0] || '/').replace(/\/+$/, '') || '/'
  }
  const pathname = location.pathname.replace(/\/+$/, '') || '/'
  if (pathname === '/' || pathname === '/library' || pathname === '/rankings' || pathname === '/deconstructions' || pathname === '/account'
    || /^\/(?:about|disclaimer|guide|contact|client)$/.test(pathname)
    || /^\/account\/(?:history|favorites|bookshelf|profile)$/.test(pathname)) return pathname
  const bookMatch = pathname.match(/^\/books\/([A-Za-z0-9_-]{22})$/)
  if (bookMatch) return `/book/${bookMatch[1]}`
  const chapterMatch = pathname.match(/^\/books\/([A-Za-z0-9_-]{22})\/chapters\/(\d+)$/)
  if (chapterMatch) return `/read/${chapterMatch[1]}/${chapterMatch[2]}`
  const volumeMatch = pathname.match(/^\/books\/([A-Za-z0-9_-]{22})\/volumes\/(\d+)$/)
  if (volumeMatch) return `/book/${volumeMatch[1]}/volume/${volumeMatch[2]}`
  if (pathname.startsWith('/deconstructions/')) {
    return `/deconstruction/${pathname.slice('/deconstructions/'.length)}`
  }
  return pathname
}

function staticPage(title, subtitle, contentNodes, canonicalPath) {
  setSeo({ title: `${title}｜OOH Story`, description: subtitle, canonicalPath })
  const page = node('div', { class: 'static-page' }, [
    node('a', { class: 'static-page-back', href: '/', text: '← 返回首页' }),
    node('h1', { text: title }),
    node('p', { class: 'page-subtitle', text: subtitle }),
    node('div', { class: 'static-page-content' }, contentNodes)
  ])
  app.replaceChildren(page)
}

function loadAbout() {
  staticPage('关于我们', 'About OOH Story', [
    node('h2', { text: '我们是谁' }),
    node('p', { text: 'OOH Story 是一个免费、开源、自托管的中文小说阅读平台。我们致力于为读者提供优质的阅读体验，让每一个好故事都能被发现、被阅读、被分享。' }),
    node('h2', { text: '我们的愿景' }),
    node('p', { text: '让阅读回归纯粹。没有广告干扰，没有付费墙，只有你和故事之间最直接的连接。我们相信好的阅读体验不应该是奢侈品。' }),
    node('h2', { text: '产品特色' }),
    node('ul', {}, [
      node('li', { text: '全本小说免费在线阅读，支持多种阅读模式' }),
      node('li', { text: '深度拆书档案，从结构、节奏、文风多维度解析作品' }),
      node('li', { text: '智能书库检索，按题材、字数、连载状态精准筛选' }),
      node('li', { text: '多端同步，PC网页、手机网页、Android APP 随时切换' }),
      node('li', { text: 'TTS语音朗读从当前段落开始，解放双眼享受故事' }),
      node('li', { text: '字里行间段落评论，与其他读者交流理解；每人可为同一条评论点赞 3 次' })
    ]),
    node('h2', { text: '开源精神' }),
    node('p', { text: 'OOH Story 基于 MIT 协议开源，任何人都可以自由使用、修改和分发。我们欢迎社区的每一位贡献者。' })
  ], '/about')
}

function loadDisclaimer() {
  staticPage('免责声明', 'Disclaimer', [
    node('h2', { text: '内容声明' }),
    node('p', { text: '本站所有小说内容均由书友制作上传，电子书版权归原作者或出版社所有。本站仅提供知识阅读服务，不以盈利为目的。' }),
    node('h2', { text: '版权保护' }),
    node('p', { text: '本站尊重并保护知识产权。如果您发现本站收录的作品侵犯了您的权益，请及时与我们联系，我们将在确认后第一时间删除相关内容。' }),
    node('h2', { text: '免责条款' }),
    node('ul', {}, [
      node('li', { text: '本站不对所收录作品的内容准确性、完整性作任何保证' }),
      node('li', { text: '用户在本站的阅读行为产生的一切后果由用户自行承担' }),
      node('li', { text: '本站可能随时修改服务条款，用户继续使用即视为接受修改' }),
      node('li', { text: '因不可抗力导致的服务中断，本站不承担任何责任' })
    ]),
    node('h2', { text: '正版支持' }),
    node('p', { text: '本站支持和鼓励读者购买正版小说。如果您喜欢某本作品，请购买正版图书支持原作者的创作。' })
  ], '/disclaimer')
}

function loadGuide() {
  staticPage('网站指引', 'Site Guide', [
    node('h2', { text: '如何找书' }),
    node('p', { text: '首页提供人气推荐、经典长篇、精彩短篇等多个推荐板块。您也可以通过顶部搜索栏直接搜索书名或作者，或进入书库页面使用高级筛选功能。' }),
    node('h2', { text: '阅读功能' }),
    node('ul', {}, [
      node('li', { text: '字号调节：阅读页面点击底部设置按钮，拖动字号滑块调整' }),
      node('li', { text: '背景切换：支持纸质、白色、绿色、深色四种背景' }),
      node('li', { text: '章节导航：点击底部目录按钮查看全部章节' }),
      node('li', { text: 'TTS朗读：点击耳机按钮，从当前屏幕可见段落开始朗读' }),
      node('li', { text: '字里行间：长按正文段落后点击「字里行间」，查看或发布段落评论；辱骂、涉黄、涉毒、涉诈、博彩、联系方式和拼接链接均无法发布' }),
      node('li', { text: '阅读进度：自动保存阅读位置，下次打开自动续读' })
    ]),
    node('h2', { text: '主题切换' }),
    node('p', { text: '点击右上角配色按钮可在「柔蓝」「护眼」「深色」三种主题间切换，系统会记住您的选择。' }),
    node('h2', { text: '拆书档案' }),
    node('p', { text: '部分作品附带深度拆书档案，从叙事结构、角色塑造、情节节奏等多个维度对作品进行专业分析。点击顶部导航栏「拆书档案」即可浏览所有可用档案。' }),
    node('h2', { text: '客户端下载' }),
    node('p', { text: 'Android 用户可下载 OOH Story APP 获得更流畅的阅读体验。iOS 用户可通过 Safari 添加到主屏幕，获得类似原生 APP 的体验。' }),
    node('p', {}, [node('a', { href: '/client', text: '前往客户端下载页 →' })])
  ], '/guide')
}

function loadContact() {
  staticPage('联系我们', 'Contact Us', [
    node('p', { text: '如果您有任何问题、建议或版权相关事宜，欢迎通过以下方式联系我们。' }),
    node('div', { class: 'contact-grid' }, [
      node('div', { class: 'contact-item' }, [
        node('div', { class: 'contact-item-icon', text: '📧' }),
        node('div', { class: 'contact-item-text' }, [
          node('h4', { text: '电子邮件' }),
          node('p', { text: '请在部署时配置项目联系方式' })
        ])
      ]),
      node('div', { class: 'contact-item' }, [
        node('div', { class: 'contact-item-icon', text: '🐛' }),
        node('div', { class: 'contact-item-text' }, [
          node('h4', { text: '问题反馈' }),
          node('p', { text: '发现 Bug 或有功能建议，欢迎发送邮件反馈' })
        ])
      ]),
      node('div', { class: 'contact-item' }, [
        node('div', { class: 'contact-item-icon', text: '©️' }),
        node('div', { class: 'contact-item-text' }, [
          node('h4', { text: '版权事宜' }),
          node('p', { text: '如有侵权内容，请联系我们立即处理' })
        ])
      ]),
      node('div', { class: 'contact-item' }, [
        node('div', { class: 'contact-item-icon', text: '💡' }),
        node('div', { class: 'contact-item-text' }, [
          node('h4', { text: '合作洽谈' }),
          node('p', { text: '对项目感兴趣？欢迎联系洽谈合作' })
        ])
      ])
    ]),
    node('h2', { text: '反馈须知' }),
    node('ul', {}, [
      node('li', { text: '版权问题请附上作品名称、章节链接以及您的身份证明' }),
      node('li', { text: '功能建议请尽量描述使用场景和期望效果' }),
      node('li', { text: '我们通常会在 1-3 个工作日内回复' })
    ])
  ], '/contact')
}

function loadClient() {
  setSeo({ title: '客户端下载｜OOH Story', description: '下载 OOH Story 客户端，多端畅享阅读体验', canonicalPath: '/client' })
  const page = node('div', { class: 'static-page' }, [
    node('a', { class: 'static-page-back', href: '/', text: '← 返回首页' }),
    node('h1', { text: '客户端下载' }),
    node('p', { class: 'page-subtitle', text: '多端畅享，随时随地阅读好故事' }),
    node('div', { class: 'static-page-content' }, [
      node('div', { class: 'client-list' }, [
        node('div', { class: 'client-list-item' }, [
          node('div', { class: 'client-list-icon', text: '🤖' }),
          node('div', { class: 'client-list-info' }, [
            node('h3', { text: 'Android 客户端' }),
            node('p', { text: '原生 Android 应用，支持离线缓存、TTS 语音朗读、自定义阅读设置等全部功能。' }),
            node('span', { class: 'client-btn client-btn-secondary', text: '请从自己的 GitHub Releases 提供已签名安装包' })
          ])
        ]),
        node('div', { class: 'client-list-item' }, [
          node('div', { class: 'client-list-icon', text: '🍎' }),
          node('div', { class: 'client-list-info' }, [
            node('h3', { text: 'iOS Web App' }),
            node('p', { text: '通过 Safari 添加到主屏幕，获得接近原生 APP 的沉浸式阅读体验，无需从 App Store 下载。' }),
            node('span', { class: 'client-btn client-btn-secondary', text: '查看安装教程 ↓' }),
            node('div', { class: 'ios-steps' }, [
              node('h4', { text: '安装步骤' }),
              node('ol', {}, [
                node('li', { text: '使用 iPhone / iPad 的 Safari 浏览器打开本站首页' }),
                node('li', { text: '点击底部工具栏的「分享」按钮（方框加箭头图标）' }),
                node('li', { text: '在弹出菜单中选择「添加到主屏幕」' }),
                node('li', { text: '确认名称后点击「添加」' }),
                node('li', { text: '返回主屏幕即可看到 OOH Story 图标，点击即可使用' })
              ])
            ])
          ])
        ]),
        node('div', { class: 'client-list-item' }, [
          node('div', { class: 'client-list-icon', text: '💻' }),
          node('div', { class: 'client-list-info' }, [
            node('h3', { text: 'PC 端' }),
            node('p', { text: '直接在浏览器中访问本站即可享受完整功能，支持 Chrome、Firefox、Safari、Edge 等主流浏览器。推荐使用最新版本的浏览器以获得最佳体验。' })
          ])
        ])
      ])
    ])
  ])
  app.replaceChildren(page)
}

function updateNavigation() {
  const path = pathFromLocation()
  document.querySelectorAll('.site-header nav a').forEach(link => {
    const target = link.getAttribute('href')
    let active = false
    if (target === '/') active = path === '/'
    else if (target === '/library') active = path === '/library' || path.startsWith('/book/') || path.startsWith('/read/')
    else if (target === '/rankings') active = path === '/rankings'
    else if (target === '/deconstructions') active = path === '/deconstructions' || path.startsWith('/deconstruction/')
    link.classList.toggle('active', active)
  })
}

async function loadRankings() {
  setSeo({
    title: '排行榜｜热门小说排行 - OOH Story',
    description: '查看 OOH Story 热门小说排行榜，包括周点击榜、月点击榜、月推荐榜、新书榜、收藏榜、完本榜。',
    canonicalPath: '/rankings'
  })
  const data = await api('/api/v1/rankings', { cache: 'no-store' })
  const rankingPanel = buildRankingSection(data)
  app.replaceChildren(
    pageHeading('RANKINGS', '排行榜', '发现最受欢迎和最新的小说作品'),
    rankingPanel
  )
}

async function loadVolume(bookId, volId) {
  const libraryReturnPath = safeLibraryReturnPath(paramsFromHash().get('from'))
  const contextualHref = path => withLibraryReturn(path, libraryReturnPath)
  const [catalog, bookDetails] = await Promise.all([
    api(`/api/v1/books/${bookId}/chapters`),
    api(`/api/v1/books/${bookId}`)
  ])
  const vol = (catalog.volumes || []).find(v => v.id === Number(volId))
  if (!vol) throw new Error('分卷不存在')
  const book = { ...(catalog.book || {}), ...(bookDetails || {}) }
  const chapterMap = {}
  catalog.chapters.forEach(ch => { chapterMap[ch.id] = ch })
  const volChapters = vol.chapter_ids.map(cid => chapterMap[cid]).filter(Boolean)

  const volumeSeparator = '[\\s\\-–—_:：·・/\\\\]'
  const volumeParts = String(vol.title || '')
    .normalize('NFKC')
    .trim()
    .split(new RegExp(`${volumeSeparator}+`, 'u'))
    .filter(Boolean)
  const volumePrefix = volumeParts
    .map(part => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join(`${volumeSeparator}*`)
  const repeatedVolumePrefix = volumePrefix
    ? new RegExp(`^${volumeSeparator}*${volumePrefix}${volumeSeparator}+`, 'iu')
    : null
  const volumeChapterDisplayTitle = title => {
    const original = String(title || '').trim()
    if (!original || !repeatedVolumePrefix) return original
    const stripped = original.normalize('NFKC').replace(repeatedVolumePrefix, '').trim()
    return stripped || original
  }
  const genericVolumeTitle = /^第[0-9０-９一二三四五六七八九十百零〇两]+卷$/u.test(String(vol.title || '').trim())
  const volumeDisplayTitle = genericVolumeTitle && book.title
    ? `${book.title} ${String(vol.title || '').trim()}`
    : vol.title

  setSeo({
    title: `${volumeDisplayTitle}｜${book.title || ''}｜OOH Story`,
    description: `${volumeDisplayTitle} - ${volChapters.length} 章`,
    canonicalPath: `/books/${bookId}/volumes/${volId}`
  })

  const chapterList = node('div', { class: 'chapter-list' })
  volChapters.forEach((ch, i) => {
    chapterList.append(node('a', { class: 'chapter-link', href: contextualHref(`/books/${bookId}/chapters/${ch.id}`) }, [
      node('span', { class: 'chapter-index', text: `${i + 1}` }),
      node('strong', { text: volumeChapterDisplayTitle(ch.title) })
    ]))
  })

  const illustPaths = vol.illustration_paths || []
  let illustSection = null
  if (illustPaths.length > 0) {
    const illustGrid = node('div', { class: 'illust-grid' })
    illustPaths.forEach((p, i) => {
      const img = node('img', { alt: `插画 ${i + 1}` })
      img.addEventListener('error', () => img.parentElement.remove())
      coverLoader.observe(img, `/api/v1/books/${bookId}/illustrations/${encodeURI(p)}`)
      const wrap = node('div', { class: 'illust-item' })
      wrap.append(img)
      wrap.addEventListener('click', () => openIllustViewer(bookId, illustPaths, i))
      illustGrid.append(wrap)
    })
    illustSection = node('section', { class: 'chapter-panel' }, [
      node('div', { class: 'chapter-panel-head' }, [
        node('h2', { text: '插画' }),
        node('span', { class: 'tag', text: `${illustPaths.length} 张` })
      ]),
      illustGrid
    ])
  }

  const volCoverEl = node('div', { class: 'vol-detail-cover' })
  const renderVolumePlaceholder = () => {
    volCoverEl.classList.add('is-placeholder')
    volCoverEl.replaceChildren(
      node('span', { class: 'vol-placeholder-mark', text: 'LIGHT NOVEL' }),
      node('strong', { text: `第 ${vol.id} 卷` }),
      node('small', { text: vol.title })
    )
  }
  if (vol.cover_path) {
    const coverImg = node('img', { alt: vol.title })
    coverImg.addEventListener('error', renderVolumePlaceholder, { once: true })
    coverLoader.loadNow(coverImg, `/api/v1/books/${bookId}/illustrations/${encodeURI(vol.cover_path)}`)
    volCoverEl.append(coverImg)
  } else if (Number(vol.id) === 1 && book.cover_url && !book.cover_is_default) {
    const coverImg = node('img', { alt: `${book.title || vol.title} 封面` })
    coverImg.addEventListener('error', renderVolumePlaceholder, { once: true })
    coverLoader.loadNow(coverImg, book.cover_url)
    volCoverEl.append(coverImg)
  } else {
    renderVolumePlaceholder()
  }

  app.replaceChildren(node('div', { class: 'detail-page' }, [
    node('div', { class: 'detail-backbar' }, [
      node('a', { class: 'detail-back', href: contextualHref(`/books/${bookId}`), text: `← 返回《${book.title || ''}》` })
    ]),
    node('section', { class: 'vol-detail-layout' }, [
      volCoverEl,
      node('article', { class: 'detail-main' }, [
        node('span', { class: 'eyebrow', text: `第 ${vol.id} 卷` }),
        node('h1', { text: volumeDisplayTitle }),
        node('p', { class: 'detail-author', text: `${volChapters.length} 章${illustPaths.length ? ' · ' + illustPaths.length + ' 插画' : ''}` }),
        node('section', { class: 'chapter-panel' }, [
          node('div', { class: 'chapter-panel-head' }, [
            node('h2', { text: '章节目录' }),
            node('span', { class: 'tag', text: `${volChapters.length} 章` })
          ]),
          chapterList
        ]),
        illustSection
      ].filter(Boolean))
    ])
  ]))
}

function openIllustViewer(bookId, paths, startIndex) {
  let current = startIndex
  const overlay = node('div', { class: 'illust-viewer-overlay' })
  const img = node('img', { src: `/api/v1/books/${bookId}/illustrations/${encodeURI(paths[current])}` })
  const counter = node('span', { class: 'illust-viewer-counter', text: `${current + 1} / ${paths.length}` })
  const closeBtn = node('button', { class: 'illust-viewer-close', text: '✕', onclick: () => overlay.remove() })

  function update() {
    img.src = `/api/v1/books/${bookId}/illustrations/${encodeURI(paths[current])}`
    counter.textContent = `${current + 1} / ${paths.length}`
  }
  const prevBtn = node('button', { class: 'illust-viewer-prev', text: '‹', onclick: () => { if (current > 0) { current--; update() } } })
  const nextBtn = node('button', { class: 'illust-viewer-next', text: '›', onclick: () => { if (current < paths.length - 1) { current++; update() } } })

  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove() })
  overlay.addEventListener('keydown', e => {
    if (e.key === 'Escape') overlay.remove()
    else if (e.key === 'ArrowLeft' && current > 0) { current--; update() }
    else if (e.key === 'ArrowRight' && current < paths.length - 1) { current++; update() }
  })
  overlay.tabIndex = 0
  overlay.append(closeBtn, counter, prevBtn, img, nextBtn)
  document.body.append(overlay)
  overlay.focus()
}

async function route() {
  state.readerNavigation?.cancelTap?.()
  state.readerNavigation = null
  loading()
  updateNavigation()
  const path = pathFromLocation()
  const wasReader = document.body.classList.contains('reader-mode')
  document.body.classList.toggle('reader-mode', path.startsWith('/read/'))
  if (wasReader && !path.startsWith('/read/')) {
    setReaderScrollTop(null, 0, 'vertical')
    if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(() => {})
  }
  try {
    if (path === '/') await loadHome()
    else if (path === '/library') await loadLibrary()
    else if (path === '/deconstructions') await loadDeconstructions()
    else if (path === '/account') await loadAccountPage()
    else if (path === '/account/history') await loadAccountCollection('history')
    else if (path === '/account/favorites') await loadAccountCollection('favorites')
    else if (path === '/account/bookshelf') await loadAccountCollection('bookshelf')
    else if (path === '/account/submissions') await loadSubmissionPage()
    else if (path === '/account/notifications') await loadNotificationsPage()
    else if (path === '/account/profile') await loadProfilePage()
    else if (/^\/book\/[A-Za-z0-9_-]{22}\/volume\/\d+$/.test(path)) {
      const parts = path.split('/')
      await loadVolume(parts[2], parts[4])
    }
    else if (/^\/book\/[A-Za-z0-9_-]{22}$/.test(path)) await loadBook(path.split('/')[2])
    else if (/^\/read\/[A-Za-z0-9_-]{22}\/\d+$/.test(path)) {
      const [, , bookId, chapterId] = path.split('/')
      await loadReader(bookId, chapterId)
    } else if (path.startsWith('/deconstruction/')) {
      await loadDeconstruction(decodeURIComponent(path.slice('/deconstruction/'.length)))
    } else if (path === '/rankings') {
      await loadRankings()
    } else if (path === '/about') {
      loadAbout()
    } else if (path === '/disclaimer') {
      loadDisclaimer()
    } else if (path === '/guide') {
      loadGuide()
    } else if (path === '/contact') {
      loadContact()
    } else if (path === '/client') {
      loadClient()
    } else {
      throw new Error('页面不存在')
    }
  } catch (error) {
    setSeo({
      title: '页面暂时无法打开｜OOH Story',
      description: '当前页面不存在或暂时无法读取，请返回 OOH Story 首页继续浏览。',
      canonicalPath: '/',
      robots: 'noindex, nofollow'
    })
    errorView(error)
  }
  if (!path.startsWith('/read/')) window.scrollTo(0, 0)
  updateGlobalTtsReturn()
  app.focus({ preventScroll: true })
}

function updatePaletteButton() {
  const theme = document.documentElement.dataset.theme
  const labels = { paper: '柔蓝', 'eye-care': '深色', dark: '默认' }
  themeToggle.textContent = labels[theme] || '柔蓝'
  themeToggle.setAttribute('aria-label', `当前配色：${theme === 'eye-care' ? '柔蓝' : theme === 'dark' ? '深色' : '默认'}，点击切换`)
}

themeToggle.addEventListener('click', () => {
  const current = document.documentElement.dataset.theme
  const next = current === 'paper' ? 'eye-care' : current === 'eye-care' ? 'dark' : 'paper'
  document.documentElement.dataset.theme = next
  localStorageSet('oohstory-theme', next)
  updatePaletteButton()
})

accountButton.addEventListener('click', () => {
  if (state.account) location.hash = '#/account'
  else openAuthDialog('login')
})

document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && ttsEmotionSheet && !ttsEmotionSheet.hidden) {
    event.preventDefault()
    closeTtsEmotionSheet()
    return
  }
  if (event.key === 'Escape' && ttsPlayerIsOpen()) {
    event.preventDefault()
    closeTtsPlayer()
    return
  }
  const navigation = state.readerNavigation
  if (!navigation || event.altKey || event.ctrlKey || event.metaKey) return
  const target = event.target
  if (target instanceof HTMLElement && target.closest('input, textarea, select, [contenteditable="true"]')) return

  if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
    event.preventDefault()
    navigation.scrollBy({
      top: event.key === 'ArrowUp' ? -180 : 180,
      behavior: 'smooth'
    })
    return
  }

  if ((event.key === 'ArrowLeft' || event.key === 'ArrowRight') && navigation.mode?.() !== 'vertical') {
    event.preventDefault()
    navigation.changePage(event.key === 'ArrowLeft' ? -1 : 1)
    return
  }

  const chapterId = event.key === 'ArrowLeft' ? navigation.previousId
    : event.key === 'ArrowRight' ? navigation.nextId
      : null
  if (chapterId) {
    event.preventDefault()
    navigateInApp(`/books/${navigation.bookId}/chapters/${chapterId}`)
  }
})

document.addEventListener('click', event => {
  const chapterLink = event.target instanceof Element
    ? event.target.closest('a[href^="/books/"][href*="/chapters/"]')
    : null
  const link = event.target instanceof Element ? event.target.closest('a[href]') : null
  if (!link) return
  if (chapterLink) requestReaderFullscreen()
  if (!state.ttsSession?.active || link.hasAttribute('download') || link.target
    || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return
  let url
  try { url = new URL(link.href, location.href) } catch { return }
  if (url.origin !== location.origin || !/^https?:$/.test(url.protocol)) return
  event.preventDefault()
  navigateInApp(`${url.pathname}${url.search}${url.hash}`)
}, { capture: true })

globalTtsReturn?.addEventListener('click', openTtsPlayer)
ttsPlayerClose?.addEventListener('click', closeTtsPlayer)
ttsPlayerReturn?.addEventListener('click', returnTtsToReader)
ttsPlayerText?.addEventListener('click', returnTtsToReader)
ttsPlayerPrevious?.addEventListener('click', () => state.ttsController?.previous?.())
ttsPlayerNext?.addEventListener('click', () => state.ttsController?.next?.())
ttsPlayerToggle?.addEventListener('click', () => {
  if (!state.ttsSession?.active) return
  if (ttsSessionIsPlaying()) state.ttsController?.pause?.()
  else state.ttsController?.resume?.()
})
ttsPlayerRate?.addEventListener('click', () => {
  const rates = [0.8, 1, 1.2, 1.5, 2]
  const current = Number(state.reader.ttsRate || 1)
  const index = rates.findIndex(rate => Math.abs(rate - current) < .01)
  state.ttsController?.setRate?.(rates[(index + 1 + rates.length) % rates.length])
})
ttsPlayerMode?.addEventListener('click', () => {
  state.ttsController?.setMode?.(state.reader.ttsMode === 'smart' ? 'normal' : 'smart')
})
ttsPlayerEmotion?.addEventListener('click', openTtsEmotionSheet)
ttsEmotionClose?.addEventListener('click', closeTtsEmotionSheet)
ttsPlayerStop?.addEventListener('click', () => {
  state.ttsController?.stop?.()
  closeTtsPlayer()
})

const savedTheme = localStorageGet('oohstory-theme')
document.documentElement.dataset.theme = ['eye-care', 'dark'].includes(savedTheme) ? savedTheme : 'paper'
updatePaletteButton()
window.addEventListener('hashchange', route)
window.addEventListener('popstate', route)
applyReaderSettings()

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
