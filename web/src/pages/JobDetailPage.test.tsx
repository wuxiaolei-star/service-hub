import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, test, vi } from 'vitest'
import JobDetailPage from './JobDetailPage'
import { cancelJob, getJob, getJobLogs, getJobOutputs } from '../api/jobs'
import { downloadFile } from '../api/files'
import type { Job } from '../types/api'

const navigate = vi.fn()

vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useParams: () => ({ jobId: 'job-1' }),
  useNavigate: () => navigate,
}))

vi.mock('../api/jobs', () => ({
  getJob: vi.fn(),
  cancelJob: vi.fn(),
  getJobLogs: vi.fn(),
  getJobOutputs: vi.fn(),
}))
vi.mock('../api/files', () => ({ downloadFile: vi.fn() }))

const mockedGetJob = vi.mocked(getJob)
const mockedCancelJob = vi.mocked(cancelJob)
const mockedGetJobLogs = vi.mocked(getJobLogs)
const mockedGetJobOutputs = vi.mocked(getJobOutputs)
const mockedDownloadFile = vi.mocked(downloadFile)

const runningJob: Job = {
  job_id: 'job-1',
  plugin_id: 'nc_to_shp',
  version: '1.0.0',
  build_id: 'build-1',
  runtime_type: 'docker',
  status: 'RUNNING',
  cancel_requested: false,
  error_summary: null,
  created_at: '2026-09-13T09:00:00Z',
  started_at: '2026-09-13T09:00:05Z',
  finished_at: null,
}

const failedJob: Job = { ...runningJob, status: 'FAILED', error_summary: '插件执行失败' }

const outputRecord = {
  file_id: 'file_out',
  name: 'result.zip',
  size: 2048,
  sha256: 'a'.repeat(64),
  extension: '.zip',
  mime_type: 'application/zip',
  status: 'AVAILABLE' as const,
  created_at: '2026-09-13T09:02:00Z',
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <JobDetailPage />
    </QueryClientProvider>,
  )
}

describe('JobDetailPage', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    mockedGetJobLogs.mockResolvedValue({
      items: [{ type: 'log', level: 'INFO', message: 'job started' }],
      next_cursor: null,
    })
  })

  test('shows job details with polling and logs while running', async () => {
    mockedGetJob.mockResolvedValue(runningJob)

    renderPage()

    expect(await screen.findByText('nc_to_shp')).toBeInTheDocument()
    expect(screen.getByLabelText('状态：运行中')).toBeInTheDocument()
    expect(await screen.findByText(/job started/)).toBeInTheDocument()
    expect(mockedGetJobOutputs).not.toHaveBeenCalled()
  })

  test('shows the failure summary for failed jobs', async () => {
    mockedGetJob.mockResolvedValue(failedJob)

    renderPage()

    expect(await screen.findByText('插件执行失败')).toBeInTheDocument()
    expect(screen.getByLabelText('状态：失败')).toBeInTheDocument()
  })

  test('cancels a running job after confirmation', async () => {
    mockedGetJob.mockResolvedValue(runningJob)
    mockedCancelJob.mockResolvedValue({
      job_id: 'job-1',
      status: 'CANCEL_REQUESTED',
      cancel_requested: true,
    })
    const user = userEvent.setup()

    renderPage()
    await screen.findByText('nc_to_shp')
    await user.click(screen.getByRole('button', { name: /取消任务/ }))
    await user.click(await screen.findByRole('button', { name: /确\s*定/ }))

    await waitFor(() => expect(mockedCancelJob).toHaveBeenCalledWith('job-1'))
  }, 15000)

  test('fetches outputs only after success and downloads them safely', async () => {
    mockedGetJob.mockResolvedValue({ ...runningJob, status: 'SUCCESS', finished_at: '2026-09-13T09:01:35Z' })
    mockedGetJobOutputs.mockResolvedValue({ items: [outputRecord] })
    mockedDownloadFile.mockResolvedValue(new Blob(['zip']))
    const createObjectURL = vi.fn(() => 'blob:mock-url')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const user = userEvent.setup()

    renderPage()

    expect(await screen.findByText('result.zip')).toBeInTheDocument()
    expect(mockedGetJobOutputs).toHaveBeenCalledTimes(1)

    await user.click(screen.getByText('result.zip'))
    await waitFor(() => expect(mockedDownloadFile).toHaveBeenCalledWith('file_out'))
    await waitFor(() => expect(anchorClick).toHaveBeenCalledTimes(1))
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')
    anchorClick.mockRestore()
    vi.unstubAllGlobals()
  }, 15000)
})
