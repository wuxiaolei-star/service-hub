import type { ThemeConfig } from 'antd'

/**
 * Design tokens for the console. Keep this file the single source of truth
 * for the palette and typography scale; component-level overrides go in
 * `components` below, one-off values stay out of the components.
 */
export const appTheme: ThemeConfig = {
  token: {
    // Palette
    colorPrimary: '#3B82F6',
    colorInfo: '#3B82F6',
    colorSuccess: '#16A34A',
    colorWarning: '#D97706',
    colorError: '#DC2626',
    // Surfaces
    colorBgLayout: '#F4F8FD',
    colorBgContainer: '#FFFFFF',
    colorBorder: '#DCE8F5',
    colorBorderSecondary: '#E8F0FA',
    // Text ramp (primary / secondary / tertiary)
    colorText: '#1F2937',
    colorTextSecondary: '#64748B',
    colorTextTertiary: '#94A3B8',
    // Typography `type="secondary"` resolves to this token, not colorTextSecondary
    colorTextDescription: '#64748B',
    // Shape & elevation
    borderRadius: 8,
    boxShadowTertiary: '0 2px 8px rgb(15 23 42 / 6%)',
  },
  components: {
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
  },
}
