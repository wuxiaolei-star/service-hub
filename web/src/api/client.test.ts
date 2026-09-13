import { AxiosError, AxiosHeaders } from 'axios'

import { apiClient, toHubApiError } from './client'

describe('API client errors', () => {
  test('converts the standard failed-response envelope into a HubApiError', () => {
    const error = new AxiosError(
      'Request failed with status code 409',
      undefined,
      undefined,
      undefined,
      {
        data: {
          success: false,
          error: {
            code: 'PLUGIN_BUILD_NOT_READY',
            message: '插件 Build 尚未 READY, 不能启用',
            details: { build_id: 'build/one' },
          },
        },
        status: 409,
        statusText: 'Conflict',
        headers: {},
        config: { headers: new AxiosHeaders() },
      },
    )

    expect(toHubApiError(error)).toEqual({
      code: 'PLUGIN_BUILD_NOT_READY',
      message: '插件 Build 尚未 READY, 不能启用',
      details: { build_id: 'build/one' },
      status: 409,
    })
  })

  test('uses NETWORK_ERROR when a request has no server response', () => {
    const error = new AxiosError('Network Error')

    expect(toHubApiError(error)).toEqual({
      code: 'NETWORK_ERROR',
      message: 'Network Error',
    })
  })

  test('uses the relative public API base URL', () => {
    expect(apiClient.defaults.baseURL).toBe('/api/v1')
  })
})
