import type {
  Job,
  JobCancelResponse,
  JobCreateRequest,
  JobListResponse,
  JobLogResponse,
  JobOutputsResponse,
} from '../types/api'
import { apiClient } from './client'

export async function createJob(request: JobCreateRequest): Promise<Job> {
  const response = await apiClient.post<Job>('/jobs', request)
  return response.data
}

export async function listJobs(): Promise<JobListResponse> {
  const response = await apiClient.get<JobListResponse>('/jobs')
  return response.data
}

export async function getJob(jobId: string): Promise<Job> {
  const response = await apiClient.get<Job>(`/jobs/${encodeURIComponent(jobId)}`)
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
