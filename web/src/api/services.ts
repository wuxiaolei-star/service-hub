import type { ListResponse } from '../types/api'
import { apiClient } from './client'

export interface ServicePort {
  host: number
  container: number
}

export interface ServiceMount {
  source: string
  target: string
  read_only: boolean
}

export interface ServiceRuntime {
  state: string | null
  health: string | null
  ports: Record<string, unknown> | null
}

export type ServiceDesiredState = 'RUNNING' | 'STOPPED'

export interface ServiceRow {
  name: string
  image: string
  container_name: string
  desired_state: ServiceDesiredState
  runtime: ServiceRuntime | null
}

export interface ServiceCreateRequest {
  name: string
  image: string
  ports: ServicePort[]
  env: Record<string, string>
  mounts: ServiceMount[]
  command?: string[]
}

export interface ServiceActionResponse {
  name: string
  runtime: ServiceRuntime | null
}

export async function listServices(): Promise<ListResponse<ServiceRow>> {
  const response = await apiClient.get<ListResponse<ServiceRow>>('/services')
  return response.data
}

export async function createService(request: ServiceCreateRequest): Promise<ServiceRow> {
  const response = await apiClient.post<ServiceRow>('/services', request)
  return response.data
}

export async function updateService(
  name: string,
  request: ServiceCreateRequest,
): Promise<ServiceRow> {
  const response = await apiClient.put<ServiceRow>(
    `/services/${encodeURIComponent(name)}`,
    request,
  )
  return response.data
}

export async function startService(name: string): Promise<ServiceActionResponse> {
  const response = await apiClient.post<ServiceActionResponse>(
    `/services/${encodeURIComponent(name)}/start`,
  )
  return response.data
}

export async function stopService(name: string): Promise<ServiceActionResponse> {
  const response = await apiClient.post<ServiceActionResponse>(
    `/services/${encodeURIComponent(name)}/stop`,
  )
  return response.data
}

export async function restartService(name: string): Promise<ServiceActionResponse> {
  const response = await apiClient.post<ServiceActionResponse>(
    `/services/${encodeURIComponent(name)}/restart`,
  )
  return response.data
}

export async function deleteService(name: string): Promise<void> {
  await apiClient.delete(`/services/${encodeURIComponent(name)}`)
}

export async function getServiceLogs(name: string, tail: number): Promise<string> {
  const response = await apiClient.get<string>(`/services/${encodeURIComponent(name)}/logs`, {
    params: { tail },
    // Logs are plain text; keep axios from trying to parse them as JSON.
    transformResponse: (data: string) => data,
  })
  return response.data
}
