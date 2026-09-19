import { afterEach, describe, test, vi } from 'vitest'
import { apiClient } from './client'
import { listJobs, rerunJob } from './jobs'

interface CapturedConfig {
  params: Record<string, unknown>
  paramsSerializer: { serialize: (params: unknown) => string }
}

describe('jobs api', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  test('serializes status filters as repeated status query params', async () => {
    const get = vi
      .spyOn(apiClient, 'get')
      .mockResolvedValue({ data: { items: [], total: 0 } } as never)

    await listJobs({ statuses: ['RUNNING', 'SUCCESS'], pluginId: 'nc_to_shp', limit: 20, offset: 40 })

    expect(get).toHaveBeenCalledWith(
      '/jobs',
      expect.objectContaining({
        params: { status: ['RUNNING', 'SUCCESS'], plugin_id: 'nc_to_shp', limit: 20, offset: 40 },
      }),
    )
    const config = get.mock.calls[0]?.[1] as CapturedConfig
    // The backend binds repeated `status=` params; axios' default would emit `status[]=`.
    expect(config.paramsSerializer.serialize(config.params)).toBe(
      'status=RUNNING&status=SUCCESS&plugin_id=nc_to_shp&limit=20&offset=40',
    )
  })

  test('omits filters that are not set', async () => {
    const get = vi
      .spyOn(apiClient, 'get')
      .mockResolvedValue({ data: { items: [], total: 0 } } as never)

    await listJobs()

    expect(get).toHaveBeenCalledWith(
      '/jobs',
      expect.objectContaining({ params: {} }),
    )
  })

  test('posts rerun to the documented route with an encoded job key', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} } as never)

    await rerunJob('job/one')

    expect(post).toHaveBeenCalledWith('/jobs/job%2Fone/rerun')
  })
})
