/**
 * AutoCAD Frame Editor
 *
 * Architecture mirrors AutoScribe:
 *  - Canvas-based rendering (letterbox layout, real Gaussian blur, click circles)
 *  - Shape format:
 *      blur_region : { id, type:'blur_region', shape:'rect', points:[x,y,w,h], intensity }
 *      click_circle: { id, type:'click_circle', points:[cx,cy,r], color, text, label_font_size_px }
 *  - Property panel with X/Y/radius sliders (same as AutoScribe PropertyPanel.jsx)
 *  - Filmstrip at bottom
 *  - Auto-save on frame switch
 */

import { useRef, useState, useCallback, useEffect } from 'react'
import { useNavigate, useParams, useLocation } from 'react-router-dom'
import {
  listFrames, updateFrame, deleteFrame, distributeNarration,
  generateNarratedVideo, getNarratedVideoStatus,
  getAutoCADSession, regenerateAutoCADNarration,
  localUrl,
} from '../api'
import { useT } from '../hooks/useT'

// ─────────────────────────────────────────────────────────────────────────────
// Coordinate helpers  (adapted from AutoScribe Viewer.jsx)
// ─────────────────────────────────────────────────────────────────────────────

/** Canvas-normalised (0-1) → image-relative (0-1), accounting for letterbox. */
function canvasToImage(rx, ry, L) {
  if (!L?.drawW || !L?.drawH) return { x: rx, y: ry }
  return {
    x: Math.min(1, Math.max(0, (rx * L.cw - L.offsetX) / L.drawW)),
    y: Math.min(1, Math.max(0, (ry * L.ch - L.offsetY) / L.drawH)),
  }
}

/** Image-relative rect → canvas pixel rect. */
function imgRectToPx(nx, ny, nw, nh, L) {
  return { x: L.offsetX + nx * L.drawW, y: L.offsetY + ny * L.drawH, w: nw * L.drawW, h: nh * L.drawH }
}

// ─────────────────────────────────────────────────────────────────────────────
// Canvas draw helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Draw a Gaussian-blurred rect on the canvas.
 * Uses an offscreen canvas + ctx.filter = blur() — identical strategy to AutoScribe.
 */
function drawBlurRegion(ctx, imgEl, shape, L, imgW, imgH, { showBorder = false } = {}) {
  const pts = shape.points
  if (!pts || pts.length < 4) return
  const [nx, ny, nw, nh] = pts
  const { x: dx, y: dy, w: dw, h: dh } = imgRectToPx(nx, ny, nw, nh, L)
  if (dw < 1 || dh < 1) return

  const intensity = typeof shape.intensity === 'number' && shape.intensity > 0 ? shape.intensity : 10
  const blurPx = Math.max(0.5, intensity * L.scale)

  // Offscreen canvas: sample the original image pixels at source resolution
  const off = document.createElement('canvas')
  off.width  = Math.max(1, Math.ceil(dw))
  off.height = Math.max(1, Math.ceil(dh))
  const oc = off.getContext('2d')
  if (!oc) return
  oc.drawImage(imgEl, nx * imgW, ny * imgH, nw * imgW, nh * imgH, 0, 0, dw, dh)

  ctx.save()
  ctx.beginPath()
  ctx.rect(dx, dy, dw, dh)
  ctx.clip()
  ctx.filter = `blur(${blurPx}px)`
  ctx.drawImage(off, 0, 0, dw, dh, dx, dy, dw, dh)
  ctx.restore()
  ctx.filter = 'none'

  if (showBorder) {
    ctx.strokeStyle = 'rgba(115,218,202,0.85)'
    ctx.lineWidth = 2
    ctx.setLineDash([6, 4])
    ctx.strokeRect(dx + 0.5, dy + 0.5, Math.max(0, dw - 1), Math.max(0, dh - 1))
    ctx.setLineDash([])
  }
}

