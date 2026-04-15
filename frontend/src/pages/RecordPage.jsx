/**
 * RecordPage — Universal 5-step recording wizard
 *
 * Step 0: Select target application
 * Step 1: Recording configuration (app-specific)
 * Step 2: Start / stop monitoring
 * Step 3: Edit frames + generate AI narration (manual trigger)
 * Step 4: Generate and download video
 */
import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Steps, ConfigProvider, theme as antTheme } from 'antd'
import {
  getRunningWindows,
  startAutoCAD, stopAutoCAD, getAutoCADStatus,
  getAutoCADSession, regenerateAutoCADNarration,
  generateAutoCADVideo, getAutoCADVideoStatus,
  micCheck,
} from '../api'
import { useSettingsStore } from '../store/settingsStore'
import { useT } from '../hooks/useT'

// ── Ant Design dark theme to match app palette ────────────────────────────────

const ANT_THEME = {
  algorithm: antTheme.darkAlgorithm,
  token: {
    colorPrimary:       '#7aa2f7',
    colorBgContainer:   '#1a1b2e',
    colorBgElevated:    '#1e2030',
    colorText:          '#cdd6f4',
    colorTextSecondary: '#a9b1d6',
    colorTextDescription:'#565f89',
    colorBorder:        '#2a2d3e',
    fontSize:           13,
    borderRadius:       8,
  },
  components: {
    Steps: {
      colorPrimary:    '#7aa2f7',
      colorText:       '#cdd6f4',
      colorTextLabel:  '#a9b1d6',
      iconSize:        28,
      titleLineHeight: 1.4,
    },
  },
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function RecordPage() {
  const navigate = useNavigate()
  const language = useSettingsStore(s => s.language)
  const t = useT()

  // wizard
  const [step, setStep]     = useState(0)

  // step 0: app selector
  const [runningApps,  setRunningApps]  = useState([])
  const [appsLoading,  setAppsLoading]  = useState(false)
  const [targetExe,    setTargetExe]    = useState('acad.exe')

  // step 1: config
  const [title,           setTitle]         = useState('')
  const [enableCom,       setEnableCom]     = useState(true)
  const [enableVoice,     setEnableVoice]   = useState(true)
  const [screenshotOnCmd, setShotCmd]       = useState(true)
  const [screenshotOnClick,setShotClick]    = useState(true)
  const [shotMiddleDrag,   setShotMiddle]   = useState(true)
  const [shotScrollZoom,   setShotScroll]   = useState(true)
  const [shotShiftPan,     setShotShiftPan] = useState(true)
  const [background,       setBackground]   = useState('')

  // step 2: monitoring
  const [sessionId,    setSessionId]    = useState(null)
  const [agentStatus,  setAgentStatus]  = useState(null)
  const [starting,     setStarting]     = useState(false)
  const [stopping,     setStopping]     = useState(false)

  // step 3: edit
  const [session,         setSession]         = useState(null)
  const [narrationLoading,setNarLoading]      = useState(false)

  // step 4: video
  const [videoStatus, setVideoStatus] = useState(null)  // null|generating|ready|error
  const [videoDiag,   setVideoDiag]   = useState(null)

  const pollRef  = useRef(null)
  const isAcad   = targetExe.toLowerCase().includes('acad')
  const isCreo   = ['xtop', 'creo'].some(k => targetExe.toLowerCase().includes(k))
  const isRunning = agentStatus?.running

  // Step definitions (i18n)
  const stepItems = [
    { title: t.rec_step0_title, description: t.rec_step0_desc },
    { title: t.rec_step1_title, description: t.rec_step1_desc },
    { title: t.rec_step2_title, description: t.rec_step2_desc },
    { title: t.rec_step3_title, description: t.rec_step3_desc },
    { title: t.rec_step4_title, description: t.rec_step4_desc },
  ]

  // ── Init: refresh app list + restore in-progress session ──────────────────

  useEffect(() => {
    refreshApps()
    getAutoCADStatus().then(st => {
      if (st.running) {
        setSessionId(st.session_id)
        setAgentStatus(st)
        setStep(2)
      }
    }).catch(() => {})
  }, [])

  // ── Poll agent stats while in step 2 ──────────────────────────────────────

  useEffect(() => {
    clearInterval(pollRef.current)
    if (step === 2) {
      pollRef.current = setInterval(async () => {
        try {
          const st = await getAutoCADStatus()
          setAgentStatus(st)
        } catch (_) {}
      }, 2000)
    }
    return () => clearInterval(pollRef.current)
  }, [step])

  // ── Reload session detail when entering step 3 ────────────────────────────

  useEffect(() => {
    if (step === 3 && sessionId) {
      getAutoCADSession(sessionId).then(setSession).catch(() => {})
    }
  }, [step, sessionId])

  // ── Actions ───────────────────────────────────────────────────────────────

  async function refreshApps() {
    setAppsLoading(true)
    try {
      const list = await getRunningWindows()
      setRunningApps(list)
      const acad = list.find(a => a.exe.toLowerCase().includes('acad'))
      if (acad) setTargetExe(acad.exe)
      else if (list.length > 0) setTargetExe(list[0].exe)
    } catch (_) {}
    finally { setAppsLoading(false) }
  }

  async function handleStart() {
    setStarting(true)
    try {
      const st = await startAutoCAD({
        title,
        targetExe,
        enableVoice,
        enableCom: isAcad ? enableCom : false,
        screenshotOnCommand: screenshotOnCmd,
        screenshotOnClick,
        screenshotOnMiddleDrag: shotMiddleDrag,
        screenshotOnScrollZoom: shotScrollZoom,
        screenshotOnShiftPan:   shotShiftPan,
        background,
      })
      setSessionId(st.session_id)
      setAgentStatus(st)
    } catch (e) {
      alert(e?.response?.data?.detail || String(e))
    } finally {
      setStarting(false)
    }
  }

  async function handleStop() {
    setStopping(true)
    try {
      await stopAutoCAD()
      clearInterval(pollRef.current)
      const sess = await getAutoCADSession(sessionId)
      setSession(sess)
      setAgentStatus(prev => ({ ...prev, running: false }))
      setStep(3)
    } catch (e) {
      alert(e?.response?.data?.detail || String(e))
    } finally {
      setStopping(false)
    }
  }

  async function handleGenerateNarration() {
    if (!sessionId) return
    setNarLoading(true)
    try {
      await regenerateAutoCADNarration(sessionId, language)
      // Poll until done
      const poll = async () => {
        try {
          const s = await getAutoCADSession(sessionId)
          setSession(s)
          if (s.status === 'done' || s.status === 'error') {
            setNarLoading(false)
            return
          }
        } catch (_) {}
        setTimeout(poll, 2000)
      }
      setTimeout(poll, 1000)
    } catch (e) {
      setNarLoading(false)
      alert(e?.response?.data?.detail || String(e))
    }
  }

  async function handleGenerateVideo() {
    setVideoStatus('generating')
    setVideoDiag(null)
    try {
      await generateAutoCADVideo(sessionId)
      const poll = async () => {
        try {
          const s = await getAutoCADVideoStatus(sessionId)
          setVideoDiag(s)
          if (s.status === 'ready') { setVideoStatus('ready'); return }
          if (s.status === 'error') { setVideoStatus('error'); return }
        } catch (_) {}
        setTimeout(poll, 2000)
      }
      setTimeout(poll, 1000)
    } catch { setVideoStatus('error') }
  }

  async function handleRefreshVideoStatus() {
    if (!sessionId) return
    try {
      const s = await getAutoCADVideoStatus(sessionId)
      setVideoDiag(s)
      if (s.status === 'ready') setVideoStatus('ready')
      if (s.status === 'error') setVideoStatus('error')
    } catch (_) {}
  }

  // ── Step navigation ───────────────────────────────────────────────────────

  // Sidebar can only navigate back to already-visited steps
  function canGoTo(i) {
    if (isRunning) return false
    return i < step
  }

  function goTo(i) { if (canGoTo(i)) setStep(i) }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div style={s.page}>

      {/* ── Left: Steps panel ── */}
      <div style={s.stepsPanel}>
        <div style={s.panelTitle}>{t.rec_wizard_title}</div>
        <ConfigProvider theme={ANT_THEME}>
          <Steps
            direction="vertical"
            current={step}
            onChange={goTo}
            items={stepItems.map((item, i) => ({
              ...item,
              disabled: !canGoTo(i),
              status: i < step ? 'finish' : i === step ? 'process' : 'wait',
            }))}
          />
        </ConfigProvider>
      </div>

      {/* ── Right: Content ── */}
      <div style={s.content}>
        {step === 0 && (
          <StepSelectApp
            runningApps={runningApps} appsLoading={appsLoading}
            targetExe={targetExe} setTargetExe={setTargetExe}
            onRefresh={refreshApps} onNext={() => setStep(1)}
          />
        )}
        {step === 1 && (
          <StepConfig
            isAcad={isAcad} isCreo={isCreo} title={title} setTitle={setTitle}
            background={background} setBackground={setBackground}
            enableCom={enableCom} setEnableCom={setEnableCom}
            enableVoice={enableVoice} setEnableVoice={setEnableVoice}
            screenshotOnCmd={screenshotOnCmd} setShotCmd={setShotCmd}
            screenshotOnClick={screenshotOnClick} setShotClick={setShotClick}
            shotMiddleDrag={shotMiddleDrag} setShotMiddle={setShotMiddle}
            shotScrollZoom={shotScrollZoom} setShotScroll={setShotScroll}
            shotShiftPan={shotShiftPan}     setShotShiftPan={setShotShiftPan}
            onBack={() => goTo(0)} onNext={() => setStep(2)}
          />
        )}
        {step === 2 && (
          <StepMonitor
            isRunning={isRunning} agentStatus={agentStatus}
            starting={starting} stopping={stopping}
            onStart={handleStart} onStop={handleStop}
          />
        )}
        {step === 3 && (
          <StepEdit
            sessionId={sessionId}
            onOpenEditor={() =>
              navigate(`/record/editor/${sessionId}`, { state: { backTo: '/record' } })
            }
            onBack={() => goTo(2)} onNext={() => setStep(4)}
          />
        )}
        {step === 4 && (
          <StepVideo
            sessionId={sessionId} videoStatus={videoStatus} videoDiag={videoDiag}
            onGenerate={handleGenerateVideo} onRefresh={handleRefreshVideoStatus}
            onBack={() => goTo(3)}
          />
        )}
      </div>
    </div>
  )
}

