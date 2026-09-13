import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Button, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { listJobs } from '../api/jobs'
import { toHubApiError } from '../api/errors'
import HubErrorAlert from '../components/HubErrorAlert'
import PageHeader from '../components/PageHeader'
import StatusTag from '../components/StatusTag'
import { queryKeys } from '../hooks/queryKeys'
import type { Job } from '../types/api'
import { formatDateTime } from '../utils/format'
import { formatDuration } from '../utils/jobTime'

const ACTIVE_STATUSES = new Set(['PENDING', 'PREPARING', 'RUNNING', 'CANCEL_REQUESTED'])

export default function JobsPage() {
  const navigate = useNavigate()
  const jobs = useQuery({
    queryKey: queryKeys.jobs.list(),
    queryFn: listJobs,
    refetchInterval: (query) => {
      const active = (query.state.data?.items ?? []).some((item) =>
        ACTIVE_STATUSES.has(item.status),
      )
      return active ? 2000 : false
    },
  })

  const columns: ColumnsType<Job> = [
    { title: '任务 ID', dataIndex: 'job_id', ellipsis: true },
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
      render: (value: string) => formatDateTime(value),
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
        <Button size="small" onClick={() => navigate(`/jobs/${record.job_id}`)}>
          查看
        </Button>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="任务中心"
        subtitle="服务器最近 100 条任务的执行状态与耗时。"
      />
      {jobs.error !== null && jobs.error !== undefined && (
        <HubErrorAlert error={toHubApiError(jobs.error)} onRetry={() => void jobs.refetch()} />
      )}
      <Table
        rowKey="job_id"
        loading={jobs.isLoading}
        pagination={false}
        dataSource={jobs.data?.items ?? []}
        columns={columns}
        locale={{ emptyText: '暂无任务' }}
      />
    </div>
  )
}
