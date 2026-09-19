import axios from 'axios'

import { redirectToLogin } from './auth'
import { toHubApiError } from './errors'

export const apiClient = axios.create({
  baseURL: '/api/v1',
})

/**
 * True when a failed request is a 401 raised by anything other than the login
 * call itself, i.e. the Web session expired and every card would otherwise
 * surface AUTH_REQUIRED instead of returning to /login.
 */
export function handleUnauthorized(reason: unknown): boolean {
  return (
    axios.isAxiosError(reason) &&
    reason.response?.status === 401 &&
    !reason.config?.url?.includes('/auth/login')
  )
}

apiClient.interceptors.response.use(
  (response) => response,
  (reason: unknown) => {
    if (handleUnauthorized(reason)) {
      redirectToLogin()
    }
    return Promise.reject(toHubApiError(reason))
  },
)

export { toHubApiError }
