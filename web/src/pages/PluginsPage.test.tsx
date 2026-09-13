import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import PluginsPage from './PluginsPage'
import {
  disablePluginBuild,
  enablePluginBuild,
  getPlugin,
  getPluginBuild,
  installPlugin,
  listPluginBuilds,
  listPlugins,
} from '../api/plugins'
import type { PluginDetail } from '../types/api'

vi.mock('../api/plugins', () => ({
  installPlugin: vi.fn(),
  listPlugins: vi.fn(),
  getPlugin: vi.fn(),
  listPluginBuilds: vi.fn(),
  getPluginBuild: vi.fn(),
  enablePluginBuild: vi.fn(),
  disablePluginBuild: vi.fn(),
}))

const mockedInstall = vi.mocked(installPlugin)
const mockedListPlugins = vi.mocked(listPlugins)
const mockedGetPlugin = vi.mocked(getPlugin)
const mockedListBuilds = vi.mocked(listPluginBuilds)
const mockedGetBuild = vi.mocked(getPluginBuild)
const mockedEnable = vi.mocked(enablePluginBuild)
const mockedDisable = vi.mocked(disablePluginBuild)

const pluginSummary = {
  id: 'nc_to_shp',
  name: 'NC 转 Shapefile',
  description: '水动力结果转换',
  category: 'gis',
  latest_version: '1.0.0',
}

const buildDetail = (overrides: Partial<Record<string, unknown>> = {}) => ({
  build_id: 'build-1',
  plugin_id: 'nc_to_shp',
  version: '1.0.0',
  runtime_type: 'docker' as const,
  target_os: 'linux',
  target_arch: 'amd64',
  status: 'READY' as const,
  package_sha256: 'a'.repeat(64),
  runtime_fingerprint: 'b'.repeat(64),
  error_summary: null,
  ...overrides,
})

const pluginDetail: PluginDetail = {
  ...pluginSummary,
  author: 'Lane',
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
          { name: 'group_name', label: 'HDF5 Group', type: 'string', required: false, default: '1' },
        ],
        inputs: [],
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
      <PluginsPage />
    </QueryClientProvider>,
  )
}

function makePackageFile(): File {
  return new File(['pypkg-bytes'], 'nc_to_shp.pypkg', { type: 'application/octet-stream' })
}

describe('PluginsPage', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    mockedListPlugins.mockResolvedValue({ items: [pluginSummary] })
    mockedListBuilds.mockResolvedValue({ items: [buildDetail()] })
    mockedGetPlugin.mockResolvedValue(pluginDetail)
  })

  test('installs a package, polls the build to READY, and enables it', async () => {
    const user = userEvent.setup()
    mockedInstall.mockImplementation(async (_file, onProgress) => {
      onProgress?.(100)
      return buildDetail({ status: 'INSTALLING' })
    })
    mockedGetBuild.mockResolvedValue(buildDetail({ status: 'READY' }))
    mockedEnable.mockResolvedValue(buildDetail({ status: 'ENABLED' }))

    renderPage()
    await screen.findByText('nc_to_shp')

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await user.upload(input, makePackageFile())

    expect(await screen.findByText('build-1')).toBeInTheDocument()
    expect(await screen.findByLabelText('状态：就绪')).toBeInTheDocument()
    expect(mockedGetBuild).toHaveBeenCalledWith('build-1')

    await user.click(await screen.findByRole('button', { name: /启\s*用/ }))
    expect(await screen.findByLabelText('状态：已启用')).toBeInTheDocument()
    await waitFor(() => expect(mockedEnable).toHaveBeenCalledWith('build-1'))
  })

  test('shows a failed build with its error summary', async () => {
    mockedInstall.mockImplementation(async (_file, onProgress) => {
      onProgress?.(100)
      return buildDetail({ status: 'INSTALLING' })
    })
    mockedGetBuild.mockResolvedValue(
      buildDetail({ status: 'FAILED', error_summary: '包校验失败' }),
    )

    renderPage()
    await screen.findByText('nc_to_shp')

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    const user = userEvent.setup()
    await user.upload(input, makePackageFile())

    expect(await screen.findByText('包校验失败')).toBeInTheDocument()
    expect(screen.getByLabelText('状态：失败')).toBeInTheDocument()
  })

  test('opens the detail drawer with manifest parameters and disables an enabled build', async () => {
    const user = userEvent.setup()
    // Call order: drawer builds query first, then refetch after disable.
    mockedListBuilds
      .mockResolvedValueOnce({ items: [buildDetail({ status: 'ENABLED' })] })
      .mockResolvedValue({ items: [buildDetail({ status: 'READY' })] })
    mockedDisable.mockResolvedValue(buildDetail({ status: 'READY' }))

    renderPage()
    await screen.findByText('nc_to_shp')

    await user.click(screen.getByRole('button', { name: '查看详情' }))
    const drawer = await screen.findByRole('dialog')
    expect(await within(drawer).findByText('HDF5 Group')).toBeInTheDocument()
    expect(await within(drawer).findByLabelText('状态：已启用')).toBeInTheDocument()

    await user.click(within(drawer).getByRole('button', { name: /停\s*用/ }))
    await user.click(await screen.findByRole('button', { name: /确\s*定/ }))
    await waitFor(() => expect(mockedDisable).toHaveBeenCalledWith('build-1'))
    expect(await screen.findByLabelText('状态：就绪')).toBeInTheDocument()
  }, 15000)

  test('reports install failure through the shared error alert', async () => {
    mockedInstall.mockRejectedValue({
      code: 'PACKAGE_INVALID',
      message: '插件包无效',
    })

    renderPage()
    await screen.findByText('nc_to_shp')

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    const user = userEvent.setup()
    await user.upload(input, makePackageFile())

    expect(await screen.findByText('插件包无效')).toBeInTheDocument()
    expect(screen.getByText(/PACKAGE_INVALID/)).toBeInTheDocument()
  })
})
