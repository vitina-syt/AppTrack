import { useState, useEffect, useRef } from 'react'
import { useT } from '../hooks/useT'
import {
  getScribeStatus, startScribe, stopScribe,
  listScribeSessions, getScribeSession,
  regenerateNarration, submitAvatar, pollAvatarStatus,
  deleteScribeSession,
} from '../api'

// ── helpers ───────────────────────────────────────────────────────────────────

function fmt(ts) {
  if (!ts) return '—'
  const d = new Date(ts)
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function StatusBadge({ status }) {
  const colors = {
    recording:  ['#f7768e', '#2d1a20'],
    processing: ['#e0af68', '#2d2510'],
    done:       ['#9ece6a', '#1a2d1a'],
    error:      ['#565f89', '#1a1b2e'],
  }
  const [fg, bg] = colors[status] || ['#a9b1d6', '#1a1b2e']
  return (
    <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 10,
                   background: bg, color: fg, letterSpacing: 0.4 }}>
      {status?.toUpperCase()}
    </span>
  )
}

// ── ScribePage ────────────────────────────────────────────────────────────────

export default function ScribePage() {
  const t = useT()

  const [agentStatus, setAgentStatus] = useState(null)
  const [sessions, setSessions]       = useState([])
  const [selected, setSelected]       = useState(null)    // full session object
  const [loading, setLoading]         = useState(true)

  // Start-form state
  const [title, setTitle]           = useState('')
  const [targetApp, setTargetApp]   = useState('xtop.exe')
  const [enableVoice, setEnableVoice] = useState(true)
  const [enableUia, setEnableUia]   = useState(true)

  // Avatar export state
  const [avatarProvider, setAvatarProvider] = useState('heygen')
  const [avatarId, setAvatarId]   = useState('')
  const [voiceId, setVoiceId]     = useState('')
  const [avatarKey, setAvatarKey] = useState('')
  const [avatarResult, setAvatarResult] = useState(null)

  const pollRef = useRef(null)

  // ── data loading ────────────────────────────────────────────────────────────

  async function reload() {
    try {
      const [status, list] = await Promise.all([getScribeStatus(), listScribeSessions()])
      setAgentStatus(status)
      setSessions(list)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    reload()
    pollRef.current = setInterval(() => {
      reload()
      if (selected) {
        getScribeSession(selected.id).then(s => setSelected(s)).catch(() => {})
      }
    }, 4000)
    return () => clearInterval(pollRef.current)
  }, [selected?.id])

  // ── actions ─────────────────────────────────────────────────────────────────

  async function handleStart() {
    await startScribe({ title, targetApp, enableVoice, enableUia })
    reload()
  }

  async function handleStop() {
    await stopScribe(true)
    reload()
  }

  async function handleSelectSession(sess) {
    const full = await getScribeSession(sess.id)
    setSelected(full)
    setAvatarResult(null)
  }

  async function handleRegenerate() {
    if (!selected) return
    await regenerateNarration(selected.id)
    setSelected(s => ({ ...s, status: 'processing' }))
  }

  async function handleSubmitAvatar() {
    if (!selected) return
    try {
      const result = await submitAvatar(selected.id, {
        provider: avatarProvider, avatarId, voiceId, apiKey: avatarKey,
      })
      setAvatarResult(result)
    } catch (e) {
      setAvatarResult({ error: e?.response?.data?.detail || String(e) })
    }
  }

  async function handlePollAvatar() {
    if (!selected) return
    try {
      const result = await pollAvatarStatus(selected.id, { provider: avatarProvider, apiKey: avatarKey })
      setAvatarResult(result)
      if (result.video_url) {
        setSelected(s => ({ ...s, avatar_video_url: result.video_url }))
      }
    } catch (e) {
      setAvatarResult({ error: String(e) })
    }
  }

  async function handleDelete(id) {
    await deleteScribeSession(id)
    if (selected?.id === id) setSelected(null)
    reload()
  }

  // ── render ───────────────────────────────────────────────────────────────────

  if (loading) return <div style={s.loading}>…</div>

  const isRunning = agentStatus?.running

  return (
    <div style={s.page}>

      {/* ── Header ── */}
      <div style={s.header}>
        <div>
          <h2 style={s.title}>{t.scribe_title}</h2>
          <div style={s.sub}>{t.scribe_subtitle}</div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {isRunning && (
            <span style={s.recBadge}>
              <span style={s.recDot} /> {t.scribe_recording}
            </span>
          )}
        </div>
      </div>

      <div style={s.layout}>

        {/* ── Left: control panel + session list ── */}
        <div style={s.leftCol}>

          {/* Control panel */}
          <div style={s.card}>
            <div style={s.cardTitle}>{t.scribe_control}</div>

            {!isRunning ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <label style={s.label}>{t.scribe_title_label}
                  <input style={s.input} value={title}
                    onChange={e => setTitle(e.target.value)}
                    placeholder={t.scribe_title_placeholder} />
                </label>
                <label style={s.label}>{t.scribe_target_app}
                  <input style={s.input} value={targetApp}
                    onChange={e => setTargetApp(e.target.value)}
                    placeholder="xtop.exe" />
                </label>
                <div style={{ display: 'flex', gap: 16 }}>
                  <label style={s.checkLabel}>
                    <input type="checkbox" checked={enableUia}
                      onChange={e => setEnableUia(e.target.checked)} />
                    {t.scribe_uia}
                  </label>
                  <label style={s.checkLabel}>
                    <input type="checkbox" checked={enableVoice}
                      onChange={e => setEnableVoice(e.target.checked)} />
                    {t.scribe_voice}
                  </label>
                </div>
                <button style={s.btnStart} onClick={handleStart}>{t.scribe_btn_start}</button>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <div style={s.statRow}>
                  <span style={s.statLabel}>{t.scribe_session_id}</span>
                  <span style={s.statVal}>#{agentStatus.session_id}</span>
                </div>
                <div style={s.statRow}>
                  <span style={s.statLabel}>{t.scribe_events}</span>
                  <span style={s.statVal}>{agentStatus.events_captured}</span>
                </div>
                <div style={s.statRow}>
                  <span style={s.statLabel}>{t.scribe_voice_segs}</span>
                  <span style={s.statVal}>{agentStatus.voice_segments}</span>
                </div>
                <button style={s.btnStop} onClick={handleStop}>{t.scribe_btn_stop}</button>
              </div>
            )}
          </div>

          {/* Session list */}
          <div style={s.card}>
            <div style={s.cardTitle}>{t.scribe_sessions}</div>
            {sessions.length === 0
              ? <div style={s.empty}>{t.scribe_no_sessions}</div>
              : sessions.map(sess => (
                  <div key={sess.id}
                    style={{ ...s.sessRow, ...(selected?.id === sess.id ? s.sessRowActive : {}) }}
                    onClick={() => handleSelectSession(sess)}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={s.sessTitle}>{sess.title || `Session #${sess.id}`}</div>
                      <div style={s.sessMeta}>{fmt(sess.started_at)} · {sess.event_count ?? 0} events</div>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
                      <StatusBadge status={sess.status} />
                      <button style={s.btnDel}
                        onClick={e => { e.stopPropagation(); handleDelete(sess.id) }}>✕</button>
                    </div>
                  </div>
                ))
            }
          </div>
        </div>

        {/* ── Right: session detail ── */}
        <div style={s.rightCol}>
          {!selected ? (
            <div style={{ ...s.card, alignItems: 'center', justifyContent: 'center',
                          display: 'flex', minHeight: 300 }}>
              <div style={s.empty}>{t.scribe_select_hint}</div>
            </div>
          ) : (
            <>
              {/* Session info */}
              <div style={s.card}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                  <div style={s.cardTitle}>{selected.title || `Session #${selected.id}`}</div>
                  <StatusBadge status={selected.status} />
                </div>
                <div style={s.metaGrid}>
                  <span style={s.metaKey}>{t.scribe_target_app}</span>
                  <span style={s.metaVal}>{selected.target_app}</span>
                  <span style={s.metaKey}>{t.hist_start}</span>
                  <span style={s.metaVal}>{fmt(selected.started_at)}</span>
                  <span style={s.metaKey}>{t.hist_end}</span>
                  <span style={s.metaVal}>{fmt(selected.ended_at)}</span>
                  <span style={s.metaKey}>{t.scribe_events}</span>
                  <span style={s.metaVal}>{selected.event_count ?? '—'}</span>
                </div>
                {selected.error_message && (
                  <div style={s.errorBox}>{selected.error_message}</div>
                )}
              </div>

              {/* Narration */}
              <div style={s.card}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                  <div style={s.cardTitle}>{t.scribe_narration}</div>
                  <button style={s.btnSecondary} onClick={handleRegenerate}
                    disabled={selected.status === 'processing'}>
                    {selected.status === 'processing' ? t.scribe_generating : t.scribe_regenerate}
                  </button>
                </div>
                {selected.narration_text
                  ? <pre style={s.narration}>{selected.narration_text}</pre>
                  : <div style={s.empty}>
                      {selected.status === 'processing'
                        ? t.scribe_generating
                        : selected.status === 'recording'
                          ? t.scribe_still_recording
                          : t.scribe_no_narration}
                    </div>
                }
              </div>

              {/* Avatar export */}
              <div style={s.card}>
                <div style={s.cardTitle}>{t.scribe_avatar}</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <label style={s.label}>{t.scribe_provider}
                    <select style={s.input} value={avatarProvider}
                      onChange={e => setAvatarProvider(e.target.value)}>
                      <option value="heygen">HeyGen</option>
                      <option value="did">D-ID</option>
                    </select>
                  </label>
                  <label style={s.label}>{t.scribe_avatar_id}
                    <input style={s.input} value={avatarId}
                      onChange={e => setAvatarId(e.target.value)}
                      placeholder={avatarProvider === 'heygen'
                        ? 'HeyGen avatar_id'
                        : 'D-ID presenter image URL'} />
                  </label>
                  <label style={s.label}>{t.scribe_voice_id}
                    <input style={s.input} value={voiceId}
                      onChange={e => setVoiceId(e.target.value)}
                      placeholder={avatarProvider === 'heygen'
                        ? 'HeyGen voice_id'
                        : 'e.g. en-US-JennyNeural'} />
                  </label>
                  <label style={s.label}>{t.scribe_api_key}
                    <input style={{ ...s.input, fontFamily: 'monospace' }}
                      type="password" value={avatarKey}
                      onChange={e => setAvatarKey(e.target.value)}
                      placeholder={`${avatarProvider === 'heygen' ? 'HEYGEN' : 'DID'}_API_KEY`} />
                  </label>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button style={s.btnPrimary} onClick={handleSubmitAvatar}
                      disabled={!selected.narration_text}>
                      {t.scribe_submit_avatar}
                    </button>
                    {(selected.avatar_job_id || avatarResult?.job_id) && (
                      <button style={s.btnSecondary} onClick={handlePollAvatar}>
                        {t.scribe_poll_status}
                      </button>
                    )}
                  </div>

                  {/* Avatar result */}
                  {avatarResult && (
                    <div style={avatarResult.error ? s.errorBox : s.infoBox}>
                      {avatarResult.error
                        ? avatarResult.error
                        : <>
                            <strong>{avatarResult.status}</strong>
                            {avatarResult.video_url && (
                              <div style={{ marginTop: 6 }}>
                                <a href={avatarResult.video_url} target="_blank"
                                   rel="noreferrer" style={{ color: 'var(--accent)' }}>
                                  {t.scribe_video_link}
                                </a>
                              </div>
                            )}
                          </>
                      }
                    </div>
                  )}

                  {selected.avatar_video_url && (
                    <div style={s.infoBox}>
                      {t.scribe_video_ready}:{' '}
                      <a href={selected.avatar_video_url} target="_blank"
                         rel="noreferrer" style={{ color: 'var(--accent)' }}>
                        {t.scribe_video_link}
                      </a>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── styles ────────────────────────────────────────────────────────────────────

const s = {
  page:        { height: '100%', overflowY: 'auto', paddingBottom: 24 },
  loading:     { display: 'flex', alignItems: 'center', justifyContent: 'center',
                 height: '100%', color: 'var(--text-s)' },
  header:      { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 },
  title:       { margin: 0, fontSize: 22, fontWeight: 800, color: 'var(--text)' },
  sub:         { fontSize: 13, color: 'var(--text-s)', marginTop: 4 },

  recBadge:    { display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12,
                 background: '#2d1a20', color: '#f7768e', padding: '4px 10px', borderRadius: 20 },
  recDot:      { width: 7, height: 7, borderRadius: '50%', background: '#f7768e',
                 boxShadow: '0 0 6px #f7768e', animation: 'none' },

  layout:      { display: 'flex', gap: 20, alignItems: 'flex-start' },
  leftCol:     { width: 280, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 16 },
  rightCol:    { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 16 },

  card:        { background: 'var(--surface)', border: '1px solid var(--border)',
                 borderRadius: 12, padding: '18px 20px' },
  cardTitle:   { fontSize: 12, fontWeight: 700, color: 'var(--text-m)', marginBottom: 14,
                 textTransform: 'uppercase', letterSpacing: 0.6 },

  label:       { display: 'flex', flexDirection: 'column', gap: 4,
                 fontSize: 12, color: 'var(--text-s)' },
  input:       { background: 'var(--surface2, #1e2030)', border: '1px solid var(--border)',
                 borderRadius: 7, padding: '7px 10px', color: 'var(--text)', fontSize: 13,
                 outline: 'none', width: '100%', boxSizing: 'border-box' },
  checkLabel:  { display: 'flex', alignItems: 'center', gap: 6, fontSize: 13,
                 color: 'var(--text-m)', cursor: 'pointer' },

  btnStart:    { padding: '9px 0', background: 'var(--accent)', border: 'none',
                 borderRadius: 8, color: '#1a1b2e', fontWeight: 700, fontSize: 14,
                 cursor: 'pointer', width: '100%' },
  btnStop:     { padding: '9px 0', background: '#f7768e', border: 'none',
                 borderRadius: 8, color: '#1a1b2e', fontWeight: 700, fontSize: 14,
                 cursor: 'pointer', width: '100%' },
  btnPrimary:  { padding: '8px 16px', background: 'var(--accent)', border: 'none',
                 borderRadius: 7, color: '#1a1b2e', fontWeight: 700, fontSize: 13,
                 cursor: 'pointer' },
  btnSecondary:{ padding: '8px 14px', background: 'var(--surface2, #1e2030)',
                 border: '1px solid var(--border)', borderRadius: 7,
                 color: 'var(--text-m)', fontSize: 13, cursor: 'pointer' },
  btnDel:      { background: 'transparent', border: 'none', color: 'var(--text-s)',
                 cursor: 'pointer', fontSize: 12, padding: '1px 4px' },

  statRow:     { display: 'flex', justifyContent: 'space-between', fontSize: 13 },
  statLabel:   { color: 'var(--text-s)' },
  statVal:     { color: 'var(--accent)', fontWeight: 700 },

  sessRow:     { display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 8px',
                 borderRadius: 8, cursor: 'pointer', marginBottom: 2 },
  sessRowActive: { background: 'var(--sb-active, rgba(122,162,247,0.12))' },
  sessTitle:   { fontSize: 13, color: 'var(--text)', fontWeight: 600,
                 whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  sessMeta:    { fontSize: 11, color: 'var(--text-s)', marginTop: 3 },
  empty:       { color: 'var(--text-s)', fontSize: 13, textAlign: 'center', padding: '20px 0' },

  metaGrid:    { display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '6px 16px' },
  metaKey:     { fontSize: 12, color: 'var(--text-s)' },
  metaVal:     { fontSize: 12, color: 'var(--text-m)' },

  narration:   { fontSize: 13, color: 'var(--text)', lineHeight: 1.7, whiteSpace: 'pre-wrap',
                 margin: 0, fontFamily: 'inherit', maxHeight: 420, overflowY: 'auto',
                 background: 'var(--surface2, #1e2030)', borderRadius: 8, padding: 14 },

  errorBox:    { background: '#2d1a20', border: '1px solid #f7768e', borderRadius: 8,
                 padding: '10px 14px', fontSize: 12, color: '#f7768e' },
  infoBox:     { background: '#1a2d1a', border: '1px solid #9ece6a', borderRadius: 8,
                 padding: '10px 14px', fontSize: 12, color: '#9ece6a' },
}