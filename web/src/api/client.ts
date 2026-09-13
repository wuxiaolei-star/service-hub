import axios from 'axios'

import { toHubApiError } from './errors'

export const apiClient = axios.create({
  baseURL: '/api/v1',
})

apiClient.interceptors.response.use(
  (response) => response,
  (reason: unknown) => Promise.reject(toHubApiError(reason)),
)

export { toHubApiError }
