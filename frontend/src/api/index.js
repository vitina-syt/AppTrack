import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

// ── AutoCAD / Recording ───────────────────────────────────────────────────────
export const getAutoCADStatus      = ()         => api.get('/autocad/status').then(r => r.data)
export const getRunningWindows     = ()         => api.get('/autocad/running-windows').then(r => r.data)
export const startAutoCAD          = ({ title = '', targetExe = 'acad.exe', enableVoice = true, enableCom = true, screenshotOnCommand = true, screenshotOnClick = false, screenshotOnMiddleDrag = false, screenshotOnScrollZoom = false, screenshotOnShiftPan = false, creoTrailFile = '', background = '' } = {}) =>
  api.post(`/autocad/start?title=${encodeURIComponent(title)}&target_exe=${encodeURIComponent(targetExe)}&enable_voice=${enableVoice}&enable_com=${enableCom}&screenshot_on_command=${screenshotOnCommand}&screenshot_on_click=${screenshotOnClick}&screenshot_on_middle_drag=${screenshotOnMiddleDrag}&screenshot_on_scroll_zoom=${screenshotOnScrollZoom}&screenshot_on_shift_pan=${screenshotOnShiftPan}&creo_trail_file=${encodeURIComponent(creoTrailFile)}&background=${encodeURIComponent(background)}`).then(r => r.data)
export const stopAutoCAD           = () => api.post('/autocad/stop').then(r => r.data)

export const listAutoCADSessions   = ()         => api.get('/autocad/sessions').then(r => r.data)
export const getAutoCADSession     = (id)       => api.get(`/autocad/sessions/${id}`).then(r => r.data)
export const deleteAutoCADSession  = (id)       => api.delete(`/autocad/sessions/${id}`).then(r => r.data)
export const getAutoCADEvents      = (id, params = {}) =>
  api.get(`/autocad/sessions/${id}/events`, { params }).then(r => r.data)
export const regenerateAutoCADNarration = (id, lang = 'zh') => api.post(`/autocad/sessions/${id}/generate?lang=${lang}`).then(r => r.data)

export const submitAutoCADAvatar   = (id, { provider = 'heygen', avatarId = '', voiceId = '', apiKey = '' } = {}) =>
  api.post(`/autocad/sessions/${id}/avatar`, null, {
    params: { provider, avatar_id: avatarId, voice_id: voiceId, api_key: apiKey },
  }).then(r => r.data)

export const pollAutoCADAvatar     = (id, { provider = 'heygen', apiKey = '' } = {}) =>
  api.get(`/autocad/sessions/${id}/avatar/status`, { params: { provider, api_key: apiKey } }).then(r => r.data)

export const generateAutoCADVideo  = (id, fps = 1.0) =>
  api.post(`/autocad/sessions/${id}/video`, null, { params: { fps } }).then(r => r.data)
export const getAutoCADVideoStatus = (id) =>
  api.get(`/autocad/sessions/${id}/video/status`).then(r => r.data)

// ── Frame Editor ──────────────────────────────────────────────────────────────
export const listFrames            = (id)                  => api.get(`/autocad/sessions/${id}/frames`).then(r => r.data)
export const updateFrame           = (id, eventId, body)   => api.patch(`/autocad/sessions/${id}/frames/${eventId}`, body).then(r => r.data)
export const deleteFrame           = (id, eventId)         => api.delete(`/autocad/sessions/${id}/frames/${eventId}`).then(r => r.data)
export const distributeNarration   = (id)                  => api.post(`/autocad/sessions/${id}/frames/distribute`).then(r => r.data)
export const generateAnnotatedVideo= (id, fps = 1.0)       => api.post(`/autocad/sessions/${id}/video/annotated`, null, { params: { fps } }).then(r => r.data)
export const generateNarratedVideo = (id, voice = 'alloy') => api.post(`/autocad/sessions/${id}/video/narrated`, null, { params: { voice } }).then(r => r.data)
export const getNarratedVideoStatus= (id)                  => api.get(`/autocad/sessions/${id}/video/narrated/status`).then(r => r.data)

// ── Utilities ─────────────────────────────────────────────────────────────────
export const pickFile = ({ title = '选择文件', filterName = '所有文件', filterExt = '*.*' } = {}) =>
  api.get('/util/pick-file', { params: { title, filter_name: filterName, filter_ext: filterExt } }).then(r => r.data)

export const micCheck = (durationMs = 2000) =>
  api.get('/util/mic-check', { params: { duration_ms: durationMs }, timeout: 30000 }).then(r => r.data)

// ── Gallery ───────────────────────────────────────────────────────────────────
export const getGallery         = ()    => api.get('/gallery').then(r => r.data)
export const deleteGalleryItem  = (id)  => api.delete(`/gallery/${id}`).then(r => r.data)

// ── Sync ──────────────────────────────────────────────────────────────────────
export const syncPushSession  = (sessionId, serverUrl) =>
  api.post(`/sync/push/${sessionId}?server_url=${encodeURIComponent(serverUrl)}`, null, { timeout: 120000 }).then(r => r.data)

export const syncGetStatus = (sessionId) =>
  api.get(`/sync/status/${sessionId}`).then(r => r.data)
