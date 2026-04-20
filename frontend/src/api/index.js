import axios from 'axios'

// ── API clients ───────────────────────────────────────────────────────────────
//
// Two backends may exist simultaneously:
//
//   localApi  → always http://127.0.0.1:8001  (local Python, for recording)
//   api       → relative /api                 (current host, local or remote)
//
// Recording APIs (running-windows, start, stop, status, mic-check, pick-file)
// MUST use localApi so they run against the user's own machine.
//
// Gallery / Editor / Sync use the regular `api` so they work whether the
// frontend is served locally or from a remote server.

const LOCAL_BASE = 'http://127.0.0.1:8001/api'

// Detect if we are running inside the packaged Electron app in remote mode.
// In remote mode window.electronAPI.isElectron is true but the page origin is
// NOT 127.0.0.1, so we need a separate axios instance for local recording.
export const isRemoteMode = (
  typeof window !== 'undefined' &&
  window.location.hostname !== '127.0.0.1' &&
  window.location.hostname !== 'localhost'
)

/**
 * Build a URL for a local-only resource (screenshots, local session data).
 * In remote mode the path must be prefixed with the local backend origin
 * so the browser fetches from 127.0.0.1 instead of the remote server.
 *
 * Usage:  <img src={localUrl(`/api/autocad/sessions/${id}/events/${eid}/image`)} />
 */
export const localUrl = (path) =>
  isRemoteMode ? `http://127.0.0.1:8001${path}` : path

const api      = axios.create({ baseURL: '/api' })
const localApi = isRemoteMode
  ? axios.create({ baseURL: LOCAL_BASE })
  : api   // in local mode both point to the same backend

// ── AutoCAD / Recording  (always local) ──────────────────────────────────────
export const getAutoCADStatus  = () => localApi.get('/autocad/status').then(r => r.data)
export const getRunningWindows = () => localApi.get('/autocad/running-windows').then(r => r.data)
export const startAutoCAD      = ({ title = '', targetExe = 'acad.exe', enableVoice = true, enableCom = true, screenshotOnCommand = true, screenshotOnClick = false, screenshotOnMiddleDrag = false, screenshotOnScrollZoom = false, screenshotOnShiftPan = false, creoTrailFile = '', background = '' } = {}) =>
  localApi.post(`/autocad/start?title=${encodeURIComponent(title)}&target_exe=${encodeURIComponent(targetExe)}&enable_voice=${enableVoice}&enable_com=${enableCom}&screenshot_on_command=${screenshotOnCommand}&screenshot_on_click=${screenshotOnClick}&screenshot_on_middle_drag=${screenshotOnMiddleDrag}&screenshot_on_scroll_zoom=${screenshotOnScrollZoom}&screenshot_on_shift_pan=${screenshotOnShiftPan}&creo_trail_file=${encodeURIComponent(creoTrailFile)}&background=${encodeURIComponent(background)}`).then(r => r.data)
export const stopAutoCAD       = () => localApi.post('/autocad/stop').then(r => r.data)

export const listAutoCADSessions        = ()               => localApi.get('/autocad/sessions').then(r => r.data)
export const getAutoCADSession          = (id)             => localApi.get(`/autocad/sessions/${id}`).then(r => r.data)
export const deleteAutoCADSession       = (id)             => localApi.delete(`/autocad/sessions/${id}`).then(r => r.data)
export const getAutoCADEvents           = (id, params = {})=> localApi.get(`/autocad/sessions/${id}/events`, { params }).then(r => r.data)
export const regenerateAutoCADNarration = (id, lang = 'zh')=> localApi.post(`/autocad/sessions/${id}/generate?lang=${lang}`).then(r => r.data)

export const submitAutoCADAvatar = (id, { provider = 'heygen', avatarId = '', voiceId = '', apiKey = '' } = {}) =>
  localApi.post(`/autocad/sessions/${id}/avatar`, null, {
    params: { provider, avatar_id: avatarId, voice_id: voiceId, api_key: apiKey },
  }).then(r => r.data)

export const pollAutoCADAvatar = (id, { provider = 'heygen', apiKey = '' } = {}) =>
  localApi.get(`/autocad/sessions/${id}/avatar/status`, { params: { provider, api_key: apiKey } }).then(r => r.data)

export const generateAutoCADVideo  = (id, fps = 1.0) =>
  localApi.post(`/autocad/sessions/${id}/video`, null, { params: { fps } }).then(r => r.data)
export const getAutoCADVideoStatus = (id) =>
  localApi.get(`/autocad/sessions/${id}/video/status`).then(r => r.data)

// ── Frame Editor  (local — frames are stored locally until synced) ────────────
export const listFrames             = (id)                  => localApi.get(`/autocad/sessions/${id}/frames`).then(r => r.data)
export const updateFrame            = (id, eventId, body)   => localApi.patch(`/autocad/sessions/${id}/frames/${eventId}`, body).then(r => r.data)
export const deleteFrame            = (id, eventId)         => localApi.delete(`/autocad/sessions/${id}/frames/${eventId}`).then(r => r.data)
export const distributeNarration    = (id)                  => localApi.post(`/autocad/sessions/${id}/frames/distribute`).then(r => r.data)
export const generateAnnotatedVideo = (id, fps = 1.0)       => localApi.post(`/autocad/sessions/${id}/video/annotated`, null, { params: { fps } }).then(r => r.data)
export const generateNarratedVideo  = (id, voice = 'alloy') => localApi.post(`/autocad/sessions/${id}/video/narrated`, null, { params: { voice } }).then(r => r.data)
export const getNarratedVideoStatus = (id)                  => localApi.get(`/autocad/sessions/${id}/video/narrated/status`).then(r => r.data)

// ── Utilities  (always local — file picker and mic are on the user's machine) ─
export const pickFile = ({ title = '选择文件', filterName = '所有文件', filterExt = '*.*' } = {}) =>
  localApi.get('/util/pick-file', { params: { title, filter_name: filterName, filter_ext: filterExt } }).then(r => r.data)

export const micCheck = (durationMs = 2000) =>
  localApi.get('/util/mic-check', { params: { duration_ms: durationMs }, timeout: 30000 }).then(r => r.data)

// ── Gallery  (local backend — shows the current user's recordings) ────────────
// In remote mode localApi → 127.0.0.1:8001; in local mode it's the same as api.
export const getGallery        = () => localApi.get('/gallery').then(r => r.data)
export const deleteGalleryItem = (id) => localApi.delete(`/gallery/${id}`).then(r => r.data)

// ── Sync  (local → push to remote server) ────────────────────────────────────
export const syncPushSession = (sessionId, serverUrl) =>
  localApi.post(`/sync/push/${sessionId}?server_url=${encodeURIComponent(serverUrl)}`, null, { timeout: 120000 }).then(r => r.data)

export const syncGetStatus = (sessionId) =>
  localApi.get(`/sync/status/${sessionId}`).then(r => r.data)