// ── Step 0: Select App ────────────────────────────────────────────────────────

function StepSelectApp({ runningApps, appsLoading, targetExe, setTargetExe, onRefresh, onNext }) {
  const t = useT()
  const isAcad = targetExe.toLowerCase().includes('acad')
  return (
    <div>
      <h2 style={s.stepTitle}>{t.rec_sel_title}</h2>
      <p style={s.stepDesc}>{t.rec_sel_desc}</p>

      <div style={s.card}>
        <div style={s.cardTitle}>{t.rec_sel_running}</div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
          <select style={{ ...s.input, flex: 1 }} value={targetExe}
            onChange={e => setTargetExe(e.target.value)}>
            {runningApps.length === 0
              ? <option value={targetExe}>{targetExe}</option>
              : runningApps.map(a => (
                  <option key={a.pid} value={a.exe}>
                    {a.exe}  —  {a.title.length > 42 ? a.title.slice(0, 42) + '…' : a.title}
                  </option>
                ))
            }
          </select>
          <button style={s.btnIcon} onClick={onRefresh} disabled={appsLoading} title={t.rec_sel_refresh}>
            {appsLoading ? '…' : '↻'}
          </button>
        </div>

        {targetExe && (
          <div style={{ fontSize: 13, color: 'var(--text-s)', display: 'flex', alignItems: 'center', gap: 8 }}>
            {t.rec_sel_selected}<span style={{ color: '#7aa2f7', fontWeight: 700 }}>{targetExe}</span>
            {isAcad && (
              <span style={s.badge}>{t.rec_sel_acad_mode}</span>
            )}
          </div>
        )}
      </div>

      <div style={s.navRow}>
        <button style={s.btnPrimary} onClick={onNext} disabled={!targetExe}>
          {t.rec_next}
        </button>
      </div>
    </div>
  )
}

