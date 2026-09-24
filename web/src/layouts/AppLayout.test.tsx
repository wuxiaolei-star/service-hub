import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, vi } from 'vitest'
import App from '../App'
import '../styles.css'

beforeEach(() => {
  window.localStorage.clear()
  document.documentElement.dataset.theme = 'light'
})

vi.mock('../api/auth', () => ({
  fetchMe: vi.fn().mockResolvedValue({
    actor_type: 'user',
    id: 1,
    username: 'admin',
    role: 'admin',
    must_change_password: false,
  }),
  logout: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('../api/system', () => ({
  getHealth: vi.fn().mockResolvedValue({ status: 'UP' }),
  getSystemInfo: vi.fn().mockResolvedValue({
    hub_version: '1.0.0',
    platform: { os: 'linux', arch: 'amd64' },
    python_version: '3.12.0',
    deployment_mode: 'offline',
  }),
}))

vi.mock('../api/plugins', () => ({
  listPlugins: vi.fn().mockResolvedValue({ items: [] }),
  listPluginBuilds: vi.fn().mockResolvedValue({ items: [] }),
}))

vi.mock('../api/jobs', () => ({
  listJobs: vi.fn().mockResolvedValue({ items: [] }),
}))

test('renders a pale, accessible, collapsible service navigation shell', async () => {
  render(<App />)

  await screen.findByText(/admin/)

  const user = userEvent.setup()
  const sidebar = document.querySelector<HTMLElement>('aside.app-sider')

  expect(sidebar).not.toBeNull()
  expect(getComputedStyle(sidebar!).backgroundColor).toBe('rgb(234, 243, 255)')
  expect(screen.getByText('Service Hub')).toBeInTheDocument()
  const mainNav = document.querySelector<HTMLElement>('nav#main-navigation')
  expect(mainNav).not.toBeNull()
  expect(within(mainNav!).getByText('概览')).toBeInTheDocument()
  expect(getComputedStyle(screen.getByText('服务状态')).color).toBe('rgb(100, 116, 139)')

  await screen.findByText('已注册插件')
  expect(
    getComputedStyle(document.querySelector<HTMLElement>('.app-content .ant-card')!).boxShadow,
  ).not.toBe('none')

  const toggle = screen.getByRole('button', { name: '收起侧边栏' })
  expect(toggle).toHaveAttribute('aria-controls', 'main-navigation')
  expect(toggle).toHaveAttribute('aria-expanded', 'true')

  toggle.focus()
  await user.keyboard('{Enter}')

  expect(toggle).toHaveAccessibleName('展开侧边栏')
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
  expect(sidebar).toHaveClass('ant-layout-sider-collapsed')
})

test('mirrors an explicit dark-mode choice onto the document and persists it', async () => {
  render(<App />)

  await screen.findByText(/admin/)
  expect(document.documentElement.dataset.theme).toBe('light')

  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: '切换为深色' }))

  expect(document.documentElement.dataset.theme).toBe('dark')
  expect(window.localStorage.getItem('service-hub.theme-mode')).toBe('dark')
  expect(screen.getByRole('button', { name: '切换为浅色' })).toBeInTheDocument()
})

test('starts dark when the OS prefers dark and no choice was made', async () => {
  window.matchMedia = ((query: string) => ({
    matches: query.includes('prefers-color-scheme: dark'),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia

  render(<App />)

  await screen.findByText(/admin/)
  expect(document.documentElement.dataset.theme).toBe('dark')
})
