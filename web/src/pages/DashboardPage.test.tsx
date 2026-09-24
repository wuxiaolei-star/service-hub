import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import DashboardPage from './DashboardPage'
import { getJobStats, listJobs } from '../api/jobs'
import { listPluginBuilds, listPlugins } from '../api/plugins'

vi.mock('../api/jobs', () => ({ listJobs: vi.fn(), getJobStats: vi.fn() }))
vi.mock('../api/plugins', () => ({ listPlugins: vi.fn(), listPluginBuilds: vi.fn() }))

const mockedListJobs = vi.mocked(listJobs)
const mockedGetJobStats = vi.mocked(getJobStats)
const mockedListPlugins = vi.mocked(listPlugins)
const mockedListPluginBuilds = vi.mocked(listPluginBuilds)

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('DashboardPage', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  test('shows plugin, build and recent job summaries', async () => {
    mockedListPlugins.mockResolvedValue({ items: [{ id: 'nc-to-shp', name: 'NC 转 Shapefile', description: null, category: 'GIS', latest_version: '1.0.0' }] })
    mockedListPluginBuilds.mockResolvedValue({ items: [
      { build_id: 'build-1', plugin_id: 'nc-to-shp', version: '1.0.0', runtime_type: 'docker', target_os: 'linux', target_arch: 'amd64', status: 'ENABLED', package_sha256: 'a', runtime_fingerprint: 'b', error_summary: null },
      { build_id: 'build-2', plugin_id: 'nc-to-shp', version: '1.0.0', runtime_type: 'conda-pack', target_os: 'linux', target_arch: 'amd64', status: 'READY', package_sha256: 'a', runtime_fingerprint: 'b', error_summary: null },
    ] })
    mockedListJobs.mockResolvedValue({ items: [{ job_id: 'job-1', plugin_id: 'nc-to-shp', version: '1.0.0', build_id: 'build-1', runtime_type: 'docker', status: 'SUCCESS', cancel_requested: false, error_summary: null, created_at: '2026-09-13T08:00:00Z', started_at: null, finished_at: null, replayed_from: null }], total: 1 })
    mockedGetJobStats.mockResolvedValue({ days: 14, buckets: [] })

    renderPage()

    expect(await screen.findByText('已注册插件')).toBeInTheDocument()
    expect(screen.getByText('1', { selector: '.ant-statistic-content-value-int' })).toBeInTheDocument()
    expect(screen.getByText('运行环境 Build')).toBeInTheDocument()
    expect(screen.getByText('已启用 1 / 就绪 1')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '最近任务（最多 100 条）' })).toBeInTheDocument()
    expect(screen.getByText('job-1')).toBeInTheDocument()
    expect(screen.getByLabelText('状态：成功')).toBeInTheDocument()
  })

  test('shows the duration trend card when job stats load', async () => {
    mockedListPlugins.mockResolvedValue({ items: [] })
    mockedListPluginBuilds.mockResolvedValue({ items: [] })
    mockedListJobs.mockResolvedValue({ items: [], total: 0 })
    mockedGetJobStats.mockResolvedValue({
      days: 14,
      buckets: [
        { date: '2026-09-11', count: 5, success_count: 3, p50_ms: 1000, p95_ms: 5000 },
        { date: '2026-09-12', count: 2, success_count: 2, p50_ms: null, p95_ms: null },
      ],
    })

    renderPage()

    expect(await screen.findByRole('heading', { name: '任务耗时趋势' })).toBeInTheDocument()
    expect(screen.getByTestId('duration-trend-chart')).toBeInTheDocument()
  })

  test('shows loading placeholders while summary queries are pending', () => {
    mockedListPlugins.mockReturnValue(new Promise(() => {}))
    mockedListPluginBuilds.mockReturnValue(new Promise(() => {}))
    mockedListJobs.mockReturnValue(new Promise(() => {}))
    mockedGetJobStats.mockReturnValue(new Promise(() => {}))

    renderPage()

    expect(document.querySelectorAll('.ant-skeleton').length).toBeGreaterThan(0)
  })

  test('offers retry after a summary request fails', async () => {
    mockedListPlugins.mockRejectedValueOnce(new Error('network down')).mockResolvedValueOnce({ items: [] })
    mockedListPluginBuilds.mockResolvedValue({ items: [] })
    mockedListJobs.mockResolvedValue({ items: [], total: 0 })
    mockedGetJobStats.mockResolvedValue({ days: 14, buckets: [] })

    const { user } = renderPage() as ReturnType<typeof render> & { user?: never }
    expect(await screen.findByRole('alert')).toHaveTextContent('network down')
    await import('@testing-library/user-event').then(async ({ default: userEvent }) => {
      await userEvent.setup().click(screen.getByRole('button', { name: '重试' }))
    })
    await waitFor(() => expect(mockedListPlugins).toHaveBeenCalledTimes(2))
    expect(user).toBeUndefined()
  })
})