// ── MicCheck ─────────────────────────────────────────────────────────────────

function MicCheck() {
  const t = useT()
  const [state,   setState]   = useState('idle')   // idle | checking | done | error
  const [result,  setResult]  = useState(null)

  async function run() {
    setState('checking')
    setResult(null)
    try {
      const r = await micCheck(2000)
      setResult(r)
      setState('done')
    } catch (e) {
      setResult({ error: e?.response?.data?.detail || String(e) })
      setState('error')
    }
  }

  const rmsBar = result?.rms != null
    ? Math.min(100, Math.round(result.rms / 0.1 * 100))   // 0.1 RMS → 100%
    : 0

  const statusColor = !result ? '#a9b1d6'
    : result.error          ? '#f7768e'
    : result.has_speech     ? '#9ece6a'
    : result.pyaudio_available ? '#e0af68'
    : '#f7768e'

  const statusText = !result ? ''
    : result.error                          ? result.error
    : !result.pyaudio_available             ? t.rec_mic_no_pyaudio
    : !result.whisper_available             ? t.rec_mic_no_whisper
    : result.has_speech                     ? `${t.rec_mic_has_speech} (RMS ${result.rms})`
    : `${t.rec_mic_no_speech} (RMS ${result.rms} < ${t.rec_mic_thresh} ${result.silence_thresh})`

  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <button
          style={{ ...sp.pickBtn, opacity: state === 'checking' ? 0.6 : 1 }}
          onClick={run}
          disabled={state === 'checking'}>
          {state === 'checking' ? t.rec_mic_checking : t.rec_mic_check}
        </button>
        {statusText && (
          <span style={{ fontSize: 12, color: statusColor }}>{statusText}</span>
        )}
      </div>

      {/* RMS bar */}
      {result?.pyaudio_available && (
        <div style={{ marginTop: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between',
                        fontSize: 11, color: 'var(--text-s)', marginBottom: 3 }}>
            <span>{t.rec_mic_volume}</span>
            <span>{result.rms} RMS / {t.rec_mic_peak} {result.peak}</span>
          </div>
          <div style={{ height: 6, background: 'var(--surface2,#1e2030)',
                        borderRadius: 3, border: '1px solid var(--border)', overflow: 'hidden' }}>
            <div style={{
              height: '100%', borderRadius: 3,
              width: `${rmsBar}%`,
              background: result.has_speech ? '#9ece6a' : '#e0af68',
              transition: 'width 0.3s',
            }} />
          </div>
          <div style={{ height: 4, marginTop: 2, position: 'relative' }}>
            {/* threshold marker */}
            <div style={{
              position: 'absolute',
              left: `${Math.min(100, Math.round(result.silence_thresh / 0.1 * 100))}%`,
              top: 0, height: 4, width: 2, background: '#e0af68',
            }} title={`${t.rec_mic_thresh} ${result.silence_thresh}`} />
          </div>
        </div>
      )}

      {/* Device list */}
      {result?.devices?.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 11, color: 'var(--text-s)', marginBottom: 4 }}>
            {t.rec_mic_devices} ({result.devices.length})：
          </div>
          {result.devices.map(d => (
            <div key={d.index} style={{
              fontSize: 11, color: d.default ? '#7aa2f7' : 'var(--text-s)',
              display: 'flex', gap: 6, alignItems: 'center', marginBottom: 2,
            }}>
              {d.default ? '● ' : '○ '}
              <span style={{ fontWeight: d.default ? 700 : 400 }}>
                [{d.index}] {d.name}
              </span>
              <span style={{ color: 'var(--text-s)' }}>
                {d.channels}ch · {d.sample_rate}Hz
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Install hint — only show when backend responded (not on network/timeout errors) */}
      {state === 'done' && result && !result.pyaudio_available && (
        <div style={{ marginTop: 6, fontSize: 11, color: '#e0af68',
                      background: '#2d2510', borderRadius: 5,
                      padding: '5px 8px', border: '1px solid #e0af6844' }}>
          Run：<code style={{ color: '#e0af68' }}>pip install pyaudio</code>
          {' '}or：
          <code style={{ color: '#e0af68' }}>pip install pipwin &amp;&amp; pipwin install pyaudio</code>
        </div>
      )}
      {result?.pyaudio_available && !result.whisper_available && (
        <div style={{ marginTop: 6, fontSize: 11, color: '#e0af68',
                      background: '#2d2510', borderRadius: 5,
                      padding: '5px 8px', border: '1px solid #e0af6844' }}>
          ⚠ Run：<code style={{ color: '#e0af68' }}>pip install openai-whisper</code>
          {' '}or set <code style={{ color: '#e0af68' }}>OPENAI_API_KEY</code>
        </div>
      )}

      {/* Transcription test result */}
      {result?.transcription_text != null && (
        <div style={{ marginTop: 6, fontSize: 11, background: '#1a2d1a',
                      border: '1px solid #9ece6a55', borderRadius: 5,
                      padding: '6px 10px' }}>
          <span style={{ color: '#9ece6a', fontWeight: 700 }}>{t.rec_mic_transcribed}</span>
          {result.transcription_conf != null &&
            <span style={{ color: 'var(--text-s)', marginLeft: 6 }}>
              {Math.round(result.transcription_conf * 100)}% {t.rec_mic_confidence}
            </span>
          }
          <div style={{ color: 'var(--text)', marginTop: 4, fontStyle: 'italic' }}>
            "{result.transcription_text}"
          </div>
        </div>
      )}
      {result?.transcription_error && (
        <div style={{ marginTop: 6, fontSize: 11, color: '#f7768e',
                      background: '#2d1a20', borderRadius: 5,
                      padding: '5px 8px', border: '1px solid #f7768e44' }}>
          ✗ {result.transcription_error}
        </div>
      )}
      {result?.has_speech && result?.whisper_available && result?.transcription_text == null
        && !result?.transcription_error && (
        <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text-s)' }}>
          {t.rec_mic_transcribing}
        </div>
      )}
    </div>
  )
}

