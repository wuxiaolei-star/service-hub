import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, test, vi } from 'vitest'
import JobsPage from './JobsPage'
import { listJobs, rerunJob } from '../api/jobs'
import type { Job } from '../types/api'

const navigate = vi.fn()

vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => navigate,
}))

vi.mock('../api/jobs', () => ({ listJobs: vi.fn(), rerunJob: vi.fn() }))

const mockedListJobs = vi.mocked(listJobs)
const mockedRerunJob = vi.mocked(rerunJob)

const job = (overrides: Partial<Job>): Job => ({
  job_id: 'job-1',
  plugin_id: 'nc_to_shp',
  version: '1.0.0',
  build_id: 'build-1',
  runtime_type: 'docker',
  status: 'SUCCESS',
  cancel_requested: false,
  error_summary: null,
  created_at: '2026-09-13T09:00:00Z',
  started_at: '2026-09-13T09:00:05Z',
  finished_at: '2026-09-13T09:01:35Z',
  replayed_from: null,
  ...overrides,
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <JobsPage />
    </QueryClientProvider>,
  )
}

describe('JobsPage', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    navigate.mockReset()
  })

  test('renders jobs with statuses and durations', async () => {
    mockedListJobs.mockResolvedValue({
      items: [
        job({ job_id: 'job-running', status: 'RUNNING', started_at: '2026-09-13T09:00:05Z', finished_at: null }),
        job({ job_id: 'job-done' }),
      ],
      total: 2,
    })

    renderPage()

    expect(await screen.findByText('job-running')).toBeInTheDocument()
    expect(screen.getByText('job-done')).toBeInTheDocument()
    expect(screen.getByLabelText('状态：运行中')).toBeInTheDocument()
    expect(screen.getByLabelText('状态：成功')).toBeInTheDocument()
    expect(screen.getAllByText('1 分 30 秒').length).toBeGreaterThan(0)
    expect(screen.getByText(/按状态与插件筛选/)).toBeInTheDocument()
    expect(mockedListJobs).toHaveBeenCalledWith({ limit: 20, offset: 0 })
  })

  test('navigates to the job detail from the row action', async () => {
    mockedListJobs.mockResolvedValue({ items: [job({})], total: 1 })
    const user = userEvent.setup()

    renderPage()
    await user.click(await screen.findByRole('button', { name: /查\s*看/ }))

    expect(navigate).toHaveBeenCalledWith('/jobs/job-1')
  })

  test('refetches with status filters when the segmented control changes', async () => {
    mockedListJobs.mockResolvedValue({ items: [], total: 0 })
    const user = userEvent.setup()

    renderPage()
    await screen.findByText('暂无任务')

    await user.click(screen.getByText('失败', { selector: '.ant-segmented-item-label' }))

    await waitFor(() =>
      expect(mockedListJobs).toHaveBeenLastCalledWith({
        statuses: ['FAILED', 'TIMED_OUT'],
        limit: 20,
        offset: 0,
      }),
    )
  })

  test('requests the next page with an offset when pagination changes', async () => {
    mockedListJobs.mockResolvedValue({ items: [job({})], total: 45 })
    const user = userEvent.setup()

    renderPage()
    await screen.findByText('job-1')

    await user.click(screen.getByTitle('2'))

    await waitFor(() =>
      expect(mockedListJobs).toHaveBeenLastCalledWith({ limit: 20, offset: 20 }),
    )
  })

  test('reruns a failed job and navigates to the new job detail', async () => {
    mockedListJobs.mockResolvedValue({
      items: [job({ job_id: 'job-failed', status: 'FAILED', error_summary: '插件执行失败' })],
      total: 1,
    })
    mockedRerunJob.mockResolvedValue(
      job({
        job_id: 'job-new',
        status: 'PENDING',
        started_at: null,
        finished_at: null,
        replayed_from: 'job-failed',
      }),
    )
    const user = userEvent.setup()

    renderPage()
    await user.click(await screen.findByRole('button', { name: /重\s*跑/ }))

    await waitFor(() => expect(mockedRerunJob).toHaveBeenCalledWith('job-failed'))
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/jobs/job-new'))
  })
})
