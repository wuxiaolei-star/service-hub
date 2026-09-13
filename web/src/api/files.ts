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
  const formData = new FormData()
  formData.append('file', file)
  const response = await apiClient.post<FileRecord>('/files', formData, {
    onUploadProgress: uploadProgressHandler(onProgress),
  })
  return response.data
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
