import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { vi } from 'vitest'
import SystemPage from './SystemPage'
import { getSystemInfo } from '../api/system'

vi.mock('../api/system', () => ({ getSystemInfo: vi.fn() }))

const mockedGetSystemInfo = vi.mocked(getSystemInfo)

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <SystemPage />
    </QueryClientProvider>,
  )
}

describe('SystemPage', () => {
  test('shows host architecture, Python, deployment mode and V1 security warning', async () => {
    mockedGetSystemInfo.mockResolvedValue({
      hub_version: '1.0.0',
      platform: { os: 'linux', arch: 'amd64' },
      python_version: '3.12.0',
      deployment_mode: 'offline',
    })

    renderPage()

    expect(await screen.findByText('linux / amd64')).toBeInTheDocument()
    expect(screen.getByText('3.12.0')).toBeInTheDocument()
    expect(screen.getByText('offline')).toBeInTheDocument()
    expect(screen.getByText(/V1 当前未启用身份认证/)).toBeInTheDocument()
  })
})
