import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { Alert, Button, Card, Descriptions, message, Popconfirm, Progress, Space, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { downloadFile } from '../api/files'
import { listJobCallbacks, replayCallback } from '../api/automation'
import type { CallbackRow } from '../api/automation'
import { toHubApiError } from '../api/errors'
import { cancelJob, getJob, getJobOutputs, rerunJob } from '../api/jobs'
import EmptyState from '../components/EmptyState'
import HubErrorAlert from '../components/HubErrorAlert'
import JobLogViewer from '../components/JobLogViewer'
import PageHeader from '../components/PageHeader'
import StatusTag from '../components/StatusTag'
import { queryKeys } from '../hooks/queryKeys'
import { useJobLogs } from '../hooks/useJobLogs'
import { useJobLogStream } from '../hooks/useJobLogStream'
import type { JobStatus, FileRecord } from '../types/api'
import { formatDateTime } from '../utils/format'
import { formatDuration } from '../utils/jobTime'
import { safeDownloadName, saveBlob } from '../utils/download'

const ACTIVE_STATUSES: ReadonlySet<JobStatus> = new Set([
  'PENDING',
  'PREPARING',
  'RUNNING',
  'CANCEL_REQUESTED',
])

const REPLAYABLE_CALLBACK_STATES: ReadonlySet<CallbackRow['state']> = new Set(['EXHAUSTED', 'FAILED'])

const CALLBACK_STATE_COLORS: Record<CallbackRow['state'], string> = {
  PENDING: 'default',
  SUCCEEDED: 'success',
  FAILED: 'warning',
  EXHAUSTED: 'error',
}

interface OutputRow {
  key: string
  record: FileRecord
}

export default function JobDetailPage() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const job = useQuery({
    queryKey: queryKeys.jobs.detail(jobId ?? 'none'),
    queryFn: () => getJob(jobId as string),
    enabled: jobId !== undefined,
    refetchInterval: (query) =>
      query.state.data !== undefined && ACTIVE_STATUSES.has(query.state.data.status)
        ? 2000
        : false,
  })

  const isActive = job.data !== undefined && ACTIVE_STATUSES.has(job.data.status)
  // Prefer the SSE log stream while the job runs; once the stream ends or
  // degrades (exhausted), cursor polling takes over for a final full pass.
  // Until the job status is known, polling stays parked so a healthy stream
  // can own the log without an extra request.
  const stream = useJobLogStream(jobId ?? '', isActive)
  const pollEnabled = job.data !== undefined && (!isActive || stream.exhausted)
  const logs = useJobLogs(jobId ?? '', isActive, pollEnabled)
  const logEvents = pollEnabled && logs.events.length > 0 ? logs.events : stream.events
  const logLoading = pollEnabled
    ? logs.isLoading && logs.events.length === 0 && stream.events.length === 0
    : stream.isLoading

  // Latest runner progress event (0-100) reported over the log stream/poll.
  const lastPercent: number | null = (() => {
    for (let index = logEvents.length - 1; index >= 0; index -= 1) {
      const event = logEvents[index]
      if (event.type === 'progress' && typeof event.percent === 'number') {
        return event.percent
      }
    }
    return null
  })()

  const outputs = useQuery({
    queryKey: queryKeys.jobs.outputs(jobId ?? 'none'),
    queryFn: () => getJobOutputs(jobId as string),
    enabled: jobId !== undefined && job.data?.status === 'SUCCESS',
  })

  const callbacks = useQuery({
    queryKey: queryKeys.jobs.callbacks(jobId ?? 'none'),
    queryFn: () => listJobCallbacks(jobId as string),
    enabled: jobId !== undefined,
  })

  const cancel = useMutation({
    mutationFn: () => cancelJob(jobId as string),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.detail(jobId ?? 'none') })
    },
  })

  const rerun = useMutation({
    mutationFn: (jobKey: string) => rerunJob(jobKey),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.list() })
      message.success(`已创建重跑任务 ${created.job_id}`)
      navigate(`/jobs/${created.job_id}`)
    },
    onError: (error) => {
      message.error(toHubApiError(error).message)
    },
  })

  const replay = useMutation({
    mutationFn: (callbackId: number) => replayCallback(jobId as string, callbackId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.callbacks(jobId ?? 'none') })
      message.success('回调已重新排队')
    },
    onError: (error) => {
      message.error(toHubApiError(error).message)
    },
  })

  const download = useMutation({
    mutationFn: async (row: OutputRow) => {
      const blob = await downloadFile(row.record.file_id)
      return { blob, record: row.record }
    },
    onSuccess: ({ blob, record }) => {
      saveBlob(blob, safeDownloadName(record.name, record.extension ?? '', 'download.bin'))
    },
  })

  const outputRows: OutputRow[] = (outputs.data?.items ?? []).map((record) => ({
    key: record.file_id,
    record,
  }))

  const callbackRows: CallbackRow[] = callbacks.data?.items ?? []
  const callbacksError =
    callbacks.error !== null && callbacks.error !== undefined
      ? toHubApiError(callbacks.error)
      : null
  const callbacksMissing =
    callbackRows.length === 0 &&
    (callbacks.isSuccess || (callbacksError !== null && callbacksError.status === 404))

  const callbackColumns: ColumnsType<CallbackRow> = [
    { title: 'URL', dataIndex: 'url', ellipsis: true },
    {
      title: '状态',
      dataIndex: 'state',
      render: (state: CallbackRow['state']) => (
        <Tag color={CALLBACK_STATE_COLORS[state]}>{state}</Tag>
      ),
    },
    { title: '尝试次数', dataIndex: 'attempts' },
    {
      title: '最近状态码',
      dataIndex: 'last_status_code',
      render: (value: number | null) => (value === null ? '-' : value),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, record) =>
        REPLAYABLE_CALLBACK_STATES.has(record.state) ? (
          <Button
            size="small"
            loading={replay.isPending && replay.variables === record.id}
            onClick={() => replay.mutate(record.id)}
          >
            重放
          </Button>
        ) : null,
    },
  ]

  if (job.isLoading) {
    return (
      <div>
        <PageHeader title="任务详情" />
        <Card>加载中…</Card>
      </div>
    )
  }

  if (job.error !== null && job.error !== undefined) {
    return (
      <div>
        <PageHeader title="任务详情" />
        <HubErrorAlert error={toHubApiError(job.error)} onRetry={() => void job.refetch()} />
      </div>
    )
  }

  const data = job.data
  if (data === undefined) {
    return null
  }

  return (
    <div>
      <PageHeader
        title={`任务 ${data.job_id}`}
        subtitle={`由 ${data.plugin_id} ${data.version}（${data.runtime_type}）创建于 ${formatDateTime(data.created_at)}`}
        extra={
          <Space>
            <Button onClick={() => navigate('/jobs')}>返回列表</Button>
            {isActive && (
              <Popconfirm
                title="取消该任务？"
                okText="确定"
                cancelText="返回"
                onConfirm={() => cancel.mutate()}
              >
                <Button danger loading={cancel.isPending}>
                  取消任务
                </Button>
              </Popconfirm>
            )}
          </Space>
        }
      />
      <Card style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <Space wrap align="center">
            <StatusTag status={data.status} />
            <span>
              耗时 {formatDuration(data.created_at, data.started_at, data.finished_at, Date.now())}
            </span>
            {!isActive && (
              <Button
                loading={rerun.isPending}
                onClick={() => rerun.mutate(data.job_id)}
              >
                按原参数重跑
              </Button>
            )}
            {data.cancel_requested && (
              <Alert type="warning" showIcon message="已请求取消，等待 Runner 结束" />
            )}
          </Space>
          {isActive && lastPercent !== null && (
            <Progress percent={lastPercent} size="small" aria-label="任务进度" />
          )}
          <Descriptions column={{ xs: 1, sm: 2 }} size="small" bordered>
            <Descriptions.Item label="插件">{data.plugin_id}</Descriptions.Item>
            <Descriptions.Item label="版本">{data.version}</Descriptions.Item>
            <Descriptions.Item label="Build">{data.build_id}</Descriptions.Item>
            <Descriptions.Item label="开始时间">{formatDateTime(data.started_at)}</Descriptions.Item>
            <Descriptions.Item label="结束时间">{formatDateTime(data.finished_at)}</Descriptions.Item>
            {data.replayed_from !== null && (
              <Descriptions.Item label="重跑自">{data.replayed_from}</Descriptions.Item>
            )}
          </Descriptions>
          {data.status === 'FAILED' && data.error_summary !== null && (
            <Alert type="error" showIcon message="任务失败" description={data.error_summary} />
          )}
        </Space>
      </Card>

      <Card title="运行日志" style={{ marginBottom: 16 }}>
        <JobLogViewer events={logEvents} isLoading={logLoading} jobKey={jobId} />
      </Card>

      <Card title="Webhook 回调" style={{ marginBottom: 16 }}>
        {callbackRows.length > 0 ? (
          <Table
            rowKey="id"
            loading={callbacks.isLoading}
            pagination={false}
            dataSource={callbackRows}
            columns={callbackColumns}
            size="small"
          />
        ) : callbacksMissing ? (
          <EmptyState description="无回调注册" />
        ) : callbacksError !== null ? (
          <HubErrorAlert error={callbacksError} onRetry={() => void callbacks.refetch()} />
        ) : (
          <EmptyState description="加载中…" />
        )}
      </Card>

      <Card title="输出文件">
        {data.status !== 'SUCCESS' ? (
          <EmptyState description="任务成功后即可下载输出文件。" />
        ) : (
          <>
            {outputs.error !== null && outputs.error !== undefined && (
              <HubErrorAlert
                error={toHubApiError(outputs.error)}
                onRetry={() => void outputs.refetch()}
              />
            )}
            <Table
              rowKey="key"
              pagination={false}
              dataSource={outputRows}
              locale={{ emptyText: <EmptyState description="该任务没有输出文件" /> }}
              columns={[
                {
                  title: '文件名',
                  dataIndex: ['record', 'name'],
                  render: (value: string, row) => (
                    <a onClick={() => download.mutate(row)}>{value}</a>
                  ),
                },
                {
                  title: '文件 ID',
                  key: 'file_id',
                  render: (_, row) => row.record.file_id,
                },
                {
                  title: '操作',
                  key: 'actions',
                  render: (_, row) => (
                    <Button
                      size="small"
                      loading={download.isPending}
                      onClick={() => download.mutate(row)}
                    >
                      下载
                    </Button>
                  ),
                },
              ]}
            />
          </>
        )}
      </Card>
    </div>
  )
}
