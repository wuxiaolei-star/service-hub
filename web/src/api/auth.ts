import axios from 'axios'

import { apiClient } from './client'

export interface MeResponse {
  actor_type: 'user' | 'api_key' | 'anonymous'
  id: number | null
  username: string
  role: 'viewer' | 'operator' | 'publisher' | 'admin'
  must_change_password: boolean
}

export async function login(username: string, password: string): Promise<MeResponse> {
  const response = await apiClient.post<MeResponse>('/auth/login', { username, password })
  return response.data
}

export async function logout(): Promise<void> {
  await apiClient.post('/auth/logout')
}

export async function fetchMe(): Promise<MeResponse> {
  const response = await apiClient.get<MeResponse>('/auth/me')
  return response.data
}

export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  await apiClient.post('/auth/change-password', {
    old_password: oldPassword,
    new_password: newPassword,
  })
}

/** True when the interceptor produced a 401 HubApiError. */
export function isUnauthorized(error: unknown): boolean {
  return axios.isAxiosError(error) && error.response?.status === 401
}

/** Send the browser back to the login page after a session-expiring 401. */
export function redirectToLogin(): void {
  window.location.assign('/login')
}
