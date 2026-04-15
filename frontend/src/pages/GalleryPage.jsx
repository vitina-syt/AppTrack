/**
 * GalleryPage — 视频广场
 *
 * 汇总所有录制会话，展示应用标签、截图数、解说词、视频状态，
 * 支持跳转帧编辑器、下载视频、删除会话。
 */
import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Table, Tag, ConfigProvider, theme as antTheme, Tooltip, Popconfirm } from 'antd'
import { getGallery, deleteGalleryItem } from '../api'
import { useT } from '../hooks/useT'

// ── Ant Design dark theme ─────────────────────────────────────────────────────

const ANT_THEME = {
  algorithm: antTheme.darkAlgorithm,
  token: {
    colorPrimary:        '#7aa2f7',
    colorBgContainer:    '#1a1b2e',
    colorBgElevated:     '#1e2030',
    colorText:           '#cdd6f4',
    colorTextSecondary:  '#a9b1d6',
    colorBorder:         '#2a2d3e',
    colorBorderSecondary:'#2a2d3e',
    fontSize:            13,
    borderRadius:        8,
  },
  components: {
    Table: {
      headerBg:           '#1e2030',
      headerColor:        '#a9b1d6',
      rowHoverBg:         '#1e2035',
      borderColor:        '#2a2d3e',
      colorBgContainer:   '#1a1b2e',
    },
    Pagination: { colorText: '#a9b1d6' },
  },
}

// ── App tag config ─────────────────────────────────────────────────────────────

const APP_TAG = {
  'acad.exe':   { label: 'AutoCAD',  color: '#7aa2f7' },
  'autocad':    { label: 'AutoCAD',  color: '#7aa2f7' },
  'xtop.exe':   { label: 'Creo',     color: '#bb9af7' },
  'creo':       { label: 'Creo',     color: '#bb9af7' },
  'solidworks': { label: 'SOLIDWORKS', color: '#73daca' },
  'catia':      { label: 'CATIA',    color: '#e0af68' },
}

function appTag(target_app, genericLabel) {
  if (!target_app) return { label: genericLabel || target_app || '—', color: '#565f89' }
  const key = target_app.toLowerCase()
  for (const [k, v] of Object.entries(APP_TAG)) {
    if (key.includes(k)) return v
  }
  // Fallback: strip .exe, capitalize
  const label = target_app.replace(/\.exe$/i, '')
  return { label, color: '#a9b1d6' }
}

// ── Status badge ───────────────────────────────────────────────────────────────

const STATUS_COLOR = {
  recording:  '#f7768e',
  processing: '#e0af68',
  done:       '#9ece6a',
  error:      '#565f89',
}

/** Fuzzy match: every char in needle must appear in haystack in order. */
function fuzzyMatch(needle, haystack) {
  const n = needle.toLowerCase()
  const h = haystack.toLowerCase()
  let ni = 0
  for (let i = 0; i < h.length && ni < n.length; i++) {
    if (h[i] === n[ni]) ni++
  }
  return ni === n.length
}

