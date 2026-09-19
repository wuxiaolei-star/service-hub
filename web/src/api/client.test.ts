import { AxiosError, AxiosHeaders, type AxiosProgressEvent } from 'axios'
import { afterEach, vi } from 'vitest'

import { apiClient, handleUnauthorized, toHubApiError } from './client'
import { downloadFile, getFileMetadata, listFiles, uploadFile } from './files'
import {
  disablePluginBuild,
  enablePluginBuild,
  getPlugin,
  getPluginBuild,
  installPlugin,
  listPluginBuilds,
  listPlugins,
} from './plugins'
import { getHealth, getSystemInfo } from './system'
import { cancelJob, createJob, getJob, getJobLogs, getJobOutputs, listJobs } from './jobs'
import { queryKeys } from '../hooks/queryKeys'

const redirectToLoginMock = vi.hoisted(() => vi.fn())

vi.mock('./auth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./auth')>()),
  redirectToLogin: redirectToLoginMock,
}))

afterEach(() => {
  vi.restoreAllMocks()
})

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

  test('omits null details from a standard server error envelope', () => {
    const error = new AxiosError(
      'Request failed with status code 404',
      undefined,
      undefined,
      undefined,
      {
        data: {
          success: false,
          error: {
            code: 'FILE_NOT_FOUND',
            message: '文件不存在',
            details: null,
          },
        },
        status: 404,
        statusText: 'Not Found',
        headers: {},
        config: { headers: new AxiosHeaders() },
      },
    )

    expect(toHubApiError(error)).toEqual({
      code: 'FILE_NOT_FOUND',
      message: '文件不存在',
      status: 404,
    })
  })

  test('uses the relative public API base URL', () => {
    expect(apiClient.defaults.baseURL).toBe('/api/v1')
  })
})

describe('public endpoint contracts', () => {
  test('uses the documented public plugin routes and encodes path IDs', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: {} } as never)
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} } as never)

    await listPlugins()
    await getPlugin('plugin/one')
    await listPluginBuilds('plugin/one')
    await getPluginBuild('build/one')
    await enablePluginBuild('build/one')
    await disablePluginBuild('build/one')

    expect(get).toHaveBeenNthCalledWith(1, '/plugins')
    expect(get).toHaveBeenNthCalledWith(2, '/plugins/plugin%2Fone')
    expect(get).toHaveBeenNthCalledWith(3, '/plugin-builds', {
      params: { plugin_id: 'plugin/one' },
    })
    expect(get).toHaveBeenNthCalledWith(4, '/plugin-builds/build%2Fone')
    expect(post).toHaveBeenNthCalledWith(1, '/plugin-builds/build%2Fone/enable')
    expect(post).toHaveBeenNthCalledWith(2, '/plugin-builds/build%2Fone/disable')
  })

  test('sends plugin and file uploads under the file field and forwards progress', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} } as never)
    const packageProgress = vi.fn()
    const fileProgress = vi.fn()
    const packageFile = new File(['package'], 'plugin.pypkg')
    const inputFile = new File(['content'], 'input.nc')

    await installPlugin(packageFile, packageProgress)
    await uploadFile(inputFile, fileProgress)

    expect(post.mock.calls[0]?.[0]).toBe('/plugins/install')
    expect(post.mock.calls[1]?.[0]).toBe('/files')

    for (const [index, expectedFile, progress] of [
      [1, packageFile, packageProgress],
      [2, inputFile, fileProgress],
    ] as const) {
      const formData = post.mock.calls[index - 1]?.[1] as FormData
      const config = post.mock.calls[index - 1]?.[2]

      expect([...formData.keys()]).toEqual(['file'])
      expect(formData.get('file')).toBe(expectedFile)
      expect(config?.onUploadProgress).toEqual(expect.any(Function))
      config?.onUploadProgress?.({
        loaded: 3,
        total: 4,
        bytes: 3,
        lengthComputable: true,
      } as AxiosProgressEvent)
      expect(progress).toHaveBeenCalledWith(75)
    }
  })

  test('uses documented file and Job methods, encoded IDs, and blob downloads', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: {} } as never)
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} } as never)

    await getFileMetadata('file/one')
    await downloadFile('file/one')
    await listFiles()
    await createJob({ plugin_id: 'plugin', version: '1.0.0', inputs: {} })
    await listJobs()
    await getJob('job/one')
    await cancelJob('job/one')
    await getJobLogs('job/one', 20, 50)
    await getJobOutputs('job/one')

    expect(get).toHaveBeenNthCalledWith(1, '/files/file%2Fone')
    expect(get).toHaveBeenNthCalledWith(2, '/files/file%2Fone/download', {
      responseType: 'blob',
    })
    expect(get).toHaveBeenNthCalledWith(3, '/files')
    expect(post).toHaveBeenNthCalledWith(1, '/jobs', {
      plugin_id: 'plugin',
      version: '1.0.0',
      inputs: {},
    })
    expect(get).toHaveBeenNthCalledWith(4, '/jobs', {
      params: {},
      paramsSerializer: expect.anything(),
    })
    expect(get).toHaveBeenNthCalledWith(5, '/jobs/job%2Fone')
    expect(post).toHaveBeenNthCalledWith(2, '/jobs/job%2Fone/cancel')
    expect(get).toHaveBeenNthCalledWith(6, '/jobs/job%2Fone/logs', {
      params: { cursor: 20, limit: 50 },
    })
    expect(get).toHaveBeenNthCalledWith(7, '/jobs/job%2Fone/outputs')
  })

  test('uses the documented relative system routes', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: {} } as never)

    await getHealth()
    await getSystemInfo()

    expect(get).toHaveBeenNthCalledWith(1, '/system/health')
    expect(get).toHaveBeenNthCalledWith(2, '/system/info')
  })
})

