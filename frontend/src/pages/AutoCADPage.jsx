import { useState, useEffect, useRef } from 'react'
import { useT } from '../hooks/useT'
import {
  getAutoCADStatus, startAutoCAD, stopAutoCAD,
  listAutoCADSessions, getAutoCADSession,
  getAutoCADEvents,
  regenerateAutoCADNarration,
  submitAutoCADAvatar, pollAutoCADAvatar,
  deleteAutoCADSession,
} from '../api'

// ── constants ─────────────────────────────────────────────────────────────────

const CAT_COLORS = {
  draw:     '#7aa2f7',
  edit:     '#e0af68',
  '3d':     '#bb9af7',
  annotate: '#9ece6a',
  view:     '#73daca',
  layer:    '#ff9e64',
  block:    '#f7768e',
  file:     '#a9b1d6',
  other:    '#565f89',
}

const CAT_LABELS_ZH = {
  draw:     '2D 绘图',
  edit:     '编辑修改',
  '3d':     '三维建模',
  annotate: '标注注释',
  view:     '视图显示',
  layer:    '图层管理',
  block:    '块与参照',
  file:     '文件操作',
  other:    '其他',
}

// ── helpers ───────────────────────────────────────────────────────────────────

function fmt(ts) {
  if (!ts) return '—'
  return new Date(ts).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function fmtTime(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function getCatFromType(uia_element_type) {
  if (!uia_element_type) return null
  const m = uia_element_type.match(/acad_cmd:(\w+)/)
  return m ? m[1] : null
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
    <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px',
                   borderRadius: 10, background: bg, color: fg, letterSpacing: 0.4 }}>
      {status?.toUpperCase()}
    </span>
  )
}

function CommandPill({ name, category }) {
  const color = CAT_COLORS[category] || CAT_COLORS.other
  const cleanName = name?.replace(' ✓', '') || ''
  const isDone = name?.includes('✓')
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      padding: '2px 8px', borderRadius: 6, fontSize: 11, fontWeight: 700,
      background: color + '22', color: color,
      border: `1px solid ${color}44`,
      opacity: isDone ? 1 : 0.7,
    }}>
      {cleanName}
      {isDone && <span style={{ fontSize: 10 }}>✓</span>}
    </span>
  )
}

// ── AutoCADPage ───────────────────────────────────────────────────────────────

