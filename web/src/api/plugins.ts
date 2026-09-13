import type {
  PluginBuild,
  PluginBuildListResponse,
  PluginDetail,
  PluginListResponse,
  UploadProgressHandler,
} from '../types/api'
import { apiClient } from './client'

function uploadProgressHandler(onProgress?: UploadProgressHandler) {
  if (onProgress === undefined) {
    return undefined
  }

  return ({ loaded, total }: { loaded: number; total?: number }) => {
    onProgress(total === undefined || total === 0 ? 0 : Math.round((loaded / total) * 100))
  }
}

export async function installPlugin(
  packageFile: File,
  onProgress?: UploadProgressHandler,
): Promise<PluginBuild> {
  const formData = new FormData()
  formData.append('file', packageFile)
  const response = await apiClient.post<PluginBuild>('/plugins/install', formData, {
    onUploadProgress: uploadProgressHandler(onProgress),
  })
  return response.data
}

export async function listPlugins(): Promise<PluginListResponse> {
  const response = await apiClient.get<PluginListResponse>('/plugins')
  return response.data
}

export async function getPlugin(pluginId: string): Promise<PluginDetail> {
  const response = await apiClient.get<PluginDetail>(`/plugins/${encodeURIComponent(pluginId)}`)
  return response.data
}

export async function listPluginBuilds(pluginId?: string): Promise<PluginBuildListResponse> {
  const response = await apiClient.get<PluginBuildListResponse>('/plugin-builds', {
    params: pluginId === undefined ? undefined : { plugin_id: pluginId },
  })
  return response.data
}

export async function getPluginBuild(buildId: string): Promise<PluginBuild> {
  const response = await apiClient.get<PluginBuild>(
    `/plugin-builds/${encodeURIComponent(buildId)}`,
  )
  return response.data
}

export async function enablePluginBuild(buildId: string): Promise<PluginBuild> {
  const response = await apiClient.post<PluginBuild>(
    `/plugin-builds/${encodeURIComponent(buildId)}/enable`,
  )
  return response.data
}

export async function disablePluginBuild(buildId: string): Promise<PluginBuild> {
  const response = await apiClient.post<PluginBuild>(
    `/plugin-builds/${encodeURIComponent(buildId)}/disable`,
  )
  return response.data
}
