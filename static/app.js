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
  cloudSyncGeneration: 0,
  routeGeneration: 0,
  readingActivity: null,
  ttsController: null,
  ttsSession: null
}
// Keep one media element for the lifetime of the page. Safari/iOS grants
// autoplay permission to the element that received the user's initial tap;
// replacing it while routing or re-entering listening loses that permission.
let ttsAudioEl = null
let ttsAudioUnlockPromise = null
let ttsAudioUnlocked = false
let ttsAudioUnlockGeneration = 0
let ttsProgressTimer = null
const TTS_AUDIO_UNLOCK_SRC = 'data:audio/wav;base64,UklGRrQBAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YZABAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICA'
const TTS_CHECKPOINT_STORAGE_KEY = 'oohstory-tts-checkpoint'
const TTS_STREAM_BATCH_SEGMENTS = 5
const AUDIOBOOK_CLIENT_STORAGE_KEY = 'oohstory-audiobook-client-id'
const audiobookClientId = (() => {
  let value = localStorage.getItem(AUDIOBOOK_CLIENT_STORAGE_KEY) || ''
  if (!/^[A-Za-z0-9_-]{16,96}$/.test(value)) {
    const bytes = crypto.getRandomValues(new Uint8Array(18))
    value = btoa(String.fromCharCode(...bytes)).replace(/[+/=]/g, '')
    localStorage.setItem(AUDIOBOOK_CLIENT_STORAGE_KEY, value)
  }
  return value
})()
window.OOHStoryAudiobookClientId = audiobookClientId
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
    absoluteItemIndex: Math.max(0, Number(session.absoluteItemIndex ?? session.itemIndex) || 0),
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
  weary: { label: '疲惫', desc: '缓慢虚弱、气息低落' },
  surprised: { label: '惊讶', desc: '语调上扬、反应鲜明' },
  comforting: { label: '安抚', desc: '柔和放慢、给予安全感' },
  confident: { label: '笃定', desc: '节奏稳健、语气有力' },
  shy: { label: '羞怯', desc: '轻柔迟疑、情绪内收' },
  disgusted: { label: '厌恶', desc: '冷硬克制、排斥感突出' },
  whispering: { label: '低语', desc: '压低声线、贴近耳语' }
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
  const absoluteItemIndex = Math.max(0, Number(session.absoluteItemIndex ?? itemIndex) || 0)
  const absoluteItemCount = Math.max(1, Number(session.absoluteItemCount ?? itemCount) || 1)
  // The reader cursor is paragraph-based while smart narration can split one
  // paragraph into several speaker segments. Showing segment progress beside
  // a paragraph cursor makes the two appear to drift even when playback is
  // correct. Keep transcript navigation segment-based, but expose reading
  // progress through the same paragraph coordinate as the highlighted line.
  const paragraphIndex = Math.max(0, Number(session.paragraphIndex) || 0)
  const paragraphCount = Math.max(1, Number(session.paragraphCount) || 1)
  const progress = Math.min(100, ((paragraphIndex + 1) / paragraphCount) * 100)
  const mode = ttsPlayerModeLabels[state.reader.ttsMode] || ttsPlayerModeLabels.normal
  const selectedEmotion = ttsEmotionModes[state.reader.ttsEmotion] || ttsEmotionModes.auto
  const activeEmotion = ttsEmotionModes[session.currentEmotion] || ttsEmotionModes.neutral
  const isPlaying = ttsSessionIsPlaying()
  const isBlocked = Boolean(session.playbackBlocked)
  const isConnecting = Boolean(session.playbackConnecting)
  ttsPlayer.classList.toggle('is-playing', isPlaying)
  ttsPlayer.classList.toggle('is-paused', !isPlaying)
  ttsPlayer.classList.toggle('is-blocked', isBlocked)
  if (ttsPlayerHeading) ttsPlayerHeading.textContent = isBlocked
    ? '等待继续播放'
    : isConnecting
      ? String(session.playbackStatusText || '正在连接音频')
      : isPlaying ? '正在播放' : '已暂停'
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
  const ttsCtrl = state.ttsController
  if (ttsPlayerPrevious) ttsPlayerPrevious.disabled = !ttsCtrl?.hasPreviousChapter?.()
  if (ttsPlayerNext) ttsPlayerNext.disabled = !ttsCtrl?.hasNextChapter?.()
  if (ttsPlayerProgressCopy) ttsPlayerProgressCopy.textContent = `第 ${Math.min(paragraphCount, paragraphIndex + 1)} / ${paragraphCount} 段`
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
  ttsPlayer.scrollTop = 0
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

function routeIsCurrent(generation, expectedPath = '') {
  if (generation !== state.routeGeneration) return false
  return !expectedPath || pathFromLocation() === expectedPath
}

