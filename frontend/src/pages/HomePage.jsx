import { useState, useEffect, useCallback } from 'react'
import { useT } from '../hooks/useT'
import { useSettingsStore } from '../store/settingsStore'
import { getStatus, startTracker, stopTracker, getStatsToday } from '../api'

function fmt(seconds) {
  if (!seconds || seconds < 1) return '0s'
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return `${h}h ${m}m`
}

function MonitorIllustration() {
  return (
    <svg viewBox="0 0 380 260" fill="none" xmlns="http://www.w3.org/2000/svg"
      style={{ width: '100%', height: 'auto', opacity: 0.82 }} aria-hidden="true">
      <defs>
        <radialGradient id="mg1" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.2" />
          <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="mg2" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="var(--surface2)" />
          <stop offset="100%" stopColor="var(--surface)" />
        </linearGradient>
        <filter id="mb"><feGaussianBlur stdDeviation="5" /></filter>
      </defs>

      {/* glow */}
      <ellipse cx="190" cy="130" rx="160" ry="110" fill="url(#mg1)" filter="url(#mb)" />

      {/* monitor body */}
      <rect x="50" y="30" width="240" height="160" rx="10" fill="url(#mg2)" stroke="var(--border)" strokeWidth="1.5"/>
      {/* stand */}
      <rect x="155" y="190" width="30" height="12" rx="2" fill="var(--border)" opacity="0.6"/>
      <rect x="135" y="202" width="70" height="5" rx="2.5" fill="var(--border)" opacity="0.5"/>

      {/* screen content — app rows */}
      {[0,1,2,3,4].map((i) => (
        <g key={i} transform={`translate(70, ${50 + i * 28})`}>
          <rect width="8" height="8" rx="2" fill="var(--accent)" opacity={0.6 - i * 0.08} />
          <rect x="14" y="1" width={80 + i * 6} height="6" rx="3" fill="var(--border-s)" opacity={0.55 - i * 0.06}/>
          {/* usage bar */}
          <rect x="110" y="1" width={90 - i * 14} height="6" rx="3" fill="var(--accent)" opacity={0.35 - i * 0.04}/>
        </g>
      ))}

      {/* pulse ring — active indicator */}
      <circle cx="310" cy="60" r="16" stroke="var(--success)" strokeWidth="1.5" fill="var(--success)" fillOpacity="0.08">
        <animate attributeName="r" values="16;22;16" dur="2s" repeatCount="indefinite"/>
        <animate attributeName="strokeOpacity" values="1;0.2;1" dur="2s" repeatCount="indefinite"/>
      </circle>
      <circle cx="310" cy="60" r="6" fill="var(--success)" opacity="0.9"/>

      {/* floating stat card */}
      <rect x="290" y="120" width="80" height="50" rx="8" fill="var(--surface)" stroke="var(--border)" strokeWidth="1"/>
      <rect x="300" y="132" width="36" height="4" rx="2" fill="var(--accent)" opacity="0.7"/>
      <rect x="300" y="142" width="55" height="4" rx="2" fill="var(--border-s)" opacity="0.5"/>
      <rect x="300" y="152" width="45" height="4" rx="2" fill="var(--border-s)" opacity="0.4"/>

      {/* scan line */}
      <line x1="50" y1="30" x2="290" y2="30" stroke="var(--accent)" strokeWidth="0.7" strokeOpacity="0.5">
        <animateTransform attributeName="transform" type="translate" values="0,0;0,160;0,0" dur="4s" repeatCount="indefinite"/>
        <animate attributeName="strokeOpacity" values="0.5;0;0.5" dur="4s" repeatCount="indefinite"/>
      </line>
    </svg>
  )
}