export default function AutoCADPage() {
  const t = useT()

  const [agentStatus, setAgentStatus] = useState(null)
  const [sessions, setSessions]       = useState([])
  const [selected, setSelected]       = useState(null)
  const [events, setEvents]           = useState([])
  const [loading, setLoading]         = useState(true)
  const [tab, setTab]                 = useState('commands') // 'commands' | 'narration' | 'avatar'

  // form
  const [title, setTitle]       = useState('')
  const [enableVoice, setVoice] = useState(true)
  const [enableCom, setCom]     = useState(true)

  // avatar
  const [provider, setProvider]   = useState('heygen')
  const [avatarId, setAvatarId]   = useState('')
  const [voiceId, setVoiceId]     = useState('')
  const [apiKey, setApiKey]       = useState('')
  const [avatarResult, setAvatarResult] = useState(null)

  const pollRef = useRef(null)

  // ── loading ──────────────────────────────────────────────────────────────────

  async function reload() {
    try {
      const [st, list] = await Promise.all([getAutoCADStatus(), listAutoCADSessions()])
      setAgentStatus(st)
      setSessions(list)
    } finally {
      setLoading(false)
    }
  }

  async function reloadEvents(sid) {
    if (!sid) return
    try {
      const evts = await getAutoCADEvents(sid)
      setEvents(evts)
    } catch (_) {}
  }

  useEffect(() => {
    reload()
    pollRef.current = setInterval(() => {
      reload()
      if (selected) {
        getAutoCADSession(selected.id).then(s => setSelected(s)).catch(() => {})
        reloadEvents(selected.id)
      }
    }, 3000)
    return () => clearInterval(pollRef.current)
  }, [selected?.id])

  // ── actions ──────────────────────────────────────────────────────────────────

  async function handleStart() {
    await startAutoCAD({ title, enableVoice, enableCom })
    reload()
  }

  async function handleStop() {
    await stopAutoCAD(true)
    reload()
  }

  async function handleSelectSession(sess) {
    const full = await getAutoCADSession(sess.id)
    setSelected(full)
    setAvatarResult(null)
    reloadEvents(sess.id)
    setTab('commands')
  }

  async function handleRegenerate() {
    if (!selected) return
    await regenerateAutoCADNarration(selected.id)
    setSelected(s => ({ ...s, status: 'processing' }))
  }

  async function handleSubmitAvatar() {
    if (!selected) return
    try {
      const r = await submitAutoCADAvatar(selected.id, { provider, avatarId, voiceId, apiKey })
      setAvatarResult(r)
    } catch (e) {
      setAvatarResult({ error: e?.response?.data?.detail || String(e) })
    }
  }

  async function handlePollAvatar() {
    if (!selected) return
    try {
      const r = await pollAutoCADAvatar(selected.id, { provider, apiKey })
      setAvatarResult(r)
      if (r.video_url) setSelected(s => ({ ...s, avatar_video_url: r.video_url }))
    } catch (e) {
      setAvatarResult({ error: String(e) })
    }
  }

  // ── derived ───────────────────────────────────────────────────────────────────

  const cmdEvents = events.filter(e =>
    e.event_type === 'uia_invoke' && e.uia_element_type?.includes('acad_cmd')
    && e.uia_automation_id?.startsWith('BeginCommand')
  )

  // Category stats
  const catStats = {}
  for (const e of cmdEvents) {
    const cat = getCatFromType(e.uia_element_type) || 'other'
    catStats[cat] = (catStats[cat] || 0) + 1
  }

  const isRunning = agentStatus?.running

  if (loading) return <div style={s.loading}>…</div>

  return (
    <div style={s.page}>

      {/* ── Header ── */}
      <div style={s.header}>
        <div>
          <h2 style={s.title}>{t.acad_title}</h2>
          <div style={s.sub}>{t.acad_subtitle}</div>
        </div>
        {isRunning && (
          <div style={s.recBadge}>
            <span style={s.recDot} />
            {t.acad_recording} &nbsp;
            <strong style={{ color: '#7aa2f7' }}>acad.exe</strong>
            &nbsp;· {agentStatus.events_captured} {t.acad_events}
          </div>
        )}
      </div>

      <div style={s.layout}>

        {/* ── Left column ── */}
        <div style={s.leftCol}>

          {/* Control panel */}
          <div style={s.card}>
            <div style={s.cardTitle}>{t.acad_control}</div>
            {!isRunning ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <label style={s.label}>{t.acad_title_label}
                  <input style={s.input} value={title}
                    onChange={e => setTitle(e.target.value)}
                    placeholder={t.acad_title_ph} />
                </label>
                <div style={{ display: 'flex', gap: 16 }}>
                  <label style={s.checkLabel}>
                    <input type="checkbox" checked={enableCom}
                      onChange={e => setCom(e.target.checked)} />
                    COM API
                  </label>
                  <label style={s.checkLabel}>
                    <input type="checkbox" checked={enableVoice}
                      onChange={e => setVoice(e.target.checked)} />
                    {t.acad_voice}
                  </label>
                </div>
                <div style={s.hintBox}>{t.acad_prereq}</div>
                <button style={s.btnStart} onClick={handleStart}>{t.acad_btn_start}</button>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={s.statRow}>
                  <span style={s.statLabel}>Session</span>
                  <span style={s.statVal}>#{agentStatus.session_id}</span>
                </div>
                <div style={s.statRow}>
                  <span style={s.statLabel}>{t.acad_events}</span>
                  <span style={s.statVal}>{agentStatus.events_captured}</span>
                </div>
                <div style={s.statRow}>
                  <span style={s.statLabel}>{t.acad_voice_segs}</span>
                  <span style={s.statVal}>{agentStatus.voice_segments}</span>
                </div>
                <button style={s.btnStop} onClick={handleStop}>{t.acad_btn_stop}</button>
              </div>
            )}
          </div>

          {/* Sessions list */}
          <div style={s.card}>
            <div style={s.cardTitle}>{t.acad_sessions}</div>
            {sessions.length === 0
              ? <div style={s.empty}>{t.acad_no_sessions}</div>
              : sessions.map(sess => (
                  <div key={sess.id}
                    style={{ ...s.sessRow, ...(selected?.id === sess.id ? s.sessActive : {}) }}
                    onClick={() => handleSelectSession(sess)}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={s.sessName}>{sess.title || `Session #${sess.id}`}</div>
                      <div style={s.sessMeta}>{fmt(sess.started_at)} · {sess.event_count ?? 0} events</div>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
                      <StatusBadge status={sess.status} />
                      <button style={s.btnDel}
                        onClick={e => { e.stopPropagation(); deleteAutoCADSession(sess.id).then(reload) }}>✕</button>
                    </div>
                  </div>
                ))
            }
          </div>
        </div>

        {/* ── Right column ── */}
        <div style={s.rightCol}>
          {!selected ? (
            <div style={{ ...s.card, display: 'flex', alignItems: 'center',
                          justifyContent: 'center', minHeight: 300 }}>
              <div style={s.empty}>{t.acad_select_hint}</div>
            </div>
          ) : (
            <>
              {/* Session info + tabs */}
              <div style={s.card}>
                <div style={{ display: 'flex', justifyContent: 'space-between',
                               alignItems: 'center', marginBottom: 12 }}>
                  <div style={s.cardTitle}>{selected.title || `Session #${selected.id}`}</div>
                  <StatusBadge status={selected.status} />
                </div>

                {/* Category summary pills */}
                {Object.keys(catStats).length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 14 }}>
                    {Object.entries(catStats)
                      .sort((a, b) => b[1] - a[1])
                      .map(([cat, cnt]) => (
                        <span key={cat} style={{
                          fontSize: 11, padding: '2px 8px', borderRadius: 20,
                          background: (CAT_COLORS[cat] || '#565f89') + '22',
                          color: CAT_COLORS[cat] || '#565f89',
                          border: `1px solid ${(CAT_COLORS[cat] || '#565f89')}44`,
                        }}>
                          {CAT_LABELS_ZH[cat] || cat} ×{cnt}
                        </span>
                      ))
                    }
                  </div>
                )}

                {/* Tab bar */}
                <div style={s.tabBar}>
                  {[['commands', t.acad_tab_commands],
                    ['narration', t.acad_tab_narration],
                    ['avatar', t.acad_tab_avatar],
                  ].map(([id, label]) => (
                    <button key={id} style={{ ...s.tab, ...(tab === id ? s.tabActive : {}) }}
                      onClick={() => setTab(id)}>
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {/* ── Commands tab ── */}
              {tab === 'commands' && (
                <div style={s.card}>
                  <div style={s.cardTitle}>
                    {t.acad_tab_commands} ({cmdEvents.length})
                  </div>
                  {cmdEvents.length === 0
                    ? <div style={s.empty}>{t.acad_no_commands}</div>
                    : <div style={s.cmdLog}>
                        {cmdEvents.map(e => {
                          const cat = getCatFromType(e.uia_element_type)
                          return (
                            <div key={e.id} style={s.cmdRow}>
                              <span style={s.cmdTime}>{fmtTime(e.timestamp)}</span>
                              <CommandPill name={e.uia_element_name} category={cat} />
                              <span style={{ ...s.cmdCat, color: CAT_COLORS[cat] || '#565f89' }}>
                                {CAT_LABELS_ZH[cat] || cat}
                              </span>
                            </div>
                          )
                        })}
                      </div>
                  }
                </div>
              )}

              {/* ── Narration tab ── */}
              {tab === 'narration' && (
                <div style={s.card}>
                  <div style={{ display: 'flex', justifyContent: 'space-between',
                                 alignItems: 'center', marginBottom: 12 }}>
                    <div style={s.cardTitle}>{t.acad_narration}</div>
                    <button style={s.btnSecondary} onClick={handleRegenerate}
                      disabled={selected.status === 'processing'}>
                      {selected.status === 'processing' ? t.acad_generating : t.acad_regenerate}
                    </button>
                  </div>
                  {selected.narration_text
                    ? <pre style={s.narration}>{selected.narration_text}</pre>
                    : <div style={s.empty}>
                        {selected.status === 'processing' ? t.acad_generating
                          : selected.status === 'recording' ? t.acad_still_recording
                          : t.acad_no_narration}
                      </div>
                  }
                </div>
              )}

              {/* ── Avatar tab ── */}
              {tab === 'avatar' && (
                <div style={s.card}>
                  <div style={s.cardTitle}>{t.acad_avatar}</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    <label style={s.label}>{t.acad_provider}
                      <select style={s.input} value={provider}
                        onChange={e => setProvider(e.target.value)}>
                        <option value="heygen">HeyGen</option>
                        <option value="did">D-ID</option>
                      </select>
                    </label>
                    <label style={s.label}>Avatar ID
                      <input style={s.input} value={avatarId}
                        onChange={e => setAvatarId(e.target.value)}
                        placeholder={provider === 'heygen' ? 'HeyGen avatar_id' : 'presenter image URL'} />
                    </label>
                    <label style={s.label}>Voice ID
                      <input style={s.input} value={voiceId}
                        onChange={e => setVoiceId(e.target.value)}
                        placeholder={provider === 'heygen' ? 'HeyGen voice_id' : 'en-US-JennyNeural'} />
                    </label>
                    <label style={s.label}>API Key
                      <input style={{ ...s.input, fontFamily: 'monospace' }}
                        type="password" value={apiKey}
                        onChange={e => setApiKey(e.target.value)}
                        placeholder={`${provider === 'heygen' ? 'HEYGEN' : 'DID'}_API_KEY`} />
                    </label>
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button style={s.btnPrimary} onClick={handleSubmitAvatar}
                        disabled={!selected.narration_text}>
                        {t.acad_submit_avatar}
                      </button>
                      {(selected.avatar_job_id || avatarResult?.job_id) && (
                        <button style={s.btnSecondary} onClick={handlePollAvatar}>
                          {t.acad_poll}
                        </button>
                      )}
                    </div>

                    {avatarResult && (
                      <div style={avatarResult.error ? s.errorBox : s.infoBox}>
                        {avatarResult.error
                          ? avatarResult.error
                          : <>
                              <strong>{avatarResult.status}</strong>
                              {avatarResult.video_url && (
                                <div style={{ marginTop: 4 }}>
                                  <a href={avatarResult.video_url} target="_blank"
                                     rel="noreferrer" style={{ color: 'var(--accent)' }}>
                                    {t.acad_video_link}
                                  </a>
                                </div>
                              )}
                            </>
                        }
                      </div>
                    )}

                    {selected.avatar_video_url && (
                      <div style={s.infoBox}>
                        {t.acad_video_ready}:{' '}
                        <a href={selected.avatar_video_url} target="_blank"
                           rel="noreferrer" style={{ color: 'var(--accent)' }}>
                          {t.acad_video_link}
                        </a>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── styles ────────────────────────────────────────────────────────────────────

const s = {
  page:     { height: '100%', overflowY: 'auto', paddingBottom: 24 },
  loading:  { display: 'flex', alignItems: 'center', justifyContent: 'center',
              height: '100%', color: 'var(--text-s)' },
  header:   { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 },
  title:    { margin: 0, fontSize: 22, fontWeight: 800, color: 'var(--text)' },
  sub:      { fontSize: 13, color: 'var(--text-s)', marginTop: 4 },

  recBadge: { display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12,
              background: '#1a1e2e', border: '1px solid #7aa2f744', color: '#a9b1d6',
              padding: '6px 12px', borderRadius: 20 },
  recDot:   { width: 7, height: 7, borderRadius: '50%', background: '#f7768e',
              boxShadow: '0 0 6px #f7768e' },

  layout:   { display: 'flex', gap: 20, alignItems: 'flex-start' },
  leftCol:  { width: 260, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 16 },
  rightCol: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 16 },

  card:     { background: 'var(--surface)', border: '1px solid var(--border)',
              borderRadius: 12, padding: '18px 20px' },
  cardTitle:{ fontSize: 12, fontWeight: 700, color: 'var(--text-m)', marginBottom: 14,
              textTransform: 'uppercase', letterSpacing: 0.6 },

  label:    { display: 'flex', flexDirection: 'column', gap: 4,
              fontSize: 12, color: 'var(--text-s)' },
  input:    { background: 'var(--surface2, #1e2030)', border: '1px solid var(--border)',
              borderRadius: 7, padding: '7px 10px', color: 'var(--text)',
              fontSize: 13, outline: 'none', width: '100%', boxSizing: 'border-box' },
  checkLabel: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 13,
                color: 'var(--text-m)', cursor: 'pointer' },

  btnStart: { padding: '9px 0', background: '#7aa2f7', border: 'none', borderRadius: 8,
              color: '#1a1b2e', fontWeight: 700, fontSize: 14, cursor: 'pointer', width: '100%' },
  btnStop:  { padding: '9px 0', background: '#f7768e', border: 'none', borderRadius: 8,
              color: '#1a1b2e', fontWeight: 700, fontSize: 14, cursor: 'pointer', width: '100%' },
  btnPrimary: { padding: '8px 16px', background: '#7aa2f7', border: 'none', borderRadius: 7,
                color: '#1a1b2e', fontWeight: 700, fontSize: 13, cursor: 'pointer' },
  btnSecondary: { padding: '8px 14px', background: 'var(--surface2, #1e2030)',
                  border: '1px solid var(--border)', borderRadius: 7,
                  color: 'var(--text-m)', fontSize: 13, cursor: 'pointer' },
  btnDel:   { background: 'transparent', border: 'none', color: 'var(--text-s)',
              cursor: 'pointer', fontSize: 12, padding: '1px 4px' },

  statRow:  { display: 'flex', justifyContent: 'space-between', fontSize: 13 },
  statLabel:{ color: 'var(--text-s)' },
  statVal:  { color: '#7aa2f7', fontWeight: 700 },

  sessRow:  { display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 8px',
              borderRadius: 8, cursor: 'pointer', marginBottom: 2 },
  sessActive:{ background: 'rgba(122,162,247,0.10)' },
  sessName: { fontSize: 13, color: 'var(--text)', fontWeight: 600,
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  sessMeta: { fontSize: 11, color: 'var(--text-s)', marginTop: 3 },
  empty:    { color: 'var(--text-s)', fontSize: 13, textAlign: 'center', padding: '20px 0' },

  tabBar:   { display: 'flex', gap: 4, borderBottom: '1px solid var(--border)',
              paddingBottom: 12, marginTop: 4 },
  tab:      { padding: '5px 14px', background: 'transparent', border: 'none', borderRadius: 6,
              color: 'var(--text-s)', fontSize: 13, cursor: 'pointer' },
  tabActive:{ background: 'rgba(122,162,247,0.15)', color: '#7aa2f7', fontWeight: 700 },

  cmdLog:   { maxHeight: 500, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 4 },
  cmdRow:   { display: 'flex', alignItems: 'center', gap: 10, padding: '4px 0',
              borderBottom: '1px solid var(--border)' },
  cmdTime:  { fontSize: 11, color: 'var(--text-s)', fontVariantNumeric: 'tabular-nums',
              flexShrink: 0, width: 70 },
  cmdCat:   { fontSize: 11, marginLeft: 'auto', flexShrink: 0 },

  narration:{ fontSize: 13, color: 'var(--text)', lineHeight: 1.7, whiteSpace: 'pre-wrap',
              margin: 0, fontFamily: 'inherit', maxHeight: 420, overflowY: 'auto',
              background: 'var(--surface2, #1e2030)', borderRadius: 8, padding: 14 },

  hintBox:  { fontSize: 12, color: '#e0af68', background: '#2d2510',
              border: '1px solid #e0af6844', borderRadius: 7,
              padding: '7px 10px', lineHeight: 1.5 },
  errorBox: { background: '#2d1a20', border: '1px solid #f7768e', borderRadius: 8,
              padding: '10px 14px', fontSize: 12, color: '#f7768e' },
  infoBox:  { background: '#1a2d1a', border: '1px solid #9ece6a', borderRadius: 8,
              padding: '10px 14px', fontSize: 12, color: '#9ece6a' },
}