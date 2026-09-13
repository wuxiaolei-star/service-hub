import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import App from '../App'
import '../styles.css'

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
  expect(screen.getByRole('navigation')).toHaveAttribute('id', 'main-navigation')
  expect(within(screen.getByRole('navigation')).getByText('概览')).toBeInTheDocument()
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
