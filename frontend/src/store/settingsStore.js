import { create } from 'zustand'

const STORAGE_KEY = 'apptrack.settings.v1'

function load() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) } catch { return null }
}
function save(s) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(s)) } catch {}
}

const defaults = {
  language: 'zh',
  theme: 'dark',
  pollInterval: 5,
  ignoredApps: ['explorer.exe', 'SearchHost.exe', 'ShellExperienceHost.exe'],
}

export const useSettingsStore = create((set, get) => {
  const p = typeof window !== 'undefined' ? load() : null
  const initial = {
    ...defaults,
    ...(p?.language && ['zh','en','de'].includes(p.language) ? { language: p.language } : {}),
    ...(p?.theme && ['dark','light'].includes(p.theme) ? { theme: p.theme } : {}),
    ...(p?.pollInterval >= 1 ? { pollInterval: p.pollInterval } : {}),
    ...(Array.isArray(p?.ignoredApps) ? { ignoredApps: p.ignoredApps } : {}),
  }

  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('data-theme', initial.theme)
  }

  return {
    ...initial,
    setLanguage: (language) => { set({ language }); save({ ...get(), language }) },
    setTheme: (theme) => {
      set({ theme }); save({ ...get(), theme })
      document.documentElement.setAttribute('data-theme', theme)
    },
    setPollInterval: (pollInterval) => { set({ pollInterval }); save({ ...get(), pollInterval }) },
    setIgnoredApps: (ignoredApps) => { set({ ignoredApps }); save({ ...get(), ignoredApps }) },
    reset: () => { set({ ...defaults }); save({ ...defaults }); document.documentElement.setAttribute('data-theme', defaults.theme) },
  }
})
