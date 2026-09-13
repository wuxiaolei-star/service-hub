import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, test, vi } from 'vitest'
import JobDetailPage from './JobDetailPage'
import { cancelJob, getJob, getJobLogs, getJobOutputs } from '../api/jobs'
import { listJobCallbacks } from '../api/automation'
import type { CallbackRow } from '../api/automation'
import { downloadFile } from '../api/files'
import { setEventSourceFactory } from '../hooks/useJobLogStream'
import type { StreamEventSource } from '../hooks/useJobLogStream'
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
vi.mock('../api/automation', () => ({ listJobCallbacks: vi.fn() }))
vi.mock('../api/files', () => ({ downloadFile: vi.fn() }))

const mockedGetJob = vi.mocked(getJob)
const mockedCancelJob = vi.mocked(cancelJob)
const mockedGetJobLogs = vi.mocked(getJobLogs)
const mockedGetJobOutputs = vi.mocked(getJobOutputs)
const mockedListJobCallbacks = vi.mocked(listJobCallbacks)
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

const callbackRow: CallbackRow = {
  id: 1,
  url: 'https://example.com/hooks/done',
  state: 'EXHAUSTED',
  attempts: 5,
  last_status_code: 500,
  last_error: 'HTTP 500',
  next_attempt_at: null,
}

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

class FakeStreamSource implements StreamEventSource {
  onmessage: ((event: { data: unknown }) => void) | null = null
  onerror: ((event: unknown) => void) | null = null
  closed = false

  addEventListener(): void {}

  close(): void {
    this.closed = true
  }

  emit(data: string): void {
    this.onmessage?.({ data })
  }
}

describe('JobDetailPage', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    mockedGetJobLogs.mockResolvedValue({
      items: [{ type: 'log', level: 'INFO', message: 'job started' }],
      next_cursor: null,
    })
    mockedListJobCallbacks.mockResolvedValue({ items: [] })
  })

  afterEach(() => {
    setEventSourceFactory(null)
  })

  test('shows job details with polling and logs while running', async () => {
    mockedGetJob.mockResolvedValue(runningJob)

    renderPage()

    expect(await screen.findByText('nc_to_shp')).toBeInTheDocument()
    expect(screen.getByLabelText('状态：运行中')).toBeInTheDocument()
    expect(await screen.findByText(/job started/)).toBeInTheDocument()
    expect(mockedGetJobOutputs).not.toHaveBeenCalled()
  })

  test('prefers the SSE log stream while the job is active', async () => {
    const sources: FakeStreamSource[] = []
    let streamUrl = ''
    setEventSourceFactory((url: string) => {
      streamUrl = url
      const source = new FakeStreamSource()
      sources.push(source)
      return source
    })
    mockedGetJob.mockResolvedValue(runningJob)

    renderPage()
    await screen.findByText('nc_to_shp')

    expect(streamUrl).toBe('/api/v1/jobs/job-1/logs/stream')
    act(() => {
      sources[0].emit('event: log\ndata: {"type":"log","message":"streamed line"}\n\n')
    })

    expect(await screen.findByText(/streamed line/)).toBeInTheDocument()
    expect(mockedGetJobLogs).not.toHaveBeenCalled()
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

  test('shows webhook callbacks with state tags', async () => {
    mockedGetJob.mockResolvedValue(runningJob)
    mockedListJobCallbacks.mockResolvedValue({ items: [callbackRow] })

    renderPage()

    expect(await screen.findByText('Webhook 回调')).toBeInTheDocument()
    expect(await screen.findByText('https://example.com/hooks/done')).toBeInTheDocument()
    expect(screen.getByText('EXHAUSTED')).toBeInTheDocument()
    expect(screen.getByText('5')).toBeInTheDocument()
    expect(screen.getByText('500')).toBeInTheDocument()
    expect(mockedListJobCallbacks).toHaveBeenCalledWith('job-1')
  }, 15000)

  test('shows a no-callbacks placeholder when the endpoint returns 404', async () => {
    mockedGetJob.mockResolvedValue(runningJob)
    mockedListJobCallbacks.mockRejectedValue({
      code: 'NOT_FOUND',
      message: 'no callbacks',
      status: 404,
    })

    renderPage()

    expect(await screen.findByText('无回调注册')).toBeInTheDocument()
  }, 15000)

  test('shows a no-callbacks placeholder when the endpoint returns empty items', async () => {
    mockedGetJob.mockResolvedValue(runningJob)

    renderPage()

    expect(await screen.findByText('无回调注册')).toBeInTheDocument()
  }, 15000)
})
