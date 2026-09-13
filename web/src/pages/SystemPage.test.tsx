import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, test, vi } from 'vitest'
import SystemPage from './SystemPage'
import { getMetrics, getSystemInfo } from '../api/system'

vi.mock('../api/system', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  getSystemInfo: vi.fn(),
  getMetrics: vi.fn(),
}))

const mockedGetSystemInfo = vi.mocked(getSystemInfo)
const mockedGetMetrics = vi.mocked(getMetrics)

const METRICS_TEXT = [
  '# HELP hub_files_total Stored files.',
  '# TYPE hub_files_total gauge',
  'hub_files_total 3',
  'hub_files_bytes_total 12345',
  'hub_jobs_total{status="PENDING"} 1',
  'hub_jobs_active 1',
  'hub_users_total 2',
  'hub_sessions_active 1',
  'hub_uptime_seconds 42.1',
].join('\n')

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <SystemPage />
    </QueryClientProvider>,
  )
}

function statisticOf(title: string): HTMLElement {
  const statistic = screen.getByText(title).closest('.ant-statistic')
  if (statistic === null) {
    throw new Error(`Statistic for ${title} not found`)
  }
  return statistic as HTMLElement
}

describe('SystemPage', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    mockedGetSystemInfo.mockResolvedValue({
      hub_version: '1.0.0',
      platform: { os: 'linux', arch: 'amd64' },
      python_version: '3.12.0',
      deployment_mode: 'offline',
    })
    mockedGetMetrics.mockResolvedValue(METRICS_TEXT)
  })

  test('shows host architecture, Python, deployment mode and V1 security warning', async () => {
    renderPage()

    expect(await screen.findByText('linux / amd64')).toBeInTheDocument()
    expect(screen.getByText('3.12.0')).toBeInTheDocument()
    expect(screen.getByText('offline')).toBeInTheDocument()
    expect(screen.getByText(/V1 当前未启用身份认证/)).toBeInTheDocument()
  })

  test('renders runtime metrics from the Prometheus payload', async () => {
    renderPage()

    expect(await screen.findByText('文件总数')).toBeInTheDocument()
    expect(within(statisticOf('文件总数')).getByText('3')).toBeInTheDocument()
    expect(within(statisticOf('存储字节')).getByText('12.1 KB')).toBeInTheDocument()
    expect(within(statisticOf('活动任务')).getByText('1')).toBeInTheDocument()
    expect(within(statisticOf('用户数')).getByText('2')).toBeInTheDocument()
    expect(within(statisticOf('活跃会话')).getByText('1')).toBeInTheDocument()
  })

  test('hides the whole metrics section when the admin-only endpoint returns 403', async () => {
    mockedGetMetrics.mockRejectedValue({ code: 'FORBIDDEN', message: '需要 admin 角色', status: 403 })

    renderPage()

    expect(await screen.findByText('linux / amd64')).toBeInTheDocument()
    expect(screen.queryByText('运行指标')).not.toBeInTheDocument()
    expect(screen.queryByText('文件总数')).not.toBeInTheDocument()
  })
})