describe('unauthorized request handling', () => {
  const originalAdapter = apiClient.defaults.adapter

  const unauthorized = (url: string) => {
    const config = { url, headers: new AxiosHeaders() }
    return new AxiosError('Request failed with status code 401', undefined, config, undefined, {
      data: {},
      status: 401,
      statusText: 'Unauthorized',
      headers: {},
      config,
    })
  }

  afterEach(() => {
    apiClient.defaults.adapter = originalAdapter
    redirectToLoginMock.mockClear()
  })

  test('flags 401 failures outside the login call only', () => {
    expect(handleUnauthorized(unauthorized('/jobs'))).toBe(true)
    expect(handleUnauthorized(unauthorized('/auth/me'))).toBe(true)
    expect(handleUnauthorized(unauthorized('/auth/login'))).toBe(false)
    expect(handleUnauthorized(new AxiosError('Network Error'))).toBe(false)
    expect(handleUnauthorized(new Error('请求失败'))).toBe(false)
  })

  test('sends the browser to /login and still rejects with a HubApiError', async () => {
    apiClient.defaults.adapter = () => Promise.reject(unauthorized('/jobs'))

    await expect(apiClient.get('/jobs')).rejects.toEqual({
      code: 'HTTP_ERROR',
      message: 'Request failed with status code 401',
      status: 401,
    })
    expect(redirectToLoginMock).toHaveBeenCalledTimes(1)
  })

  test('keeps a failed login attempt on the login page', async () => {
    apiClient.defaults.adapter = () => Promise.reject(unauthorized('/auth/login'))

    await expect(apiClient.post('/auth/login', {})).rejects.toMatchObject({
      code: 'HTTP_ERROR',
      status: 401,
    })
    expect(redirectToLoginMock).not.toHaveBeenCalled()
  })
})

describe('query key contracts', () => {
  test('separates each filtered list key by filter values', () => {
    const filteredListKeys = [
      queryKeys.plugins.list,
      queryKeys.plugins.builds,
      queryKeys.files.list,
      queryKeys.jobs.list,
    ]

    for (const makeKey of filteredListKeys) {
      expect(makeKey({ status: 'active' })).not.toEqual(makeKey({ status: 'archived' }))
    }
  })

  test('separates plugin version, IDs, cursors, and limits', () => {
    expect(queryKeys.plugins.version('plugin/one', '1.0.0')).not.toEqual(
      queryKeys.plugins.version('plugin/one', '2.0.0'),
    )
    expect(queryKeys.files.detail('file/one')).not.toEqual(queryKeys.files.detail('file/two'))
    expect(queryKeys.jobs.logs('job/one', 0, 100)).not.toEqual(
      queryKeys.jobs.logs('job/one', 1, 100),
    )
    expect(queryKeys.jobs.logs('job/one', 0, 100)).not.toEqual(
      queryKeys.jobs.logs('job/one', 0, 200),
    )
  })
})
