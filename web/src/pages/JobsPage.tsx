import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Button, message, Segmented, Select, Space, Table, Tooltip, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'
import { listJobs, rerunJob } from '../api/jobs'
import { toHubApiError } from '../api/errors'
import HubErrorAlert from '../components/HubErrorAlert'
import PageHeader from '../components/PageHeader'
import StatusTag from '../components/StatusTag'
import { queryKeys } from '../hooks/queryKeys'
import type { Job, JobStatus } from '../types/api'
import { shortJobId } from '../utils/format'
import { formatDuration } from '../utils/jobTime'
import { formatRelative } from '../utils/time'

const PAGE_SIZE = 20

const ACTIVE_STATUSES = new Set<JobStatus>(['PENDING', 'PREPARING', 'RUNNING', 'CANCEL_REQUESTED'])

const RERUNNABLE_STATUSES = new Set<JobStatus>(['FAILED', 'TIMED_OUT'])

type StatusFilter = 'ALL' | 'ACTIVE' | 'SUCCESS' | 'FAILED' | 'CANCELLED'

const STATUS_FILTERS: { label: string; value: StatusFilter; statuses?: JobStatus[] }[] = [
  { label: '全部', value: 'ALL' },
  {
    label: '运行中',
    value: 'ACTIVE',
    statuses: ['PENDING', 'PREPARING', 'RUNNING', 'CANCEL_REQUESTED'],
  },
  { label: '成功', value: 'SUCCESS', statuses: ['SUCCESS'] },
  { label: '失败', value: 'FAILED', statuses: ['FAILED', 'TIMED_OUT'] },
  { label: '已取消', value: 'CANCELLED', statuses: ['CANCELLED'] },
]

export default function JobsPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('ALL')
  const [pluginFilter, setPluginFilter] = useState<string>()
  const [page, setPage] = useState(1)

  const offset = (page - 1) * PAGE_SIZE
  const statuses = STATUS_FILTERS.find((option) => option.value === statusFilter)?.statuses

  const jobs = useQuery({
    queryKey: queryKeys.jobs.list({
      status: statusFilter,
      plugin_id: pluginFilter,
      offset,
      limit: PAGE_SIZE,
    }),
    queryFn: () =>
      listJobs({
        ...(statuses !== undefined ? { statuses } : {}),
        ...(pluginFilter !== undefined && pluginFilter !== '' ? { pluginId: pluginFilter } : {}),
        limit: PAGE_SIZE,
        offset,
      }),
    placeholderData: (previous) => previous,
    refetchInterval: (query) => {
      const active = (query.state.data?.items ?? []).some((item) =>
        ACTIVE_STATUSES.has(item.status),
      )
      return active ? 2000 : false
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

  const pluginOptions = Array.from(
    new Set((jobs.data?.items ?? []).map((item) => item.plugin_id)),
  ).map((pluginId) => ({ label: pluginId, value: pluginId }))

  const columns: ColumnsType<Job> = [
    {
      title: '任务 ID',
      dataIndex: 'job_id',
      render: (_, record) => (
        <Tooltip title={record.job_id}>
          <Typography.Text
            code
            copyable={{ text: record.job_id, tooltips: ['复制完整 ID', '已复制'] }}
            style={{ cursor: 'pointer' }}
            onClick={() => navigate(`/jobs/${record.job_id}`)}
          >
            {shortJobId(record.job_id)}
          </Typography.Text>
        </Tooltip>
      ),
    },
    { title: '插件', dataIndex: 'plugin_id' },
    { title: '版本', dataIndex: 'version' },
    {
      title: '运行时',
      dataIndex: 'runtime_type',
      render: (value: Job['runtime_type']) => (value === 'conda-pack' ? 'conda-pack' : 'docker'),
    },
    {
      title: '状态',
      dataIndex: 'status',
      render: (status: Job['status']) => <StatusTag status={status} />,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      render: (value: string) => <Tooltip title={value}>{formatRelative(value)}</Tooltip>,
    },
    {
      title: '耗时',
      key: 'duration',
      render: (_, record) => formatDuration(record.created_at, record.started_at, record.finished_at, Date.now()),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, record) => (
        <Space size={4}>
          <Button size="small" onClick={() => navigate(`/jobs/${record.job_id}`)}>
            查看
          </Button>
          {RERUNNABLE_STATUSES.has(record.status) && (
            <Button
              size="small"
              loading={rerun.isPending && rerun.variables === record.job_id}
              onClick={() => rerun.mutate(record.job_id)}
            >
              重跑
            </Button>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="任务中心"
        subtitle="按状态与插件筛选任务，支持服务端分页与失败重跑。"
      />
      <Space style={{ marginBottom: 12 }} wrap>
        <Segmented
          aria-label="按状态筛选"
          value={statusFilter}
          onChange={(value) => {
            setStatusFilter(value as StatusFilter)
            setPage(1)
          }}
          options={STATUS_FILTERS.map(({ label, value }) => ({ label, value }))}
        />
        <Select
          allowClear
          aria-label="按插件筛选"
          placeholder="全部插件"
          style={{ minWidth: 180 }}
          value={pluginFilter}
          options={pluginOptions}
          onChange={(value) => {
            setPluginFilter(value)
            setPage(1)
          }}
        />
      </Space>
      {jobs.error !== null && jobs.error !== undefined && (
        <HubErrorAlert error={toHubApiError(jobs.error)} onRetry={() => void jobs.refetch()} />
      )}
      <Table
        rowKey="job_id"
        loading={jobs.isLoading}
        dataSource={jobs.data?.items ?? []}
        columns={columns}
        locale={{ emptyText: '暂无任务' }}
        pagination={{
          pageSize: PAGE_SIZE,
          current: page,
          total: jobs.data?.total ?? 0,
          showSizeChanger: false,
          onChange: (next) => setPage(next),
        }}
      />
    </div>
  )
}
