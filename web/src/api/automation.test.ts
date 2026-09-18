import { describe, expect, test, vi } from 'vitest'
import { apiClient } from './client'
import { listPipelineRuns } from './automation'

vi.mock('./client', () => ({
  apiClient: { get: vi.fn().mockResolvedValue({ data: { items: [] } }), post: vi.fn() },
}))

describe('automation api paths', () => {
  test('lists pipeline runs under /pipelines/runs, not /pipeline-runs', async () => {
    await listPipelineRuns(7)

    expect(apiClient.get).toHaveBeenCalledWith('/pipelines/runs', {
      params: { pipeline_id: 7 },
    })
  })
})
