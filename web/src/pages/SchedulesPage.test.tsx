import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, test, vi } from 'vitest'
import SchedulesPage from './SchedulesPage'
import { createSchedule, deleteSchedule, disableSchedule, listSchedules } from '../api/automation'
import type { ScheduleRow } from '../api/automation'

vi.mock('../api/automation', () => ({
  createSchedule: vi.fn(),
  listSchedules: vi.fn(),
  enableSchedule: vi.fn(),
  disableSchedule: vi.fn(),
  deleteSchedule: vi.fn(),
}))

const mockedListSchedules = vi.mocked(listSchedules)
const mockedCreateSchedule = vi.mocked(createSchedule)
const mockedDisableSchedule = vi.mocked(disableSchedule)
const mockedDeleteSchedule = vi.mocked(deleteSchedule)

const enabledRow: ScheduleRow = {
  id: 1,
  name: '每夜裁剪',
  plugin_id: 'nc_to_shp',
  version: '1.0.0',
  runtime_type: 'docker',
  interval_minutes: 60,
  enabled: true,
  next_run_at: '2026-09-13T18:00:00Z',
  last_job_id: 'job-9',
}

const disabledRow: ScheduleRow = {
  ...enabledRow,
  id: 2,
  name: '历史归档',
  interval_minutes: 120,
  enabled: false,
  next_run_at: null,
  last_job_id: null,
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <SchedulesPage />
    </QueryClientProvider>,
  )
}

async function findEnabledRowActions() {
  const row = (await screen.findByText('每夜裁剪')).closest('tr')
  expect(row).not.toBeNull()
  return within(row as HTMLElement)
}

describe('SchedulesPage', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    mockedListSchedules.mockResolvedValue({ items: [enabledRow, disabledRow] })
  })

  test('renders schedule rows with status and interval', async () => {
    renderPage()

    expect(await screen.findByText('每夜裁剪')).toBeInTheDocument()
    expect(screen.getByText('历史归档')).toBeInTheDocument()
    expect(screen.getByText('启用中')).toBeInTheDocument()
    expect(screen.getByText('已停用')).toBeInTheDocument()
    expect(screen.getByText('60')).toBeInTheDocument()
    expect(screen.getByText('120')).toBeInTheDocument()
    expect(screen.getByText('job-9')).toBeInTheDocument()
  })

  test('creates a schedule through the modal form', async () => {
    mockedCreateSchedule.mockResolvedValue(enabledRow)
    const user = userEvent.setup()

    renderPage()
    await screen.findByText('每夜裁剪')

    await user.click(screen.getByRole('button', { name: /新建定时任务/ }))
    await user.type(await screen.findByLabelText('名称'), '每夜裁剪')
    await user.type(screen.getByLabelText('插件 ID'), 'nc_to_shp')
    await user.type(screen.getByLabelText('版本'), '1.0.0')
    const intervalInput = screen.getByLabelText(/间隔（分钟）/)
    fireEvent.change(intervalInput, { target: { value: '30' } })
    await user.click(screen.getByRole('button', { name: /创\s*建/ }))

    await waitFor(() => expect(mockedCreateSchedule).toHaveBeenCalledTimes(1))
    expect(mockedCreateSchedule.mock.calls[0][0]).toMatchObject({
      name: '每夜裁剪',
      plugin_id: 'nc_to_shp',
      version: '1.0.0',
      runtime_type: 'docker',
      interval_minutes: 30,
      inputs: {},
      params: {},
    })
  }, 15000)

  test('deletes a schedule after confirmation', async () => {
    mockedDeleteSchedule.mockResolvedValue(undefined)
    const user = userEvent.setup()

    renderPage()
    const row = await findEnabledRowActions()

    await user.click(row.getByRole('button', { name: /删\s*除/ }))
    await user.click(await screen.findByRole('button', { name: /确\s*定/ }))

    await waitFor(() => expect(mockedDeleteSchedule).toHaveBeenCalledWith(1))
  }, 15000)

  test('disables an enabled schedule from the row action', async () => {
    mockedDisableSchedule.mockResolvedValue({ ...enabledRow, enabled: false })
    const user = userEvent.setup()

    renderPage()
    const row = await findEnabledRowActions()

    await user.click(row.getByRole('button', { name: /停\s*用/ }))

    await waitFor(() => expect(mockedDisableSchedule).toHaveBeenCalledWith(1))
  }, 15000)

  test('shows a 403 delete failure through the error alert', async () => {
    mockedDeleteSchedule.mockRejectedValue({
      code: 'PERMISSION_DENIED',
      message: '无权删除定时任务',
      status: 403,
    })
    const user = userEvent.setup()

    renderPage()
    const row = await findEnabledRowActions()

    await user.click(row.getByRole('button', { name: /删\s*除/ }))
    await user.click(await screen.findByRole('button', { name: /确\s*定/ }))

    expect(await screen.findByText('无权删除定时任务')).toBeInTheDocument()
  }, 15000)
})
