import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, test, vi } from 'vitest'
import NewJobPage from './NewJobPage'
import { createJob } from '../api/jobs'
import { getPlugin, listPlugins, listPluginBuilds } from '../api/plugins'
import type { PluginDetail } from '../types/api'

const navigate = vi.fn()

vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => navigate,
}))

vi.mock('../api/jobs', () => ({ createJob: vi.fn() }))
vi.mock('../api/plugins', () => ({
  listPlugins: vi.fn(),
  getPlugin: vi.fn(),
  listPluginBuilds: vi.fn(),
}))

const mockedListPlugins = vi.mocked(listPlugins)
const mockedGetPlugin = vi.mocked(getPlugin)
const mockedListBuilds = vi.mocked(listPluginBuilds)
const mockedCreateJob = vi.mocked(createJob)

const pluginDetail: PluginDetail = {
  id: 'nc_to_shp',
  name: 'NC 转 Shapefile',
  description: null,
  category: 'gis',
  latest_version: '1.0.0',
  author: null,
  versions: [
    {
      version: '1.0.0',
      spec_version: '1.0',
      sdk_version: '1.0',
      source_sha256: 'c'.repeat(64),
      status: 'ENABLED',
      manifest: {
        spec_version: '1.0',
        plugin: { id: 'nc_to_shp', name: 'NC 转 Shapefile', version: '1.0.0', tags: [] },
        sdk: { version: '1.0' },
        runtime: { type: 'process', python: { version: '3.12' } },
        entrypoint: { module: 'nc_to_shp_plugin.main', function: 'run' },
        parameters: [
          {
            name: 'group_name',
            label: 'HDF5 Group',
            type: 'string',
            required: false,
            default: '1',
          },
        ],
        inputs: [
          { name: 'source_nc', label: 'NC 文件', type: 'file', required: true, extensions: ['.nc'] },
        ],
        outputs: [],
        execution: { timeout: 3600, concurrency: 1 },
        environment_variables: { required: [] },
        healthcheck: { enabled: true, type: 'import' },
      },
    },
  ],
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <NewJobPage />
    </QueryClientProvider>,
  )
}

describe('NewJobPage', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    window.localStorage.clear()
    navigate.mockReset()
    mockedListPlugins.mockResolvedValue({
      items: [{ id: 'nc_to_shp', name: 'NC 转 Shapefile', description: null, category: 'gis', latest_version: '1.0.0' }],
    })
    mockedGetPlugin.mockResolvedValue(pluginDetail)
    mockedListBuilds.mockResolvedValue({
      items: [
        {
          build_id: 'build-docker',
          plugin_id: 'nc_to_shp',
          version: '1.0.0',
          runtime_type: 'docker',
          target_os: 'linux',
          target_arch: 'amd64',
          status: 'ENABLED',
          package_sha256: 'a'.repeat(64),
          runtime_fingerprint: 'b'.repeat(64),
          error_summary: null,
        },
      ],
    })
  })

  async function selectPlugin(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByText('NC 转 Shapefile（nc_to_shp）'))
  }

  test('renders manifest-driven form with defaults and runtime from ENABLED builds', async () => {
    const user = userEvent.setup()
    renderPage()
    await selectPlugin(user)

    expect(await screen.findByLabelText(/NC 文件/)).toBeInTheDocument()
    const groupInput = await screen.findByLabelText('HDF5 Group')
    expect(groupInput).toHaveValue('1')
    expect(screen.getByRole('radio', { name: 'docker' })).toBeInTheDocument()
    expect(screen.getByTestId('request-preview')).toHaveTextContent('"runtime_type": "docker"')
    expect(screen.getByRole('button', { name: /创建任务/ })).toBeEnabled()
  })

  test('submits the canonical request and navigates to the job detail', async () => {
    mockedCreateJob.mockResolvedValue({
      job_id: 'job-1',
      plugin_id: 'nc_to_shp',
      version: '1.0.0',
      build_id: 'build-docker',
      runtime_type: 'docker',
      status: 'PENDING',
      cancel_requested: false,
      error_summary: null,
      created_at: '2026-09-13T09:00:00Z',
      started_at: null,
      finished_at: null,
    })

    const user = userEvent.setup()
    renderPage()
    await selectPlugin(user)

    await user.type(await screen.findByLabelText(/NC 文件/), 'file_123')
    await user.click(screen.getByRole('button', { name: /创建任务/ }))

    await waitFor(() => expect(mockedCreateJob).toHaveBeenCalledTimes(1))
    expect(mockedCreateJob.mock.calls[0][0]).toMatchObject({
      plugin_id: 'nc_to_shp',
      version: '1.0.0',
      runtime_type: 'docker',
      inputs: { source_nc: 'file_123' },
    })
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/jobs/job-1'))
  })

  test('blocks submission when a required input is missing', async () => {
    const user = userEvent.setup()
    renderPage()
    await selectPlugin(user)

    await screen.findByLabelText(/NC 文件/)
    await user.click(screen.getByRole('button', { name: /创建任务/ }))

    expect(await screen.findByText(/必填/)).toBeInTheDocument()
    expect(mockedCreateJob).not.toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()
  }, 15000)

  test('JSON mode validates input and shows the request preview', async () => {
    const user = userEvent.setup()
    renderPage()
    await selectPlugin(user)
    await screen.findByLabelText(/NC 文件/)

    await user.click(screen.getByText('JSON 模式'))
    const jsonArea = await screen.findByLabelText('JSON 请求')
    fireEvent.change(jsonArea, { target: { value: '{bad json' } })
    expect(await screen.findByText('JSON 格式无效')).toBeInTheDocument()

    fireEvent.change(
      jsonArea,
      {
        target: {
          value:
            '{"plugin_id":"nc_to_shp","version":"1.0.0","runtime_type":"docker","inputs":{"source_nc":"file_9"},"params":{}}',
        },
      },
    )
    expect(screen.getByText(/"source_nc": "file_9"/)).toBeInTheDocument()
  }, 15000)
})
