import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export const getStatus    = ()          => api.get('/tracker/status').then(r => r.data)
export const startTracker = (interval)  => api.post(`/tracker/start?poll_interval=${interval}`).then(r => r.data)
export const stopTracker  = ()          => api.post('/tracker/stop').then(r => r.data)

export const getSessions  = (date)      => api.get('/sessions', { params: { date, limit: 500 } }).then(r => r.data)
export const getStatsToday = ()         => api.get('/stats/today').then(r => r.data)
export const getStatsDate  = (date)     => api.get(`/stats/date/${date}`).then(r => r.data)
export const getActiveDays = ()         => api.get('/stats/days').then(r => r.data)

// ── CreoScribe ────────────────────────────────────────────────────────────────
export const getScribeStatus     = ()         => api.get('/scribe/status').then(r => r.data)
export const startScribe         = ({ title = '', targetApp = 'xtop.exe', enableVoice = true, enableUia = true } = {}) =>
  api.post(`/scribe/start?title=${encodeURIComponent(title)}&target_app=${encodeURIComponent(targetApp)}&enable_voice=${enableVoice}&enable_uia=${enableUia}`).then(r => r.data)
export const stopScribe          = (generate = true) => api.post(`/scribe/stop?generate=${generate}`).then(r => r.data)

export const listScribeSessions  = ()         => api.get('/scribe/sessions').then(r => r.data)
export const getScribeSession    = (id)       => api.get(`/scribe/sessions/${id}`).then(r => r.data)
export const deleteScribeSession = (id)       => api.delete(`/scribe/sessions/${id}`).then(r => r.data)
export const regenerateNarration = (id)       => api.post(`/scribe/sessions/${id}/generate`).then(r => r.data)

export const submitAvatar = (id, { provider = 'heygen', avatarId = '', voiceId = '', apiKey = '' } = {}) =>
  api.post(`/scribe/sessions/${id}/avatar`, null, {
    params: { provider, avatar_id: avatarId, voice_id: voiceId, api_key: apiKey },
  }).then(r => r.data)

export const pollAvatarStatus = (id, { provider = 'heygen', apiKey = '' } = {}) =>
  api.get(`/scribe/sessions/${id}/avatar/status`, { params: { provider, api_key: apiKey } }).then(r => r.data)

// ── AutoCAD Monitor ───────────────────────────────────────────────────────────
export const getAutoCADStatus      = ()         => api.get('/autocad/status').then(r => r.data)
export const startAutoCAD          = ({ title = '', enableVoice = true, enableCom = true } = {}) =>
  api.post(`/autocad/start?title=${encodeURIComponent(title)}&enable_voice=${enableVoice}&enable_com=${enableCom}`).then(r => r.data)
export const stopAutoCAD           = (generate = true) => api.post(`/autocad/stop?generate=${generate}`).then(r => r.data)

export const listAutoCADSessions   = ()         => api.get('/autocad/sessions').then(r => r.data)
export const getAutoCADSession     = (id)       => api.get(`/autocad/sessions/${id}`).then(r => r.data)
export const deleteAutoCADSession  = (id)       => api.delete(`/autocad/sessions/${id}`).then(r => r.data)
export const getAutoCADEvents      = (id, params = {}) =>
  api.get(`/autocad/sessions/${id}/events`, { params }).then(r => r.data)
export const regenerateAutoCADNarration = (id)  => api.post(`/autocad/sessions/${id}/generate`).then(r => r.data)

export const submitAutoCADAvatar   = (id, { provider = 'heygen', avatarId = '', voiceId = '', apiKey = '' } = {}) =>
  api.post(`/autocad/sessions/${id}/avatar`, null, {
    params: { provider, avatar_id: avatarId, voice_id: voiceId, api_key: apiKey },
  }).then(r => r.data)

export const pollAutoCADAvatar     = (id, { provider = 'heygen', apiKey = '' } = {}) =>
  api.get(`/autocad/sessions/${id}/avatar/status`, { params: { provider, api_key: apiKey } }).then(r => r.data)
