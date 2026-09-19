import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, test, vi } from 'vitest'
import JobLogViewer from './JobLogViewer'
import { getJobLogs } from '../api/jobs'
import { saveBlob } from '../utils/download'
import type { JobLogLine } from '../hooks/useJobLogs'

vi.mock('../api/jobs', () => ({ getJobLogs: vi.fn() }))
vi.mock('../utils/download', () => ({ saveBlob: vi.fn() }))

const mockedGetJobLogs = vi.mocked(getJobLogs)
const mockedSaveBlob = vi.mocked(saveBlob)

const events: JobLogLine[] = [
  { type: 'log', level: 'INFO', message: 'job started' },
  { type: 'log', level: 'ERROR', message: 'boom failed' },
  { type: 'log', level: 'WARNING', message: 'slow step' },
  { type: 'progress', message: '转换中', percent: 60 },
]

describe('JobLogViewer', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  test('colors error and warning lines with dedicated classes', () => {
    render(<JobLogViewer events={events} isLoading={false} />)

    expect(screen.getByText(/\[ERROR\] boom failed/, { selector: 'span.log-line-error' })).toBeInTheDocument()
    expect(screen.getByText(/\[WARNING\] slow step/, { selector: 'span.log-line-warning' })).toBeInTheDocument()
    expect(screen.getByText(/\[INFO\] job started/)).toBeInTheDocument()
    expect(screen.getByText(/\[进度 60%\] 转换中/)).toBeInTheDocument()
  })

  test('filters rendered lines by keyword', async () => {
    const user = userEvent.setup()
    render(<JobLogViewer events={events} isLoading={false} />)

    await user.type(screen.getByLabelText('日志关键字过滤'), 'BOOM')

    expect(screen.getByText(/boom failed/)).toBeInTheDocument()
    expect(screen.queryByText(/job started/)).not.toBeInTheDocument()
    expect(screen.queryByText(/slow step/)).not.toBeInTheDocument()
    expect(screen.queryByText(/转换中/)).not.toBeInTheDocument()
  })

  test('shows an empty hint when no line matches the keyword', async () => {
    const user = userEvent.setup()
    render(<JobLogViewer events={events} isLoading={false} />)

    await user.type(screen.getByLabelText('日志关键字过滤'), 'missing')

    expect(screen.getByText('暂无匹配日志')).toBeInTheDocument()
  })

  test('downloads the full log across cursor pages via saveBlob', async () => {
    mockedGetJobLogs
      .mockResolvedValueOnce({
        items: [{ type: 'log', level: 'INFO', message: 'page one' }],
        next_cursor: 1,
      })
      .mockResolvedValueOnce({
        items: [{ type: 'log', level: 'ERROR', message: 'page two' }],
        next_cursor: null,
      })
    const user = userEvent.setup()

    render(<JobLogViewer events={[]} isLoading={false} jobKey="job-1" />)
    await user.click(screen.getByRole('button', { name: /下\s*载\s*日\s*志/ }))

    await waitFor(() => expect(mockedSaveBlob).toHaveBeenCalledTimes(1))
    const [blob, filename] = mockedSaveBlob.mock.calls[0]
    expect(filename).toBe('job-1.log')
    // jsdom's Blob lacks .text(), so read through FileReader instead.
    const text = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.onerror = () => reject(reader.error)
      reader.readAsText(blob)
    })
    expect(text).toContain('[INFO] page one')
    expect(text).toContain('[ERROR] page two')
    expect(mockedGetJobLogs).toHaveBeenNthCalledWith(1, 'job-1', 0, 200)
    expect(mockedGetJobLogs).toHaveBeenNthCalledWith(2, 'job-1', 1, 200)
  })
})
