import { useState } from 'react'
import { useT } from '../hooks/useT'
import { useSettingsStore } from '../store/settingsStore'
import { LANGUAGE_OPTIONS } from '../i18n/locale'

export default function SettingsPage() {
  const t = useT()
  const {
    language, setLanguage,
    theme, setTheme,
    pollInterval, setPollInterval,
    ignoredApps, setIgnoredApps,
    avatarEnabled, setAvatarEnabled,
    serverUrl, setServerUrl,
    syncAuto, setSyncAuto,
  } = useSettingsStore()

  const [interval, setInterval_]   = useState(String(pollInterval))
  const [ignored, setIgnored]      = useState(ignoredApps.join('\n'))
  const [serverInput, setServerInput] = useState(serverUrl)
  const [saved, setSaved]          = useState(false)

  function save() {
    const n = parseInt(interval, 10)
    if (n >= 1 && n <= 60) setPollInterval(n)
    setIgnoredApps(ignored.split('\n').map(s => s.trim()).filter(Boolean))
    setServerUrl(serverInput.trim().replace(/\/$/, ''))
    setSaved(true)
    setTimeout(() => setSaved(false), 1800)
  }

  return (
    <div style={s.page}>
      <h2 style={s.title}>{t.settings_title}</h2>

      <div style={s.card}>
        {/* Theme */}
        <Section label={t.settings_theme} desc={t.settings_theme_desc}>
          <div style={s.row}>
            {['dark','light'].map(v => (
              <button key={v}
                style={{ ...s.chip, ...(theme === v ? s.chipActive : {}) }}
                onClick={() => setTheme(v)}>
                {v === 'dark' ? t.theme_dark : t.theme_light}
              </button>
            ))}
          </div>
        </Section>

        <Divider />

        {/* Language */}
        <Section label={t.settings_language} desc={t.settings_language_desc}>
          <div style={s.row}>
            {LANGUAGE_OPTIONS.map(opt => (
              <button key={opt.value}
                style={{ ...s.chip, ...(language === opt.value ? s.chipActive : {}) }}
                onClick={() => setLanguage(opt.value)}>
                {opt.label}
              </button>
            ))}
          </div>
        </Section>

        <Divider />

        {/* Poll interval */}
        <Section label={t.settings_poll_label} desc={t.settings_poll_desc}>
          <div style={s.row}>
            <input
              type="number" min={1} max={60} value={interval}
              onChange={e => setInterval_(e.target.value)}
              style={s.numInput}
            />
            <span style={s.unit}>s</span>
          </div>
        </Section>

        <Divider />

        {/* Avatar toggle */}
        <Section label={t.settings_avatar_label} desc={t.settings_avatar_desc}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }}>
            <div
              onClick={() => setAvatarEnabled(!avatarEnabled)}
              style={{
                width: 44, height: 24, borderRadius: 12,
                background: avatarEnabled ? 'var(--accent)' : 'var(--border)',
                position: 'relative', cursor: 'pointer', flexShrink: 0,
                transition: 'background 0.2s',
              }}>
              <div style={{
                position: 'absolute', top: 3, left: avatarEnabled ? 22 : 3,
                width: 18, height: 18, borderRadius: '50%', background: '#fff',
                transition: 'left 0.2s',
              }} />
            </div>
            <span style={{ fontSize: 13, color: avatarEnabled ? 'var(--accent)' : 'var(--text-s)' }}>
              {avatarEnabled ? t.settings_avatar_on : t.settings_avatar_off}
            </span>
          </label>
        </Section>

        <Divider />

        {/* Server sync */}
        <Section label="服务器同步" desc="配置中心服务器地址，录制完成后可将会话上传至服务器共享。">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <input
              type="text"
              value={serverInput}
              onChange={e => setServerInput(e.target.value)}
              placeholder="https://StepCast.example.com"
              style={{ ...s.numInput, width: '100%', textAlign: 'left', padding: '7px 10px' }}
            />
            <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }}>
              <div
                onClick={() => setSyncAuto(!syncAuto)}
                style={{
                  width: 44, height: 24, borderRadius: 12,
                  background: syncAuto ? 'var(--accent)' : 'var(--border)',
                  position: 'relative', cursor: 'pointer', flexShrink: 0,
                  transition: 'background 0.2s',
                }}>
                <div style={{
                  position: 'absolute', top: 3, left: syncAuto ? 22 : 3,
                  width: 18, height: 18, borderRadius: '50%', background: '#fff',
                  transition: 'left 0.2s',
                }} />
              </div>
              <span style={{ fontSize: 13, color: syncAuto ? 'var(--accent)' : 'var(--text-s)' }}>
                {syncAuto ? '录制完成后自动同步' : '手动同步'}
              </span>
            </label>
          </div>
        </Section>

        <Divider />

        {/* Ignored apps */}
        <Section label={t.settings_ignored_label} desc={t.settings_ignored_desc}>
          <textarea
            value={ignored}
            onChange={e => setIgnored(e.target.value)}
            rows={5}
            placeholder={t.settings_ignored_placeholder}
            style={s.textarea}
          />
        </Section>

        <div style={s.footer}>
          <button onClick={save} style={s.saveBtn}>
            {saved ? t.settings_saved : t.settings_save}
          </button>
        </div>
      </div>
    </div>
  )
}

function Section({ label, desc, children }) {
  return (
    <div style={sec.wrap}>
      <div style={sec.left}>
        <div style={sec.label}>{label}</div>
        <div style={sec.desc}>{desc}</div>
      </div>
      <div style={sec.right}>{children}</div>
    </div>
  )
}

function Divider() {
  return <div style={{ borderTop: '1px solid var(--border)', margin: '0 -24px' }} />
}

const sec = {
  wrap:  { display: 'flex', alignItems: 'flex-start', gap: 32, padding: '20px 0' },
  left:  { flex: '0 0 260px' },
  label: { fontSize: 14, fontWeight: 600, color: 'var(--text)' },
  desc:  { fontSize: 13, color: 'var(--text-s)', marginTop: 4, lineHeight: 1.5 },
  right: { flex: 1 },
}

const s = {
  page:     { maxWidth: 700, paddingBottom: 32 },
  title:    { margin: '0 0 24px', fontSize: 22, fontWeight: 800, color: 'var(--text)' },
  card:     { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 14, padding: '0 24px' },
  row:      { display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' },
  chip:     { padding: '6px 14px', border: '1px solid var(--border)', borderRadius: 8, background: 'transparent', color: 'var(--text-m)', cursor: 'pointer', fontSize: 13 },
  chipActive:{ background: 'var(--accent-bg)', borderColor: 'var(--accent)', color: 'var(--accent)', fontWeight: 700 },
  numInput: { width: 72, padding: '6px 10px', background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text)', fontSize: 14, textAlign: 'center' },
  unit:     { fontSize: 14, color: 'var(--text-s)' },
  textarea: { width: '100%', padding: '10px 12px', background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text)', fontSize: 13, fontFamily: 'monospace', resize: 'vertical', lineHeight: 1.6 },
  footer:   { padding: '20px 0', display: 'flex', justifyContent: 'flex-end' },
  saveBtn:  { padding: '10px 28px', background: 'var(--accent)', color: 'var(--accent-fg)', border: 'none', borderRadius: 8, fontWeight: 700, fontSize: 14, cursor: 'pointer' },
}