function fmt(ts) {
  if (!ts) return '—'
  return new Date(ts).toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

// ── GalleryPage ────────────────────────────────────────────────────────────────

export default function GalleryPage() {
  const navigate = useNavigate()
  const t = useT()
  const [data,    setData]    = useState([])
  const [loading, setLoading] = useState(true)
  const [search,  setSearch]  = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const list = await getGallery()
      setData(list)
    } catch (_) {}
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  async function handleDelete(id) {
    try {
      await deleteGalleryItem(id)
      setData(prev => prev.filter(r => r.id !== id))
    } catch (e) {
      alert(e?.response?.data?.detail || String(e))
    }
  }

  // ── Filter ────────────────────────────────────────────────────────────────

  const filtered = search.trim()
    ? data.filter(r => {
        const q = search.trim()
        const { label } = appTag(r.target_app, t.gal_app_generic)
        return (
          fuzzyMatch(q, r.title || '') ||
          fuzzyMatch(q, r.target_app || '') ||
          fuzzyMatch(q, label)
        )
      })
    : data

  // ── Status labels (i18n) ──────────────────────────────────────────────────

  const statusLabels = {
    recording:  t.gal_status_recording,
    processing: t.gal_status_processing,
    done:       t.gal_status_done,
    error:      t.gal_status_error,
  }

  // ── Columns ───────────────────────────────────────────────────────────────

  const columns = [
    {
      title: t.gal_col_title,
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
      width: 200,
      render: (v, r) => (
        <span style={{ fontWeight: 600, color: 'var(--text)' }}>
          {v || `${t.gal_session_prefix}${r.id}`}
        </span>
      ),
    },
    {
      title: t.gal_col_app,
      dataIndex: 'target_app',
      key: 'target_app',
      width: 130,
      filters: [...new Map(data.map(r => {
        const tg = appTag(r.target_app, t.gal_app_generic)
        return [tg.label, { text: tg.label, value: r.target_app }]
      })).values()],
      onFilter: (value, record) => record.target_app === value,
      render: (v) => {
        const { label, color } = appTag(v, t.gal_app_generic)
        return (
          <Tag style={{
            background: color + '22', color, borderColor: color + '66',
            fontWeight: 700, fontSize: 11, borderRadius: 6,
          }}>
            {label}
          </Tag>
        )
      },
    },
    {
      title: t.gal_col_status,
      dataIndex: 'status',
      key: 'status',
      width: 95,
      filters: [
        { text: t.gal_status_done,       value: 'done' },
        { text: t.gal_status_recording,  value: 'recording' },
        { text: t.gal_status_processing, value: 'processing' },
        { text: t.gal_status_error,      value: 'error' },
      ],
      onFilter: (value, record) => record.status === value,
      render: (v) => {
        const c = STATUS_COLOR[v] || '#565f89'
        return (
          <span style={{
            fontSize: 11, fontWeight: 700, padding: '2px 8px',
            borderRadius: 10, background: c + '22', color: c,
          }}>
            {statusLabels[v] || v}
          </span>
        )
      },
    },
    {
      title: t.gal_col_screenshots,
      dataIndex: 'screenshot_count',
      key: 'screenshot_count',
      width: 72,
      align: 'center',
      sorter: (a, b) => a.screenshot_count - b.screenshot_count,
      render: v => <span style={{ color: '#7aa2f7', fontWeight: 600 }}>{v}</span>,
    },
    {
      title: t.gal_col_frames,
      dataIndex: 'frame_count',
      key: 'frame_count',
      width: 72,
      align: 'center',
      sorter: (a, b) => a.frame_count - b.frame_count,
      render: v => v > 0
        ? <span style={{ color: '#bb9af7', fontWeight: 600 }}>{v}</span>
        : <span style={{ color: '#565f89' }}>0</span>,
    },
    {
      title: t.gal_col_narration,
      dataIndex: 'narration_text',
      key: 'narration',
      width: 88,
      align: 'center',
      render: v => v
        ? <Tooltip title={v.slice(0, 200) + (v.length > 200 ? '…' : '')}>
            <span style={{ color: '#9ece6a', cursor: 'default' }}>{t.gal_narr_done}</span>
          </Tooltip>
        : <span style={{ color: '#565f89' }}>—</span>,
    },
    {
      title: t.gal_col_video,
      key: 'video',
      width: 120,
      render: (_, r) => {
        if (!r.has_video) return <span style={{ color: '#565f89' }}>—</span>
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Tag style={{
              background: '#9ece6a22', color: '#9ece6a',
              borderColor: '#9ece6a44', fontSize: 11, borderRadius: 6,
            }}>
              {r.video_type}
            </Tag>
            <span style={{ fontSize: 11, color: '#565f89' }}>
              {r.video_size_mb != null ? `${r.video_size_mb} MB` : ''}
            </span>
          </div>
        )
      },
    },
    {
      title: t.gal_col_time,
      dataIndex: 'started_at',
      key: 'started_at',
      width: 130,
      sorter: (a, b) => a.started_at.localeCompare(b.started_at),
      defaultSortOrder: 'descend',
      render: v => <span style={{ color: 'var(--text-s)', fontSize: 12 }}>{fmt(v)}</span>,
    },
    {
      title: t.gal_col_actions,
      key: 'actions',
      width: 160,
      fixed: 'right',
      render: (_, r) => (
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          {/* Edit frames */}
          <button style={sa.btnEdit}
            onClick={() => navigate(`/gallery/editor/${r.id}`,
              { state: { backTo: '/gallery' } })}>
            {t.gal_edit}
          </button>

          {/* Download video */}
          {r.has_video && (
            <a href={`/api/autocad/sessions/${r.id}/video/download`}
              style={sa.btnDownload}>
              {t.gal_download}
            </a>
          )}

          {/* Delete */}
          <Popconfirm
            title={t.gal_delete_title}
            description={t.gal_delete_desc}
            okText={t.gal_delete_ok}
            cancelText={t.gal_delete_cancel}
            okButtonProps={{ danger: true }}
            onConfirm={() => handleDelete(r.id)}
          >
            <button style={sa.btnDel}>✕</button>
          </Popconfirm>
        </div>
      ),
    },
  ]

  // ── Render ────────────────────────────────────────────────────────────────

  const stats = {
    total:    data.length,
    withVideo: data.filter(r => r.has_video).length,
    withNarr:  data.filter(r => r.narration_text).length,
    screenshots: data.reduce((s, r) => s + r.screenshot_count, 0),
  }

  return (
    <div style={sa.page}>

      {/* Header */}
      <div style={sa.header}>
        <div>
          <h2 style={sa.title}>{t.gal_title}</h2>
          <div style={sa.sub}>{t.gal_sub}</div>
        </div>
        <button style={sa.btnRefresh} onClick={load} disabled={loading}>
          {loading ? '…' : t.gal_refresh}
        </button>
      </div>

      {/* Stats bar */}
      <div style={sa.statsBar}>
        {[
          [t.gal_stat_sessions,    stats.total,        '#7aa2f7'],
          [t.gal_stat_with_video,  stats.withVideo,    '#9ece6a'],
          [t.gal_stat_with_narr,   stats.withNarr,     '#bb9af7'],
          [t.gal_stat_screenshots, stats.screenshots,  '#73daca'],
        ].map(([label, val, color]) => (
          <div key={label} style={sa.statCard}>
            <span style={{ ...sa.statVal, color }}>{val}</span>
            <span style={sa.statLabel}>{label}</span>
          </div>
        ))}
      </div>

      {/* Search */}
      <div style={sa.searchRow}>
        <input
          style={sa.searchInput}
          placeholder={t.gal_search_ph}
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
        {search && (
          <button style={sa.btnClear} onClick={() => setSearch('')}>✕</button>
        )}
      </div>

      {/* Table */}
      <ConfigProvider theme={ANT_THEME}>
        <Table
          rowKey="id"
          dataSource={filtered}
          columns={columns}
          loading={loading}
          size="middle"
          scroll={{ x: 1100 }}
          pagination={{
            pageSize: 20,
            showSizeChanger: true,
            pageSizeOptions: ['10', '20', '50'],
            showTotal: (total) => `${t.gal_total_pre}${total}${t.gal_total_suf}`,
          }}
          locale={{ emptyText: t.gal_empty }}
        />
      </ConfigProvider>
    </div>
  )
}

