import type { ListResponse, RuntimeType } from '../types/api'
import { apiClient } from './client'

export interface ScheduleRow {
  id: number
  name: string
  plugin_id: string
  version: string
  runtime_type: RuntimeType
  interval_minutes: number
  enabled: boolean
  next_run_at: string | null
  last_job_id: string | null
}

export interface ScheduleCreateRequest {
  name: string
  plugin_id: string
  version: string
  runtime_type: RuntimeType
  inputs: Record<string, unknown>
  params: Record<string, unknown>
  interval_minutes: number
}

export interface PipelineStep {
  plugin_id: string
  version: string
  runtime_type: RuntimeType
  inputs: Record<string, unknown>
  params: Record<string, unknown>
}

export interface PipelineRow {
  id: number
  name: string
  steps: PipelineStep[]
}

export interface PipelineCreateRequest {
  name: string
  steps: PipelineStep[]
}

export type PipelineRunState = 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'

export interface PipelineRunRow {
  id: number
  pipeline_id: number
  state: PipelineRunState
  current_step: number | null
  created_at: string
}

export interface PipelineExecuteResponse {
  run_id: string
  job_id: string
  state: PipelineRunState
}

export type CallbackState = 'PENDING' | 'SUCCEEDED' | 'FAILED' | 'EXHAUSTED'

export interface CallbackRow {
  id: number
  url: string
  state: CallbackState
  attempts: number
  last_status_code: number | null
  last_error: string | null
  next_attempt_at: string | null
}

export async function createSchedule(request: ScheduleCreateRequest): Promise<ScheduleRow> {
  const response = await apiClient.post<ScheduleRow>('/schedules', request)
  return response.data
}

export async function listSchedules(): Promise<ListResponse<ScheduleRow>> {
  const response = await apiClient.get<ListResponse<ScheduleRow>>('/schedules')
  return response.data
}

export async function enableSchedule(scheduleId: number): Promise<ScheduleRow> {
  const response = await apiClient.post<ScheduleRow>(`/schedules/${scheduleId}/enable`)
  return response.data
}

export async function disableSchedule(scheduleId: number): Promise<ScheduleRow> {
  const response = await apiClient.post<ScheduleRow>(`/schedules/${scheduleId}/disable`)
  return response.data
}

export async function deleteSchedule(scheduleId: number): Promise<void> {
  await apiClient.delete(`/schedules/${scheduleId}`)
}

export async function listJobCallbacks(jobKey: string): Promise<ListResponse<CallbackRow>> {
  const response = await apiClient.get<ListResponse<CallbackRow>>(
    `/jobs/${encodeURIComponent(jobKey)}/callbacks`,
  )
  return response.data
}

export async function createPipeline(request: PipelineCreateRequest): Promise<PipelineRow> {
  const response = await apiClient.post<PipelineRow>('/pipelines', request)
  return response.data
}

export async function listPipelines(): Promise<ListResponse<PipelineRow>> {
  const response = await apiClient.get<ListResponse<PipelineRow>>('/pipelines')
  return response.data
}

export async function executePipeline(pipelineId: number): Promise<PipelineExecuteResponse> {
  const response = await apiClient.post<PipelineExecuteResponse>(`/pipelines/${pipelineId}/execute`)
  return response.data
}

export async function listPipelineRuns(pipelineId: number): Promise<ListResponse<PipelineRunRow>> {
  const response = await apiClient.get<ListResponse<PipelineRunRow>>('/pipeline-runs', {
    params: { pipeline_id: pipelineId },
  })
  return response.data
}
