import type { AxiosInstance } from 'axios'
import { apiClient } from './client'
import type { ListResponse } from '../types/api'

export interface UserRow {
  id: number
  username: string
  role: string
  is_active: boolean
  must_change_password: boolean
}

export interface ApiKeyRow {
  id: number
  name: string
  key_prefix: string
  role: string
  revoked: boolean
  last_used_at: string | null
}

interface AuditRow {
  id: number
  at: string | null
  actor_name: string
  action: string
  resource_type: string | null
  resource_id: string | null
  detail: Record<string, unknown> | null
  ip: string | null
  result: string
}

function client(): AxiosInstance {
  return apiClient
}

export async function listUsers(): Promise<ListResponse<UserRow>> {
  const response = await client().get<ListResponse<UserRow>>('/users')
  return response.data
}

export async function createUser(body: {
  username: string
  password: string
  role: string
}): Promise<UserRow> {
  const response = await client().post<UserRow>('/users', body)
  return response.data
}

export async function disableUser(userId: number): Promise<UserRow> {
  const response = await client().post<UserRow>(`/users/${userId}/disable`)
  return response.data
}

export async function resetUserPassword(
  userId: number,
): Promise<{ username: string; password: string }> {
  const response = await client().post(`/users/${userId}/reset-password`)
  return response.data
}

export async function listApiKeys(): Promise<ListResponse<ApiKeyRow>> {
  const response = await client().get<ListResponse<ApiKeyRow>>('/api-keys')
  return response.data
}

export async function createApiKey(body: {
  name: string
  role: string
}): Promise<ApiKeyRow & { key: string }> {
  const response = await client().post<ApiKeyRow & { key: string }>('/api-keys', body)
  return response.data
}

export async function revokeApiKey(keyId: number): Promise<{ id: number }> {
  const response = await client().post(`/api-keys/${keyId}/revoke`)
  return response.data
}

export async function listAuditLogs(params: {
  action?: string
  actor_name?: string
  limit?: number
}): Promise<ListResponse<AuditRow>> {
  const response = await client().get<ListResponse<AuditRow>>('/audit-logs', { params })
  return response.data
}
