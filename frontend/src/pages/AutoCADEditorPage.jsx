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
import { useNavigate, useParams } from 'react-router-dom'
import {
  listFrames, updateFrame, distributeNarration,
  generateAnnotatedVideo, getAutoCADVideoStatus,
} from '../api'

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

  const imgUrl = `/api/autocad/sessions/${sessionId}/events/${eventId}/image`

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
      ctx.fillText('无法加载截图', 12, 30)
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

  const [frames,       setFrames]       = useState([])
  const [idx,          setIdx]          = useState(0)
  const [tool,         setTool]         = useState('select')
  const [selectedAnnId,setSelectedAnnId]= useState(null)
  const [dirty,        setDirty]        = useState(false)
  const [saving,       setSaving]       = useState(false)
  const [distributing, setDistributing] = useState(false)
  const [videoState,   setVideoState]   = useState(null)  // null|generating|ready|error

  // ── Load frames ────────────────────────────────────────────────────────────

  useEffect(() => {
    listFrames(sessionId).then(data =>
      setFrames(data.map(f => ({ ...f, shapes: tryParse(f.shapes_json) })))
    )
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
      await distributeNarration(sessionId)
      const data = await listFrames(sessionId)
      setFrames(data.map(f => ({ ...f, shapes: tryParse(f.shapes_json) })))
      setDirty(false)
    } catch (e) {
      alert('AI 分配失败：' + (e?.response?.data?.detail || String(e)))
    } finally {
      setDistributing(false)
    }
  }

  // ── Generate video ─────────────────────────────────────────────────────────

  async function handleGenerateVideo() {
    await doSave()
    setVideoState('generating')
    try {
      await generateAnnotatedVideo(sessionId)
      const poll = async () => {
        try {
          const s = await getAutoCADVideoStatus(sessionId)
          if (s.status === 'ready') { setVideoState('ready'); return }
          if (s.status === 'error') { setVideoState('error'); return }
        } catch (_) {}
        setTimeout(poll, 2000)
      }
      setTimeout(poll, 1500)
    } catch { setVideoState('error') }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  if (!frame) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center',
                    height: '100%', color: 'var(--text-s)', fontSize: 14 }}>
        {frames.length === 0 ? '加载中…' : '此会话暂无截图帧'}
      </div>
    )
  }

  const blurs   = (frame.shapes || []).filter(s => s.type === 'blur_region')
  const circles = (frame.shapes || []).filter(s => s.type === 'click_circle')

  function pct(v) { return Math.round(v * 1000) / 10 }
  function clamp01(v) { return Math.min(1, Math.max(0, v)) }
  function clampR(v) { return Math.min(0.45, Math.max(0.008, v)) }

  return (
    <div style={s.page}>

      {/* ── Ribbon ── */}
      <header style={s.ribbon}>
        <div style={s.ribLeft}>
          <button style={s.btnBack}
            onClick={() => { if (dirty) doSave(); navigate('/autocad') }}>
            ← 返回
          </button>
          <span style={s.sessLabel}>Session #{sessionId} 帧编辑</span>
          <div style={s.toolGroup}>
            {[
              ['select',  '选择',  '#a9b1d6'],
              ['blur',    '虚化',  '#73daca'],
              ['circle',  '画圈',  '#ff4d4f'],
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
            {distributing ? '分配中…' : 'AI 分配解说'}
          </button>
          <button style={s.btnSave} onClick={() => doSave()} disabled={saving || !dirty}>
            {saving ? '保存中…' : dirty ? '● 保存' : '已保存'}
          </button>
          {videoState === 'ready'
            ? <a href={`/api/autocad/sessions/${sessionId}/video/download`}
                style={{ ...s.btnGenerate, background: '#9ece6a', textDecoration: 'none',
                         display: 'inline-flex', alignItems: 'center' }}>
                ↓ 下载视频
              </a>
            : <button style={s.btnGenerate} onClick={handleGenerateVideo}
                disabled={videoState === 'generating'}>
                {videoState === 'generating' ? '生成中…' : '生成视频'}
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
            <div style={s.secLabel}>步骤标题</div>
            <input style={s.input}
              value={frame.title || ''}
              onChange={e => patchFrame({ title: e.target.value })}
              placeholder={`步骤 ${idx + 1}`}
            />
          </div>

          {/* Narration / description */}
          <div style={{ ...s.sec, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 120 }}>
            <div style={s.secLabel}>解说词</div>
            <textarea style={s.textarea}
              value={frame.narration || ''}
              onChange={e => patchFrame({ narration: e.target.value })}
              placeholder="此帧的解说词…"
            />
          </div>

          {/* Blur regions */}
          <div style={s.sec}>
            <div style={s.secLabel}>虚化区域 ({blurs.length})</div>
            {blurs.length === 0
              ? <p style={s.hint}>切换「虚化」工具后拖拽矩形区域。</p>
              : blurs.map((br, i) => (
                  <div key={br.id} style={s.shapeRow}>
                    <span style={s.shapeRowLabel}>矩形 {i + 1}</span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={s.sliderName}>强度</span>
                      <input type="range" min={1} max={30}
                        value={br.intensity || 10}
                        style={{ width: 72, accentColor: '#73daca' }}
                        onChange={e => patchShape(br.id, { intensity: Number(e.target.value) })}
                      />
                      <span style={{ ...s.sliderVal, color: '#73daca' }}>{br.intensity || 10}</span>
                    </div>
                    <button style={s.btnDanger} onClick={() => removeShape(br.id)}>删除</button>
                  </div>
                ))
            }
          </div>

          {/* Click circles */}
          <div style={s.sec}>
            <div style={s.secLabel}>圆圈标注 ({circles.length})</div>
            {circles.length === 0
              ? <p style={s.hint}>切换「画圈」工具后点击图片添加。</p>
              : circles.map(a => {
                  const [cx, cy, r] = a.points || [0.5, 0.5, 0.04]
                  const isSel = selectedAnnId === a.id
                  return (
                    <div key={a.id}
                      style={{ ...s.circleCard, ...(isSel ? s.circleCardSel : {}) }}
                      onClick={() => setSelectedAnnId(isSel ? null : a.id)}>

                      <div style={s.circleCardHead}>
                        <span style={{ fontSize: 12, fontWeight: 700, color: a.color || '#ff4d4f' }}>点击圈</span>
                        <button style={s.btnDanger}
                          onClick={e => { e.stopPropagation(); removeShape(a.id) }}>删除</button>
                      </div>

                      {/* X / Y / Radius sliders */}
                      {[
                        ['中心 X（相对画面宽）', pct(cx), 0, 100, 0.1,
                          v => patchShape(a.id, { points: [clamp01(v / 100), cy, r] })],
                        ['中心 Y（相对画面高）', pct(cy), 0, 100, 0.1,
                          v => patchShape(a.id, { points: [cx, clamp01(v / 100), r] })],
                        ['半径（相对短边）',     pct(r),  0.8, 45, 0.1,
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
                          placeholder="标签文字"
                          style={{ ...s.input, flex: 1, fontSize: 12 }}
                          onClick={e => e.stopPropagation()}
                          onChange={e => patchShape(a.id, { text: e.target.value })}
                        />
                        <input type="color"
                          value={a.color || '#ff4d4f'}
                          title="圆圈颜色"
                          style={{ width: 28, height: 28, padding: 2, border: '1px solid var(--border)',
                                   borderRadius: 4, background: 'none', cursor: 'pointer', flexShrink: 0 }}
                          onClick={e => e.stopPropagation()}
                          onChange={e => patchShape(a.id, { color: e.target.value })}
                        />
                      </div>

                      {/* Font size */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={s.sliderName}>字号</span>
                        <input type="number" min={0} max={96} step={1}
                          value={a.label_font_size_px || 0}
                          title="0 = 自动随圆圈大小"
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
                        <span style={s.sliderName}>px（0=自动）</span>
                      </div>
                    </div>
                  )
                })
            }
          </div>

          <div style={s.frameCounter}>{idx + 1} / {frames.length} 帧</div>
        </div>
      </div>

      {/* ── Filmstrip ── */}
      <div style={s.film}>
        {frames.map((f, i) => (
          <div key={f.event_id}
            style={{ ...s.thumb, ...(i === idx ? s.thumbSel : {}) }}
            onClick={() => switchFrame(i)}>
            <img
              src={`/api/autocad/sessions/${sessionId}/events/${f.event_id}/image`}
              style={s.thumbImg} alt="" loading="lazy"
            />
            <div style={s.thumbLabel}>{f.title || `步骤 ${i + 1}`}</div>
            {(f.shapes?.length ?? 0) > 0 && (
              <div style={s.thumbBadge}>{f.shapes.length}</div>
            )}
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
    position: 'absolute', top: 3, right: 3,
    background: '#f7768e', color: '#1a1b2e',
    borderRadius: '50%', width: 16, height: 16,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 10, fontWeight: 700,
  },
}
