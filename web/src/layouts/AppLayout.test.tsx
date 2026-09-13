import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../App'
import '../styles.css'

test('renders a pale, accessible, collapsible service navigation shell', async () => {
  await act(async () => {
    render(<App />)
  })

  const user = userEvent.setup()
  const sidebar = document.querySelector<HTMLElement>('aside.app-sider')

  expect(sidebar).not.toBeNull()
  expect(getComputedStyle(sidebar!).backgroundColor).toBe('rgb(234, 243, 255)')
  expect(screen.getByText('Service Hub')).toBeInTheDocument()
  expect(screen.getByRole('navigation')).toHaveAttribute('id', 'main-navigation')
  expect(screen.getByText('概览')).toBeInTheDocument()
  expect(getComputedStyle(screen.getByText('服务状态')).color).toBe('rgb(100, 116, 139)')
  expect(getComputedStyle(document.querySelector<HTMLElement>('.app-content .ant-card')!).boxShadow).not.toBe('none')

  const toggle = screen.getByRole('button', { name: '收起侧边栏' })
  expect(toggle).toHaveAttribute('aria-controls', 'main-navigation')
  expect(toggle).toHaveAttribute('aria-expanded', 'true')

  toggle.focus()
  await user.keyboard('{Enter}')

  expect(toggle).toHaveAccessibleName('展开侧边栏')
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
  expect(sidebar).toHaveClass('ant-layout-sider-collapsed')
})