// ── Step 1: Config ────────────────────────────────────────────────────────────


const creoTag = {
  fontSize: 10, padding: '1px 7px', borderRadius: 10,
  background: '#bb9af722', color: '#bb9af7',
  border: '1px solid #bb9af744', marginLeft: 4,
}

const sp = {
  pickBtn: {
    padding: '6px 14px', background: '#bb9af722',
    border: '1px solid #bb9af744', borderRadius: 7,
    color: '#bb9af7', fontSize: 13, cursor: 'pointer', fontWeight: 600,
  },
  clearBtn: {
    background: 'transparent', border: '1px solid #f7768e44',
    color: '#f7768e', borderRadius: 5, fontSize: 12,
    padding: '4px 8px', cursor: 'pointer',
  },
  pathBox: {
    marginTop: 6, padding: '6px 10px',
    background: 'var(--surface2, #1e2030)',
    border: '1px solid var(--border)', borderRadius: 6,
    minHeight: 32, display: 'flex', alignItems: 'center',
  },
  pathText:  { fontFamily: 'monospace', fontSize: 12, color: 'var(--text)', wordBreak: 'break-all' },
  pathEmpty: { fontSize: 12, color: 'var(--text-s)', fontStyle: 'italic' },
}

// ── Step 1: Config ────────────────────────────────────────────────────────────