function isSpaNavigationTarget(url) {
  if (url.hash && !url.hash.startsWith('#/')) return false
  if (url.hash.startsWith('#/')) return true
  const pathname = url.pathname.replace(/\/+$/, '') || '/'
  return pathname === '/'
    || ['/library', '/rankings', '/deconstructions', '/account'].includes(pathname)
    || /^\/(?:about|disclaimer|guide|contact|client)$/.test(pathname)
    || /^\/account\/(?:history|favorites|bookshelf|deconstruction-tasks|submit|submissions|notifications|profile)$/.test(pathname)
    || /^\/admin(?:\/.*)?$/.test(pathname)
    || /^\/books\/[A-Za-z0-9_-]{22}$/.test(pathname)
    || /^\/books\/[A-Za-z0-9_-]{22}\/chapters\/\d+$/.test(pathname)
    || /^\/books\/[A-Za-z0-9_-]{22}\/volumes\/\d+$/.test(pathname)
    || /^\/deconstructions\/[^/]+$/.test(pathname)
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

const EDGE_RECOVERY_MARKER = '__cf_recover'
const EDGE_RECOVERY_SESSION_KEY = 'oohstory-edge-recovery-at'
const EDGE_RECOVERY_GUARD_MS = 15000
let edgeRecoveryInFlight = false
let edgeHiddenAt = 0

function isCloudflareEdgeChallenge(response) {
  if (!response || response.status !== 403) return false
  const mitigated = String(response.headers.get('cf-mitigated') || '').toLowerCase()
  const contentType = String(response.headers.get('content-type') || '').toLowerCase()
  return mitigated === 'challenge'
    || (response.headers.has('cf-ray') && contentType.includes('text/html'))
}

function edgeRetryUrl(input) {
  const raw = typeof input === 'string' ? input : input?.url
  if (!raw) return input
  const url = new URL(raw, location.href)
  if (url.origin !== location.origin) return input
  url.searchParams.set('__edge_retry', String(Date.now()))
  return `${url.pathname}${url.search}${url.hash}`
}

function clearEdgeRecoveryMarker() {
  try { sessionStorage.removeItem(EDGE_RECOVERY_SESSION_KEY) } catch (_error) {}
  const url = new URL(location.href)
  if (!url.searchParams.has(EDGE_RECOVERY_MARKER)) return
  url.searchParams.delete(EDGE_RECOVERY_MARKER)
  history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`)
}

function beginEdgeRecovery() {
  const now = Date.now()
  let lastAttempt = 0
  try { lastAttempt = Number(sessionStorage.getItem(EDGE_RECOVERY_SESSION_KEY) || 0) } catch (_error) {}
  if (now - lastAttempt < EDGE_RECOVERY_GUARD_MS) {
    const error = new Error('Cloudflare 安全验证尚未恢复，请点击“重新加载”后继续')
    error.edgeChallenge = true
    throw error
  }
  try { sessionStorage.setItem(EDGE_RECOVERY_SESSION_KEY, String(now)) } catch (_error) {}
  edgeRecoveryInFlight = true
  const url = new URL(location.href)
  url.searchParams.set(EDGE_RECOVERY_MARKER, String(now))
  window.setTimeout(() => location.replace(`${url.pathname}${url.search}${url.hash}`), 0)
  const error = new Error('正在重新连接 Cloudflare 安全验证…')
  error.edgeRecovery = true
  throw error
}

async function edgeFetch(input, options = {}, { recoverNavigation = true } = {}) {
  const method = String(options.method || input?.method || 'GET').toUpperCase()
  let response = await fetch(input, options)
  if (!isCloudflareEdgeChallenge(response)) {
    if (response.ok) clearEdgeRecoveryMarker()
    return response
  }
  if (['GET', 'HEAD'].includes(method)) {
    await new Promise(resolve => window.setTimeout(resolve, 180))
    response = await fetch(edgeRetryUrl(input), { ...options, cache: 'no-store' })
    if (!isCloudflareEdgeChallenge(response)) {
      if (response.ok) clearEdgeRecoveryMarker()
      return response
    }
  }
  if (recoverNavigation) beginEdgeRecovery()
  return response
}

window.OOHStoryEdgeFetch = edgeFetch

const probeEdgeSessionAfterResume = () => {
  if (document.hidden || edgeRecoveryInFlight) return
  edgeFetch(`/healthz?edge_probe=${Date.now()}`, {
    method: 'HEAD', credentials: 'same-origin', cache: 'no-store'
  }).catch(error => {
    if (!error?.edgeRecovery && !error?.edgeChallenge) {
      console.warn('[Edge] Cloudflare session probe unavailable', error)
    }
  })
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    edgeHiddenAt = Date.now()
    return
  }
  if (edgeHiddenAt && Date.now() - edgeHiddenAt >= 300000) probeEdgeSessionAfterResume()
  edgeHiddenAt = 0
})
window.addEventListener('pageshow', event => {
  if (event.persisted) probeEdgeSessionAfterResume()
})

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {})
  headers.set('Accept', 'application/json')
  const response = await edgeFetch(path, { ...options, headers })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`)
  return data
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
  if (validReaderCatalog(stored, bookId)) {
    state.readerCatalogs.set(key, stored)
    return stored
  }
  const request = api(`/api/v1/books/${bookId}/chapters`).then(data => {
    if (!validReaderCatalog(data, bookId)) throw new Error('章节目录响应无效')
    state.readerCatalogs.set(key, data)
    try {
      sessionStorage.setItem(readerCatalogCacheKey(bookId), JSON.stringify(data))
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

function isFrontMatterChapter(chapter) {
  return chapter?.is_front_matter === true
    || (
      String(chapter?.label || '').trim() === '序'
      && String(chapter?.title || '').trim() === '作品信息'
    )
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
  const isNumberedChapterLabel = value =>
    /^第?\s*[零〇一二三四五六七八九十百千万两\d]+\s*[章节回卷篇部集]$/u.test(value)
  const isGenericLabel = value => !value
    || value === '正文'
    || isNumberedChapterLabel(value)

  if (!isMissingTitle(title) && title !== label) {
    return { label: label || fallback, title }
  }
  // Preserve an explicit source ordinal. Front matter may occupy an earlier
  // section slot, so the array position is not necessarily the chapter number.
  if (!isMissingTitle(label) && isNumberedChapterLabel(label)) {
    return { label: '', title: label }
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

function libraryReturnStorageKey(bookId) {
  return `oohstory.library.return.v1:${String(bookId || '').trim()}`
}

function rememberLibraryReturnPath(bookId, value) {
  const libraryPath = safeLibraryReturnPath(value)
  if (!bookId || libraryPath === '/library') return libraryPath
  try {
    sessionStorage.setItem(libraryReturnStorageKey(bookId), libraryPath)
  } catch {
    // Storage may be disabled; clean crawlable URLs still take precedence.
  }
  return libraryPath
}

function libraryReturnPathFor(bookId) {
  const explicit = paramsFromHash().get('from')
  if (explicit) return rememberLibraryReturnPath(bookId, explicit)
  try {
    return safeLibraryReturnPath(sessionStorage.getItem(libraryReturnStorageKey(bookId)))
  } catch {
    return '/library'
  }
}

function withLibraryReturn(path, returnTo) {
  const libraryPath = safeLibraryReturnPath(returnTo)
  const url = new URL(path, location.origin)
  const bookMatch = url.pathname.match(/^\/books\/([A-Za-z0-9_-]{22})(?:\/|$)/)
  if (bookMatch) rememberLibraryReturnPath(bookMatch[1], libraryPath)
  url.searchParams.delete('from')
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
    pageHeading('THE LIBRARY', '全局书库', '海量收录小说免费阅读，输入书名或作者即可搜索。'),
    filterPanel,
    toolbar,
    node('p', { class: 'result-meta', text: `找到 ${formatNumber(data.total)} 本可读作品` }),
    grid,
    pagination,
    node('div', { style: 'height:80px' })
  )
}

async function loadBook(bookId) {
  const libraryReturnPath = libraryReturnPathFor(bookId)
  const contextualHref = path => withLibraryReturn(path, libraryReturnPath)
  const [book, catalog, metrics, recommendationState, initialBookComments] = await Promise.all([
    api(`/api/v1/books/${bookId}`),
    api(`/api/v1/books/${bookId}/chapters`),
    api(`/api/v1/books/${bookId}/metrics`, { cache: 'no-store' }),
    state.account
      ? accountApi(`/api/v1/books/${bookId}/recommendation`).catch(() => null)
      : Promise.resolve(null),
    accountApi(`/api/v1/books/${bookId}/comments`).catch(() => ({ comments: [], comment_count: 0 }))
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
    const eagerVolumeCoverCount = window.matchMedia('(max-width: 720px)').matches ? 3 : 6
    catalog.volumes.forEach((vol, volumeIndex) => {
      const coverEl = node('div', { class: 'vol-cover-wrap' })
      if (vol.cover_path) {
        const img = node('img', { alt: vol.title })
        img.addEventListener('error', () => img.remove())
        const coverUrl = `/api/v1/books/${bookId}/illustrations/${encodeURI(vol.cover_path)}`
        if (volumeIndex < eagerVolumeCoverCount) coverLoader.loadNow(img, coverUrl)
        else coverLoader.observe(img, coverUrl)
        coverEl.append(img)
      } else if (Number(vol.id) === 1 && book.cover_url && !book.cover_is_default) {
        const img = node('img', { alt: `${book.title} 封面` })
        img.addEventListener('error', () => img.remove())
        if (volumeIndex < eagerVolumeCoverCount) coverLoader.loadNow(img, book.cover_url)
        else coverLoader.observe(img, book.cover_url)
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
  let chaptersAscending = true
  const sortBtn = node('button', { class: 'chapter-sort-btn', type: 'button' })
  sortBtn.textContent = '正序 ↓'
  sortBtn.addEventListener('click', () => {
    chaptersAscending = !chaptersAscending
    sortBtn.textContent = chaptersAscending ? '正序 ↓' : '倒序 ↑'
    const displayChapters = chaptersAscending
      ? [...catalog.chapters]
      : [...catalog.chapters].reverse()
    chapterList.replaceChildren()
    displayChapters.forEach((chapter) => {
      const origIndex = catalog.chapters.indexOf(chapter)
      const presentation = chapterPresentation(chapter, origIndex)
      chapterList.append(node('a', { class: 'chapter-link', href: contextualHref(`/books/${bookId}/chapters/${chapter.id}`) }, [
        presentation.label ? node('span', { text: presentation.label }) : null,
        node('strong', { text: presentation.title })
      ]))
    })
  })
  const chapterPanel = node('section', { class: `chapter-panel${hasVolumes ? ' volume-gallery-panel' : ''}` }, [
    node('div', { class: 'chapter-panel-head' }, [
      node('div', {}, [
        hasVolumes ? node('span', { class: 'eyebrow', text: 'VOLUME GALLERY' }) : null,
        node('h2', { text: hasVolumes ? '分卷封面与目录' : '章节目录' })
      ]),
      node('div', { class: 'chapter-panel-head-right' }, [
        !hasVolumes ? sortBtn : null,
        node('span', { class: 'tag', text: hasVolumes
          ? `共 ${catalog.volumes.length} 卷 · ${formatNumber(catalog.chapter_count)} 章`
          : `${formatNumber(catalog.chapter_count)} 章`
        })
      ])
    ]),
    chapterList
  ])
  const volumeCoverPreview = null
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
  const resumeCandidate = savedProgress
    ? catalog.chapters.find(chapter => Number(chapter.id) === Number(savedProgress.chapterId))
    : null
  const resumeChapter = resumeCandidate && !isFrontMatterChapter(resumeCandidate)
    ? resumeCandidate
    : null
  const firstChapter = catalog.chapters.find(chapter =>
    Number(chapter.id) === Number(catalog.first_chapter_id)
  ) || catalog.chapters.find(chapter => !isFrontMatterChapter(chapter)) || catalog.chapters[0]
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
    book.deconstruction_slug ? node('a', {
      class: 'ghost-button',
      href: `/deconstructions/${encodeURIComponent(book.deconstruction_slug)}`,
      text: '拆书档案'
    }) : null
  ])
  const sourcePanel = null
  let bookCommentState = initialBookComments || { comments: [], comment_count: 0 }
  const bookCommentSection = node('section', { class: 'book-comment-panel' })
  const renderBookComments = () => {
    const comments = Array.isArray(bookCommentState.comments) ? bookCommentState.comments : []
    const list = node('div', { class: 'book-comment-list' })
    if (!comments.length) {
      list.append(node('div', { class: 'book-comment-empty' }, [
        node('span', { text: '💬' }), node('strong', { text: '还没有书评' }),
        node('p', { text: '读完简介或章节后，留下第一条阅读感受。' })
      ]))
    } else comments.forEach(comment => {
      const author = comment.author || {}
      const reading = author.reading || {}
      const viewerLikes = Math.max(0, Math.min(3, Number(comment.viewer_like_count || 0)))
      const totalLikes = Number(comment.like_count ?? comment.thanks_count ?? 0)
      const likeButton = node('button', {
        class: `interline-like${viewerLikes ? ' active' : ''}${viewerLikes >= 3 ? ' maxed' : ''}`,
        type: 'button',
        disabled: comment.is_own || viewerLikes >= 3 ? '' : null,
        text: comment.is_own ? `收到点赞 · ${totalLikes}` : viewerLikes >= 3
          ? `已点满 3/3 · ${totalLikes}` : viewerLikes
            ? `再赞一次 ${viewerLikes}/3 · ${totalLikes}` : `♡ 点赞 · ${totalLikes}`
      })
      if (!comment.is_own && viewerLikes < 3) likeButton.onclick = async () => {
        if (!state.account) { openAuthDialog('login', '登录后才能为书评点赞。'); return }
        likeButton.disabled = true
        try {
          await accountApi(`/api/v1/comments/${comment.id}/likes`, { method: 'POST' })
          bookCommentState = await accountApi(`/api/v1/books/${bookId}/comments`)
          renderBookComments()
        } catch (error) {
          window.alert(error.message || '暂时无法点赞')
          likeButton.disabled = false
        }
      }
      list.append(node('article', { class: 'book-comment-card' }, [
        node('header', {}, [
          node('div', { class: 'book-comment-author' }, [
            node('span', { class: 'book-comment-avatar', text: accountInitials(author) }),
            node('div', {}, [node('strong', { text: author.display_name || '读者' }),
              node('span', { class: 'book-comment-rank' }, [
                readingRankIcon(reading, { decorative: true }),
                node('span', { text: `${reading.roman || 'Ⅰ'} · ${reading.name || '只如初见'}` })
              ])])
          ]),
          node('time', { datetime: comment.created_at || '', text: comment.created_at ? new Date(comment.created_at).toLocaleString('zh-CN') : '' })
        ]),
        node('p', { text: comment.content || '' }),
        node('footer', {}, [likeButton])
      ]))
    })
    const composer = node('div', { class: 'book-comment-composer' })
    if (!state.account) {
      composer.append(node('button', { class: 'ghost-button', type: 'button', text: '登录后发表评论', onclick: () => openAuthDialog('login') }))
    } else {
      const textarea = node('textarea', { maxlength: '500', rows: '3', placeholder: '写下你对这本书的感受…', 'aria-label': '书籍评论内容' })
      const counter = node('span', { text: '0 / 500' })
      const submit = node('button', { class: 'primary-button', type: 'button', text: '发布评论' })
      textarea.addEventListener('input', () => { counter.textContent = `${[...textarea.value].length} / 500` })
      submit.onclick = async () => {
        const content = textarea.value.trim()
        if (!content) return
        const issue = localUserContentIssue(content)
        if (issue) { openUserContentNotice(issue, { returnFocus: textarea }); return }
        submit.disabled = true
        try {
          bookCommentState = await accountApi(`/api/v1/books/${bookId}/comments`, { method: 'POST', body: { content } })
          renderBookComments()
        } catch (error) {
          if (isUserContentGuardIssue(error.message)) openUserContentNotice(error.message, { returnFocus: textarea })
          else window.alert(error.message || '评论无法发布')
          submit.disabled = false
        }
      }
      composer.append(textarea, node('div', { class: 'book-comment-composer-actions' }, [counter, submit]))
    }
    bookCommentSection.replaceChildren(
      node('div', { class: 'book-comment-head' }, [
        node('div', {}, [node('span', { class: 'eyebrow', text: 'BOOK DISCUSSION' }), node('h2', { text: '读者评论' })]),
        node('span', { class: 'tag', text: `${comments.length} 条` })
      ]), list, composer
    )
  }
  renderBookComments()
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
        volumeCoverPreview,
        node('div', { class: 'tag-row' }, tags.map(tag => node('span', { class: 'tag', text: tag }))),
        actionRow,
        sourcePanel,
        hasVolumes ? chapterPanel : null,
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
        hasVolumes ? null : chapterPanel,
        bookCommentSection
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
    const deltaX = touch.clientX - touchStartX
    const deltaY = touch.clientY - touchStartY
    if (Math.abs(deltaX) > 10 || Math.abs(deltaY) > 10) {
      touchMoved = true
    }
    // Paginated reading owns horizontal swipes. Cancelling the native pan
    // prevents iOS Safari's edge-history gesture from racing our chapter
    // navigation and restoring an older reader route.
    if (state.reader.mode !== 'vertical'
      && Math.abs(deltaX) > 12
      && Math.abs(deltaX) > Math.abs(deltaY)) {
      event.preventDefault()
    }
  }, { passive: false })
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

async function loadReader(bookId, chapterId, navigationGeneration = state.routeGeneration) {
  const requestedBookId = String(bookId)
  const requestedChapterId = Number(chapterId)
  const expectedReaderPath = `/read/${requestedBookId}/${requestedChapterId}`
  const libraryReturnPath = libraryReturnPathFor(bookId)
  const contextualHref = path => withLibraryReturn(path, libraryReturnPath)
  const [chapter, catalog, initialChapterComments] = await Promise.all([
    getReaderChapter(requestedBookId, requestedChapterId),
    getReaderCatalog(requestedBookId),
    api(`/api/v1/books/${requestedBookId}/chapters/${requestedChapterId}/comments`)
      .catch(() => ({ paragraphs: {}, comment_count: 0 }))
  ])
  if (!routeIsCurrent(navigationGeneration, expectedReaderPath)) return
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
  let chapterNavigationPending = false
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
  let ttsExitButton = null
  let ttsStateBar = null
  let ttsExitControl = null
  let ttsParagraphIndex = -1
  let ttsPendingHighlightIndex = null, ttsHighlightRetryFrame = null, ttsHighlightRetryAttempts = 0
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

  const readerParagraphIndex = paragraph => {
    const value = Number(paragraph?.dataset?.ttsIndex ?? paragraph?.dataset?.paragraphIndex)
    return Number.isFinite(value) ? Math.max(0, value) : -1
  }

  const readerParagraphFromPoint = (clientX, clientY, fallbackParagraph = null) => {
    if (!readerContent) return null
    const x = Number(clientX)
    const y = Number(clientY)
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null
    const insideReader = paragraph => (
      paragraph && readerContent.contains(paragraph) ? paragraph : null
    )
    const directParagraph = document.elementsFromPoint?.(x, y)
      ?.map(element => element.closest?.('.reader-paragraph'))
      .find(insideReader)
    if (directParagraph) return directParagraph
    const caretNode = document.caretPositionFromPoint?.(x, y)?.offsetNode
      || document.caretRangeFromPoint?.(x, y)?.startContainer
    const caretElement = caretNode?.nodeType === Node.TEXT_NODE
      ? caretNode.parentElement
      : caretNode
    const caretParagraph = insideReader(caretElement?.closest?.('.reader-paragraph'))
    if (caretParagraph) return caretParagraph
    const fallback = insideReader(fallbackParagraph)
    if (fallback) {
      const rect = fallback.getBoundingClientRect()
      if (x >= rect.left - 8 && x <= rect.right + 8 && y >= rect.top - 8 && y <= rect.bottom + 8) {
        return fallback
      }
    }
    let nearest = null
    let nearestDistance = Infinity
    readerContent.querySelectorAll('.reader-paragraph').forEach(paragraph => {
      const rect = paragraph.getBoundingClientRect()
      if (rect.width <= 0 || rect.height <= 0) return
      const xDistance = x < rect.left ? rect.left - x : x > rect.right ? x - rect.right : 0
      if (xDistance > Math.max(48, rect.width * 0.25)) return
      const yDistance = y < rect.top ? rect.top - y : y > rect.bottom ? y - rect.bottom : 0
      const distance = yDistance + xDistance * 0.15
      if (distance < nearestDistance) {
        nearestDistance = distance
        nearest = paragraph
      }
    })
    return nearestDistance <= 96 ? nearest : null
  }

  const updateCurrentParagraphFromPoint = (clientX, clientY, fallbackParagraph = null) => {
    const paragraph = readerParagraphFromPoint(clientX, clientY, fallbackParagraph)
    const index = readerParagraphIndex(paragraph)
    if (index >= 0) currentParagraphHint = index
    return paragraph
  }

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
    if (!id || chapterNavigationPending) return false
    chapterNavigationPending = true
    if (!automatic) stopAutoReading()
    flushReadingProgress()
    state.readerAutoContinue = Boolean(automatic)
    const navigated = navigateInApp(contextualHref(`/books/${requestedBookId}/chapters/${id}`))
    if (!navigated) chapterNavigationPending = false
    return navigated
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
    if (state.reader.mode === 'cover' && direction !== 'none') {
      readerContent.style.transition = 'none'
      readerContent.style.transform = `translate3d(${x}px,0,0)`
      readerContent.style.clipPath = direction === 'next'
        ? 'inset(0 0 0 100%)' : 'inset(0 100% 0 0)'
      void readerContent.offsetWidth
      readerContent.style.transition = 'clip-path 0.34s cubic-bezier(0.16, 0, 0.1, 1)'
      readerContent.style.clipPath = 'inset(0)'
      pageAnimationTimer = window.setTimeout(() => {
        readerContent.style.clipPath = ''
        readerContent.style.transition = ''
      }, 380)
    } else if (state.reader.mode === 'simulation' && direction !== 'none') {
      readerContent.style.transformOrigin = direction === 'next' ? 'right center' : 'left center'
      readerContent.style.transform = `translate3d(${x}px,0,0) rotateY(${direction === 'next' ? '-20deg' : '20deg'})`
      pageAnimationTimer = window.setTimeout(() => {
        readerContent.style.transform = `translate3d(${x}px,0,0) rotateY(0deg)`
      }, 500)
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

  const ttsVoicePolicyPromise = window.oohstoryTtsVoicePolicyPromise || (window.oohstoryTtsVoicePolicyPromise = fetch('/api/v1/tts/voices', { credentials: 'same-origin' }).then(response => {
    if (!response.ok) throw new Error(`voice registry ${response.status}`)
    return response.json()
  }))
  let ttsChapterPlan = []
  let ttsPlanIndex = 0
  let ttsNextChapterPlan = []
  let ttsNextChapterSignature = ''
  let ttsNextChapterId = null
  let ttsNextChapterFollowingId = null
  let ttsNextChapterTitle = ''
  let ttsFollowingChapterId = nextId
  let ttsNextChapterCached = false
  let ttsHeartbeat = null
  let ttsPlanGeneration = 0
  let ttsRebuildTimer = null
  let ttsRebuildRequested = false
  const ttsLifecycle = window.OOHStoryAudiobookLifecycle.create()
  let ttsFailureToast = null
  const ttsOwner = {}
  let audiobookServerSessionId = ''
  let audiobookAbortController = null
  let audiobookManifestHash = ''
  let ttsChapterStreamUrl = ''
  let ttsNextChapterStreamUrl = '', ttsContinuousStreamMode = false
  let ttsNextChapterPrefetchPromise = null, ttsNextChapterPrefetchSourceId = ''
  let ttsChapterTransitionPromise = null, ttsStreamCompletionPromise = null
  let ttsChapterEndWatchdogTimer = null, ttsChapterEndWatchdogAttempts = 0
  let ttsStreamStartPlanIndex = 0
  let ttsStreamResumeBaseSeconds = 0
  let ttsTimelineLoadedThrough = -1
  let ttsActiveStreamId = '', ttsStreamEnding = false
  let ttsResumeOffsetSeconds = 0
  let ttsLastTimelineRefresh = 0
  let ttsTimelinePromise = null
  let ttsLastProgressAt = 0
  let ttsSegmentFallbackMode = false
  let ttsFallbackPlayback = null
  let ttsBatchPreloadKey = '', ttsBatchPreloadPromise = null
  let ttsLastStreamCurrentTimeSeconds = 0, ttsTrustedPlanIndex = 0, ttsTrustedItemOffsetSeconds = 0, ttsReplayRecoveryPromise = null
  let ttsHlsMode = false, ttsHlsQueueId = '', ttsHlsStatusUrl = ''
  let ttsHlsItems = [], ttsHlsQueueIndex = 0, ttsHlsRefreshTimer = null, ttsHlsRefreshPromise = null
  let ttsHlsNavigatingChapterId = '', ttsHlsStartOffsetSeconds = 0

  const ttsIllustLine = /^\[illustration:.+\]$/
  const ttsParagraphs = () => {
    if (!readerContent) return []
    return Array.from(readerContent.querySelectorAll('.reader-paragraph'))
      .map(paragraph => paragraph.dataset.paragraphText || '')
      .filter(Boolean)
  }

  const ttsScheduleHighlightRetry = index => {
    ttsPendingHighlightIndex = Number(index); if (ttsHighlightRetryFrame) return
    ttsHighlightRetryFrame = requestAnimationFrame(() => { ttsHighlightRetryFrame = null; const pending = ttsPendingHighlightIndex; if (pending == null || ttsHighlightRetryAttempts >= 12) { ttsPendingHighlightIndex = null; ttsHighlightRetryAttempts = 0; return } ttsHighlightRetryAttempts++; ttsHighlight(pending) })
  }
  const ttsHighlight = index => {
    const paragraph = readerContent?.querySelector?.(`.reader-paragraph[data-tts-index="${Number(index)}"]`)
    if (!paragraph) { ttsScheduleHighlightRetry(index); return }
    ttsPendingHighlightIndex = null; ttsHighlightRetryAttempts = 0; ttsClearHighlight()
    paragraph.classList.add('tts-active-line')
    paragraph.scrollIntoView({ behavior: 'smooth', block: window.matchMedia('(max-width: 720px)').matches ? 'start' : 'center' })
  }
  const ttsClearHighlight = () => readerContent?.querySelectorAll('.tts-active-line').forEach(el => el.classList.remove('tts-active-line'))
  const ttsActiveHighlightPresent = index => Boolean(readerContent?.querySelector?.(`.reader-paragraph.tts-active-line[data-tts-index="${Number(index)}"]`))
  // Keep one Audio instance for the lifetime of the page. Mobile Safari grants
  // playback permission to the element created from the user's tap, so chapter
  // changes and player re-renders must reuse this instance.
  const ttsEnsureAudio = () => {
    if (!ttsAudioEl) {
      ttsAudioEl = new Audio()
      ttsAudioEl.preload = 'auto'
      ttsAudioEl.playsInline = true
      ttsAudioEl.muted = false
      ttsAudioEl.volume = 1
    }
    return ttsAudioEl
  }

  // Creating an Audio element during a tap is not enough on Safari/iOS. A
  // real play() call must happen in the same user-activation stack; otherwise
  // the asynchronously generated first segment is accepted but remains
  // inaudible until a second tap. Prime the long-lived element with 50 ms of
  // PCM silence, then reuse that exact element for the active session.
  const ttsPrimeAudioFromGesture = () => {
    const audio = ttsEnsureAudio()
    if (ttsAudioUnlocked || (!audio.paused && !audio.ended)) return Promise.resolve(true)
    if (ttsAudioUnlockPromise) return ttsAudioUnlockPromise
    const unlockGeneration = ++ttsAudioUnlockGeneration
    audio.muted = false
    audio.volume = 1
    audio.src = TTS_AUDIO_UNLOCK_SRC
    try {
      const unlockPromise = Promise.resolve(audio.play()).then(() => {
        if (unlockGeneration !== ttsAudioUnlockGeneration || audio !== ttsAudioEl) return false
        ttsAudioUnlocked = true
        return true
      }).catch(error => {
        if (unlockGeneration === ttsAudioUnlockGeneration && audio === ttsAudioEl) {
          console.warn('[TTS] initial audio unlock deferred', error)
        }
        return false
      })
      ttsAudioUnlockPromise = unlockPromise
      unlockPromise.finally(() => {
        if (ttsAudioUnlockPromise === unlockPromise) ttsAudioUnlockPromise = null
      })
    } catch (error) {
      if (unlockGeneration === ttsAudioUnlockGeneration && audio === ttsAudioEl) {
        ttsAudioUnlockPromise = null
        console.warn('[TTS] initial audio unlock deferred', error)
      }
      return Promise.resolve(false)
    }
    return ttsAudioUnlockPromise
  }

  const ttsSettingsSignature = () => JSON.stringify({
    mode: state.reader.ttsMode,
    voice: state.reader.ttsVoice,
    narrator: state.reader.ttsNarrator,
    rate: Number(state.reader.ttsRate).toFixed(1),
    emotion: state.reader.ttsEmotion
  })

  const audiobookHeaders = () => ({ 'Content-Type': 'application/json', 'X-Audiobook-Client': audiobookClientId })

  const ttsBackendResponseError = window.OOHStoryAudiobookLifecycle.responseError
  const ttsBackendFailureNotice = window.OOHStoryAudiobookLifecycle.failureNotice

  const ttsSupportsNativeIosHls = () => {
    const ua = String(navigator.userAgent || '')
    const ios = /iP(?:hone|ad|od)/i.test(ua)
      || (/Macintosh/i.test(ua) && Number(navigator.maxTouchPoints || 0) > 1)
    if (!ios) return false
    const audio = ttsEnsureAudio()
    return Boolean(audio.canPlayType?.('application/vnd.apple.mpegurl') || audio.canPlayType?.('application/x-mpegURL'))
  }

  const ttsHlsNormalizeItems = payload => (Array.isArray(payload?.items) ? payload.items : []).map(item => ({
    ...item,
    chapterId: String(item.chapter_id),
    chapterTitle: item.chapter_title || '未命名章节',
    manifestHash: String(item.manifest_hash || ''),
    paraIdx: Math.max(0, Number(item.paragraph_index) || 0),
    index: Math.max(0, Number(item.index) || 0),
    durationSeconds: Math.max(0.1, Number(item.duration_seconds) || 0.8),
    durationExact: Boolean(item.duration_exact),
    queueOffsetSeconds: Math.max(0, Number(item.queue_offset_seconds) || 0),
    nextChapterId: Number(item.next_chapter_id) > 0 ? String(item.next_chapter_id) : null,
    text: item.text || ''
  }))

  const ttsHlsApplyPayload = payload => {
    const incoming = ttsHlsNormalizeItems(payload)
    if (incoming.length) ttsHlsItems = incoming
    if (payload?.queue_id) ttsHlsQueueId = String(payload.queue_id)
    if (payload?.status_endpoint) ttsHlsStatusUrl = String(payload.status_endpoint)
    return ttsHlsItems.length > 0
  }

  const ttsHlsRefreshQueue = () => {
    if (!ttsHlsMode || !ttsHlsStatusUrl || ttsHlsRefreshPromise) return ttsHlsRefreshPromise
    ttsHlsRefreshPromise = fetch(ttsHlsStatusUrl, {
      method: 'GET', credentials: 'same-origin', cache: 'no-store',
      headers: { 'X-Audiobook-Client': audiobookClientId },
      signal: audiobookAbortController?.signal
    }).then(async response => {
      if (!response.ok) throw new Error(`HLS queue status ${response.status}`)
      ttsHlsApplyPayload(await response.json())
      return true
    }).catch(error => {
      if (error?.name !== 'AbortError') console.warn('[TTS] iOS HLS queue refresh unavailable', error)
      return false
    }).finally(() => { ttsHlsRefreshPromise = null })
    return ttsHlsRefreshPromise
  }

  const ttsHlsSeekToQueueIndex = index => {
    if (!ttsHlsMode || !ttsAudioEl || !ttsHlsItems.length) return
    const target = Math.max(0, Math.min(Number(index) || 0, ttsHlsItems.length - 1))
    ttsAudioEl.currentTime = Math.max(0, Number(ttsHlsItems[target].queueOffsetSeconds) || 0)
    ttsHlsQueueIndex = target
  }

  const ttsHlsSyncPosition = () => {
    if (!ttsHlsMode || !ttsAudioEl || !ttsHlsItems.length || !state.reader.ttsActive) return
    const currentTime = Math.max(0, Number(ttsAudioEl.currentTime) || 0)
    let queueIndex = 0
    for (let index = 0; index < ttsHlsItems.length; index++) {
      if (Number(ttsHlsItems[index].queueOffsetSeconds) <= currentTime + 0.05) queueIndex = index
      else break
    }
    const active = ttsHlsItems[queueIndex]
    if (!active) return
    const chapterChanged = String(state.ttsSession?.chapterId || '') !== active.chapterId
    let activePlanIndex = ttsPlanIndex
    if (chapterChanged) {
      const chapterItems = ttsHlsItems.filter(item => item.chapterId === active.chapterId)
      ttsChapterPlan = chapterItems
      activePlanIndex = Math.max(0, chapterItems.findIndex(item => item.index === active.index))
      ttsPlanIndex = activePlanIndex
      ttsParagraphIndex = -1
      ttsStreamStartPlanIndex = 0
      audiobookManifestHash = active.manifestHash
      ttsFollowingChapterId = active.nextChapterId
      if (state.ttsSession?.active) {
        state.ttsSession.chapterId = active.chapterId
        state.ttsSession.chapterTitle = active.chapterTitle
        state.ttsSession.paragraphCount = ttsPlanParagraphCount(chapterItems)
        state.ttsSession.absoluteItemCount = chapterItems.length
        state.ttsSession.itemCount = chapterItems.length
        const metrics = ttsChapterMetrics(active.chapterId)
        state.ttsSession.chapterNumber = metrics.number
        state.ttsSession.chapterCount = metrics.count
        state.ttsSession.contextItems = chapterItems.map(item => item.text || '')
        state.ttsSession.returnPath = contextualHref(`/books/${requestedBookId}/chapters/${active.chapterId}`)
      }
      ttsActivateChapter(active.chapterId).catch(error => console.warn('[TTS] HLS chapter activation delayed', error))
      if (!state.ttsSession?.detached && String(requestedChapterId) !== active.chapterId
          && ttsHlsNavigatingChapterId !== active.chapterId) {
        ttsHlsNavigatingChapterId = active.chapterId
        requestAnimationFrame(() => navigateInApp(contextualHref(`/books/${requestedBookId}/chapters/${active.chapterId}`)))
      }
    } else {
      activePlanIndex = Math.max(0, ttsChapterPlan.findIndex(item => Number(item.index) === active.index))
    }
    ttsHlsQueueIndex = queueIndex
    ttsSetActiveItem(activePlanIndex)
    ttsTrustedPlanIndex = ttsPlanIndex
    ttsTrustedItemOffsetSeconds = Math.max(0, currentTime - active.queueOffsetSeconds)
    if (Date.now() - ttsLastProgressAt >= 5000) {
      ttsLastProgressAt = Date.now()
      ttsQueueServerProgress()
    }
  }

  const ttsStartNativeIosHls = async (currentManifest, startIndex, offsetSeconds) => {
    if (!ttsSupportsNativeIosHls() || !audiobookServerSessionId) return false
    const response = await fetch(`/api/v1/audiobook/sessions/${audiobookServerSessionId}/hls/queues`, {
      method: 'POST', credentials: 'same-origin', headers: audiobookHeaders(),
      signal: audiobookAbortController?.signal,
      body: JSON.stringify({
        manifest_hash: String(currentManifest.manifest_hash || ''),
        start_index: Math.max(0, Number(startIndex) || 0),
        offset_ms: Math.max(0, Math.round((Number(offsetSeconds) || 0) * 1000))
      })
    })
    if (!response.ok) throw await ttsBackendResponseError(response)
    const payload = await response.json()
    if (!state.reader.ttsActive || !ttsHlsApplyPayload(payload)) return false
    const audio = ttsEnsureAudio()
    ttsHlsMode = true
    ttsSegmentFallbackMode = false
    ttsContinuousStreamMode = false
    ttsHlsQueueIndex = 0
    ttsHlsStartOffsetSeconds = Math.max(0, Number(payload.offset_ms || 0) / 1000)
    ttsActiveStreamId = ttsHlsQueueId
    if (state.ttsSession?.active) state.ttsSession.playbackStatusText = '正在连接 iOS 后台播放队列'
    ttsLifecycle.connect()
    ttsUpdateControls()
    audio.src = String(payload.playlist_endpoint || '')
    audio.onloadedmetadata = () => {
      // Safari may preserve the old HLS media time when the page-lifetime
      // Audio element receives a new queue URL.  Always seek to the exact
      // queue-relative start, including zero, so ordinary listen and
      // long-press "from here" cannot inherit the previous playback cursor.
      const currentTime = Math.max(0, Number(audio.currentTime) || 0)
      if (Math.abs(currentTime - ttsHlsStartOffsetSeconds) >= 0.05) {
        try { audio.currentTime = ttsHlsStartOffsetSeconds } catch (_error) {}
      }
    }
    audio.ontimeupdate = ttsHlsSyncPosition
    audio.onplaying = () => {
      if (!ttsHlsMode || !state.reader.ttsActive) return
      ttsAudioUnlocked = true
      if (state.ttsSession?.active) state.ttsSession.playbackStatusText = ''
      if (ttsLifecycle.isConnecting()) ttsLifecycle.playing()
      ttsUpdateControls()
    }
    audio.onended = () => {
      if (ttsHlsMode && state.reader.ttsActive) stopTTS()
    }
    audio.onerror = () => {
      if (!ttsHlsMode || !state.reader.ttsActive) return
      ttsMarkPlaybackBlocked(new Error('native HLS playback failed'), 'iOS 后台播放队列中断，点击重试')
    }
    if (ttsHlsRefreshTimer) clearInterval(ttsHlsRefreshTimer)
    ttsHlsRefreshTimer = setInterval(() => ttsHlsRefreshQueue(), 15000)
    const playPromise = audio.play()
    Promise.resolve(playPromise).catch(error => ttsMarkPlaybackBlocked(error))
    return true
  }

  const ttsBackendManifest = async (startParagraph, allowServerResume = true) => {
    audiobookAbortController?.abort()
    audiobookAbortController = new AbortController()
    const generation = ttsPlanGeneration
    const requestedNarrator = String(state.reader.ttsNarrator || 'mocheng')
    const requestedVoice = String(state.reader.ttsVoice || 'nuanxi')
    const requestedSettings = {
      mode: state.reader.ttsMode,
      narrator: requestedNarrator,
      voice: requestedVoice,
      emotion: state.reader.ttsEmotion,
      rate: Number(state.reader.ttsRate || 1)
    }
    const response = await fetch('/api/v1/audiobook/sessions', {
      method: 'POST',
      credentials: 'same-origin',
      headers: audiobookHeaders(),
      signal: audiobookAbortController.signal,
      body: JSON.stringify({
        book_id: String(requestedBookId),
        chapter_id: Number(requestedChapterId),
        client_id: audiobookClientId,
        ...requestedSettings,
        resume: Boolean(allowServerResume),
        start_paragraph_index: Math.max(0, Number(startParagraph) || 0)
      })
    })
    if (!response.ok) throw await ttsBackendResponseError(response)
    const payload = await response.json()
    if (!state.reader.ttsActive || generation !== ttsPlanGeneration) {
      if (payload.session_id) fetch(`/api/v1/audiobook/sessions/${payload.session_id}`, {
        method: 'DELETE', credentials: 'same-origin', keepalive: true,
        headers: { 'X-Audiobook-Client': audiobookClientId }
      }).catch(() => {})
      return
    }
    const responseRequestedNarrator = String(payload.current?.requested_narrator || requestedNarrator)
    const effectiveNarrator = String(payload.current?.effective_narrator || responseRequestedNarrator)
    if (requestedSettings.mode === 'smart'
        && (responseRequestedNarrator !== requestedNarrator || effectiveNarrator !== requestedNarrator)) {
      if (payload.session_id) {
        fetch(`/api/v1/audiobook/sessions/${payload.session_id}`, {
          method: 'DELETE', credentials: 'same-origin', keepalive: true,
          headers: { 'X-Audiobook-Client': audiobookClientId }
        }).catch(() => {})
      }
      const error = new Error(`旁白音色未按选择生效（选择 ${requestedNarrator}，实际 ${effectiveNarrator}）`)
      error.code = 'narrator_voice_mismatch'
      throw error
    }
    audiobookServerSessionId = payload.session_id
    audiobookManifestHash = payload.current.manifest_hash
    ttsChapterStreamUrl = payload.current.stream_endpoint || ''; ttsContinuousStreamMode = true
    ttsBatchPreloadKey = ''; ttsBatchPreloadPromise = null
    const effectiveChapterId = String(payload.current.chapter_id || requestedChapterId)
    const resumesCurrent = allowServerResume && String(payload.resume?.chapter_id || '') === effectiveChapterId
    const resumeParagraph = resumesCurrent ? Number(payload.resume?.paragraph_index || 0) : 0
    const resumeItemIndex = resumesCurrent ? Number(payload.resume?.item_index || 0) : 0
    const startInfo = payload.start && typeof payload.start === 'object' ? payload.start : null
    const resolvedStartParagraph = Number(startInfo?.paragraph_index ?? startInfo?.requested_paragraph_index ?? startParagraph)
    const startItemIndex = startInfo ? Number(startInfo.item_index) : NaN
    const minParagraph = Math.max(0, resumesCurrent ? resumeParagraph : resolvedStartParagraph || 0)
    let selected = payload.current.segments.filter(item => resumesCurrent
      ? Number(item.index) >= resumeItemIndex
      : (Number.isFinite(startItemIndex) ? Number(item.index) >= startItemIndex : Number(item.paragraph_index) >= minParagraph))
    const resumeSelectionValid = selected.length > 0
    if (!selected.length) selected = payload.current.segments.filter(item => Number(item.paragraph_index) >= minParagraph)
    const plan = selected.map(item => ({
      ...item,
      paraIdx: Number(item.paragraph_index),
      url: '',
      text: item.text || ''
    }))
    ttsChapterPlan = plan
    ttsFollowingChapterId = payload.current.next_chapter_id
    ttsResumeOffsetSeconds = resumesCurrent && resumeSelectionValid
      ? Math.max(0, Number(payload.resume?.audio_offset_ms || 0) / 1000)
      : 0
    if (state.ttsSession?.active) {
      state.ttsSession.chapterId = effectiveChapterId
      state.ttsSession.chapterTitle = payload.current.title || state.ttsSession.chapterTitle
      state.ttsSession.paragraphIndex = Math.max(0, Number(plan[0]?.paraIdx ?? minParagraph) || 0)
      state.ttsSession.paragraphCount = ttsPlanParagraphCount(plan)
      state.ttsSession.absoluteItemIndex = Math.max(0, Number(plan[0]?.index || 0))
      state.ttsSession.absoluteItemCount = payload.current.segments.length
      const metrics = ttsChapterMetrics(effectiveChapterId)
      state.ttsSession.chapterNumber = metrics.number
      state.ttsSession.chapterCount = metrics.count
      state.ttsSession.returnPath = contextualHref(`/books/${requestedBookId}/chapters/${effectiveChapterId}`)
      state.ttsSession.requestedNarrator = requestedNarrator
      state.ttsSession.effectiveNarrator = effectiveNarrator
    }
    state.ttsSession.contextItems = plan.map(item => item.text)
    state.ttsSession.itemCount = plan.length
    if (!ttsChapterStreamUrl || !plan.length) throw new Error('audiobook stream unavailable')
    if (!ttsLifecycle.isPausedByUser()) {
      let nativeHlsStarted = false
      try {
        nativeHlsStarted = await ttsStartNativeIosHls(
          payload.current,
          Number(plan[0]?.index || 0),
          ttsResumeOffsetSeconds
        )
      } catch (error) {
        if (error?.name !== 'AbortError') console.warn('[TTS] native iOS HLS unavailable; using chapter stream', error)
      }
      if (!nativeHlsStarted) ttsPlayItem(0)
    } else ttsUpdateControls()
    if (effectiveChapterId !== String(requestedChapterId)) {
      requestAnimationFrame(() => navigateInApp(
        contextualHref(`/books/${requestedBookId}/chapters/${effectiveChapterId}`),
        { replace: true }
      ))
    }
  }

  const ttsCacheWindow = (planStartIndex = 0) => {
    if (!state.reader.ttsActive || !audiobookServerSessionId || !audiobookManifestHash || !ttsChapterStreamUrl) return null
    const start = Math.max(0, Number(planStartIndex) || 0); const batch = ttsChapterPlan.slice(start, start + TTS_STREAM_BATCH_SEGMENTS)
    if (start >= ttsChapterPlan.length || !batch.length) return null
    const batchStart = Number(batch[0].index); const key = `${ttsPlanGeneration}:${audiobookManifestHash}:stream:${batchStart}`
    if (ttsBatchPreloadKey === key && ttsBatchPreloadPromise) return ttsBatchPreloadPromise
    const url = `${ttsChapterStreamUrl}?start=${encodeURIComponent(batchStart)}&preload=1`
    ttsBatchPreloadKey = key; return (ttsBatchPreloadPromise = fetch(url, { method: 'GET', credentials: 'same-origin', cache: 'no-store', headers: { 'X-Audiobook-Client': audiobookClientId }, signal: audiobookAbortController?.signal })
      .then(async response => { if (!response.ok) throw await ttsBackendResponseError(response); await response.arrayBuffer(); return true })
      .catch(error => { if (error?.name !== 'AbortError') console.warn('[TTS] next five-segment stream preload unavailable', error); return null }).finally(() => { if (ttsBatchPreloadKey === key) ttsBatchPreloadPromise = null }))
  }
  const ttsStopPlayback = () => {
    const audio = ttsAudioEl
    ttsFallbackPlayback?.release()
    ttsSegmentFallbackMode = false
    ttsBatchPreloadKey = ''; ttsBatchPreloadPromise = null; ttsStreamEnding = false
    ttsAudioUnlockGeneration++
    ttsAudioUnlockPromise = null
    if (audio) {
      // Playback permission is attached to the media element on Safari/iOS.
      // Keep the one page-lifetime Audio instance across explicit exits so a
      // rapid stop/start sequence cannot strand a fresh unlock promise and
      // prevent the real chapter stream from ever being requested.
      audio.onended = null
      audio.onerror = null
      audio.ontimeupdate = null
      audio.onloadedmetadata = null
      audio.onplaying = null
      audio.pause()
      audio.removeAttribute('src')
      audio.load()
    }
  }

  const ttsModeLabel = () => {
    if (ttsLifecycle.isBlocked()) return ttsLifecycle.snapshot().notice || (state.reader.ttsMode === 'smart' ? '点击继续智能听书' : '点击继续听书')
    const labels = { smart: '停止智能听书', cantonese: '停止粤语听书', hokkien: '停止闽南语听书' }
    return labels[state.reader.ttsMode] || `停止听书 · ${state.reader.ttsRate}x`
  }

  const ttsUpdateControls = () => {
    const playback = ttsLifecycle.snapshot()
    const playbackBlocked = ttsLifecycle.isBlocked()
    if (!playbackBlocked && ttsFailureToast) {
      ttsFailureToast.remove()
      ttsFailureToast = null
    }
    if (ttsButton) {
      ttsButton.classList.toggle('active', state.reader.ttsActive)
      ttsButton.textContent = !state.reader.ttsActive ? '听书' : '从头听书'
    }
    if (ttsStateBar) {
      ttsStateBar.hidden = !state.reader.ttsActive
      if (state.reader.ttsActive) ttsStateBar.textContent = playbackBlocked
        ? ttsModeLabel()
        : `正在听 · ${Number(state.reader.ttsRate || 1).toFixed(1)}x · 打开播放页`
    }
    if (ttsExitButton) ttsExitButton.hidden = !state.reader.ttsActive
    if (ttsExitControl) ttsExitControl.hidden = !state.reader.ttsActive
    mobileNav?.classList.toggle('tts-active', state.reader.ttsActive)
    if (state.ttsSession?.active) state.ttsSession.playbackBlocked = playbackBlocked
    if (state.ttsSession?.active) state.ttsSession.playbackConnecting = ttsLifecycle.isConnecting()
    if (state.ttsSession?.active) state.ttsSession.playbackState = playback.state
    updateTtsPlayer()
  }

  const ttsIsPlaybackPolicyError = error => ['NotAllowedError', 'AbortError'].includes(String(error?.name || ''))

  const ttsShowFailure = notice => {
    ttsFailureToast = window.OOHStoryAudiobookLifecycle.showFailure(
      ttsFailureToast, notice, ttsResumePlayback
    )
  }

  const ttsMarkPlaybackBlocked = (error, notice = '') => {
    ttsLifecycle.block(notice)
    ttsUpdateControls()
    ttsShowFailure(notice)
    console.warn('[TTS] playback paused for retry:', error)
  }

  const ttsPendingPlanForCurrentSettings = () => {
    const pending = state.ttsPendingPlan
    if (!pending || pending.signature !== ttsSettingsSignature()) return null
    if (String(pending.chapterId) !== String(requestedChapterId)) return null
    if (!Array.isArray(pending.items) || !pending.items.length) return null
    return pending
  }

  const ttsChapterMetrics = chapterId => {
    const readableChapters = catalog.chapters.filter(item => !isFrontMatterChapter(item))
    const position = readableChapters.findIndex(item => String(item.id) === String(chapterId))
    const exactCount = Number(catalog.chapter_count)
    return {
      number: position >= 0 ? position + 1 : 1,
      count: exactCount > 0 ? exactCount : Math.max(1, readableChapters.length)
    }
  }

  const ttsActivateChapter = async chapterId => {
    if (!audiobookServerSessionId) return
    const response = await fetch(
      `/api/v1/audiobook/sessions/${audiobookServerSessionId}/chapters/${encodeURIComponent(chapterId)}/activate`,
      {
        method: 'POST', credentials: 'same-origin',
        headers: { 'X-Audiobook-Client': audiobookClientId },
        signal: audiobookAbortController?.signal
      }
    )
    if (!response.ok) throw new Error(`activate chapter ${response.status}`)
  }

  const ttsChapterEndOnce = async transitionGeneration => {
    if (state.reader.ttsActive && ttsFollowingChapterId) {
      const keepReaderInSync = Boolean(state.ttsSession?.active && !state.ttsSession.detached)
      if (!ttsNextChapterPlan.length || String(ttsNextChapterId) !== String(ttsFollowingChapterId)) {
        await ttsPrefetchNextChapter()
      }
      if (!state.reader.ttsActive || transitionGeneration !== ttsPlanGeneration || !ttsNextChapterPlan.length) {
        ttsStreamEnding = false; ttsMarkPlaybackBlocked(new Error('next chapter unavailable'), '下一章加载失败，点击重试')
        return
      }
      const enteringChapterId = String(ttsNextChapterId)
      try {
        await ttsActivateChapter(enteringChapterId)
      } catch (error) {
        if (error?.name !== 'AbortError') {
          ttsStreamEnding = false; ttsMarkPlaybackBlocked(error, '章节切换失败，仍停留在当前章，点击重试')
        }
        return
      }
      if (!state.reader.ttsActive || transitionGeneration !== ttsPlanGeneration) return
      ttsChapterPlan = ttsNextChapterPlan
      ttsChapterStreamUrl = ttsNextChapterStreamUrl; ttsContinuousStreamMode = true
      audiobookManifestHash = ttsNextChapterSignature
      ttsBatchPreloadKey = ''; ttsBatchPreloadPromise = null
      ttsPlanIndex = 0
      ttsFollowingChapterId = ttsNextChapterFollowingId
      state.ttsSession.chapterId = enteringChapterId
      state.ttsSession.chapterTitle = ttsNextChapterTitle
      const chapterMetrics = ttsChapterMetrics(enteringChapterId)
      state.ttsSession.chapterNumber = chapterMetrics.number
      state.ttsSession.chapterCount = chapterMetrics.count
      state.ttsSession.contextItems = ttsChapterPlan.map(item => item.text || '')
      state.ttsSession.paragraphIndex = 0
      state.ttsSession.paragraphCount = ttsPlanParagraphCount(ttsChapterPlan)
      state.ttsSession.itemIndex = 0
      state.ttsSession.absoluteItemIndex = Math.max(0, Number(ttsChapterPlan[0]?.index || 0))
      state.ttsSession.absoluteItemCount = ttsChapterPlan.length
      state.ttsSession.itemCount = ttsChapterPlan.length
      ttsParagraphIndex = -1
      state.ttsSession.returnPath = contextualHref(`/books/${requestedBookId}/chapters/${enteringChapterId}`)
      ttsNextChapterPlan = []
      ttsNextChapterSignature = ''
      ttsNextChapterId = null
      ttsNextChapterFollowingId = null
      ttsNextChapterTitle = ''
      ttsNextChapterStreamUrl = ''
      ttsNextChapterCached = false
      ttsNextChapterPrefetchPromise = null
      ttsNextChapterPrefetchSourceId = ''
      state.ttsPendingPlan = null
      state.ttsContinueOnLoad = false
      saveTtsCheckpoint(state.ttsSession)
      updateGlobalTtsReturn()
      ttsPlayItem(0)
      if (keepReaderInSync) {
        requestAnimationFrame(() => navigateInApp(contextualHref(`/books/${requestedBookId}/chapters/${enteringChapterId}`)))
      }
    } else {
      stopTTS()
    }
  }

  const ttsChapterEnd = () => {
    if (ttsChapterTransitionPromise) return ttsChapterTransitionPromise
    const transitionGeneration = ttsPlanGeneration
    const transition = Promise.resolve().then(() => ttsChapterEndOnce(transitionGeneration))
      .finally(() => {
        if (ttsChapterTransitionPromise === transition) ttsChapterTransitionPromise = null
      })
    ttsChapterTransitionPromise = transition
    return transition
  }

  const ttsUpdateMediaSession = (paraIdx) => {
    if (!('mediaSession' in navigator)) return
    const title = state.ttsSession?.chapterTitle || chapter?.title || '听书'
    const bookTitle = state.ttsSession?.bookTitle || chapter?.book?.title || ''
    const cover = state.ttsSession?.mediaCoverUrl || `/api/v1/books/${requestedBookId}/cover?variant=media-art`
    navigator.mediaSession.metadata = new MediaMetadata({ title: title, artist: bookTitle, album: 'OOHStory 听书', artwork: [{ src: cover }] })
    navigator.mediaSession.setActionHandler('play', () => ttsResumePlayback())
    navigator.mediaSession.setActionHandler('pause', () => {
      if (ttsAudioEl) ttsAudioEl.pause()
      ttsUpdateControls()
    })
    navigator.mediaSession.setActionHandler('stop', () => stopTTS())
    navigator.mediaSession.setActionHandler('previoustrack', () => {
      if (ttsHlsMode) { ttsHlsSeekToQueueIndex(ttsHlsQueueIndex - 1); return }
      if (ttsPlanIndex > 0) ttsPlayItem(ttsPlanIndex - 1)
    })
    navigator.mediaSession.setActionHandler('nexttrack', () => {
      if (ttsHlsMode) { ttsHlsSeekToQueueIndex(ttsHlsQueueIndex + 1); return }
      if (ttsPlanIndex < ttsChapterPlan.length - 1) ttsPlayItem(ttsPlanIndex + 1)
    })
  }

  const ttsEstimatedDuration = item => {
    const measured = Number(item?.durationSeconds || item?.duration_seconds)
    if (Number.isFinite(measured) && measured > 0) return measured
    const text = Array.from(String(item?.text || ''))
    const punctuation = text.filter(char => /[，。！？；：,.!?;:…—]/.test(char)).length
    const rate = Math.max(0.5, Math.min(Number(item?.rate || state.reader.ttsRate || 1), 3))
    return Math.max(0.8, ((text.length / 4.45) + (punctuation * 0.12)) / rate)
  }
  const ttsPlanParagraphCount = plan => Math.max(
    1,
    ...((Array.isArray(plan) ? plan : []).map(item => Math.max(0, Number(item?.paraIdx) || 0) + 1))
  )
  const ttsStreamSegmentPlayedSeconds = planIdx => Math.max(0.05, ttsEstimatedDuration(ttsChapterPlan[planIdx]) - (planIdx === ttsStreamStartPlanIndex ? ttsStreamResumeBaseSeconds : 0))
  const ttsResolvedStreamPlanIndex = () => {
    const audio = ttsAudioEl
    if (!audio || !ttsChapterPlan.length) return Math.max(0, ttsPlanIndex)
    const currentTime = Math.max(0, Number(audio.currentTime) || 0); let elapsed = 0
    let candidate = Math.max(0, Math.min(ttsStreamStartPlanIndex, ttsChapterPlan.length - 1))
    const streamEndPlanIndex = ttsContinuousStreamMode
      ? ttsChapterPlan.length
      : Math.min(ttsChapterPlan.length, ttsStreamStartPlanIndex + TTS_STREAM_BATCH_SEGMENTS)
    for (let idx = candidate; idx < streamEndPlanIndex; idx++) {
      if (!ttsChapterPlan[idx]?.durationExact) {
        candidate = idx
        break
      }
      const duration = ttsStreamSegmentPlayedSeconds(idx)
      candidate = idx
      if (currentTime < elapsed + duration) break; elapsed += duration
    }
    return candidate
  }
  const ttsRememberTrustedPosition = (planIndex = ttsPlanIndex) => { if (!ttsChapterPlan.length) return; const target = Math.max(0, Math.min(Number(planIndex) || 0, ttsChapterPlan.length - 1)); ttsTrustedPlanIndex = target; ttsTrustedItemOffsetSeconds = Math.max(0, Number(ttsCurrentItemOffsetSeconds(target)) || 0) }
  const ttsRecoverFromStreamReplay = () => {
    if (ttsReplayRecoveryPromise || !state.reader.ttsActive || !ttsChapterPlan.length) return true
    const generation = ttsPlanGeneration, streamId = ttsActiveStreamId, target = Math.max(0, Math.min(Math.max(ttsTrustedPlanIndex, ttsPlanIndex), ttsChapterPlan.length - 1)), offset = target === ttsTrustedPlanIndex ? ttsTrustedItemOffsetSeconds : ttsCurrentItemOffsetSeconds(target)
    console.warn('[TTS] media stream replayed from the beginning; reopening at trusted position', target, offset)
    ttsReplayRecoveryPromise = Promise.resolve().then(() => { if (!state.reader.ttsActive || generation !== ttsPlanGeneration || ttsActiveStreamId !== streamId) return; const audio = ttsAudioEl; if (audio) { audio.pause(); audio.removeAttribute('src'); audio.load() } ttsPlayItem(target, offset) }).finally(() => { ttsReplayRecoveryPromise = null })
    return true
  }
  const ttsRejectStreamReplay = () => { const audio = ttsAudioEl; if (!audio || !ttsContinuousStreamMode || ttsStreamEnding || !ttsChapterPlan.length) return false; const currentTime = Math.max(0, Number(audio.currentTime) || 0); if (ttsLastStreamCurrentTimeSeconds > 8 && currentTime < Math.max(1, ttsLastStreamCurrentTimeSeconds - 3)) { ttsRecoverFromStreamReplay(); return true } if (currentTime >= ttsLastStreamCurrentTimeSeconds || ttsLastStreamCurrentTimeSeconds - currentTime < 1) ttsLastStreamCurrentTimeSeconds = Math.max(ttsLastStreamCurrentTimeSeconds, currentTime); return false }
  const ttsRefreshTimeline = async (force = false) => {
    if (!audiobookServerSessionId || !audiobookManifestHash || !ttsChapterPlan.length) return
    const now = Date.now()
    if (!force && now - ttsLastTimelineRefresh < 1500) return ttsTimelinePromise
    if (ttsTimelinePromise) return ttsTimelinePromise
    const firstAbsolute = Number(ttsChapterPlan[ttsStreamStartPlanIndex]?.index || 0)
    const lastAbsolute = Number(ttsChapterPlan.at(-1)?.index ?? firstAbsolute)
    const currentPlanIndex = Math.max(ttsStreamStartPlanIndex, Math.min(ttsResolvedStreamPlanIndex(), ttsChapterPlan.length - 1))
    const currentAbsolute = Number(ttsChapterPlan[currentPlanIndex]?.index ?? firstAbsolute)
    const desiredThrough = Math.min(lastAbsolute, currentAbsolute + TTS_STREAM_BATCH_SEGMENTS - 1)
    if (!force && ttsTimelineLoadedThrough >= desiredThrough) return ttsTimelineLoadedThrough >= lastAbsolute
    ttsLastTimelineRefresh = now
    const timelineStart = Math.max(firstAbsolute, ttsTimelineLoadedThrough + 1)
    if (timelineStart > lastAbsolute) return true
    ttsTimelinePromise = fetch(
      `/api/v1/audiobook/sessions/${audiobookServerSessionId}/chapters/${audiobookManifestHash}/timeline?start=${timelineStart}&limit=${TTS_STREAM_BATCH_SEGMENTS}`,
      {
        method: 'GET', credentials: 'same-origin', cache: 'no-store',
        headers: { 'X-Audiobook-Client': audiobookClientId },
        signal: audiobookAbortController?.signal
      }
    ).then(async response => {
      if (!response.ok) throw new Error(`chapter timeline ${response.status}`)
      const payload = await response.json()
      const durations = new Map((payload.segments || []).map(item => [Number(item.index), Number(item.duration_ms || 0)]))
      for (const item of ttsChapterPlan) {
        const durationMs = durations.get(Number(item.index)) || 0
        if (durationMs > 0) {
          item.durationSeconds = durationMs / 1000
          item.durationExact = true
        }
      }
      let loaded = timelineStart - 1
      for (const item of payload.segments || []) {
        if (Number(item.index) !== loaded + 1 || Number(item.duration_ms || 0) <= 0) break
        loaded = Number(item.index)
      }
      ttsTimelineLoadedThrough = Math.max(ttsTimelineLoadedThrough, loaded)
      return payload.complete === true
    }).catch(error => {
      if (error?.name !== 'AbortError') console.warn('[TTS] exact timeline unavailable', error)
      return false
    }).finally(() => { ttsTimelinePromise = null })
    return ttsTimelinePromise
  }
  const ttsNewStreamId = () => {
    const bytes = new Uint8Array(16); if (globalThis.crypto?.getRandomValues) { globalThis.crypto.getRandomValues(bytes); return Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('') }
    return Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join('')
  }
  const ttsConfirmStreamComplete = async (streamId, generation) => {
    const statusBase = ttsChapterStreamUrl.replace(/\/stream\.mp3$/, '')
    if (!statusBase || statusBase === ttsChapterStreamUrl) return false
    for (let attempt = 0; attempt < 3; attempt++) {
      const response = await fetch(`${statusBase}/streams/${encodeURIComponent(streamId)}`, {
        method: 'GET', credentials: 'same-origin',
        headers: { 'X-Audiobook-Client': audiobookClientId },
        signal: audiobookAbortController?.signal
      })
      if (!response.ok) throw new Error(`chapter stream status ${response.status}`)
      const result = await response.json()
      if (result.complete === true) return true
      if (!state.reader.ttsActive || generation !== ttsPlanGeneration) return false
      if (attempt < 2) await new Promise(resolve => window.setTimeout(resolve, 120))
    }
    return false
  }
  const ttsClearChapterEndWatchdog = () => {
    if (ttsChapterEndWatchdogTimer) window.clearTimeout(ttsChapterEndWatchdogTimer)
    ttsChapterEndWatchdogTimer = null
  }
  const ttsExpectedStreamDuration = () => {
    if (!ttsChapterPlan.length) return 0
    let total = 0
    for (let idx = ttsStreamStartPlanIndex; idx < ttsChapterPlan.length; idx++) {
      if (!ttsChapterPlan[idx]?.durationExact) return 0
      total += ttsStreamSegmentPlayedSeconds(idx)
    }
    return total
  }
  const ttsFinishChapterStream = (streamId, generation, { hardEnd = false } = {}) => {
    if (ttsStreamCompletionPromise) return ttsStreamCompletionPromise
    ttsClearChapterEndWatchdog()
    ttsStreamEnding = true
    const completion = (async () => {
      let streamCompleted = false
      try {
        streamCompleted = await ttsConfirmStreamComplete(streamId, generation)
      } catch (error) {
        if (error?.name === 'AbortError') return false
        console.warn('[TTS] chapter stream completion check failed', error)
      }
      if (!state.reader.ttsActive || generation !== ttsPlanGeneration || ttsActiveStreamId !== streamId) return false
      if (!streamCompleted) {
        ttsStreamEnding = false
        if (hardEnd || ttsChapterEndWatchdogAttempts >= 5) {
          ttsMarkPlaybackBlocked(new Error('chapter stream ended before its final receipt'), '本章音频结束确认失败，已停在章末，点击重试')
        } else {
          ttsChapterEndWatchdogTimer = window.setTimeout(() => {
            ttsChapterEndWatchdogTimer = null
            ttsMaybeCompleteChapterAtMediaEof(streamId, generation)
          }, 750)
        }
        return false
      }
      const finalPlanIndex = Math.max(0, ttsChapterPlan.length - 1)
      ttsSetActiveItem(finalPlanIndex)
      await Promise.resolve(ttsQueueServerProgress(true))
      ttsStreamEnding = false
      await ttsChapterEnd()
      return true
    })().finally(() => {
      if (ttsStreamCompletionPromise === completion) ttsStreamCompletionPromise = null
    })
    ttsStreamCompletionPromise = completion
    return completion
  }
  const ttsMaybeCompleteChapterAtMediaEof = (streamId = ttsActiveStreamId, generation = ttsPlanGeneration) => {
    if (!state.reader.ttsActive || ttsStreamEnding || ttsLifecycle.isPausedByUser()) return
    if (!streamId || streamId !== ttsActiveStreamId || generation !== ttsPlanGeneration) return
    const finalAbsolute = Number(ttsChapterPlan.at(-1)?.index ?? -1)
    if (finalAbsolute < 0 || ttsTimelineLoadedThrough < finalAbsolute) return
    const expectedDuration = ttsExpectedStreamDuration()
    const currentTime = Math.max(0, Number(ttsAudioEl?.currentTime) || 0)
    if (!expectedDuration || currentTime < Math.max(0, expectedDuration - 0.35)) return
    if (ttsChapterEndWatchdogTimer || ttsStreamCompletionPromise) return
    ttsChapterEndWatchdogAttempts++
    ttsChapterEndWatchdogTimer = window.setTimeout(() => {
      ttsChapterEndWatchdogTimer = null
      if (!state.reader.ttsActive || generation !== ttsPlanGeneration || streamId !== ttsActiveStreamId) return
      const latestTime = Math.max(0, Number(ttsAudioEl?.currentTime) || 0)
      if (latestTime < Math.max(0, expectedDuration - 0.35)) return
      ttsFinishChapterStream(streamId, generation, { hardEnd: false })
    }, 500)
  }
  const ttsSetActiveItem = idx => {
    const item = ttsChapterPlan[idx]
    if (!item) return
    if (idx === ttsPlanIndex && ttsParagraphIndex === item.paraIdx) { if (!ttsActiveHighlightPresent(item.paraIdx)) ttsHighlight(item.paraIdx); return }
    ttsParagraphIndex = item.paraIdx
    ttsHighlight(item.paraIdx)
    ttsPlanIndex = idx
    if (!ttsHlsMode && !ttsNextChapterCached && !ttsNextChapterPlan.length
        && idx >= Math.max(0, ttsChapterPlan.length - 2) && ttsFollowingChapterId) {
        ttsNextChapterCached = true
      ttsPrefetchNextChapter()
    }
    if (state.ttsSession?.active) {
      state.ttsSession.paragraphIndex = item.paraIdx
      state.ttsSession.itemIndex = idx
      state.ttsSession.absoluteItemIndex = Math.max(0, Number(item.index ?? idx) || 0)
      state.ttsSession.itemCount = ttsChapterPlan.length
      state.ttsSession.currentText = item.text || ''
      state.ttsSession.contextItems = ttsChapterPlan.map(planItem => planItem.text || '')
      state.ttsSession.currentEmotion = item.emotion || 'neutral'
      state.ttsSession.playbackBlocked = false
      state.ttsSession.returnPath = contextualHref(`/books/${requestedBookId}/chapters/${state.ttsSession.chapterId}`)
      state.ttsSession.onParagraph?.(item.paraIdx)
      saveTtsCheckpoint(state.ttsSession)
      ttsQueueServerProgress()
      updateGlobalTtsReturn()
      updateTtsPlayer()
    }
    ttsUpdateMediaSession(item.paraIdx)
  }
  const ttsSyncStreamPosition = () => { const audio = ttsAudioEl; if (!audio || !ttsChapterPlan.length || ttsRejectStreamReplay()) return; const candidate = ttsResolvedStreamPlanIndex(); if (candidate !== ttsPlanIndex) ttsSetActiveItem(candidate); ttsRememberTrustedPosition(candidate); ttsRefreshTimeline().then(() => ttsMaybeCompleteChapterAtMediaEof()).catch(() => {}); if (Date.now() - ttsLastProgressAt >= 5000) { ttsLastProgressAt = Date.now(); ttsQueueServerProgress() } }
  const ttsCurrentItemOffsetSeconds = (planIndex = ttsPlanIndex) => {
    if (ttsHlsMode && ttsHlsItems.length) {
      const active = ttsHlsItems[Math.max(0, Math.min(ttsHlsQueueIndex, ttsHlsItems.length - 1))]
      const relative = Math.max(0, Number(ttsAudioEl?.currentTime || 0) - Number(active?.queueOffsetSeconds || 0))
      return Math.min(relative, Math.max(0.05, Number(active?.durationSeconds || 0.1) - 0.05))
    }
    const boundedPlanIndex = Math.max(0, Math.min(Number(planIndex) || 0, ttsChapterPlan.length - 1))
    let elapsedBeforeItem = 0
    for (let planIdx = ttsStreamStartPlanIndex; planIdx < boundedPlanIndex; planIdx++) elapsedBeforeItem += ttsStreamSegmentPlayedSeconds(planIdx)
    const relative = Math.max(0, Number(ttsAudioEl?.currentTime || 0) - elapsedBeforeItem)
    const rawOffset = boundedPlanIndex === ttsStreamStartPlanIndex ? ttsStreamResumeBaseSeconds + relative : relative
    const duration = Number(ttsChapterPlan[boundedPlanIndex]?.durationSeconds || ttsChapterPlan[boundedPlanIndex]?.duration_seconds)
    if (Number.isFinite(duration) && duration > 0.1) return Math.max(0, Math.min(rawOffset, duration - 0.05))
    return rawOffset
  }
  ttsFallbackPlayback = window.OOHStoryAudiobookFallback.create({
    clientId: audiobookClientId, responseError: ttsBackendResponseError,
    failureNotice: ttsBackendFailureNotice,
    timeoutMs: Number(window.OOHStoryAudiobookConnectTimeoutMs || 8000),
    isActive: () => state.reader.ttsActive, isPaused: () => ttsLifecycle.isPausedByUser(),
    isPolicyError: ttsIsPlaybackPolicyError, generation: () => ttsPlanGeneration,
    signal: () => audiobookAbortController?.signal, audio: ttsEnsureAudio,
    items: () => ttsChapterPlan, offset: ttsCurrentItemOffsetSeconds,
    begin: (idx, token, offset) => {
      ttsSegmentFallbackMode = true; ttsActiveStreamId = token; ttsStreamStartPlanIndex = idx
      ttsStreamResumeBaseSeconds = Math.max(0, Number(offset) || 0); ttsSetActiveItem(idx)
      ttsLifecycle.connect(); if (state.ttsSession?.active) state.ttsSession.playbackStatusText = '正在生成当前片段'; ttsUpdateControls()
    },
    playing: () => { if (state.ttsSession?.active) state.ttsSession.playbackStatusText = ''; if (ttsLifecycle.isConnecting()) ttsLifecycle.playing(); ttsUpdateControls() },
    timeupdate: idx => { ttsSetActiveItem(idx); if (Date.now() - ttsLastProgressAt >= 5000) { ttsLastProgressAt = Date.now(); ttsQueueServerProgress() } },
    progress: force => ttsQueueServerProgress(force), finish: () => ttsChapterEnd(),
    fail: ttsMarkPlaybackBlocked
  })

  const ttsPlayItem = (idx, resumeOverrideSeconds = null) => {
    if (!state.reader.ttsActive || idx >= ttsChapterPlan.length) { if (idx >= ttsChapterPlan.length) ttsChapterEnd(); return }
    if (ttsLifecycle.isPausedByUser()) return
    const generation = ttsPlanGeneration
    const item = ttsChapterPlan[idx]
    if (!item || !ttsChapterStreamUrl) {
      ttsMarkPlaybackBlocked(new Error('chapter stream unavailable'), '章节音频流不可用，点击重试')
      return
    }
    const audio = ttsEnsureAudio()
    ttsClearChapterEndWatchdog()
    ttsChapterEndWatchdogAttempts = 0
    ttsStreamCompletionPromise = null
    ttsFallbackPlayback.clearWatchdog()
    ttsSegmentFallbackMode = false; ttsStreamEnding = false
    ttsFallbackPlayback.release()
    ttsStreamStartPlanIndex = idx
    ttsSetActiveItem(idx)
    const streamId = ttsNewStreamId()
    ttsActiveStreamId = streamId
    const overrideOffset = Number(resumeOverrideSeconds)
    const resumeOffset = Number.isFinite(overrideOffset) && overrideOffset > 0
      ? overrideOffset
      : (idx === 0 ? ttsResumeOffsetSeconds : 0)
    ttsStreamResumeBaseSeconds = resumeOffset
    ttsTimelineLoadedThrough = Number(item.index || 0) - 1
    ttsResumeOffsetSeconds = 0
    ttsLastStreamCurrentTimeSeconds = 0; ttsTrustedPlanIndex = idx; ttsTrustedItemOffsetSeconds = resumeOffset
    ttsLifecycle.connect()
    if (state.ttsSession?.active) state.ttsSession.playbackStatusText = '正在准备本组5段音频'
    ttsUpdateControls()
    audio.src = `${ttsChapterStreamUrl}?start=${encodeURIComponent(item.index)}&offset_ms=${encodeURIComponent(Math.round(resumeOffset * 1000))}&stream_id=${encodeURIComponent(streamId)}&continuous=1&full_chapter=1`
    audio.onloadedmetadata = () => ttsRefreshTimeline(true).catch(() => {})
    const markActuallyPlaying = () => {
      if (!state.reader.ttsActive || generation !== ttsPlanGeneration || ttsActiveStreamId !== streamId) return
      if (ttsLifecycle.isPausedByUser()) { audio.pause(); return }
      ttsAudioUnlocked = true
      ttsFallbackPlayback.clearWatchdog()
      if (state.ttsSession?.active) state.ttsSession.playbackStatusText = ''
      if (ttsLifecycle.snapshot().state === 'connecting') ttsLifecycle.playing()
      ttsUpdateControls()
      ttsPrefetchNextChapter().catch(() => {})
    }
    audio.onplaying = markActuallyPlaying
    let failed = false
    const advanceAfterFailure = error => {
      if (failed || !state.reader.ttsActive || generation !== ttsPlanGeneration) return
      failed = true
      if (ttsIsPlaybackPolicyError(error)) {
        ttsMarkPlaybackBlocked(error)
        return
      }
      const fallbackIdx = Math.max(idx, ttsPlanIndex, ttsTrustedPlanIndex)
      const fallbackOffset = fallbackIdx === ttsTrustedPlanIndex ? ttsTrustedItemOffsetSeconds : ttsCurrentItemOffsetSeconds(fallbackIdx)
      console.warn('[TTS] chapter stream failed; switching to finite segment playback', fallbackIdx, error)
      audio.pause()
      audio.removeAttribute('src')
      audio.load()
      ttsFallbackPlayback.play(fallbackIdx, fallbackOffset)
    }
    audio.ontimeupdate = ttsSyncStreamPosition
    audio.onended = async () => {
      if (!state.reader.ttsActive || generation !== ttsPlanGeneration || ttsActiveStreamId !== streamId) return
      if (failed) return
      failed = true
      await ttsFinishChapterStream(streamId, generation, { hardEnd: true })
    }
    audio.onerror = () => advanceAfterFailure(new Error('audio element error'))
    ttsFallbackPlayback.guard(
      () => state.reader.ttsActive && generation === ttsPlanGeneration && ttsActiveStreamId === streamId && ttsLifecycle.isConnecting(),
      () => {
        const fallbackIdx = Math.max(idx, ttsPlanIndex, ttsTrustedPlanIndex); console.warn('[TTS] chapter stream connection stalled; switching to finite segment playback', fallbackIdx)
        audio.pause(); audio.removeAttribute('src')
        ttsFallbackPlayback.play(fallbackIdx, fallbackIdx === ttsTrustedPlanIndex ? ttsTrustedItemOffsetSeconds : ttsCurrentItemOffsetSeconds(fallbackIdx))
      }
    )
    try {
      const playPromise = audio.play()
      Promise.resolve(playPromise).then(markActuallyPlaying).catch(advanceAfterFailure)
    } catch (error) {
      advanceAfterFailure(error)
    }
    console.log('[TTS] playing', idx, '/', ttsChapterPlan.length, 'para=' + item.paraIdx)
  }

  const ttsPrefetchNextChapter = () => {
    if (audiobookServerSessionId) {
      const generation = ttsPlanGeneration
      const fromChapterId = String(state.ttsSession?.chapterId || requestedChapterId)
      if (ttsNextChapterPlan.length && String(ttsNextChapterId) === String(ttsFollowingChapterId)) return Promise.resolve(true)
      if (ttsNextChapterPrefetchPromise && ttsNextChapterPrefetchSourceId === fromChapterId) return ttsNextChapterPrefetchPromise
      const prefetch = (async () => {
       try {
        const response = await fetch(`/api/v1/audiobook/sessions/${audiobookServerSessionId}/next?from_chapter_id=${encodeURIComponent(fromChapterId)}`, {
          method: 'POST', credentials: 'same-origin',
          headers: { 'X-Audiobook-Client': audiobookClientId },
          signal: audiobookAbortController?.signal
        })
        if (!response.ok) throw new Error(`next manifest ${response.status}`)
        const manifest = (await response.json()).next
        if (!manifest || !state.reader.ttsActive || generation !== ttsPlanGeneration) return false
        const nextPlan = manifest.segments.map(item => ({ ...item, paraIdx: Number(item.paragraph_index), url: '', text: item.text || '' }))
        ttsNextChapterPlan = nextPlan
        ttsNextChapterId = manifest.chapter_id
        ttsNextChapterFollowingId = manifest.next_chapter_id
        ttsNextChapterTitle = manifest.title || '下一章'
        ttsNextChapterStreamUrl = manifest.stream_endpoint || ''
        ttsNextChapterSignature = manifest.manifest_hash || ''
        ttsNextChapterCached = true
        try {
          if (!ttsNextChapterStreamUrl || !await window.OOHStoryAudiobookCache.shouldPrefetch()) return true
          const firstIndex = Number(nextPlan[0]?.index || 0)
          const preload = await fetch(`${ttsNextChapterStreamUrl}?start=${encodeURIComponent(firstIndex)}&preload=1`, {
            method: 'GET', credentials: 'same-origin', cache: 'no-store',
            headers: { 'X-Audiobook-Client': audiobookClientId },
            signal: audiobookAbortController?.signal
          })
          if (!preload.ok) throw await ttsBackendResponseError(preload)
          await preload.arrayBuffer()
        } catch (error) {
          if (error?.name !== 'AbortError') console.warn('[TTS] next chapter stream preload unavailable', error)
        }
        return true
      } catch (error) {
        if (error?.name !== 'AbortError') console.warn('[TTS] next persistent chapter unavailable', error)
        return false
      }
      })().finally(() => {
        if (ttsNextChapterPrefetchPromise === prefetch) {
          ttsNextChapterPrefetchPromise = null
          ttsNextChapterPrefetchSourceId = ''
        }
      })
      ttsNextChapterPrefetchSourceId = fromChapterId
      ttsNextChapterPrefetchPromise = prefetch
      return prefetch
    }
    return Promise.resolve(false)
  }

  const ttsQueueServerProgress = (immediate = false) => {
    if (ttsProgressTimer) {
      clearTimeout(ttsProgressTimer)
      ttsProgressTimer = null
    }
    const sessionId = audiobookServerSessionId
    const current = state.ttsSession
    if (!sessionId || !current?.active || !current.chapterId) return null
    const progressPlanIndex = ttsHlsMode
      ? Math.max(0, ttsPlanIndex)
      : (!ttsSegmentFallbackMode && ttsChapterPlan.length ? ttsResolvedStreamPlanIndex() : Math.max(0, ttsPlanIndex))
    const activeItem = ttsChapterPlan[progressPlanIndex]
    const itemOffsetSeconds = ttsCurrentItemOffsetSeconds(progressPlanIndex)
    if (activeItem) {
      current.paragraphIndex = Number(activeItem.paraIdx ?? current.paragraphIndex) || 0
      current.itemIndex = progressPlanIndex
      current.absoluteItemIndex = Math.max(0, Number(activeItem.index ?? current.absoluteItemIndex ?? progressPlanIndex) || 0)
      current.currentText = activeItem.text || current.currentText || ''; current.currentEmotion = activeItem.emotion || current.currentEmotion || 'neutral'
      saveTtsCheckpoint(current)
    }
    const send = () => {
      ttsProgressTimer = null
      return fetch(`/api/v1/audiobook/sessions/${sessionId}/progress`, {
        method: 'PUT',
        credentials: 'same-origin',
        keepalive: immediate,
        headers: {
          'Content-Type': 'application/json',
          'X-Audiobook-Client': audiobookClientId
        },
        body: JSON.stringify({
          chapter_id: Number(current.chapterId),
          paragraph_index: Math.max(0, Number(current.paragraphIndex) || 0),
          item_index: Math.max(0, Number(activeItem?.index ?? current.absoluteItemIndex ?? current.itemIndex) || 0),
          audio_offset_ms: Math.max(0, Math.round(itemOffsetSeconds * 1000)),
          manifest_hash: !ttsSegmentFallbackMode && ttsActiveStreamId ? audiobookManifestHash : null,
          stream_id: !ttsSegmentFallbackMode && ttsActiveStreamId ? ttsActiveStreamId : null
        })
      }).catch(() => null)
    }
    if (immediate) return send()
    ttsProgressTimer = window.setTimeout(send, 1800)
    return null
  }

  const stopTTS = ({ preservePending = false } = {}) => {
    ttsClearChapterEndWatchdog()
    ttsChapterEndWatchdogAttempts = 0
    ttsStreamCompletionPromise = null
    ttsChapterTransitionPromise = null
    ttsNextChapterPrefetchPromise = null
    ttsNextChapterPrefetchSourceId = ''
    if (!preservePending) {
      const finalProgress = ttsQueueServerProgress(true)
      const closingSessionId = audiobookServerSessionId
      // If creation has not returned a session ID yet, keep that POST alive.
      // Its late-response branch owns the compensating DELETE. Aborting here
      // can let the server commit a session while preventing the browser from
      // ever learning the ID, leaving an orphan after rapid start/exit.
      if (closingSessionId) audiobookAbortController?.abort()
      audiobookAbortController = null
      window.OOHStoryAudiobookCache?.cancel?.()
      window.OOHStoryAudiobookCache?.releaseUrls?.()
      if (closingSessionId) {
        // Session cancellation must not wait behind a stalled final progress
        // request. Otherwise rapid exit/re-entry leaves the old chapter streams
        // consuming the per-user TTS slots and the next session appears paused.
        fetch(`/api/v1/audiobook/sessions/${closingSessionId}`, {
          method: 'DELETE', credentials: 'same-origin', keepalive: true,
          headers: { 'X-Audiobook-Client': audiobookClientId }
        }).catch(() => {})
        Promise.resolve(finalProgress).catch(() => {})
      }
      audiobookServerSessionId = ''
      audiobookManifestHash = ''
      ttsChapterStreamUrl = ''
    }
    if (ttsHeartbeat) { clearInterval(ttsHeartbeat); ttsHeartbeat = null }
    if (ttsHlsRefreshTimer) { clearInterval(ttsHlsRefreshTimer); ttsHlsRefreshTimer = null }
    ttsHlsRefreshPromise = null
    ttsHlsMode = false
    ttsHlsQueueId = ''
    ttsHlsStatusUrl = ''
    ttsHlsItems = []
    ttsHlsQueueIndex = 0
    ttsHlsNavigatingChapterId = ''
    ttsHlsStartOffsetSeconds = 0
    ttsFallbackPlayback?.clearWatchdog()
    if (ttsRebuildTimer) { clearTimeout(ttsRebuildTimer); ttsRebuildTimer = null }
    ttsRebuildRequested = false
    if (ttsLifecycle.snapshot().state !== 'idle') {
      if (ttsLifecycle.snapshot().state !== 'stopping') ttsLifecycle.stop()
      ttsLifecycle.finish()
    }
    ttsPlanGeneration++
    if (preservePending) {
      // The current item has ended already. Detach the old chapter callbacks,
      // but preserve the unlocked Audio element for seamless next-chapter play.
      if (ttsAudioEl) {
        ttsAudioEl.onended = null
        ttsAudioEl.onerror = null
        ttsAudioEl.ontimeupdate = null
        ttsAudioEl.onplaying = null
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
    ttsChapterPlan = []
    ttsPlanIndex = 0
    ttsNextChapterPlan = []
    ttsNextChapterSignature = ''
    ttsNextChapterId = null
    ttsNextChapterFollowingId = null
    ttsNextChapterTitle = ''
    ttsNextChapterStreamUrl = ''
    ttsContinuousStreamMode = false
    ttsStreamStartPlanIndex = 0
    ttsStreamResumeBaseSeconds = 0
    ttsTimelineLoadedThrough = -1
    ttsActiveStreamId = ''; ttsStreamEnding = false
    ttsLastStreamCurrentTimeSeconds = 0; ttsTrustedPlanIndex = 0; ttsTrustedItemOffsetSeconds = 0
    ttsNextChapterCached = false
    if (ttsHighlightRetryFrame) cancelAnimationFrame(ttsHighlightRetryFrame)
    ttsHighlightRetryFrame = null; ttsPendingHighlightIndex = null; ttsHighlightRetryAttempts = 0
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
      const paragraph = readerParagraphFromPoint(x, y)
      const index = readerParagraphIndex(paragraph)
      if (index >= 0) return index
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
    audio.ontimeupdate = null
    ttsRebuildRequested = false
    ttsLifecycle.restart()
    ttsPlanGeneration++
    ttsClearChapterEndWatchdog()
    ttsChapterEndWatchdogAttempts = 0
    ttsStreamCompletionPromise = null
    ttsChapterTransitionPromise = null
    ttsNextChapterPrefetchPromise = null
    ttsNextChapterPrefetchSourceId = ''
    audiobookAbortController?.abort()
    if (ttsHlsRefreshTimer) { clearInterval(ttsHlsRefreshTimer); ttsHlsRefreshTimer = null }
    ttsHlsRefreshPromise = null; ttsHlsMode = false; ttsHlsQueueId = ''; ttsHlsStatusUrl = ''; ttsHlsItems = []; ttsHlsQueueIndex = 0
    window.OOHStoryAudiobookCache?.cancel?.()
    window.OOHStoryAudiobookCache?.releaseUrls?.()
    if (audiobookServerSessionId) {
      fetch(`/api/v1/audiobook/sessions/${audiobookServerSessionId}`, {
        method: 'DELETE', credentials: 'same-origin', keepalive: true,
        headers: { 'X-Audiobook-Client': audiobookClientId }
      }).catch(() => {})
      audiobookServerSessionId = ''
    }
    state.ttsPendingPlan = null
    state.ttsContinueOnLoad = false
    ttsChapterPlan = []
    ttsChapterStreamUrl = ''
    ttsActiveStreamId = ''; ttsStreamEnding = false
    ttsPlanIndex = 0
    ttsLastStreamCurrentTimeSeconds = 0; ttsTrustedPlanIndex = 0; ttsTrustedItemOffsetSeconds = 0
    ttsNextChapterPlan = []
    ttsNextChapterSignature = ''
    ttsNextChapterStreamUrl = ''
    ttsNextChapterCached = false
    ttsBackendManifest(startIdx, false).catch(error => {
      if (!state.reader.ttsActive || error?.name === 'AbortError') return
      if (ttsLifecycle.isPausedByUser()) return
      console.warn('[TTS] backend manifest unavailable', error)
      ttsMarkPlaybackBlocked(error, ttsBackendFailureNotice(error))
    })
  }

  const ttsScheduleRebuild = () => {
    if (state.ttsController?.active && state.ttsController.owner !== ttsOwner) {
      state.ttsController.rebuild?.()
      return
    }
    if (!state.reader.ttsActive) return
    if (ttsRebuildTimer) clearTimeout(ttsRebuildTimer)
    ttsRebuildRequested = true
    ttsRebuildTimer = window.setTimeout(() => {
      ttsRebuildTimer = null
      if (!state.reader.ttsActive) return
      ttsRebuildActivePlan()
    }, 250)
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
    // Prime playback inside the user's click stack before any network/cache
    // await. This is the actual iOS/Safari media unlock, not just construction.
    ttsPrimeAudioFromGesture()
    state.reader.ttsActive = true
    ttsLifecycle.start()
    ttsRebuildRequested = false
    saveReaderSettings()
    const chapterMetrics = ttsChapterMetrics(requestedChapterId)
    state.ttsSession = {
      active: true,
      detached: false,
      bookId: String(requestedBookId),
      chapterId: String(requestedChapterId),
      paragraphIndex: explicitStart ? Math.max(0, startParagraph) : 0,
      paragraphCount: 1,
      itemIndex: 0,
      absoluteItemIndex: 0,
      absoluteItemCount: 1,
      itemCount: 1,
      currentText: '',
      currentEmotion: 'neutral',
      bookTitle: chapter?.book?.title || '',
      chapterTitle: chapter?.title || '',
      chapterNumber: chapterMetrics.number,
      chapterCount: chapterMetrics.count,
      contextItems: [],
      coverUrl: chapter?.book?.cover_url || `/api/v1/books/${requestedBookId}/cover`,
      mediaCoverUrl: `/api/v1/books/${requestedBookId}/cover?variant=media-art`,
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
        ttsFallbackPlayback?.clearWatchdog()
        if (ttsAudioEl && !ttsAudioEl.paused) ttsAudioEl.pause()
        if (['starting', 'connecting', 'playing'].includes(ttsLifecycle.snapshot().state)) {
          ttsLifecycle.pause()
        }
        ttsUpdateControls()
      },
      previous: () => {
        if (ttsHlsMode) { ttsHlsSeekToQueueIndex(ttsHlsQueueIndex - 1); return }
        if (ttsPlanIndex > 0) {
          if (ttsSegmentFallbackMode) ttsFallbackPlayback.play(ttsPlanIndex - 1)
          else ttsPlayItem(ttsPlanIndex - 1)
        }
      },
      next: () => {
        if (ttsHlsMode) { ttsHlsSeekToQueueIndex(ttsHlsQueueIndex + 1); return }
        if (ttsPlanIndex < ttsChapterPlan.length - 1) {
          if (ttsSegmentFallbackMode) ttsFallbackPlayback.play(ttsPlanIndex + 1)
          else ttsPlayItem(ttsPlanIndex + 1)
        }
      },
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
      previousChapter: () => {
        if (ttsHlsMode) {
          const currentChapterId = String(state.ttsSession?.chapterId || '')
          let target = ttsHlsQueueIndex - 1
          while (target >= 0 && ttsHlsItems[target].chapterId === currentChapterId) target--
          if (target >= 0) {
            const targetChapterId = ttsHlsItems[target].chapterId
            while (target > 0 && ttsHlsItems[target - 1].chapterId === targetChapterId) target--
            ttsHlsSeekToQueueIndex(target)
          }
          return
        }
        const currentPosition = catalog.chapters.findIndex(item => String(item.id) === String(state.ttsSession?.chapterId))
        const targetId = currentPosition > 0 ? catalog.chapters[currentPosition - 1]?.id : null
        if (!targetId) return
        state.ttsContinueOnLoad = true
        goToChapter(targetId, true)
      },
      nextChapter: () => {
        if (ttsHlsMode) {
          const currentChapterId = String(state.ttsSession?.chapterId || '')
          const target = ttsHlsItems.findIndex((item, index) => index > ttsHlsQueueIndex && item.chapterId !== currentChapterId)
          if (target >= 0) ttsHlsSeekToQueueIndex(target)
          return
        }
        if (ttsFollowingChapterId) ttsChapterEnd()
      },
      hasPreviousChapter: () => catalog.chapters.findIndex(item => String(item.id) === String(state.ttsSession?.chapterId)) > 0,
      hasNextChapter: () => Boolean(ttsFollowingChapterId),
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
      const startIdx = explicitStart
        ? Math.max(0, startParagraph)
        : 0
      console.log('[TTS] mode:', state.reader.ttsMode, 'narrator:', state.reader.ttsNarrator, 'paragraphs:', ttsParagraphs().length, 'startIdx:', startIdx)
      ttsChapterPlan = []
      state.ttsSession.paragraphIndex = startIdx
      ttsBackendManifest(startIdx, false).catch(error => {
        if (!state.reader.ttsActive || error?.name === 'AbortError') return
        if (ttsLifecycle.isPausedByUser()) return
        console.warn('[TTS] backend manifest unavailable', error)
        ttsMarkPlaybackBlocked(error, ttsBackendFailureNotice(error))
      })
    }
    state.ttsSession.contextItems = ttsChapterPlan.map(item => item.text || '')
    ttsNextChapterCached = false
    ttsNextChapterSignature = ''
    if (ttsHeartbeat) clearInterval(ttsHeartbeat)
    ttsHeartbeat = setInterval(() => {
      if (!state.reader.ttsActive) { clearInterval(ttsHeartbeat); ttsHeartbeat = null }
    }, 5000)
    ttsCacheWindow(1)
    if (state.reader.ttsActive && ttsChapterPlan.length) ttsPlayItem(0)
  }

  const ttsResumePlayback = () => {
    if (!state.reader.ttsActive || ttsStreamEnding) return
    if (ttsLifecycle.isPausedByUser()) ttsLifecycle.resume()
    if (!ttsChapterPlan.length) {
      if (ttsLifecycle.isBlocked()) ttsLifecycle.retry()
      ttsUpdateControls()
      ttsRebuildActivePlan()
      return
    }
    const retryCurrentItem = ttsLifecycle.isBlocked()
    if (retryCurrentItem) ttsLifecycle.retry()
    else ttsLifecycle.connect()
    ttsUpdateControls()
    const audio = ttsEnsureAudio()
    if ((ttsHlsMode || !retryCurrentItem) && audio.src && audio.paused && !audio.ended) {
      Promise.resolve(audio.play()).then(() => {
        ttsLifecycle.playing()
        ttsUpdateControls()
      }).catch(error => ttsMarkPlaybackBlocked(error))
      return
    }
    const retryIndex = Math.max(0, Math.min(Math.max(ttsPlanIndex, ttsTrustedPlanIndex), ttsChapterPlan.length - 1)), retryOffset = retryIndex === ttsTrustedPlanIndex ? ttsTrustedItemOffsetSeconds : ttsCurrentItemOffsetSeconds(retryIndex)
    if (ttsSegmentFallbackMode) ttsFallbackPlayback.play(retryIndex, retryOffset); else ttsPlayItem(retryIndex, retryOffset)
  }

  const restartTTSFromChapterStart = () => {
    try {
      startTTS(0)
    } catch (error) {
      if (ttsLifecycle.snapshot().state !== 'idle') {
        if (ttsLifecycle.snapshot().state !== 'stopping') ttsLifecycle.stop()
        ttsLifecycle.finish()
      }
      state.reader.ttsActive = false
      state.ttsController = null
      state.ttsSession = null
      saveReaderSettings()
      ttsUpdateControls()
      console.error('[TTS] player initialization failed', error)
      return
    }
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
      onclick: restartTTSFromChapterStart
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
      onclick: openTtsPlayer
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
      restartTTSFromChapterStart()
      if (state.reader.ttsActive) setSettingsVisible(false)
    }
  })
  ttsExitButton = node('button', {
    type: 'button',
    class: 'reader-tts-exit-inline',
    text: '退出听书',
    hidden: state.reader.ttsActive ? null : '',
    onclick: () => {
      stopTTS()
      setSettingsVisible(false)
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
  ttsExitControl = node('section', {
    class: 'reader-tts-exit',
    hidden: state.reader.ttsActive ? null : ''
  }, [
    node('div', { class: 'reader-tts-exit-copy' }, [
      node('span', { text: 'AUDIOBOOK ACTIVE' }),
      node('strong', { text: '正在听书' }),
      node('small', { text: '停止音频、销毁本次会话，并关闭底部听书浮层。' })
    ]),
    node('button', {
      type: 'button',
      text: '退出听书',
      onclick: () => {
        stopTTS()
        setSettingsVisible(false)
      }
    })
  ])
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
    node('div', { class: 'reader-setting-toggles' }, [eyeCareButton, autoButton, ttsButton, ttsExitButton].filter(Boolean)),
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
      let voices = []
      let modeVoiceFilter = {}
      let modeDefaults = {}
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
        const filterLang = modeVoiceFilter[mode] || 'zh-CN'
        voiceSelectEl.disabled = narratorSelectEl.disabled = voices.length === 0
        if (!voices.length) return
        if (!voices.some(v => v.lang === filterLang && v.key === state.reader.ttsVoice)) {
          state.reader.ttsVoice = modeDefaults[mode]
        }
        if (!voices.some(v => v.lang === 'zh-CN' && v.key === state.reader.ttsNarrator)) {
          state.reader.ttsNarrator = 'mocheng'
        }
        buildVoiceOptions(voiceSelectEl, filterLang, state.reader.ttsVoice)
        buildVoiceOptions(narratorSelectEl, 'zh-CN', state.reader.ttsNarrator)
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
      ttsVoicePolicyPromise.then(policy => {
        voices = (policy.voices || []).map(voice => ({
          key: voice.key, label: voice.label,
          gender: voice.gender, lang: voice.language
        }))
        modeVoiceFilter = policy.mode_languages || {}
        modeDefaults = policy.mode_defaults || {}
        refreshVoiceSelects()
      }).catch(error => console.warn('[TTS] voice registry unavailable', error))
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
    const resolvedParagraph = readerParagraphFromPoint(clientX, clientY, paragraph) || paragraph
    currentParagraphHint = readerParagraphIndex(resolvedParagraph)
    const paragraphIndex = Math.max(0, readerParagraphIndex(resolvedParagraph))
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
          onclick: event => { event.stopPropagation(); openInterlineDialog(resolvedParagraph) }
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
      const resolvedParagraph = updateCurrentParagraphFromPoint(event.clientX, event.clientY, paragraph) || paragraph
      currentParagraphHint = readerParagraphIndex(resolvedParagraph)
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
  readerContent.addEventListener('pointerdown', event => {
    updateCurrentParagraphFromPoint(event.clientX, event.clientY)
  }, { passive: true })
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
  let autoAdvanceTimer = null
  const handleAutoAdvance = () => {
    if (state.reader.mode !== 'vertical' || !nextId) return
    if (state.reader.autoReading || state.reader.ttsActive) return
    const metrics = readerScrollMetrics(stage, 'vertical')
    const atBottom = metrics.scrollTop + metrics.clientHeight >= metrics.scrollHeight - 8
    if (atBottom && metrics.scrollHeight > metrics.clientHeight + 20) {
      if (!autoAdvanceTimer) {
        autoAdvanceTimer = setTimeout(() => {
          autoAdvanceTimer = null
          const m = readerScrollMetrics(stage, 'vertical')
          if (m.scrollTop + m.clientHeight >= m.scrollHeight - 8) goToChapter(nextId)
        }, 1200)
      }
    } else if (autoAdvanceTimer) {
      clearTimeout(autoAdvanceTimer)
      autoAdvanceTimer = null
    }
  }
  stage.addEventListener('scroll', handleAutoAdvance, { passive: true })
  window.addEventListener('scroll', handleAutoAdvance, { passive: true })
  const resizeListener = () => queuePagination(false)
  window.addEventListener('resize', resizeListener)
  visibilityListener = () => {
    if (document.hidden) {
      flushReadingProgress()
      if (!state.reader.ttsActive) stopAutoReading()
    } else {
      if (state.reader.ttsActive && !ttsStreamEnding && !ttsLifecycle.isPausedByUser() && ttsAudioEl && ttsAudioEl.paused && ttsPlanIndex >= 0) {
        ttsResumePlayback()
      }
    }
  }
  document.addEventListener('visibilitychange', visibilityListener)
  const pageShowListener = () => {
    if (state.reader.ttsActive && !ttsStreamEnding && !ttsLifecycle.isPausedByUser() && ttsAudioEl?.paused && ttsPlanIndex >= 0) ttsResumePlayback()
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
      if (autoAdvanceTimer) { clearTimeout(autoAdvanceTimer); autoAdvanceTimer = null }
      window.removeEventListener('scroll', handleAutoAdvance)
      window.removeEventListener('scroll', handleProgressScroll)
      window.removeEventListener('resize', resizeListener)
      document.removeEventListener('visibilitychange', visibilityListener)
      window.removeEventListener('pageshow', pageShowListener)
      window.removeEventListener('pagehide', pageHideListener)
    }
  }
  startReadingActivity(String(requestedBookId))
  requestAnimationFrame(() => {
    if (!routeIsCurrent(navigationGeneration, expectedReaderPath)) return
    if (catalogRendered) chapterList.querySelector('.active')?.scrollIntoView({ block: 'center' })
    stage.focus({ preventScroll: true })
    recomputePagination(true)
    const afterLayout = () => {
      if (!routeIsCurrent(navigationGeneration, expectedReaderPath)) return
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
      // Establish the newly opened chapter as the authoritative local/cloud
      // reading record before an older chapter's delayed lifecycle sync fires.
      flushReadingProgress()
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
        node('p', { class: 'deconstruction-document-summary', text: item.documents.map(doc => doc.label).join(' · ') || '拆解资料整理中' }),
        item.contributor_username ? node('p', { class: 'deconstruction-contributor', text: `贡献者：${item.contributor_username}` }) : null
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
        ]),
        node('span', { class: 'deconstruction-like-count', text: `♡ ${formatNumber(item.like_count || 0)}` })
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
  const access = state.account
    ? await accountApi(`/api/v1/me/deconstructions/${encodeURIComponent(slug)}/access`).catch(() => null)
    : null
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
    ...data.documents.map(doc => Array.isArray(doc.items)
      ? {
          type: 'subdir',
          label: doc.label,
          name: doc.subdirectory,
          items: doc.items
        }
      : { type: 'doc', label: doc.label, content: doc.content }),
    ...subdirs.map(sd => ({ type: 'subdir', label: sd.label, name: sd.name, items: sd.items }))
  ]
  let active = 0
  const tabs = node('div', { class: 'report-tabs' })
  const body = node('div')
  const fileCache = {}
  let liked = Boolean(data.viewer_liked)
  let likeCount = Number(data.like_count || 0)
  const likeLabel = node('span', { text: `${liked ? '♥' : '♡'} ${formatNumber(likeCount)}` })
  const likeButton = node('button', {
    class: `ghost-button deconstruction-like-button${liked ? ' active' : ''}`,
    type: 'button',
    'aria-label': liked ? '取消点赞' : '点赞这份拆书档案',
    onclick: async () => {
      if (!state.account) {
        openAuthDialog('login')
        return
      }
      likeButton.disabled = true
      try {
        const result = await api(`/api/v1/deconstructions/${encodeURIComponent(slug)}/likes`, {
          method: 'POST',
          headers: state.csrfToken ? { 'X-CSRF-Token': state.csrfToken } : {}
        })
        liked = Boolean(result.liked)
        likeCount = Number(result.like_count || 0)
        likeButton.classList.toggle('active', liked)
        likeButton.setAttribute('aria-label', liked ? '取消点赞' : '点赞这份拆书档案')
        likeLabel.textContent = `${liked ? '♥' : '♡'} ${formatNumber(likeCount)}`
      } catch (error) {
        const current = `${liked ? '♥' : '♡'} ${formatNumber(likeCount)}`
        likeLabel.textContent = error.message || '点赞失败'
        window.setTimeout(() => { likeLabel.textContent = current }, 1600)
      } finally {
        likeButton.disabled = false
      }
    }
  }, likeLabel)
  const downloadAction = (() => {
    if (!state.account) return node('button', {
      class: 'ghost-button', type: 'button', text: '登录后下载档案', onclick: () => openAuthDialog('login')
    })
    if (!access || access.can_download) return node('a', {
      class: 'ghost-button', href: `/api/v1/me/deconstructions/${encodeURIComponent(slug)}/download`, download: '', text: '下载完整档案 ZIP'
    })
    const button = node('button', { class: 'ghost-button', type: 'button', text: `${access.download_points} 积分购买并下载` })
    button.addEventListener('click', async () => {
      button.disabled = true
      try {
        await accountApi(`/api/v1/me/deconstructions/${encodeURIComponent(slug)}/purchase`, {
          method: 'POST', body: { expected_points: Number(access.download_points) }
        })
        window.location.href = `/api/v1/me/deconstructions/${encodeURIComponent(slug)}/download`
      } catch (error) {
        button.textContent = error.message
        if (/积分已更新/.test(String(error.message || ''))) {
          window.setTimeout(() => loadDeconstruction(slug), 900)
        } else {
          window.setTimeout(() => { button.textContent = `${access.download_points} 积分购买并下载`; button.disabled = false }, 1800)
        }
      }
    })
    return button
  })()

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
      data.contributor_username ? node('p', { class: 'report-contributor', text: `贡献者：${data.contributor_username}` }) : null,
      node('div', { class: 'report-head-actions' }, [
        data.public_id ? node('a', { class: 'primary-button', href: `/books/${data.public_id}`, text: '打开原作' }) : null,
        likeButton,
        downloadAction
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
    || /^\/account\/(?:history|favorites|bookshelf|deconstruction-tasks|submit|submissions|notifications|profile)$/.test(pathname)) return pathname
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
  staticPage('版权声明与 DMCA 政策', 'Copyright & DMCA Policy', [
    node('p', { text: '最近更新：2026 年 7 月 12 日。本政策适用于 OOH Story 网站、移动端及其相关服务。' }),
    node('h2', { text: '1. 平台性质与用户生成内容（UGC）' }),
    node('p', { text: 'OOH Story（以下简称“本平台”）上的书籍、文档及其他文件主要由注册用户或第三方自发上传、提交或分享。本平台仅提供数据存储、展示、检索、分享与交流所需的技术服务空间，不主动代表用户编辑、修改或上传相关内容。' }),
    node('p', { text: '页面展示、自动化排序、搜索结果或技术处理不代表本平台对相关内容的版权状态作出确认、授权或背书。相关内容的著作权及其他合法权利归原权利人所有。' }),
    node('h2', { text: '2. 版权保护与“通知—删除”机制' }),
    node('p', { text: '本平台尊重知识产权，并按照适用的版权法律法规处理侵权投诉，包括适用时依据美国《数字千年版权法案》（Digital Millennium Copyright Act，DMCA）执行“通知—删除”程序。' }),
    node('p', { text: '如果您是版权权利人或经合法授权的代理人，并确信本平台上的用户上传内容侵犯了您的合法权益，请向下方版权投诉邮箱发送正式侵权通知（Notice of Infringement）。' }),
    node('h2', { text: '3. 有效侵权通知应包含的信息' }),
    node('p', { text: '为便于我们准确定位并有效处理，请在通知中完整提供：' }),
    node('ul', {}, [
      node('li', { text: '权利人或其授权代理人的真实姓名／名称、联系电话、电子邮箱及详细联系地址。' }),
      node('li', { text: '主张受到侵害的版权作品名称、作者及足以识别该作品的说明；涉及多项作品时，可提交具有代表性的清单。' }),
      node('li', { text: '涉嫌侵权内容在本平台上的具体网页链接（URL）、章节、文件名或其他精确定位信息。仅提供网站首页或搜索结果页可能无法完成定位。' }),
      node('li', { text: '能够证明您拥有相关版权或投诉授权的权属材料，例如版权登记证书、授权书、首次发表证明或其他有效证明。' }),
      node('li', { text: '一份善意声明，说明您确信被投诉内容的使用未获得版权人、其代理人或法律授权。' }),
      node('li', { text: '一份真实性与授权声明，确认通知中的信息准确，并在承担伪证责任的前提下声明您是权利人或有权代表权利人提出投诉。' }),
      node('li', { text: '权利人或其授权代理人的手写签名或有效电子签名。' })
    ]),
    node('h2', { text: '4. 审查与处理时效' }),
    node('p', { text: '对于材料完整、权属关系明确且能够准确定位涉嫌侵权内容的正式通知，我们将在收到后的 24 至 48 小时内完成审查，并根据审查结果采取下架、屏蔽、删除内容或断开相关链接等必要措施。' }),
    node('p', { text: '如通知信息不完整、权属存在争议或无法定位具体内容，我们可能要求您补充材料；处理时效自收到完整材料后起算。为防止重复提交，请在同一邮件会话中补充信息。' }),
    node('h2', { text: '5. 反通知（Counter-Notice）' }),
    node('p', { text: '如果相关内容因版权通知被移除或限制，而上传者确信该措施源于错误识别或误删，可以通过同一邮箱提交反通知。反通知应包含上传者的真实联系信息、被移除内容及原位置、错误或误认声明、依法接受相关司法管辖的声明，以及手写或有效电子签名。' }),
    node('p', { text: '在适用法律允许的范围内，我们可将合格的反通知转交原投诉方，并依照法定程序决定是否恢复相关内容。' }),
    node('h2', { text: '6. 重复侵权与虚假投诉' }),
    node('p', { text: '对于经核实的重复侵权用户，本平台可视情节限制或终止其账户及上传权限。故意提交虚假、误导性或恶意通知／反通知，可能导致投诉人承担相应法律责任。' }),
    node('h2', { text: '7. 版权投诉联系方式' }),
    node('p', {}, [
      node('span', { text: '版权投诉与举报专用邮箱：' }),
      node('a', { href: 'mailto:help@example.com?subject=Copyright%20Notice%20%2F%20DMCA', text: 'help@example.com' })
    ]),
    node('p', { text: '为处理投诉，我们可能在必要范围内向相关上传者、服务提供商、专业顾问或主管机关披露通知中的信息。请勿在邮件中提供与权利主张无关的敏感个人信息。' }),
    node('h2', { text: '8. 一般免责声明与正版支持' }),
    node('ul', {}, [
      node('li', { text: '本平台不对用户上传内容的准确性、完整性、合法性或持续可用性作出保证。' }),
      node('li', { text: '因系统维护、网络故障、不可抗力或第三方服务导致的中断，本平台将在合理范围内尽快恢复服务。' }),
      node('li', { text: '本页面仅说明平台的版权投诉处理流程，不构成法律意见，也不限制任何一方依法享有的权利或救济。' }),
      node('li', { text: '本平台支持并鼓励正版阅读。如果您喜欢某部作品，请通过正版渠道购买或订阅，以支持作者与出版机构。' })
    ]),
    node('h2', { text: 'English Summary' }),
    node('p', { text: 'OOH Story primarily hosts user-generated content and provides technical services for storage, display, search and sharing. We respect intellectual property rights and process valid copyright notices under applicable law, including the DMCA where applicable.' }),
    node('p', { text: 'A valid notice should identify the copyrighted work and the exact allegedly infringing URL, provide the claimant’s contact and ownership information, include good-faith and accuracy statements made under penalty of perjury, and carry a physical or electronic signature. Complete notices are reviewed within 24–48 hours. Affected uploaders may submit a valid counter-notice. Repeat infringers may have their accounts or upload privileges terminated.' }),
    node('p', {}, [
      node('span', { text: 'Copyright contact: ' }),
      node('a', { href: 'mailto:help@example.com?subject=Copyright%20Notice%20%2F%20DMCA', text: 'help@example.com' })
    ])
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
          node('p', { text: 'help@example.com' })
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
            node('a', { class: 'client-btn client-btn-primary', href: '/downloads/android/latest.apk', download: '', text: '下载 APK v1.18.21 安装包' })
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
  const libraryReturnPath = libraryReturnPathFor(bookId)
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
  const navigationGeneration = ++state.routeGeneration
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
    else if (path === '/account/deconstruction-tasks') await loadDeconstructionTasksPage()
    else if (path === '/account/submit') await loadSubmitPage()
    else if (path === '/account/submissions') await loadMySubmissionsPage()
    else if (path === '/account/notifications') await loadNotificationsPage()
    else if (path === '/account/profile') await loadProfilePage()
    else if (path === '/admin' || path.startsWith('/admin/')) await loadAdminPage(path)
    else if (/^\/book\/[A-Za-z0-9_-]{22}\/volume\/\d+$/.test(path)) {
      const parts = path.split('/')
      await loadVolume(parts[2], parts[4])
    }
    else if (/^\/book\/[A-Za-z0-9_-]{22}$/.test(path)) await loadBook(path.split('/')[2])
    else if (/^\/read\/[A-Za-z0-9_-]{22}\/\d+$/.test(path)) {
      const [, , bookId, chapterId] = path.split('/')
      await loadReader(bookId, chapterId, navigationGeneration)
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
    if (!routeIsCurrent(navigationGeneration, path)) return
    if (error?.edgeRecovery) return
    setSeo({
      title: '页面暂时无法打开｜OOH Story',
      description: '当前页面不存在或暂时无法读取，请返回 OOH Story 首页继续浏览。',
      canonicalPath: '/',
      robots: 'noindex, nofollow'
    })
    errorView(error)
  }
  if (!routeIsCurrent(navigationGeneration, path)) return
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
  if (link.hasAttribute('download') || link.target
    || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return
  let url
  try { url = new URL(link.href, location.href) } catch { return }
  if (url.origin !== location.origin || !/^https?:$/.test(url.protocol)) return
  if (!isSpaNavigationTarget(url)) return
  event.preventDefault()
  navigateInApp(`${url.pathname}${url.search}${url.hash}`)
}, { capture: true })

globalTtsReturn?.addEventListener('click', openTtsPlayer)
ttsPlayerClose?.addEventListener('click', closeTtsPlayer)
ttsPlayerReturn?.addEventListener('click', returnTtsToReader)
ttsPlayerText?.addEventListener('click', returnTtsToReader)
ttsPlayerPrevious?.addEventListener('click', () => state.ttsController?.previousChapter?.())
ttsPlayerNext?.addEventListener('click', () => state.ttsController?.nextChapter?.())
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
