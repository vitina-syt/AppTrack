import { Outlet, NavLink } from 'react-router-dom'
import { useT } from '../../hooks/useT'
import { useSettingsStore } from '../../store/settingsStore'
import { LANGUAGE_OPTIONS } from '../../i18n/locale'

export default function AppLayout() {
  const t = useT()
  const { language, setLanguage, theme, setTheme } = useSettingsStore()

  return (
    <div style={s.shell}>
      <aside style={s.sidebar}>
        <div style={s.brand}>AppTrack</div>
        <nav style={s.nav}>
          <SidebarLink to="/"          end>{t.nav_home}</SidebarLink>
          <SidebarLink to="/dashboard">{t.nav_dashboard}</SidebarLink>
          <SidebarLink to="/history"  >{t.nav_history}</SidebarLink>
          <SidebarLink to="/scribe"   >{t.nav_scribe}</SidebarLink>
          <SidebarLink to="/autocad"  >{t.nav_autocad}</SidebarLink>
          <SidebarLink to="/settings" >{t.nav_settings}</SidebarLink>
        </nav>

        <div style={s.dividerGroup}>
          <div style={s.groupLabel}>{t.theme_label}</div>
          <div style={s.row}>
            {['dark','light'].map(v => (
              <button key={v}
                style={{ ...s.btn, ...(theme === v ? s.btnActive : {}) }}
                onClick={() => setTheme(v)}>
                {v === 'dark' ? t.theme_dark : t.theme_light}
              </button>
            ))}
          </div>

          <div style={{ ...s.groupLabel, marginTop: 12 }}>{t.lang_label}</div>
          <div style={s.row}>
            {LANGUAGE_OPTIONS.map(opt => (
              <button key={opt.value}
                style={{ ...s.btn, ...(language === opt.value ? s.btnActive : {}) }}
                onClick={() => setLanguage(opt.value)}>
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        <div style={s.footer}>{t.footer}</div>
      </aside>
      <div style={s.main}>
        <Outlet />
      </div>
    </div>
  )
}

function SidebarLink({ to, end, children }) {
  return (
    <NavLink to={to} end={end}
      style={({ isActive }) => ({ ...s.link, ...(isActive ? s.linkActive : {}) })}>
      {children}
    </NavLink>
  )
}

const s = {
  shell:      { height: '100%', display: 'flex', background: 'var(--bg)', color: 'var(--text)', overflow: 'hidden' },
  sidebar:    { width: 200, flexShrink: 0, borderRight: '1px solid var(--sb-border)', padding: 16, background: 'var(--sb-bg)', display: 'flex', flexDirection: 'column', gap: 16, overflowY: 'auto' },
  brand:      { fontSize: 18, fontWeight: 800, color: 'var(--sb-brand)', letterSpacing: 0.2 },
  nav:        { display: 'flex', flexDirection: 'column', gap: 4 },
  link:       { padding: '9px 10px', borderRadius: 8, color: 'var(--sb-text)', textDecoration: 'none', fontSize: 14 },
  linkActive: { background: 'var(--sb-active)', color: 'var(--sb-text-a)' },
  dividerGroup: { marginTop: 'auto', paddingTop: 12 },
  groupLabel: { fontSize: 11, color: 'var(--sb-text)', marginBottom: 6, opacity: 0.7 },
  row:        { display: 'flex', gap: 4, flexWrap: 'wrap' },
  btn:        { padding: '4px 10px', background: 'transparent', border: 'none', borderRadius: 6, color: 'var(--sb-text)', cursor: 'pointer', fontSize: 12 },
  btnActive:  { background: 'var(--sb-active)', color: 'var(--sb-brand)', fontWeight: 700 },
  footer:     { fontSize: 11, color: 'var(--sb-text)', marginTop: 8, opacity: 0.45 },
  main:       { flex: 1, minWidth: 0, padding: 28, overflowY: 'auto' },
}