function StepConfig({ isAcad, isCreo, title, setTitle, background, setBackground,
                      enableCom, setEnableCom,
                      enableVoice, setEnableVoice,
                      screenshotOnCmd, setShotCmd,
                      screenshotOnClick, setShotClick,
                      shotMiddleDrag, setShotMiddle,
                      shotScrollZoom, setShotScroll,
                      shotShiftPan, setShotShiftPan,
                      onBack, onNext }) {
  const t = useT()
  return (
    <div>
      <h2 style={s.stepTitle}>{t.rec_cfg_title}</h2>
      <p style={s.stepDesc}>{t.rec_cfg_desc}</p>

      <div style={s.card}>
        <div style={s.cardTitle}>{t.rec_cfg_basic}</div>
        <label style={{ ...s.label, marginBottom: 14 }}>
          {t.rec_cfg_rec_title}
          <input style={s.input} value={title} onChange={e => setTitle(e.target.value)}
            placeholder={t.rec_cfg_title_ph} />
        </label>
        <label style={{ ...s.label, marginBottom: 18 }}>
          {t.rec_cfg_background}
          <span style={s.hint}>{t.rec_cfg_background_hint}</span>
          <textarea
            style={{ ...s.input, resize: 'vertical', minHeight: 72, lineHeight: 1.55, paddingTop: 8 }}
            value={background} onChange={e => setBackground(e.target.value)}
            placeholder={t.rec_cfg_background_ph}
          />
        </label>

        <div style={s.cardTitle}>{t.rec_cfg_features}</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {isAcad && (
            <label style={s.checkLabel}>
              <input type="checkbox" checked={enableCom} onChange={e => setEnableCom(e.target.checked)} />
              <span>
                COM API <span style={s.tagAcad}>{t.rec_cfg_com_tag}</span>
                <span style={s.hint}>{t.rec_cfg_com_hint}</span>
              </span>
            </label>
          )}
          <div>
            <label style={s.checkLabel}>
              <input type="checkbox" checked={enableVoice} onChange={e => setEnableVoice(e.target.checked)} />
              {t.rec_cfg_voice}
            </label>
            {enableVoice && (
              <div style={{ marginLeft: 24, marginTop: 8 }}>
                <MicCheck />
              </div>
            )}
          </div>
          <label style={s.checkLabel}>
            <input type="checkbox" checked={screenshotOnCmd} onChange={e => setShotCmd(e.target.checked)} />
            {isAcad ? t.rec_cfg_shot_cmd_acad : t.rec_cfg_shot_cmd}
          </label>
          <label style={s.checkLabel}>
            <input type="checkbox" checked={screenshotOnClick} onChange={e => setShotClick(e.target.checked)} />
            {t.rec_cfg_shot_click}
          </label>
          <label style={s.checkLabel}>
            <input type="checkbox" checked={shotMiddleDrag} onChange={e => setShotMiddle(e.target.checked)} />
            <span>
              {t.rec_cfg_shot_rotate}
              <span style={s.hint}>{t.rec_cfg_shot_rotate_hint}</span>
            </span>
          </label>
          <label style={s.checkLabel}>
            <input type="checkbox" checked={shotScrollZoom} onChange={e => setShotScroll(e.target.checked)} />
            <span>
              {t.rec_cfg_shot_scroll}
              <span style={s.hint}>{t.rec_cfg_shot_scroll_hint}</span>
            </span>
          </label>
          <label style={s.checkLabel}>
            <input type="checkbox" checked={shotShiftPan} onChange={e => setShotShiftPan(e.target.checked)} />
            <span>
              {t.rec_cfg_shot_shift}
              <span style={s.hint}>{t.rec_cfg_shot_shift_hint}</span>
            </span>
          </label>
        </div>
      </div>

      <div style={s.navRow}>
        <button style={s.btnSecondary} onClick={onBack}>{t.rec_back}</button>
        <button style={s.btnPrimary} onClick={onNext}>{t.rec_next}</button>
      </div>
    </div>
  )
}