/** Draw a click-circle annotation (circle + label box). */
function drawClickCircle(ctx, ann, L, selected) {
  const pts = ann.points
  if (!pts || pts.length < 3) return
  const [cx, cy, r] = pts
  const px = L.offsetX + cx * L.drawW
  const py = L.offsetY + cy * L.drawH
  const rad = r * Math.min(L.drawW, L.drawH)
  const color = ann.color || '#ff4d4f'

  // Selection halo
  if (selected) {
    ctx.beginPath()
    ctx.arc(px, py, rad + Math.max(6, rad * 0.08), 0, Math.PI * 2)
    ctx.fillStyle = 'rgba(122,162,247,0.14)'
    ctx.fill()
    ctx.beginPath()
    ctx.arc(px, py, rad + Math.max(4, rad * 0.05), 0, Math.PI * 2)
    ctx.strokeStyle = 'rgba(122,162,247,0.65)'
    ctx.lineWidth = 3
    ctx.stroke()
  }

  // Circle
  ctx.strokeStyle = color
  ctx.lineWidth = selected ? 4.5 : 3
  ctx.beginPath()
  ctx.arc(px, py, rad, 0, Math.PI * 2)
  ctx.stroke()

  // Label
  const label = (ann.text || '').trim()
  if (label) {
    const fontPx = typeof ann.label_font_size_px === 'number' && ann.label_font_size_px > 0
      ? Math.min(96, Math.max(8, Math.round(ann.label_font_size_px)))
      : Math.max(12, Math.round(rad * 0.45))
    ctx.font = `600 ${fontPx}px sans-serif`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    const pad = 4
    const tw = ctx.measureText(label).width + pad * 2
    const th = fontPx + pad * 2
    const bx = px - tw / 2
    const by = py - rad - th - 6
    ctx.fillStyle = 'rgba(26,27,38,0.88)'
    ctx.strokeStyle = selected ? '#7aa2f7' : color
    ctx.lineWidth = selected ? 2.25 : 1.5
    ctx.fillRect(bx, by, tw, th)
    ctx.strokeRect(bx, by, tw, th)
    ctx.fillStyle = '#e0e0e0'
    ctx.fillText(label, px, by + th / 2)
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// CanvasViewer component
// ─────────────────────────────────────────────────────────────────────────────

function CanvasViewer({ sessionId, eventId, shapes, selectedAnnId, tool, onAddBlur, onAddCircle }) {
  const containerRef = useRef(null)
  const canvasRef    = useRef(null)
  const layoutRef    = useRef({ cw: 0, ch: 0, offsetX: 0, offsetY: 0, drawW: 0, drawH: 0, scale: 1 })
  const [drawStart, setDrawStart] = useState(null)
  const [drawEnd,   setDrawEnd]   = useState(null)
  const [tick,      setTick]      = useState(0)   // bumped on resize

  const imgUrl = localUrl(`/api/autocad/sessions/${sessionId}/events/${eventId}/image`)

  // Observe container size changes
  useEffect(() => {
    const el = containerRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => setTick(t => t + 1))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Redraw whenever image, shapes, selection or draw-state changes
  useEffect(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return

    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      const cw = container.clientWidth  || img.width
      const ch = container.clientHeight || img.height
      canvas.width  = cw
      canvas.height = ch

      const scale   = Math.min(cw / img.width, ch / img.height)
      const drawW   = img.width  * scale
      const drawH   = img.height * scale
      const offsetX = (cw - drawW) / 2
      const offsetY = (ch - drawH) / 2
      layoutRef.current = { cw, ch, offsetX, offsetY, drawW, drawH, scale }

      const ctx = canvas.getContext('2d')
      const L   = layoutRef.current
      ctx.clearRect(0, 0, cw, ch)
      ctx.drawImage(img, 0, 0, img.width, img.height, offsetX, offsetY, drawW, drawH)

      // Committed blur regions
      ;(shapes || []).filter(s => s.type === 'blur_region').forEach(br =>
        drawBlurRegion(ctx, img, br, L, img.width, img.height)
      )

      // Click circles
      ;(shapes || []).filter(s => s.type === 'click_circle').forEach(a =>
        drawClickCircle(ctx, a, L, a.id === selectedAnnId)
      )

      // In-progress blur drag preview
      if (drawStart && drawEnd && tool === 'blur') {
        const x1 = Math.min(drawStart.x, drawEnd.x)
        const y1 = Math.min(drawStart.y, drawEnd.y)
        const rw = Math.abs(drawEnd.x - drawStart.x)
        const rh = Math.abs(drawEnd.y - drawStart.y)
        if (rw > 0.001 && rh > 0.001) {
          drawBlurRegion(ctx, img,
            { shape: 'rect', points: [x1, y1, rw, rh], intensity: 10 },
            L, img.width, img.height, { showBorder: true },
          )
        }
      }
    }
    img.onerror = () => {
      const ctx = canvas.getContext('2d')
      ctx.fillStyle = '#1a1b2e'
      ctx.fillRect(0, 0, canvas.width || 400, canvas.height || 300)
      ctx.fillStyle = '#565f89'
      ctx.font = '14px sans-serif'
      ctx.fillText('—', 12, 30)
    }
    // Assign src AFTER event handlers — avoids missing cached-image load events
    img.src = imgUrl
  }, [imgUrl, shapes, drawStart, drawEnd, tool, selectedAnnId, tick])

  const toImg = useCallback((clientX, clientY) => {
    const canvas = canvasRef.current
    if (!canvas) return null
    const rect = canvas.getBoundingClientRect()
    const rx = (clientX - rect.left)  / rect.width
    const ry = (clientY - rect.top)   / rect.height
    return canvasToImage(rx, ry, layoutRef.current)
  }, [])

  const onMouseDown = useCallback(e => {
    if (tool === 'blur') {
      const r = toImg(e.clientX, e.clientY)
      if (r) setDrawStart(r)
    } else if (tool === 'circle') {
      const r = toImg(e.clientX, e.clientY)
      if (r) onAddCircle?.({ type: 'click_circle', points: [r.x, r.y, 0.04], color: '#ff4d4f', text: '', label_font_size_px: 0 })
    }
  }, [tool, toImg, onAddCircle])

  const onMouseMove = useCallback(e => {
    if (!drawStart) return
    const r = toImg(e.clientX, e.clientY)
    if (r) setDrawEnd(r)
  }, [drawStart, toImg])

  const onMouseUp = useCallback(() => {
    if (tool === 'blur' && drawStart && drawEnd) {
      const x1 = Math.min(drawStart.x, drawEnd.x)
      const y1 = Math.min(drawStart.y, drawEnd.y)
      const w  = Math.abs(drawEnd.x - drawStart.x)
      const h  = Math.abs(drawEnd.y - drawStart.y)
      if (w > 0.01 && h > 0.01) {
        onAddBlur?.({ type: 'blur_region', shape: 'rect', points: [x1, y1, w, h], intensity: 10 })
      }
    }
    setDrawStart(null)
    setDrawEnd(null)
  }, [tool, drawStart, drawEnd, onAddBlur])

  return (
    <div ref={containerRef}
      style={{ width: '100%', height: '100%', background: '#0d0e1a', borderRadius: 4, overflow: 'hidden' }}
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseUp={onMouseUp}
      onMouseLeave={() => { setDrawStart(null); setDrawEnd(null) }}
    >
      <canvas ref={canvasRef}
        style={{ width: '100%', height: '100%', cursor: tool === 'select' ? 'default' : 'crosshair', display: 'block' }}
      />
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// ID generator
// ─────────────────────────────────────────────────────────────────────────────

let _seq = Date.now()
function newId() { return ++_seq }

// ─────────────────────────────────────────────────────────────────────────────
// AutoCADEditorPage
// ─────────────────────────────────────────────────────────────────────────────

export default function AutoCADEditorPage() {
  const { sessionId } = useParams()
  const navigate      = useNavigate()
  const location      = useLocation()
  const backTo        = location.state?.backTo ?? '/autocad'
  const t             = useT()

  const [frames,       setFrames]       = useState([])
  const [idx,          setIdx]          = useState(0)
  const [tool,         setTool]         = useState('select')
  const [selectedAnnId,setSelectedAnnId]= useState(null)
  const [dirty,        setDirty]        = useState(false)
  const [saving,       setSaving]       = useState(false)
  const [distributing, setDistributing] = useState(false)
  const [distStatus,   setDistStatus]   = useState('')   // label shown in button while working
  const [videoState,   setVideoState]   = useState(null)  // null|generating|ready|error
  const [voice,        setVoice]        = useState('alloy')
  const [narrationText,setNarrationText]= useState(null)  // null = not loaded yet

  // ── Load frames + session ──────────────────────────────────────────────────

  useEffect(() => {
    listFrames(sessionId).then(data =>
      setFrames(data.map(f => ({
        ...f,
        shapes:           tryParse(f.shapes_json),
        voice_text:       f.voice_text       ?? null,
        voice_confidence: f.voice_confidence ?? null,
      })))
    )
    getAutoCADSession(sessionId).then(s => setNarrationText(s.narration_text ?? '')).catch(() => {})
  }, [sessionId])

  function tryParse(raw) {
    try { return JSON.parse(raw || '[]') } catch { return [] }
  }

  // ── Current frame helpers ──────────────────────────────────────────────────

  const frame = frames[idx] || null

  function patchFrame(patch) {
    setFrames(prev => prev.map((f, i) => i === idx ? { ...f, ...patch } : f))
    setDirty(true)
  }

  // ── Save ───────────────────────────────────────────────────────────────────

  const doSave = useCallback(async (target) => {
    const f = target ?? frames[idx]
    if (!f) return
    setSaving(true)
    try {
      await updateFrame(sessionId, f.event_id, {
        title:       f.title,
        narration:   f.narration,
        shapes_json: JSON.stringify(f.shapes),
      })
      setDirty(false)
    } finally {
      setSaving(false)
    }
  }, [frames, idx, sessionId])

  async function switchFrame(newIdx) {
    if (dirty) await doSave()
    setIdx(newIdx)
    setSelectedAnnId(null)
  }

  async function handleDeleteFrame(i) {
    const f = frames[i]
    if (!f) return
    try {
      await deleteFrame(sessionId, f.event_id)
      const next = frames.filter((_, fi) => fi !== i)
      setFrames(next)
      setDirty(false)
      // Keep selection in bounds
      setIdx(prev => Math.min(prev, Math.max(0, next.length - 1)))
      setSelectedAnnId(null)
    } catch (e) {
      alert(t.ed_delete_fail + (e?.response?.data?.detail || String(e)))
    }
  }

  // ── Shape operations ───────────────────────────────────────────────────────

  function addShape(shape) {
    const s = { id: newId(), ...shape }
    patchFrame({ shapes: [...(frame?.shapes || []), s] })
    if (s.type === 'click_circle') setSelectedAnnId(s.id)
  }

  function removeShape(id) {
    patchFrame({ shapes: (frame?.shapes || []).filter(s => s.id !== id) })
    if (selectedAnnId === id) setSelectedAnnId(null)
  }

  function patchShape(id, patch) {
    patchFrame({ shapes: (frame?.shapes || []).map(s => s.id === id ? { ...s, ...patch } : s) })
  }

  // ── AI distribute ──────────────────────────────────────────────────────────

  async function handleDistribute() {
    setDistributing(true)
    try {
      // Generate narration first if none exists
      if (!narrationText) {
        setDistStatus(t.ed_overlay_gen_narr)
        await regenerateAutoCADNarration(sessionId)
        // Poll until done
        await new Promise((resolve, reject) => {
          const check = async () => {
            try {
              const s = await getAutoCADSession(sessionId)
              if (s.narration_text) { setNarrationText(s.narration_text); resolve(); return }
              if (s.status === 'error') { reject(new Error(t.ed_overlay_gen_narr)); return }
            } catch (e) { reject(e); return }
            setTimeout(check, 2000)
          }
          check()
        })
      }
      setDistStatus(t.ed_overlay_distributing)
      await distributeNarration(sessionId)
      const data = await listFrames(sessionId)
      setFrames(data.map(f => ({ ...f, shapes: tryParse(f.shapes_json) })))
      setDirty(false)
    } catch (e) {
      alert(t.ed_distribute_fail + (e?.response?.data?.detail || String(e)))
    } finally {
      setDistributing(false)
      setDistStatus('')
    }
  }

  // ── Generate narrated video ────────────────────────────────────────────────

  async function handleGenerateVideo() {
    await doSave()
    setVideoState('generating')
    try {
      await generateNarratedVideo(sessionId, voice)
      const poll = async () => {
        try {
          const s = await getNarratedVideoStatus(sessionId)
          if (s.status === 'ready') { setVideoState('ready'); return }
          if (s.status === 'error') { setVideoState('error'); return }
        } catch (_) {}
        setTimeout(poll, 2000)
      }
      setTimeout(poll, 2000)
    } catch { setVideoState('error') }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  if (!frame) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center',
                    height: '100%', color: 'var(--text-s)', fontSize: 14 }}>
        {frames.length === 0 ? t.ed_loading : t.ed_no_frames}
      </div>
    )
  }

  const blurs   = (frame.shapes || []).filter(s => s.type === 'blur_region')
  const circles = (frame.shapes || []).filter(s => s.type === 'click_circle')

  function pct(v) { return Math.round(v * 1000) / 10 }
  function clamp01(v) { return Math.min(1, Math.max(0, v)) }
  function clampR(v) { return Math.min(0.45, Math.max(0.008, v)) }

  const loadingMsg = distributing
    ? (distStatus || t.ed_overlay_ai)
    : videoState === 'generating'
      ? t.ed_overlay_video
      : null

  return (
    <div style={s.page}>

      <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>

      {/* ── Full-screen frosted-glass loading overlay ── */}
      {loadingMsg && (
        <div style={s.overlay}>
          <div style={s.overlayCard}>
            <div style={s.spinner} />
            <div style={s.overlayMsg}>{loadingMsg}</div>
            <div style={s.overlayHint}>{t.ed_overlay_hint}</div>
          </div>
        </div>
      )}

      {/* ── Ribbon ── */}
      <header style={s.ribbon}>
        <div style={s.ribLeft}>
          <button style={s.btnBack}
            onClick={() => { if (dirty) doSave(); navigate(backTo) }}>
            {t.ed_back}
          </button>
          <span style={s.sessLabel}>Session #{sessionId} {t.ed_session_label}</span>
          <div style={s.toolGroup}>
            {[
              ['select', t.ed_tool_select, '#a9b1d6'],
              ['blur',   t.ed_tool_blur,   '#73daca'],
              ['circle', t.ed_tool_circle, '#ff4d4f'],
            ].map(([id, label, color]) => (
              <button key={id}
                style={{ ...s.toolBtn, ...(tool === id ? { background: color + '22', color, borderColor: color } : {}) }}
                onClick={() => { setTool(id); setSelectedAnnId(null) }}>
                {label}
              </button>
            ))}
          </div>
        </div>
        <div style={s.ribRight}>
          <button style={s.btnAlt} onClick={handleDistribute} disabled={distributing}>
            {distributing ? (distStatus || t.ed_processing) : t.ed_ai_distribute}
          </button>
          <button style={s.btnSave} onClick={() => doSave()} disabled={saving || !dirty}>
            {saving ? t.ed_saving : dirty ? t.ed_save : t.ed_saved}
          </button>
          <select value={voice} onChange={e => setVoice(e.target.value)} style={s.voiceSelect}
            disabled={videoState === 'generating'}>
            {['alloy','echo','fable','onyx','nova','shimmer'].map(v =>
              <option key={v} value={v}>{v}</option>
            )}
          </select>
          {videoState === 'ready'
            ? <a href={`/api/autocad/sessions/${sessionId}/video/narrated/download`}
                style={{ ...s.btnGenerate, background: '#9ece6a', textDecoration: 'none',
                         display: 'inline-flex', alignItems: 'center' }}>
                {t.ed_download_video}
              </a>
            : <button style={s.btnGenerate} onClick={handleGenerateVideo}
                disabled={videoState === 'generating'}>
                {videoState === 'generating' ? t.ed_generating_video : t.ed_gen_voice_video}
              </button>
          }
        </div>
      </header>

      {/* ── Main area ── */}
      <div style={s.main}>

        {/* Canvas viewer */}
        <div style={s.viewerWrap}>
          <CanvasViewer
            sessionId={sessionId}
            eventId={frame.event_id}
            shapes={frame.shapes || []}
            selectedAnnId={selectedAnnId}
            tool={tool}
            onAddBlur={shape => addShape(shape)}
            onAddCircle={shape => addShape(shape)}
          />
        </div>

        {/* ── Property panel ── */}
        <div style={s.panel}>

          {/* Step title */}
          <div style={s.sec}>
            <div style={s.secLabel}>{t.ed_step_title}</div>
            <input style={s.input}
              value={frame.title || ''}
              onChange={e => patchFrame({ title: e.target.value })}
              placeholder={`${t.ed_step_ph} ${idx + 1}`}
            />
          </div>

          {/* Per-frame recorded voice */}
          {frame.voice_text && (
            <div style={s.sec}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <div style={s.secLabel}>
                  {t.ed_voice}
                  {frame.voice_confidence != null && (
                    <span style={{ fontSize: 10, color: 'var(--text-s)', fontWeight: 400, marginLeft: 6 }}>
                      {Math.round(frame.voice_confidence * 100)}% {t.ed_confidence}
                    </span>
                  )}
                </div>
                <button
                  style={{ fontSize: 11, padding: '2px 8px', background: '#7aa2f722',
                           border: '1px solid #7aa2f744', borderRadius: 5,
                           color: '#7aa2f7', cursor: 'pointer' }}
                  title={t.ed_fill_narration_title}
                  onClick={() => patchFrame({ narration: frame.voice_text })}>
                  {t.ed_fill_narration}
                </button>
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-m)', lineHeight: 1.6,
                            background: 'var(--surface2,#1e2030)', border: '1px solid var(--border)',
                            borderRadius: 6, padding: '7px 10px' }}>
                {frame.voice_text}
              </div>
            </div>
          )}

          {/* Narration / description */}
          <div style={{ ...s.sec, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 120 }}>
            <div style={s.secLabel}>{t.ed_narration}</div>
            <textarea style={s.textarea}
              value={frame.narration || ''}
              onChange={e => patchFrame({ narration: e.target.value })}
              placeholder={t.ed_narration_ph}
            />
          </div>

          {/* Blur regions */}
          <div style={s.sec}>
            <div style={s.secLabel}>{t.ed_blur_regions} ({blurs.length})</div>
            {blurs.length === 0
              ? <p style={s.hint}>{t.ed_blur_hint}</p>
              : blurs.map((br, i) => (
                  <div key={br.id} style={s.shapeRow}>
                    <span style={s.shapeRowLabel}>{t.ed_blur_rect} {i + 1}</span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={s.sliderName}>{t.ed_intensity}</span>
                      <input type="range" min={1} max={30}
                        value={br.intensity || 10}
                        style={{ width: 72, accentColor: '#73daca' }}
                        onChange={e => patchShape(br.id, { intensity: Number(e.target.value) })}
                      />
                      <span style={{ ...s.sliderVal, color: '#73daca' }}>{br.intensity || 10}</span>
                    </div>
                    <button style={s.btnDanger} onClick={() => removeShape(br.id)}>{t.ed_delete}</button>
                  </div>
                ))
            }
          </div>

          {/* Click circles */}
          <div style={s.sec}>
            <div style={s.secLabel}>{t.ed_circles} ({circles.length})</div>
            {circles.length === 0
              ? <p style={s.hint}>{t.ed_circle_hint}</p>
              : circles.map(a => {
                  const [cx, cy, r] = a.points || [0.5, 0.5, 0.04]
                  const isSel = selectedAnnId === a.id
                  return (
                    <div key={a.id}
                      style={{ ...s.circleCard, ...(isSel ? s.circleCardSel : {}) }}
                      onClick={() => setSelectedAnnId(isSel ? null : a.id)}>

                      <div style={s.circleCardHead}>
                        <span style={{ fontSize: 12, fontWeight: 700, color: a.color || '#ff4d4f' }}>{t.ed_click_circle}</span>
                        <button style={s.btnDanger}
                          onClick={e => { e.stopPropagation(); removeShape(a.id) }}>{t.ed_delete}</button>
                      </div>

                      {/* X / Y / Radius sliders */}
                      {[
                        [t.ed_cx, pct(cx), 0, 100, 0.1,
                          v => patchShape(a.id, { points: [clamp01(v / 100), cy, r] })],
                        [t.ed_cy, pct(cy), 0, 100, 0.1,
                          v => patchShape(a.id, { points: [cx, clamp01(v / 100), r] })],
                        [t.ed_radius, pct(r), 0.8, 45, 0.1,
                          v => patchShape(a.id, { points: [cx, cy, clampR(v / 100)] })],
                      ].map(([label, val, min, max, step, onChange]) => (
                        <div key={label} style={{ marginBottom: 8 }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                            <span style={s.sliderName}>{label}</span>
                            <span style={s.sliderVal}>{val}%</span>
                          </div>
                          <input type="range" min={min} max={max} step={step} value={val}
                            style={{ width: '100%', accentColor: 'var(--accent)' }}
                            onClick={e => e.stopPropagation()}
                            onChange={e => onChange(parseFloat(e.target.value))}
                          />
                        </div>
                      ))}

                      {/* Label + color */}
                      <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 6 }}>
                        <input type="text"
                          value={a.text || ''}
                          placeholder={t.ed_label_text}
                          style={{ ...s.input, flex: 1, fontSize: 12 }}
                          onClick={e => e.stopPropagation()}
                          onChange={e => patchShape(a.id, { text: e.target.value })}
                        />
                        <input type="color"
                          value={a.color || '#ff4d4f'}
                          title={t.ed_circle_color}
                          style={{ width: 28, height: 28, padding: 2, border: '1px solid var(--border)',
                                   borderRadius: 4, background: 'none', cursor: 'pointer', flexShrink: 0 }}
                          onClick={e => e.stopPropagation()}
                          onChange={e => patchShape(a.id, { color: e.target.value })}
                        />
                      </div>

                      {/* Font size */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={s.sliderName}>{t.ed_font_size}</span>
                        <input type="number" min={0} max={96} step={1}
                          value={a.label_font_size_px || 0}
                          title={t.ed_font_auto_title}
                          style={{ width: 52, ...s.input, padding: '3px 6px', fontSize: 12 }}
                          onClick={e => e.stopPropagation()}
                          onChange={e => {
                            const raw = parseInt(e.target.value, 10)
                            patchShape(a.id, {
                              label_font_size_px: Number.isFinite(raw) && raw > 0
                                ? Math.min(96, Math.max(8, raw)) : 0,
                            })
                          }}
                        />
                        <span style={s.sliderName}>{t.ed_font_auto}</span>
                      </div>
                    </div>
                  )
                })
            }
          </div>

          <div style={s.frameCounter}>{idx + 1} / {frames.length} {t.ed_frames_count}</div>
        </div>
      </div>

      {/* ── Filmstrip ── */}
      <div style={s.film}>
        {frames.map((f, i) => (
          <div key={f.event_id}
            style={{ ...s.thumb, ...(i === idx ? s.thumbSel : {}) }}
            onClick={() => switchFrame(i)}>
            <img
              src={localUrl(`/api/autocad/sessions/${sessionId}/events/${f.event_id}/image`)}
              style={s.thumbImg} alt="" loading="lazy"
            />
            <div style={s.thumbLabel}>{f.title || `${t.ed_frame_label} ${i + 1}`}</div>
            {(f.shapes?.length ?? 0) > 0 && (
              <div style={s.thumbBadge}>{f.shapes.length}</div>
            )}
            <button
              style={s.thumbDel}
              title={t.ed_delete_frame}
              onClick={e => { e.stopPropagation(); handleDeleteFrame(i) }}>
              ✕
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Styles
// ─────────────────────────────────────────────────────────────────────────────

const s = {
  page: {
    display: 'flex', flexDirection: 'column',
    height: '100%', overflow: 'hidden',
    background: 'var(--bg, #1a1b2e)',
    color: 'var(--text, #cdd6f4)',
  },

  // ribbon
  ribbon: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '8px 14px', borderBottom: '1px solid var(--border)',
    background: 'var(--surface)', flexShrink: 0, gap: 10, flexWrap: 'wrap',
  },
  ribLeft:    { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' },
  ribRight:   { display: 'flex', alignItems: 'center', gap: 8 },
  btnBack: {
    background: 'transparent', border: '1px solid var(--border)',
    borderRadius: 6, color: 'var(--text-m)', padding: '5px 12px',
    fontSize: 13, cursor: 'pointer',
  },
  sessLabel:  { fontSize: 13, color: 'var(--text-s)', whiteSpace: 'nowrap' },
  toolGroup:  { display: 'flex', gap: 4 },
  toolBtn: {
    padding: '5px 12px', background: 'transparent',
    border: '1px solid var(--border)', borderRadius: 6,
    color: 'var(--text-s)', fontSize: 12, cursor: 'pointer',
  },
  btnSave: {
    padding: '6px 14px', background: '#7aa2f7', border: 'none',
    borderRadius: 6, color: '#1a1b2e', fontWeight: 700, fontSize: 12, cursor: 'pointer',
  },
  btnAlt: {
    padding: '6px 12px', background: 'transparent',
    border: '1px solid var(--accent, #7aa2f7)',
    borderRadius: 6, color: 'var(--accent, #7aa2f7)',
    fontWeight: 700, fontSize: 12, cursor: 'pointer',
  },
  btnGenerate: {
    padding: '6px 14px', background: '#bb9af7', border: 'none',
    borderRadius: 6, color: '#1a1b2e', fontWeight: 700, fontSize: 12, cursor: 'pointer',
  },
  voiceSelect: {
    padding: '5px 8px', background: 'var(--surface2)', border: '1px solid var(--border)',
    borderRadius: 6, color: 'var(--text)', fontSize: 12, cursor: 'pointer',
  },

  // main
  main: { display: 'flex', flex: 1, overflow: 'hidden' },
  viewerWrap: { flex: 1, padding: 12, overflow: 'hidden' },

  // property panel
  panel: {
    width: 288, flexShrink: 0,
    borderLeft: '1px solid var(--border)',
    display: 'flex', flexDirection: 'column',
    overflowY: 'auto',
    background: 'var(--surface)',
  },
  sec: { padding: '12px 14px', borderBottom: '1px solid var(--border)' },
  secLabel: {
    fontSize: 11, fontWeight: 700, color: 'var(--text-m)',
    textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8,
  },
  input: {
    width: '100%', background: 'var(--surface2, #1e2030)',
    border: '1px solid var(--border)', borderRadius: 6,
    padding: '6px 8px', color: 'var(--text)', fontSize: 13,
    outline: 'none', boxSizing: 'border-box',
  },
  textarea: {
    flex: 1, width: '100%', minHeight: 120,
    background: 'var(--surface2, #1e2030)',
    border: '1px solid var(--border)', borderRadius: 6,
    padding: 8, color: 'var(--text)', fontSize: 12,
    lineHeight: 1.7, resize: 'vertical', outline: 'none',
    boxSizing: 'border-box', fontFamily: 'inherit',
  },
  hint: { fontSize: 12, color: 'var(--text-s)', margin: 0 },
  shapeRow: {
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '5px 0', fontSize: 12, flexWrap: 'wrap',
    borderBottom: '1px solid var(--border)', marginBottom: 4,
  },
  shapeRowLabel: { flex: 1, color: 'var(--text-m)', fontSize: 12 },
  sliderName: { fontSize: 11, color: 'var(--text-s)', whiteSpace: 'nowrap' },
  sliderVal:  { fontSize: 11, color: 'var(--accent, #7aa2f7)', fontVariantNumeric: 'tabular-nums', flexShrink: 0 },
  circleCard: {
    padding: '10px 10px', background: 'var(--bg, #1a1b2e)',
    border: '1px solid var(--border)', borderRadius: 8,
    marginBottom: 8, cursor: 'pointer',
  },
  circleCardSel: {
    borderColor: 'var(--accent, #7aa2f7)',
    boxShadow: '0 0 0 1px rgba(122,162,247,0.3)',
  },
  circleCardHead: {
    display: 'flex', justifyContent: 'space-between',
    alignItems: 'center', marginBottom: 10,
  },
  btnDanger: {
    background: 'transparent', border: '1px solid #f7768e44',
    color: '#f7768e', borderRadius: 4, fontSize: 11,
    padding: '2px 8px', cursor: 'pointer', flexShrink: 0,
  },
  frameCounter: {
    padding: '10px 14px', fontSize: 12, color: 'var(--text-s)',
    textAlign: 'center', marginTop: 'auto',
    borderTop: '1px solid var(--border)',
  },

  // filmstrip
  film: {
    height: 106, flexShrink: 0,
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '8px 12px', overflowX: 'auto',
    borderTop: '1px solid var(--border)',
    background: 'var(--surface)',
  },
  thumb: {
    position: 'relative', flexShrink: 0, cursor: 'pointer',
    border: '2px solid transparent', borderRadius: 6, overflow: 'hidden',
    width: 126, height: 82,
  },
  thumbSel:   { borderColor: '#7aa2f7' },
  thumbImg:   { width: '100%', height: '100%', objectFit: 'cover', display: 'block' },
  thumbLabel: {
    position: 'absolute', bottom: 0, left: 0, right: 0,
    background: 'rgba(0,0,0,0.65)', color: '#cdd6f4',
    fontSize: 10, padding: '2px 5px',
    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
  },
  thumbBadge: {
    position: 'absolute', top: 3, left: 3,
    background: '#7aa2f7', color: '#1a1b2e',
    borderRadius: '50%', width: 16, height: 16,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 10, fontWeight: 700,
  },
  thumbDel: {
    position: 'absolute', top: 3, right: 3,
    background: '#f7768e', border: 'none',
    color: '#1a1b2e', borderRadius: '50%',
    width: 16, height: 16,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 10, fontWeight: 700, cursor: 'pointer',
    lineHeight: 1, padding: 0,
  },

  // ── Frosted-glass overlay ──────────────────────────────────────────────────
  overlay: {
    position: 'fixed', inset: 0, zIndex: 999,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    backdropFilter: 'blur(12px)',
    WebkitBackdropFilter: 'blur(12px)',
    background: 'rgba(15, 16, 30, 0.55)',
  },
  overlayCard: {
    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16,
    padding: '36px 48px',
    background: 'rgba(255,255,255,0.06)',
    border: '1px solid rgba(255,255,255,0.12)',
    borderRadius: 20,
    boxShadow: '0 8px 40px rgba(0,0,0,0.4)',
  },
  spinner: {
    width: 40, height: 40,
    border: '3px solid rgba(255,255,255,0.12)',
    borderTop: '3px solid #bb9af7',
    borderRadius: '50%',
    animation: 'spin 0.85s linear infinite',
  },
  overlayMsg: {
    fontSize: 16, fontWeight: 600,
    color: 'var(--text, #cdd6f4)',
    letterSpacing: 0.3,
  },
  overlayHint: {
    fontSize: 12, color: 'rgba(205,214,244,0.5)',
  },
}