export default function HomePage() {
  const t = useT()
  const pollInterval = useSettingsStore((s) => s.pollInterval)
  const ignoredApps  = useSettingsStore((s) => s.ignoredApps)

  const [status, setStatus]  = useState(null)
  const [stats, setStats]    = useState([])
  const [toggling, setToggling] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [st, daily] = await Promise.all([getStatus(), getStatsToday()])
      setStatus(st)
      setStats(daily.filter(a => !ignoredApps.includes(a.app_name)))
    } catch { /* backend not ready yet */ }
  }, [ignoredApps])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 3000)
    return () => clearInterval(id)
  }, [refresh])

  async function toggle() {
    setToggling(true)
    try {
      if (status?.running) await stopTracker()
      else await startTracker(pollInterval)
      await refresh()
    } finally {
      setToggling(false)
    }
  }

  const running    = status?.running ?? false
  const totalSec   = stats.reduce((s, a) => s + a.total_seconds, 0)
  const topApp     = stats[0]?.app_name ?? '—'

  return (
    <div style={s.page}>
      <div style={s.left}>
        <h1 style={s.title}>{t.home_title}</h1>
        <p style={s.subtitle}>{t.home_subtitle}</p>

        {/* status badge */}
        <div style={s.badge}>
          <span style={{ ...s.dot, background: running ? 'var(--success)' : 'var(--border-s)' }} />
          <span style={{ color: running ? 'var(--success)' : 'var(--text-s)', fontWeight: 600, fontSize: 14 }}>
            {running ? t.home_status_running : t.home_status_stopped}
          </span>
        </div>

        {/* current app */}
        {running && (
          <div style={s.currentCard}>
            <div style={s.cardLabel}>{t.home_current_app}</div>
            <div style={s.cardValue}>{status?.current_app ?? t.home_no_app}</div>
            {status?.current_title && <div style={s.cardSub}>{status.current_title}</div>}
          </div>
        )}

        {/* quick stats */}
        <div style={s.quickStats}>
          <div style={s.stat}>
            <div style={s.statVal}>{fmt(totalSec)}</div>
            <div style={s.statLabel}>{t.home_today_total}</div>
          </div>
          <div style={s.stat}>
            <div style={s.statVal}>{topApp}</div>
            <div style={s.statLabel}>{t.home_today_top}</div>
          </div>
        </div>

        <button onClick={toggle} disabled={toggling} style={{ ...s.mainBtn, ...(running ? s.stopBtn : s.startBtn) }}>
          {toggling ? '…' : (running ? t.home_btn_stop : t.home_btn_start)}
        </button>

        <p style={s.hint}>{t.home_hint}</p>
      </div>

      <div style={s.right}>
        <MonitorIllustration />
      </div>
    </div>
  )
}

const s = {
  page:      { height: '100%', display: 'flex', alignItems: 'center', gap: 32, overflow: 'hidden' },
  left:      { flex: '0 0 auto', maxWidth: 380 },
  right:     { flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', pointerEvents: 'none' },
  title:     { fontSize: 34, fontWeight: 800, color: 'var(--accent)', margin: 0, letterSpacing: -0.5 },
  subtitle:  { fontSize: 15, color: 'var(--text-m)', marginTop: 8, lineHeight: 1.6, maxWidth: 300 },
  badge:     { display: 'flex', alignItems: 'center', gap: 8, marginTop: 20 },
  dot:       { width: 10, height: 10, borderRadius: '50%', flexShrink: 0 },
  currentCard:{ marginTop: 16, padding: '12px 16px', background: 'var(--surface)', borderRadius: 10, border: '1px solid var(--border)', maxWidth: 320 },
  cardLabel: { fontSize: 11, color: 'var(--text-s)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.5 },
  cardValue: { fontSize: 15, fontWeight: 700, color: 'var(--text)' },
  cardSub:   { fontSize: 12, color: 'var(--text-m)', marginTop: 3, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 280 },
  quickStats:{ display: 'flex', gap: 24, marginTop: 20 },
  stat:      { display: 'flex', flexDirection: 'column', gap: 2 },
  statVal:   { fontSize: 20, fontWeight: 700, color: 'var(--text)' },
  statLabel: { fontSize: 12, color: 'var(--text-s)' },
  mainBtn:   { marginTop: 28, padding: '12px 28px', border: 'none', borderRadius: 8, fontWeight: 700, fontSize: 15, cursor: 'pointer', letterSpacing: 0.2, transition: 'opacity 0.15s' },
  startBtn:  { background: 'var(--accent)', color: 'var(--accent-fg)' },
  stopBtn:   { background: 'var(--surface2)', color: 'var(--danger)', border: '1px solid var(--danger)' },
  hint:      { marginTop: 20, fontSize: 13, color: 'var(--text-s)', maxWidth: 320, lineHeight: 1.6 },
}
