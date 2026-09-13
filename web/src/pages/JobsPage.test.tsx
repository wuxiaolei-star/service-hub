import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, test, vi } from 'vitest'
import JobsPage from './JobsPage'
import { listJobs } from '../api/jobs'
import type { Job } from '../types/api'

const navigate = vi.fn()

vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => navigate,
}))

vi.mock('../api/jobs', () => ({ listJobs: vi.fn() }))

const mockedListJobs = vi.mocked(listJobs)

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

  test('renders the newest 100 jobs with statuses and durations', async () => {
    mockedListJobs.mockResolvedValue({
      items: [
        job({ job_id: 'job-running', status: 'RUNNING', started_at: '2026-09-13T09:00:05Z', finished_at: null }),
        job({ job_id: 'job-done' }),
      ],
    })

    renderPage()

    expect(await screen.findByText('job-running')).toBeInTheDocument()
    expect(screen.getByText('job-done')).toBeInTheDocument()
    expect(screen.getByLabelText('状态：运行中')).toBeInTheDocument()
    expect(screen.getByLabelText('状态：成功')).toBeInTheDocument()
    expect(screen.getAllByText('1 分 30 秒').length).toBeGreaterThan(0)
    expect(screen.getByText(/最近 100 条/)).toBeInTheDocument()
  })

  test('navigates to the job detail from the row action', async () => {
    mockedListJobs.mockResolvedValue({ items: [job({})] })
    const user = userEvent.setup()

    renderPage()
    await user.click(await screen.findByRole('button', { name: /查\s*看/ }))

    expect(navigate).toHaveBeenCalledWith('/jobs/job-1')
  })
})
