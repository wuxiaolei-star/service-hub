import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, test, vi } from 'vitest'
import FilesPage from './FilesPage'
import { downloadFile, getFileMetadata, listFiles, uploadFile } from '../api/files'
import { findFileBySha256 } from '../api/registry'
import type { FileRecord } from '../types/api'

vi.mock('../api/files', () => ({
  uploadFile: vi.fn(),
  listFiles: vi.fn(),
  getFileMetadata: vi.fn(),
  downloadFile: vi.fn(),
}))

vi.mock('../api/registry', () => ({
  listRegistryPlugins: vi.fn(),
  downloadRegistryBuild: vi.fn(),
  findFileBySha256: vi.fn(),
}))

const mockedUpload = vi.mocked(uploadFile)
const mockedListFiles = vi.mocked(listFiles)
const mockedGetMetadata = vi.mocked(getFileMetadata)
const mockedDownload = vi.mocked(downloadFile)
const mockedFindBySha = vi.mocked(findFileBySha256)

const record = (fileId: string, name: string): FileRecord => ({
  file_id: fileId,
  name,
  size: 1024,
  sha256: 'a'.repeat(64),
  extension: '.nc',
  mime_type: 'application/octet-stream',
  status: 'AVAILABLE',
  created_at: '2026-09-13T08:00:00Z',
})

const anchorClick = vi
  .spyOn(HTMLAnchorElement.prototype, 'click')
  .mockImplementation(() => {})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <FilesPage />
    </QueryClientProvider>,
  )
}

describe('FilesPage', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    window.localStorage.clear()
    anchorClick.mockClear()
    mockedListFiles.mockResolvedValue({
      items: [record('file_server', 'server.nc'), record('file_old', 'old.nc')],
    })
  })

  test('uploads a file with progress and lists newest server records', async () => {
    mockedUpload.mockImplementation(async (file, onProgress) => {
      onProgress?.(100)
      return record('file_new', file.name)
    })

    renderPage()
    expect(await screen.findByText('server.nc')).toBeInTheDocument()
    expect(screen.getByText(/服务器最近 100 条/)).toBeInTheDocument()

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    const user = userEvent.setup()
    await user.upload(input, new File(['nc-bytes'], 'sample.nc'))

    expect(await screen.findByText('sample.nc')).toBeInTheDocument()
    expect(mockedUpload).toHaveBeenCalledTimes(1)
  })

  test('looks up an id from recent records when the server list does not have it', async () => {
    renderPage()
    await screen.findByText('server.nc')

    const idInput = screen.getByLabelText('按文件 ID 查找')
    const user = userEvent.setup()
    mockedGetMetadata.mockResolvedValue(record('file_recent', 'recent.nc'))
    await user.type(idInput, 'file_recent')
    await user.click(screen.getByRole('button', { name: /查\s*找/ }))

    expect(await screen.findByText('recent.nc')).toBeInTheDocument()
  })

  test('keeps the recent record visible when metadata lookup fails', async () => {
    renderPage()
    await screen.findByText('server.nc')

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    const user = userEvent.setup()
    mockedUpload.mockResolvedValue(record('file_new', 'uploaded.nc'))
    await user.upload(input, new File(['nc-bytes'], 'uploaded.nc'))
    expect(await screen.findByText('uploaded.nc')).toBeInTheDocument()

    mockedGetMetadata.mockRejectedValue(new Error('gone'))
    await user.clear(screen.getByLabelText('按文件 ID 查找'))
    await user.type(screen.getByLabelText('按文件 ID 查找'), 'file_new')
    await user.click(screen.getByRole('button', { name: /查\s*找/ }))

    expect(await screen.findByText('查找失败，已保留本地记录')).toBeInTheDocument()
    expect(screen.getByText('uploaded.nc')).toBeInTheDocument()
  })

  test('downloads a file with a safe client-side name', async () => {
    renderPage()
    const row = await screen.findByText('server.nc')
    const createObjectURL = vi.fn(() => 'blob:mock-url')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    mockedDownload.mockResolvedValue(new Blob(['nc']))

    const user = userEvent.setup()
    await user.click(row)

    await waitFor(() => expect(mockedDownload).toHaveBeenCalledWith('file_server'))
    await waitFor(() => expect(anchorClick).toHaveBeenCalledTimes(1))
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')
    vi.unstubAllGlobals()
  })

  test('dedupe lookup shows the matched file name and id', async () => {
    renderPage()
    await screen.findByText('server.nc')

    const sha = 'b'.repeat(64)
    mockedFindBySha.mockResolvedValue(record('file_hit', 'hit.nc'))
    const dedupeButton = screen.getByRole('button', { name: /查\s*重/ })
    expect(dedupeButton).toBeDisabled()

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('按 SHA256 查重'), sha)
    await user.click(screen.getByRole('button', { name: /查\s*重/ }))

    expect(await screen.findByText('命中：hit.nc')).toBeInTheDocument()
    expect(screen.getByText('file_hit')).toBeInTheDocument()
    expect(mockedFindBySha).toHaveBeenCalledWith(sha, expect.anything())
  })

  test('dedupe lookup reports a miss when the hash is unknown', async () => {
    renderPage()
    await screen.findByText('server.nc')

    mockedFindBySha.mockRejectedValue({
      code: 'FILE_NOT_FOUND',
      message: '文件不存在',
      status: 404,
    })
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('按 SHA256 查重'), 'c'.repeat(64))
    await user.click(screen.getByRole('button', { name: /查\s*重/ }))

    expect(await screen.findByText('库中无此哈希文件')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