// ── Styles ────────────────────────────────────────────────────────────────────

const sa = {
  page:   { height: '100%', display: 'flex', flexDirection: 'column', gap: 16 },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' },
  title:  { margin: 0, fontSize: 22, fontWeight: 800, color: 'var(--text)' },
  sub:    { fontSize: 13, color: 'var(--text-s)', marginTop: 4 },

  statsBar: {
    display: 'flex', gap: 12, flexWrap: 'wrap',
  },
  statCard: {
    background: 'var(--surface)', border: '1px solid var(--border)',
    borderRadius: 10, padding: '12px 20px',
    display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 2,
    minWidth: 110,
  },
  statVal:   { fontSize: 22, fontWeight: 800, lineHeight: 1 },
  statLabel: { fontSize: 11, color: 'var(--text-s)' },

  searchRow: { display: 'flex', alignItems: 'center', gap: 6 },
  searchInput: {
    background: 'var(--surface)', border: '1px solid var(--border)',
    borderRadius: 8, padding: '7px 12px', color: 'var(--text)',
    fontSize: 13, outline: 'none', width: 260,
  },
  btnClear: {
    background: 'transparent', border: 'none',
    color: 'var(--text-s)', cursor: 'pointer', fontSize: 13,
  },

  btnRefresh: {
    padding: '7px 14px', background: 'var(--surface)',
    border: '1px solid var(--border)', borderRadius: 8,
    color: 'var(--text-m)', fontSize: 13, cursor: 'pointer',
  },
  btnEdit: {
    padding: '4px 10px', background: '#bb9af722',
    border: '1px solid #bb9af744', borderRadius: 6,
    color: '#bb9af7', fontSize: 12, cursor: 'pointer', fontWeight: 600,
  },
  btnDownload: {
    padding: '4px 10px', background: '#9ece6a22',
    border: '1px solid #9ece6a44', borderRadius: 6,
    color: '#9ece6a', fontSize: 12, cursor: 'pointer',
    textDecoration: 'none', display: 'inline-block', fontWeight: 600,
  },
  btnDel: {
    background: 'transparent', border: '1px solid #f7768e44',
    color: '#f7768e', borderRadius: 5, fontSize: 12,
    padding: '3px 7px', cursor: 'pointer',
  },
}
