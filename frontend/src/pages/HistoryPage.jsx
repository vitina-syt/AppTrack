import { useState, useEffect } from 'react'
import { useT } from '../hooks/useT'
import { useSettingsStore } from '../store/settingsStore'
import { getActiveDays, getStatsDate, getSessions } from '../api'

function fmt(seconds) {
  if (!seconds || seconds < 1) return '0s'
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return `${h}h ${m}m`
}

function fmtTime(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export default function HistoryPage() {
  const t = useT()
  const ignoredApps = useSettingsStore((s) => s.ignoredApps)

  const [days, setDays]         = useState([])
  const [selectedDate, setDate] = useState('')
  const [stats, setStats]       = useState([])
  const [sessions, setSessions] = useState([])
  const [loading, setLoading]   = useState(true)

  // Load available days
  useEffect(() => {
    getActiveDays().then(d => {
      setDays(d)
      if (d.length > 0 && !selectedDate) setDate(d[0].day)
    }).finally(() => setLoading(false))
  }, [])

  // Load stats + sessions when date changes
  useEffect(() => {
    if (!selectedDate) return
    Promise.all([getStatsDate(selectedDate), getSessions(selectedDate)]).then(([st, se]) => {
      setStats(st.filter(a => !ignoredApps.includes(a.app_name)))
      setSessions(se.filter(s => !ignoredApps.includes(s.app_name)))
    })
  }, [selectedDate, ignoredApps])

  if (loading) return <div style={s.loading}>…</div>

  const totalSec = stats.reduce((sum, a) => sum + a.total_seconds, 0)

  return (
    <div style={s.page}>
      <h2 style={s.title}>{t.hist_title}</h2>

      <div style={s.body}>
        {/* date list */}
        <div style={s.dayList}>
          {days.map(d => (
            <button key={d.day}
              onClick={() => setDate(d.day)}
              style={{ ...s.dayBtn, ...(selectedDate === d.day ? s.dayBtnActive : {}) }}>
              <span style={s.dayDate}>{d.day}</span>
              <span style={s.dayTime}>{fmt(d.total_seconds)}</span>
            </button>
          ))}
          {days.length === 0 && <div style={s.empty}>{t.hist_no_data}</div>}
        </div>

        {/* detail panel */}
        <div style={s.detail}>
          {selectedDate && (
            <>
              {/* app summary */}
              <div style={s.section}>
                <div style={s.sectionTitle}>{selectedDate} — {fmt(totalSec)}</div>
                {stats.map((a, i) => {
                  const pct = totalSec > 0 ? (a.total_seconds / totalSec) * 100 : 0
                  return (
                    <div key={a.app_name} style={s.appRow}>
                      <span style={s.appName}>{a.app_name}</span>
                      <div style={s.appTrack}>
                        <div style={{ ...s.appFill, width: `${pct}%` }} />
                      </div>
                      <span style={s.appTime}>{fmt(a.total_seconds)}</span>
                    </div>
                  )
                })}
              </div>

              {/* session list */}
              <div style={{ ...s.section, marginTop: 16 }}>
                <div style={s.sectionTitle}>{t.hist_sessions} ({sessions.length})</div>
                <div style={s.sessionTable}>
                  <div style={s.thead}>
                    <span style={s.col1}>{t.hist_start}</span>
                    <span style={s.col2}>{t.hist_end}</span>
                    <span style={s.col3}>App</span>
                    <span style={s.col4}>{t.hist_duration}</span>
                  </div>
                  {sessions.map(se => (
                    <div key={se.id} style={s.trow}>
                      <span style={s.col1}>{fmtTime(se.started_at)}</span>
                      <span style={s.col2}>{fmtTime(se.ended_at)}</span>
                      <span style={{ ...s.col3, color: 'var(--text)' }}>{se.app_name}</span>
                      <span style={s.col4}>{fmt(se.duration_seconds)}</span>
                    </div>
                  ))}
                  {sessions.length === 0 && <div style={s.empty}>{t.hist_no_data}</div>}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

const s = {
  page:        { height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' },
  loading:     { display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-s)' },
  title:       { margin: '0 0 20px', fontSize: 22, fontWeight: 800, color: 'var(--text)' },
  body:        { flex: 1, display: 'flex', gap: 16, minHeight: 0 },
  dayList:     { width: 180, flexShrink: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 4 },
  dayBtn:      { padding: '10px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center', color: 'var(--text-m)', fontSize: 13 },
  dayBtnActive:{ background: 'var(--accent-bg)', borderColor: 'var(--accent)', color: 'var(--accent)' },
  dayDate:     { fontWeight: 600 },
  dayTime:     { fontSize: 12, opacity: 0.7 },
  detail:      { flex: 1, minWidth: 0, overflowY: 'auto' },
  section:     { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: '18px 22px' },
  sectionTitle:{ fontSize: 13, fontWeight: 700, color: 'var(--text-m)', marginBottom: 14, textTransform: 'uppercase', letterSpacing: 0.4 },
  appRow:      { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 },
  appName:     { width: 140, flexShrink: 0, fontSize: 13, color: 'var(--text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  appTrack:    { flex: 1, height: 6, background: 'var(--surface2)', borderRadius: 3, overflow: 'hidden' },
  appFill:     { height: '100%', background: 'var(--accent)', borderRadius: 3, transition: 'width 0.4s' },
  appTime:     { width: 60, textAlign: 'right', fontSize: 13, color: 'var(--text-m)', fontVariantNumeric: 'tabular-nums' },
  sessionTable:{ display: 'flex', flexDirection: 'column', gap: 0 },
  thead:       { display: 'flex', gap: 8, padding: '4px 0 8px', borderBottom: '1px solid var(--border)', marginBottom: 4, fontSize: 11, color: 'var(--text-s)', textTransform: 'uppercase', letterSpacing: 0.4 },
  trow:        { display: 'flex', gap: 8, padding: '6px 0', borderBottom: '1px solid var(--border)', fontSize: 13, color: 'var(--text-m)' },
  col1:        { width: 80, flexShrink: 0, fontVariantNumeric: 'tabular-nums' },
  col2:        { width: 80, flexShrink: 0, fontVariantNumeric: 'tabular-nums' },
  col3:        { flex: 1, minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  col4:        { width: 70, textAlign: 'right', flexShrink: 0 },
  empty:       { color: 'var(--text-s)', fontSize: 14, padding: '16px 0' },
}
