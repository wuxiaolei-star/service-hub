import type { FileRecord, ListResponse } from '../types/api'
import { apiClient } from './client'

/** One downloadable Build row of the flat registry listing. */
export interface RegistryBuildItem {
  plugin_id: string
  plugin_name: string
  version: string
  build_key: string
  runtime_type: string
  target_arch: string
  status: string
  package_sha256: string
}

export type RegistryListResponse = ListResponse<RegistryBuildItem>

export async function listRegistryPlugins(): Promise<RegistryListResponse> {
  const response = await apiClient.get<RegistryListResponse>('/registry/plugins')
  return response.data
}

export async function downloadRegistryBuild(buildKey: string): Promise<Blob> {
  const response = await apiClient.get<Blob>(`/registry/download/${encodeURIComponent(buildKey)}`, {
    responseType: 'blob',
  })
  return response.data
}

export async function findFileBySha256(sha256: string): Promise<FileRecord> {
  const response = await apiClient.get<FileRecord>(`/files/by-sha256/${encodeURIComponent(sha256)}`)
  return response.data
}
