import type { FileListResponse, FileRecord, UploadProgressHandler } from '../types/api'
import { apiClient } from './client'

function uploadProgressHandler(onProgress?: UploadProgressHandler) {
  if (onProgress === undefined) {
    return undefined
  }

  return ({ loaded, total }: { loaded: number; total?: number }) => {
    onProgress(total === undefined || total === 0 ? 0 : Math.round((loaded / total) * 100))
  }
}

export async function uploadFile(file: File, onProgress?: UploadProgressHandler): Promise<FileRecord> {
  if (file.size > 10 * 1024 * 1024) {
    return uploadFileChunked(file, onProgress)
  }
  const formData = new FormData()
  formData.append('file', file)
  const response = await apiClient.post<FileRecord>('/files', formData, {
    onUploadProgress: uploadProgressHandler(onProgress),
  })
  return response.data
}

const CHUNK_SIZE = 5 * 1024 * 1024 // 5 MB

async function uploadFileChunked(file: File, onProgress?: UploadProgressHandler): Promise<FileRecord> {
  const init = await apiClient.post<{ upload_id: string }>('/files/chunk/init', {
    filename: file.name,
    total_size: file.size,
  })
  const uploadId = init.data.upload_id
  const totalChunks = Math.ceil(file.size / CHUNK_SIZE)

  for (let index = 0; index < totalChunks; index++) {
    const start = index * CHUNK_SIZE
    const end = Math.min(start + CHUNK_SIZE, file.size)
    const chunk = file.slice(start, end)
    const chunkData = await chunk.arrayBuffer()
    await apiClient.put(`/files/chunk/${uploadId}/${index}`, chunkData, {
      headers: { 'Content-Type': 'application/octet-stream' },
    })
    onProgress?.(Math.round(((index + 1) / totalChunks) * 90))
  }

  const complete = await apiClient.post<FileRecord>(`/files/chunk/${uploadId}/complete`, {
    filename: file.name,
    total_chunks: totalChunks,
  })
  onProgress?.(100)
  return complete.data
}

export async function listFiles(): Promise<FileListResponse> {
  const response = await apiClient.get<FileListResponse>('/files')
  return response.data
}

export async function getFileMetadata(fileId: string): Promise<FileRecord> {
  const response = await apiClient.get<FileRecord>(`/files/${encodeURIComponent(fileId)}`)
  return response.data
}

export async function downloadFile(fileId: string): Promise<Blob> {
  const response = await apiClient.get<Blob>(`/files/${encodeURIComponent(fileId)}/download`, {
    responseType: 'blob',
  })
  return response.data
}