// ── Step 2: Monitor ───────────────────────────────────────────────────────────

function StepMonitor({ isRunning, agentStatus, starting, stopping, onStart, onStop }) {
  const t = useT()
  return (
    <div>
      <h2 style={s.stepTitle}>{t.rec_mon_title}</h2>
      <p style={s.stepDesc}>{t.rec_mon_desc}</p>

      <div style={s.card}>
        {!isRunning ? (
          <>
            <div style={s.hintBox}>{t.rec_mon_hint}</div>
            <button
              style={{ ...s.btnPrimary, marginTop: 16, width: '100%', padding: '11px 0' }}
              onClick={onStart} disabled={starting}>
              {starting ? t.rec_mon_connecting : t.rec_mon_start}
            </button>
          </>
        ) : (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 18 }}>
              <span style={s.recDot} />
              <span style={{ color: '#f7768e', fontWeight: 700, fontSize: 14 }}>{t.rec_mon_recording}</span>
            </div>
            {[
              [t.rec_mon_session_id,  `#${agentStatus?.session_id}`],
              [t.rec_mon_events,      agentStatus?.events_captured ?? 0],
              [t.rec_mon_voice_segs,  agentStatus?.voice_segments  ?? 0],
            ].map(([label, val]) => (
              <div key={label} style={s.statRow}>
                <span style={s.statLabel}>{label}</span>
                <span style={s.statVal}>{val}</span>
              </div>
            ))}
            <button
              style={{ ...s.btnDanger, marginTop: 16, width: '100%', padding: '11px 0' }}
              onClick={onStop} disabled={stopping}>
              {stopping ? t.rec_mon_stopping : t.rec_mon_stop}
            </button>
          </>
        )}
      </div>
    </div>
  )
}

// ── Step 3: Edit ──────────────────────────────────────────────────────────────

function StepEdit({ sessionId, onOpenEditor, onBack, onNext }) {
  const t = useT()

  return (
    <div>
      <h2 style={s.stepTitle}>{t.rec_edit_title}</h2>
      <p style={s.stepDesc}>{t.rec_edit_desc}</p>

      {/* Frame editor */}
      <div style={s.card}>
        <div style={s.cardTitle}>{t.rec_edit_frame_editor}</div>
        <p style={{ fontSize: 13, color: 'var(--text-s)', marginBottom: 14, lineHeight: 1.6 }}>
          {t.rec_edit_frame_desc}
        </p>
        <button
          style={{ ...s.btnPrimary, background: '#bb9af7' }}
          onClick={onOpenEditor} disabled={!sessionId}>
          {t.rec_edit_open_editor}
        </button>
      </div>

      <div style={s.navRow}>
        <button style={s.btnSecondary} onClick={onBack}>{t.rec_back}</button>
        <button style={s.btnPrimary} onClick={onNext} disabled={!sessionId}>
          {t.rec_next}
        </button>
      </div>
    </div>
  )
}

// ── Step 4: Generate Video ────────────────────────────────────────────────────

