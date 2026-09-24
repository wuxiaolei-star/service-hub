import type {
  Job,
  JobCancelResponse,
  JobCreateRequest,
  JobListResponse,
  JobLogResponse,
  JobOutputsResponse,
  JobStatsResponse,
} from '../types/api'
import { apiClient } from './client'

export interface ListJobsParams {
  statuses?: string[]
  pluginId?: string
  limit?: number
  offset?: number
}

type JobsQuery = Record<string, string | string[] | number>

/**
 * Serialize list filters with one `key=value` pair per array item so repeated
 * status params reach the API as `status=RUNNING&status=SUCCESS`. Axios' built
 * in array serialization would emit `status[]=` instead, which the backend
 * query binding does not accept.
 */
export function serializeJobsQuery(params: JobsQuery): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) {
      for (const item of value) {
        search.append(key, item)
      }
    } else {
      search.append(key, String(value))
    }
  }
  return search.toString()
}

function buildJobsQuery(params: ListJobsParams): JobsQuery {
  const query: JobsQuery = {}
  if (params.statuses !== undefined && params.statuses.length > 0) {
    query.status = params.statuses
  }
  if (params.pluginId !== undefined && params.pluginId !== '') {
    query.plugin_id = params.pluginId
  }
  if (params.limit !== undefined) {
    query.limit = params.limit
  }
  if (params.offset !== undefined) {
    query.offset = params.offset
  }
  return query
}

export async function createJob(request: JobCreateRequest): Promise<Job> {
  const response = await apiClient.post<Job>('/jobs', request)
  return response.data
}

export async function listJobs(params: ListJobsParams = {}): Promise<JobListResponse> {
  const response = await apiClient.get<JobListResponse>('/jobs', {
    params: buildJobsQuery(params),
    paramsSerializer: { serialize: serializeJobsQuery },
  })
  return response.data
}

/** Re-run a finished job with its original inputs and params; returns the new job row. */
export async function rerunJob(jobKey: string): Promise<Job> {
  const response = await apiClient.post<Job>(`/jobs/${encodeURIComponent(jobKey)}/rerun`)
  return response.data
}

export async function getJob(jobId: string): Promise<Job> {
  const response = await apiClient.get<Job>(`/jobs/${encodeURIComponent(jobId)}`)
  return response.data
}

/** Daily job-count and duration percentiles for the dashboard trend chart. */
export async function getJobStats(days = 14): Promise<JobStatsResponse> {
  const response = await apiClient.get<JobStatsResponse>('/jobs/stats', { params: { days } })
  return response.data
}

export async function cancelJob(jobId: string): Promise<JobCancelResponse> {
  const response = await apiClient.post<JobCancelResponse>(`/jobs/${encodeURIComponent(jobId)}/cancel`)
  return response.data
}

export async function getJobLogs(
  jobId: string,
  cursor = 0,
  limit = 100,
): Promise<JobLogResponse> {
  const response = await apiClient.get<JobLogResponse>(`/jobs/${encodeURIComponent(jobId)}/logs`, {
    params: { cursor, limit },
  })
  return response.data
}

export async function getJobOutputs(jobId: string): Promise<JobOutputsResponse> {
  const response = await apiClient.get<JobOutputsResponse>(`/jobs/${encodeURIComponent(jobId)}/outputs`)
  return response.data
}
