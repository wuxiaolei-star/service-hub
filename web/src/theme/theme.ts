import { theme } from 'antd'
import type { ThemeConfig } from 'antd'

/**
 * Design tokens for the console. Keep this file the single source of truth
 * for the palette and typography scale; component-level overrides go in
 * `components` below, one-off values stay out of the components.
 *
 * Both layers must be split per mode: `token` alone is not enough because the
 * `components` section carries light-only surface values (sider/header/table
 * header/selected backgrounds) that would stay bright under `darkAlgorithm`.
 */
export type ThemeMode = 'light' | 'dark'

/** Shared brand and shape tokens that do not change with the mode. */
const brandTokens = {
  colorPrimary: '#3B82F6',
  colorInfo: '#3B82F6',
  borderRadius: 8,
}

const lightTokens = {
  ...brandTokens,
  colorSuccess: '#16A34A',
  colorWarning: '#D97706',
  colorError: '#DC2626',
  colorBgLayout: '#F4F8FD',
  colorBgContainer: '#FFFFFF',
  colorBorder: '#DCE8F5',
  colorBorderSecondary: '#E8F0FA',
  colorText: '#1F2937',
  colorTextSecondary: '#64748B',
  colorTextTertiary: '#94A3B8',
  // Typography `type="secondary"` resolves to this token, not colorTextSecondary
  colorTextDescription: '#64748B',
  boxShadowTertiary: '0 2px 8px rgb(15 23 42 / 6%)',
}

const darkTokens = {
  ...brandTokens,
  colorSuccess: '#22C55E',
  colorWarning: '#F59E0B',
  colorError: '#F87171',
  colorBgLayout: '#0E1524',
  colorBgContainer: '#161F31',
  colorBorder: '#26334A',
  colorBorderSecondary: '#1F2A3E',
  colorText: '#E5EAF3',
  colorTextSecondary: '#9AA9BF',
  colorTextTertiary: '#6B7C93',
  colorTextDescription: '#9AA9BF',
  boxShadowTertiary: '0 2px 8px rgb(0 0 0 / 35%)',
}

const lightComponents = {
  Layout: {
    lightSiderBg: '#EAF3FF',
    headerBg: '#FFFFFF',
  },
  Menu: {
    itemSelectedBg: '#DCE8F5',
  },
  Table: {
    headerBg: '#F8FAFC',
    headerColor: '#64748B',
    rowHoverBg: '#F8FAFC',
  },
  Card: {
    paddingLG: 20,
  },
  Segmented: {
    itemSelectedBg: '#FFFFFF',
  },
}

const darkComponents = {
  Layout: {
    // Sider and Menu stay on the `light` variant in both modes so a single
    // token set drives their surfaces; only the values change here.
    lightSiderBg: '#121B2B',
    headerBg: '#161F31',
  },
  Menu: {
    itemSelectedBg: '#22314B',
  },
  Table: {
    headerBg: '#1B2437',
    headerColor: '#9AA9BF',
    rowHoverBg: '#1B2437',
  },
  Card: {
    paddingLG: 20,
  },
  Segmented: {
    itemSelectedBg: '#22314B',
  },
}

/**
 * Build the antd theme for one mode.
 *
 * `darkAlgorithm` derives the full neutral ramp from our brand tokens, then the
 * explicit overrides above pin the console-specific surfaces that the generic
 * algorithm cannot know about (layout tint, sider tint, table header).
 */
export function createAppTheme(mode: ThemeMode): ThemeConfig {
  return {
    algorithm: mode === 'dark' ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: mode === 'dark' ? darkTokens : lightTokens,
    components: mode === 'dark' ? darkComponents : lightComponents,
  }
}