function StepVideo({ sessionId, videoStatus, videoDiag, onGenerate, onRefresh, onBack }) {
  const t = useT()
  return (
    <div>
      <h2 style={s.stepTitle}>{t.rec_vid_title}</h2>
      <p style={s.stepDesc}>{t.rec_vid_desc}</p>

      <div style={s.card}>
        <div style={s.cardTitle}>{t.rec_vid_compose}</div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
          <button style={s.btnPrimary} onClick={onGenerate}
            disabled={videoStatus === 'generating' || !sessionId}>
            {videoStatus === 'generating' ? t.rec_vid_generating : t.rec_vid_generate}
          </button>
          <button style={s.btnSecondary} onClick={onRefresh} disabled={!sessionId}>
            {t.rec_vid_refresh}
          </button>
          {videoStatus === 'ready' && (
            <a
              href={`/api/autocad/sessions/${sessionId}/video/download`}
              style={{ ...s.btnPrimary, background: '#9ece6a',
                       textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              {t.rec_vid_download}
            </a>
          )}
        </div>

        {videoStatus === 'generating' && (
          <div style={{ ...s.hintBox, marginBottom: 12 }}>
            {t.rec_vid_hint_generating}
          </div>
        )}
        {videoStatus === 'ready' && (
          <div style={{ ...s.infoBox, marginBottom: 12 }}>
            {t.rec_vid_hint_ready}
          </div>
        )}
        {videoStatus === 'error' && (
          <div style={{ ...s.errorBox, marginBottom: 12 }}>
            {videoDiag?.error || t.rec_vid_hint_error}
          </div>
        )}

        {videoDiag && (
          <div style={s.diagBox}>
            {[
              [t.rec_vid_diag_status,  videoDiag.status,
               videoDiag.status === 'ready' ? '#9ece6a' : videoDiag.status === 'error' ? '#f7768e' : '#e0af68'],
              [t.rec_vid_diag_disk,    `${videoDiag.screenshot_count}${videoDiag.screenshot_count === 0 ? '  ← missing' : ''}`,
               videoDiag.screenshot_count === 0 ? '#f7768e' : '#9ece6a'],
              [t.rec_vid_diag_db,      `${videoDiag.db_screenshot_count ?? 0}${(videoDiag.db_screenshot_count ?? 0) === 0 ? '  ← mss not working' : ''}`,
               (videoDiag.db_screenshot_count ?? 0) === 0 ? '#f7768e' : '#9ece6a'],
              ['mss',    videoDiag.env?.mss    ? '✓ installed' : '✗ not installed — pip install mss',
               videoDiag.env?.mss    ? '#9ece6a' : '#f7768e'],
              ['ffmpeg', videoDiag.env?.ffmpeg ? '✓ installed (MP4)' : '✗ not found (will generate GIF)',
               videoDiag.env?.ffmpeg ? '#9ece6a' : '#e0af68'],
              ['Pillow', videoDiag.env?.pillow ? '✓ installed' : '✗ not installed — pip install Pillow',
               videoDiag.env?.pillow ? '#9ece6a' : '#f7768e'],
            ].map(([k, v, c]) => (
              <div key={k} style={s.diagRow}>
                <span style={s.diagKey}>{k}</span>
                <span style={{ color: c, fontSize: 12 }}>{v}</span>
              </div>
            ))}
            {videoDiag.screenshots_dir && (
              <div style={s.diagRow}>
                <span style={s.diagKey}>{t.rec_vid_diag_dir}</span>
                <code style={{ fontSize: 11, color: 'var(--text-s)', wordBreak: 'break-all' }}>
                  {videoDiag.screenshots_dir}
                </code>
              </div>
            )}
          </div>
        )}
      </div>

      <div style={s.navRow}>
        <button style={s.btnSecondary} onClick={onBack}>{t.rec_back}</button>
      </div>
    </div>
  )
}

// ── Styles ────────────────────────────────────────────────────────────────────

const s = {
  page: {
    display: 'flex', height: '100%',
    margin: '-28px', overflow: 'hidden',
  },
  stepsPanel: {
    width: 220, flexShrink: 0,
    borderRight: '1px solid var(--border)',
    padding: '24px 16px',
    background: 'var(--surface)',
    display: 'flex', flexDirection: 'column', gap: 12,
    overflowY: 'auto',
  },
  panelTitle: {
    fontSize: 14, fontWeight: 800, color: 'var(--text)',
    letterSpacing: 0.3, paddingLeft: 4, marginBottom: 4,
  },
  content: {
    flex: 1, padding: '28px 36px',
    overflowY: 'auto',
    maxWidth: 680,
  },

  stepTitle:  { margin: '0 0 6px', fontSize: 20, fontWeight: 800, color: 'var(--text)' },
  stepDesc:   { fontSize: 13, color: 'var(--text-s)', marginBottom: 24, lineHeight: 1.65, margin: '0 0 24px' },

  card: {
    background: 'var(--surface)', border: '1px solid var(--border)',
    borderRadius: 12, padding: '18px 20px', marginBottom: 16,
  },
  cardTitle: {
    fontSize: 11, fontWeight: 700, color: 'var(--text-m)',
    textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 14,
  },
  label:      { display: 'flex', flexDirection: 'column', gap: 5, fontSize: 12, color: 'var(--text-s)' },
  input: {
    background: 'var(--surface2, #1e2030)', border: '1px solid var(--border)',
    borderRadius: 7, padding: '7px 10px', color: 'var(--text)', fontSize: 13,
    outline: 'none', width: '100%', boxSizing: 'border-box',
  },
  checkLabel: {
    display: 'flex', alignItems: 'center', gap: 8,
    fontSize: 13, color: 'var(--text-m)', cursor: 'pointer',
  },
  hint:       { fontSize: 11, color: 'var(--text-s)' },
  tagAcad: {
    fontSize: 10, padding: '1px 7px', borderRadius: 10,
    background: '#7aa2f722', color: '#7aa2f7',
    border: '1px solid #7aa2f744', marginLeft: 4,
  },
  badge: {
    fontSize: 11, padding: '2px 8px', borderRadius: 10,
    background: '#9ece6a22', color: '#9ece6a',
    border: '1px solid #9ece6a44',
  },

  btnPrimary: {
    padding: '9px 20px', background: '#7aa2f7', border: 'none', borderRadius: 8,
    color: '#1a1b2e', fontWeight: 700, fontSize: 13, cursor: 'pointer',
  },
  btnSecondary: {
    padding: '8px 16px', background: 'var(--surface2, #1e2030)',
    border: '1px solid var(--border)', borderRadius: 8,
    color: 'var(--text-m)', fontSize: 13, cursor: 'pointer',
  },
  btnDanger: {
    padding: '9px 20px', background: '#f7768e', border: 'none', borderRadius: 8,
    color: '#1a1b2e', fontWeight: 700, fontSize: 13, cursor: 'pointer',
  },
  btnIcon: {
    padding: '7px 11px', background: 'var(--surface2, #1e2030)',
    border: '1px solid var(--border)', borderRadius: 7,
    color: 'var(--text-m)', fontSize: 14, cursor: 'pointer', flexShrink: 0,
  },
  navRow: {
    display: 'flex', gap: 10, marginTop: 8,
    paddingTop: 16, borderTop: '1px solid var(--border)',
  },

  statRow:  { display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 10 },
  statLabel:{ color: 'var(--text-s)' },
  statVal:  { color: '#7aa2f7', fontWeight: 700 },

  recDot: {
    display: 'inline-block', width: 8, height: 8,
    borderRadius: '50%', background: '#f7768e',
    boxShadow: '0 0 6px #f7768e',
  },

  narration: {
    fontSize: 13, color: 'var(--text)', lineHeight: 1.7, whiteSpace: 'pre-wrap',
    margin: 0, fontFamily: 'inherit', maxHeight: 360, overflowY: 'auto',
    background: 'var(--surface2, #1e2030)', borderRadius: 8, padding: 14,
  },

  hintBox: {
    fontSize: 12, color: '#e0af68', background: '#2d2510',
    border: '1px solid #e0af6844', borderRadius: 7,
    padding: '8px 12px', lineHeight: 1.5,
  },
  infoBox: {
    background: '#1a2d1a', border: '1px solid #9ece6a', borderRadius: 8,
    padding: '10px 14px', fontSize: 12, color: '#9ece6a',
  },
  errorBox: {
    background: '#2d1a20', border: '1px solid #f7768e', borderRadius: 8,
    padding: '10px 14px', fontSize: 12, color: '#f7768e',
  },

  diagBox: {
    background: 'var(--surface2, #1e2030)', border: '1px solid var(--border)',
    borderRadius: 8, padding: '12px 14px',
    display: 'flex', flexDirection: 'column', gap: 8,
  },
  diagRow: { display: 'flex', gap: 12, alignItems: 'flex-start', fontSize: 12 },
  diagKey: { color: 'var(--text-s)', flexShrink: 0, width: 120 },
}
