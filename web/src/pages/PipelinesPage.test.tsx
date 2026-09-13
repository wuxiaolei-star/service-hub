import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, test, vi } from 'vitest'
import PipelinesPage from './PipelinesPage'
import { createPipeline, executePipeline, listPipelineRuns, listPipelines } from '../api/automation'
import type { PipelineRow, PipelineRunRow } from '../api/automation'

vi.mock('../api/automation', () => ({
  createPipeline: vi.fn(),
  listPipelines: vi.fn(),
  executePipeline: vi.fn(),
  listPipelineRuns: vi.fn(),
}))

const mockedListPipelines = vi.mocked(listPipelines)
const mockedExecutePipeline = vi.mocked(executePipeline)
const mockedListPipelineRuns = vi.mocked(listPipelineRuns)
const mockedCreatePipeline = vi.mocked(createPipeline)

const pipelineRow: PipelineRow = {
  id: 1,
  name: '裁剪转瓦片',
  steps: [
    {
      plugin_id: 'nc_to_shp',
      version: '1.0.0',
      runtime_type: 'docker',
      inputs: {},
      params: {},
    },
    {
      plugin_id: 'shp_to_tiles',
      version: '2.0.0',
      runtime_type: 'docker',
      inputs: {},
      params: {},
    },
  ],
}

const runRow: PipelineRunRow = {
  id: 1,
  pipeline_id: 1,
  state: 'SUCCEEDED',
  current_step: 2,
  created_at: '2026-09-13T10:00:00Z',
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <PipelinesPage />
    </QueryClientProvider>,
  )
}

describe('PipelinesPage', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    mockedListPipelines.mockResolvedValue({ items: [pipelineRow] })
    mockedListPipelineRuns.mockResolvedValue({ items: [runRow] })
  })

  test('renders pipelines with step counts', async () => {
    renderPage()

    expect(await screen.findByText('裁剪转瓦片')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  test('expands a row to show filtered pipeline runs with state tags', async () => {
    renderPage()
    await screen.findByText('裁剪转瓦片')

    const expandIcon = document.querySelector<HTMLElement>('.ant-table-row-expand-icon')
    expect(expandIcon).not.toBeNull()
    fireEvent.click(expandIcon!)

    expect(await screen.findByText("1")).toBeInTheDocument()
    expect(screen.getByText('SUCCEEDED')).toBeInTheDocument()
    expect(mockedListPipelineRuns).toHaveBeenCalledWith(1)
  }, 15000)

  test('executes a pipeline and shows the submitted run', async () => {
    mockedExecutePipeline.mockResolvedValue({
      run_id: 'run-2',
      job_id: 'job-2',
      state: 'RUNNING',
    })
    const user = userEvent.setup()

    renderPage()
    await screen.findByText('裁剪转瓦片')

    await user.click(screen.getByRole('button', { name: /执\s*行/ }))

    await waitFor(() => expect(mockedExecutePipeline).toHaveBeenCalledWith(1))
    expect(await screen.findByText(/run_id: run-2/)).toBeInTheDocument()
    expect(screen.getByText(/job_id: job-2/)).toBeInTheDocument()
  }, 15000)

  test('rejects invalid steps JSON with an alert and does not submit', async () => {
    const user = userEvent.setup()

    renderPage()
    await screen.findByText('裁剪转瓦片')

    await user.click(screen.getByRole('button', { name: /新建管道/ }))
    const nameInput = await screen.findByLabelText('名称')
    await user.type(nameInput, '坏管道')
    const jsonArea = screen.getByLabelText('步骤 JSON')
    fireEvent.change(jsonArea, { target: { value: '{bad json' } })
    await user.click(screen.getByRole('button', { name: /创\s*建/ }))

    expect(await screen.findByText('JSON 格式无效')).toBeInTheDocument()
    expect(mockedCreatePipeline).not.toHaveBeenCalled()
  }, 15000)

  test('submits valid steps JSON to create a pipeline', async () => {
    mockedCreatePipeline.mockResolvedValue(pipelineRow)
    const user = userEvent.setup()

    renderPage()
    await screen.findByText('裁剪转瓦片')

    await user.click(screen.getByRole('button', { name: /新建管道/ }))
    const nameInput = await screen.findByLabelText('名称')
    await user.type(nameInput, '新管道')
    const jsonArea = screen.getByLabelText('步骤 JSON')
    fireEvent.change(
      jsonArea,
      {
        target: {
          value:
            '[{"plugin_id":"nc_to_shp","version":"1.0.0","runtime_type":"docker","inputs":{},"params":{}}]',
        },
      },
    )
    await user.click(screen.getByRole('button', { name: /创\s*建/ }))

    await waitFor(() => expect(mockedCreatePipeline).toHaveBeenCalledTimes(1))
    expect(mockedCreatePipeline.mock.calls[0][0]).toMatchObject({
      name: '新管道',
      steps: [
        {
          plugin_id: 'nc_to_shp',
          version: '1.0.0',
          runtime_type: 'docker',
          inputs: {},
          params: {},
        },
      ],
    })
  }, 15000)

  test('shows run states with color tags for running and failed runs', async () => {
    mockedListPipelineRuns.mockResolvedValue({
      items: [
        runRow,
        { ...runRow, id: 3, state: 'RUNNING' },
        { ...runRow, id: 4, state: 'FAILED' },
      ],
    })
    renderPage()
    await screen.findByText('裁剪转瓦片')

    const expandIcon = document.querySelector<HTMLElement>('.ant-table-row-expand-icon')
    fireEvent.click(expandIcon!)

    const succeededRow = (await screen.findByText("1")).closest('tr')
    expect(succeededRow).not.toBeNull()
    expect(within(succeededRow!).getByText('SUCCEEDED')).toBeInTheDocument()
    const runningRow = (await screen.findByText("3")).closest('tr')
    expect(runningRow!.querySelector('.ant-tag-processing')).not.toBeNull()
    const failedRow = (await screen.findByText("4")).closest('tr')
    expect(failedRow!.querySelector('.ant-tag-error')).not.toBeNull()
  }, 15000)
})
