import { useState, useEffect } from 'react'
import { useT } from '../hooks/useT'
import { useSettingsStore } from '../store/settingsStore'
import { getStatsToday } from '../api'

function fmt(seconds) {
  if (!seconds || seconds < 1) return '0s'
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return m > 0 ? `${h}h ${m}m` : `${h}h`
}

function UsageBar({ app, seconds, maxSeconds, rank }) {
  const pct = maxSeconds > 0 ? (seconds / maxSeconds) * 100 : 0
  const colors = ['var(--accent)', '#bb9af7', '#9ece6a', '#e0af68', '#f7768e']
  const color = colors[rank % colors.length]

  return (
    <div style={s.barRow}>
      <div style={s.barRank}>{rank + 1}</div>
      <div style={s.barInfo}>
        <div style={s.barLabel}>{app}</div>
        <div style={s.barTrack}>
          <div style={{ ...s.barFill, width: `${pct}%`, background: color }} />
        </div>
      </div>
      <div style={s.barTime}>{fmt(seconds)}</div>
    </div>
  )
}

export default function DashboardPage() {
  const t = useT()
  const ignoredApps = useSettingsStore((s) => s.ignoredApps)
  const [stats, setStats] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    async function load() {
      try {
        const data = await getStatsToday()
        if (active) setStats(data.filter(a => !ignoredApps.includes(a.app_name)))
      } finally {
        if (active) setLoading(false)
      }
    }
    load()
    const id = setInterval(load, 10000)
    return () => { active = false; clearInterval(id) }
  }, [ignoredApps])

  const totalSec   = stats.reduce((sum, a) => sum + a.total_seconds, 0)
  const totalSess  = stats.reduce((sum, a) => sum + a.session_count, 0)
  const maxSec     = stats[0]?.total_seconds ?? 1
  const today      = new Date().toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })

  if (loading) return <div style={s.loading}>…</div>

  return (
    <div style={s.page}>
      <div style={s.header}>
        <div>
          <h2 style={s.title}>{t.dash_title}</h2>
          <div style={s.date}>{today}</div>
        </div>
      </div>

      {/* summary cards */}
      <div style={s.cards}>
        <StatCard label={t.dash_total}    value={fmt(totalSec)}       />
        <StatCard label={t.dash_apps}     value={stats.length}        />
        <StatCard label={t.dash_sessions} value={totalSess}           />
      </div>

      {/* usage bars */}
      <div style={s.section}>
        <div style={s.sectionTitle}>{t.dash_top_apps}</div>
        {stats.length === 0
          ? <div style={s.empty}>{t.dash_no_data}</div>
          : stats.slice(0, 15).map((a, i) => (
              <UsageBar key={a.app_name} app={a.app_name} seconds={a.total_seconds} maxSeconds={maxSec} rank={i} />
            ))
        }
      </div>
    </div>
  )
}

function StatCard({ label, value }) {
  return (
    <div style={s.card}>
      <div style={s.cardVal}>{value}</div>
      <div style={s.cardLabel}>{label}</div>
    </div>
  )
}

const s = {
  page:        { height: '100%', overflowY: 'auto', paddingBottom: 24 },
  loading:     { display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-s)' },
  header:      { display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 24 },
  title:       { margin: 0, fontSize: 22, fontWeight: 800, color: 'var(--text)' },
  date:        { fontSize: 13, color: 'var(--text-s)', marginTop: 4 },
  cards:       { display: 'flex', gap: 16, marginBottom: 28, flexWrap: 'wrap' },
  card:        { flex: '1 1 120px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: '16px 20px' },
  cardVal:     { fontSize: 26, fontWeight: 800, color: 'var(--accent)', letterSpacing: -0.5 },
  cardLabel:   { fontSize: 12, color: 'var(--text-s)', marginTop: 4 },
  section:     { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: '20px 24px' },
  sectionTitle:{ fontSize: 13, fontWeight: 700, color: 'var(--text-m)', marginBottom: 16, textTransform: 'uppercase', letterSpacing: 0.5 },
  empty:       { color: 'var(--text-s)', fontSize: 14, textAlign: 'center', padding: '32px 0' },
  barRow:      { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 },
  barRank:     { width: 20, textAlign: 'right', fontSize: 12, color: 'var(--text-s)', flexShrink: 0 },
  barInfo:     { flex: 1, minWidth: 0 },
  barLabel:    { fontSize: 13, color: 'var(--text)', marginBottom: 5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  barTrack:    { height: 6, background: 'var(--surface2)', borderRadius: 3, overflow: 'hidden' },
  barFill:     { height: '100%', borderRadius: 3, transition: 'width 0.4s ease' },
  barTime:     { width: 54, textAlign: 'right', fontSize: 13, color: 'var(--text-m)', flexShrink: 0, fontVariantNumeric: 'tabular-nums' },
}
