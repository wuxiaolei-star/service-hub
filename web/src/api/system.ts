import type { HealthResponse, SystemInfoResponse } from '../types/api'
import { apiClient } from './client'

export async function getHealth(): Promise<HealthResponse> {
  const response = await apiClient.get<HealthResponse>('/system/health')
  return response.data
}

export async function getSystemInfo(): Promise<SystemInfoResponse> {
  const response = await apiClient.get<SystemInfoResponse>('/system/info')
  return response.data
}
