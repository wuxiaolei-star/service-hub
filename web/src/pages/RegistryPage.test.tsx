import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import RegistryPage from './RegistryPage'
import { downloadRegistryBuild, listRegistryPlugins } from '../api/registry'
import type { RegistryBuildItem } from '../api/registry'

vi.mock('../api/registry', () => ({
  listRegistryPlugins: vi.fn(),
  downloadRegistryBuild: vi.fn(),
  findFileBySha256: vi.fn(),
}))

const mockedList = vi.mocked(listRegistryPlugins)
const mockedDownload = vi.mocked(downloadRegistryBuild)

const item: RegistryBuildItem = {
  plugin_id: 'nc-to-shp',
  plugin_name: 'NC 转 Shapefile',
  version: '1.0.0',
  build_key: 'bld-1',
  runtime_type: 'docker',
  target_arch: 'amd64',
  status: 'ENABLED',
  package_sha256: 'a'.repeat(64),
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <RegistryPage />
    </QueryClientProvider>,
  )
}

describe('RegistryPage', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    mockedList.mockResolvedValue({ items: [item] })
  })

  test('renders registry build rows with status tags', async () => {
    renderPage()

    expect(await screen.findByText('NC 转 Shapefile')).toBeInTheDocument()
    expect(screen.getByText('nc-to-shp')).toBeInTheDocument()
    expect(screen.getByText('1.0.0')).toBeInTheDocument()
    expect(screen.getByText('docker')).toBeInTheDocument()
    expect(screen.getByText('amd64')).toBeInTheDocument()
    expect(screen.getByLabelText('状态：已启用')).toBeInTheDocument()
  })

  test('downloads a build package as a .pypkg blob', async () => {
    mockedDownload.mockResolvedValue(new Blob(['pkg']))
    const createObjectURL = vi.fn(() => 'blob:mock-url')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    const downloadedNames: string[] = []
    const anchorClick = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function mockClick(this: HTMLAnchorElement) {
        downloadedNames.push(this.download)
      })

    renderPage()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /下\s*载/ }))

    await waitFor(() => expect(mockedDownload).toHaveBeenCalledWith('bld-1'))
    await waitFor(() => expect(anchorClick).toHaveBeenCalledTimes(1))
    expect(downloadedNames).toEqual(['nc-to-shp-1.0.0-docker.pypkg'])
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')
    anchorClick.mockRestore()
    vi.unstubAllGlobals()
  })

  test('shows a hub error when the download is forbidden', async () => {
    mockedDownload.mockRejectedValue({ code: 'FORBIDDEN', message: '需要发布者角色', status: 403 })

    renderPage()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /下\s*载/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('需要发布者角色')
  })
})
