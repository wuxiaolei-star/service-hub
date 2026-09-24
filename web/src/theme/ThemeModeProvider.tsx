import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  ThemeModeContext,
  THEME_MODE_STORAGE_KEY,
  readStoredMode,
  systemMode,
  type ThemeModeValue,
} from './themeMode'
import type { ThemeMode } from './theme'

/**
 * Owns the console colour mode: an explicit choice wins, otherwise follow the OS.
 *
 * The mode is mirrored onto `<html data-theme>` so `styles.css` can swap its
 * own custom properties with the same switch antd uses.
 */
export function ThemeModeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(() => readStoredMode() ?? systemMode())

  useEffect(() => {
    const root = document.documentElement
    root.dataset.theme = mode
    root.style.colorScheme = mode
  }, [mode])

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (event: MediaQueryListEvent) => {
      // An explicit choice is sticky; only follow the OS while unset.
      if (readStoredMode() !== null) return
      setMode(event.matches ? 'dark' : 'light')
    }
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  const toggleMode = useCallback(() => {
    setMode((current) => {
      const next: ThemeMode = current === 'dark' ? 'light' : 'dark'
      try {
        window.localStorage.setItem(THEME_MODE_STORAGE_KEY, next)
      } catch {
        // Persisting is best-effort; the in-memory mode still applies.
      }
      return next
    })
  }, [])

  const value = useMemo<ThemeModeValue>(() => ({ mode, toggleMode }), [mode, toggleMode])

  return <ThemeModeContext.Provider value={value}>{children}</ThemeModeContext.Provider>
}
