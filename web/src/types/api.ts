export type RuntimeType = 'conda-pack' | 'docker'

export type BuildStatus = 'INSTALLING' | 'READY' | 'ENABLED' | 'FAILED'

export type JobStatus =
  | 'PENDING'
  | 'PREPARING'
  | 'RUNNING'
  | 'CANCEL_REQUESTED'
  | 'SUCCESS'
  | 'FAILED'
  | 'CANCELLED'
  | 'TIMED_OUT'

export type IsoTimestamp = string | null

export interface HealthResponse {
  status: 'UP'
}

export interface SystemInfoResponse {
  hub_version: string
  platform: {
    os: 'linux'
    arch: 'amd64' | 'arm64'
  }
  python_version: string
  deployment_mode: 'offline'
}

export interface PluginParameter {
  name: string
  label: string
  type: 'string' | 'integer' | 'number' | 'boolean' | 'enum' | 'datetime' | 'string_list'
  required: boolean
  default: unknown
  options?: string[] | null
  min?: number | null
  max?: number | null
}

export interface PluginInput {
  name: string
  label: string
  type: 'file' | 'files'
  required: boolean
  extensions: string[]
  min_count?: number | null
  max_count?: number | null
  max_size?: number | null
}

export interface PluginOutput {
  name: string
  label: string
  type: 'object' | 'file' | 'files'
  required: boolean
}

export interface PluginManifest {
  spec_version: '1.0'
  plugin: {
    id: string
    name: string
    version: string
    description?: string | null
    author?: string | null
    category?: string | null
    tags: string[]
  }
  sdk: { version: '1.0' }
  runtime: { type: 'process'; python: { version: string } }
  entrypoint: { module: string; function: string }
  parameters: PluginParameter[]
  inputs: PluginInput[]
  outputs: PluginOutput[]
  execution: { timeout: number; concurrency: number }
  environment_variables: { required: string[] }
  healthcheck: { enabled: boolean; type: 'import' }
}

export interface PluginSummary {
  id: string
  name: string
  description: string | null
  category: string | null
  latest_version: string | null
}

export interface PluginVersionDetail {
  version: string
  spec_version: string
  sdk_version: string
  source_sha256: string
  status: string
  manifest: PluginManifest
}

export interface PluginDetail extends PluginSummary {
  author: string | null
  versions: PluginVersionDetail[]
}

export interface PluginBuild {
  build_id: string
  plugin_id: string
  version: string
  runtime_type: RuntimeType
  target_os: string
  target_arch: string
  status: BuildStatus
  package_sha256: string
  runtime_fingerprint: string
  error_summary: string | null
}

export interface FileRecord {
  file_id: string
  name: string
  size: number
  sha256: string
  extension: string | null
  mime_type: string | null
  status: 'AVAILABLE'
  created_at: string
}

export interface JobCreateRequest {
  plugin_id: string
  version: string
  runtime_type?: RuntimeType | null
  inputs: Record<string, unknown>
  params?: Record<string, unknown>
}

export interface Job {
  job_id: string
  plugin_id: string
  version: string
  build_id: string
  runtime_type: RuntimeType
  status: JobStatus
  cancel_requested: boolean
  error_summary: string | null
  created_at: string
  started_at: IsoTimestamp
  finished_at: IsoTimestamp
  replayed_from: string | null
}

export interface JobCancelResponse {
  job_id: string
  status: JobStatus
  cancel_requested: boolean
}

export interface JobLogResponse {
  items: Record<string, unknown>[]
  next_cursor: number | null
}

export interface ListResponse<T> {
  items: T[]
}

export type PluginListResponse = ListResponse<PluginSummary>
export type PluginBuildListResponse = ListResponse<PluginBuild>
export type FileListResponse = ListResponse<FileRecord>
export type JobOutputsResponse = ListResponse<FileRecord>

export interface JobListResponse {
  items: Job[]
  total: number
}

export interface JobStatsBucket {
  date: string
  count: number
  success_count: number
  p50_ms: number | null
  p95_ms: number | null
}

export interface JobStatsResponse {
  days: number
  buckets: JobStatsBucket[]
}

export type UploadProgressHandler = (percent: number) => void
