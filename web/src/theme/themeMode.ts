import { createContext, useContext } from 'react'
import type { ThemeMode } from './theme'

export const THEME_MODE_STORAGE_KEY = 'service-hub.theme-mode'

export interface ThemeModeValue {
  mode: ThemeMode
  /** Flip between light and dark; the choice is persisted explicitly. */
  toggleMode: () => void
}

export const ThemeModeContext = createContext<ThemeModeValue | null>(null)

/** Kept separate from the provider so the component file exports only a component. */

export function readStoredMode(): ThemeMode | null {
  try {
    const stored = window.localStorage.getItem(THEME_MODE_STORAGE_KEY)
    return stored === 'light' || stored === 'dark' ? stored : null
  } catch {
    // Private-mode or blocked storage must not break the console.
    return null
  }
}

export function systemMode(): ThemeMode {
  if (typeof window.matchMedia !== 'function') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function useThemeMode(): ThemeModeValue {
  const value = useContext(ThemeModeContext)
  if (value === null) {
    throw new Error('useThemeMode must be used inside ThemeModeProvider')
  }
  return value
}
