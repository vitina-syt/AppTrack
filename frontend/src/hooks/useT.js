import { useSettingsStore } from '../store/settingsStore'
import { LOCALES } from '../i18n/locale'

export function useT() {
  const language = useSettingsStore((s) => s.language)
  return LOCALES[language] || LOCALES.zh
}
