import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  AppstoreOutlined,
  CloudServerOutlined,
  PlayCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { Card, Empty, Segmented, Skeleton, Statistic, Table, Tooltip, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { listJobs } from '../api/jobs'
import { listPluginBuilds, listPlugins } from '../api/plugins'
import HubErrorAlert from '../components/HubErrorAlert'
import PageHeader from '../components/PageHeader'
import StatusTag from '../components/StatusTag'
import { queryKeys } from '../hooks/queryKeys'
import type { Job, JobStatus } from '../types/api'
import { shortJobId } from '../utils/format'
import { formatDuration } from '../utils/jobTime'
import { formatRelative } from '../utils/time'

function dashboardError(error: unknown) {
  return error instanceof Error
    ? { code: 'REQUEST_FAILED', message: error.message }
    : { code: 'REQUEST_FAILED', message: '请求失败' }
}

type RecentFilter = 'ALL' | 'ACTIVE' | 'SUCCESS' | 'FAILED'

const RECENT_FILTERS: { label: string; value: RecentFilter; statuses?: JobStatus[] }[] = [
  { label: '全部', value: 'ALL' },
  { label: '运行中', value: 'ACTIVE', statuses: ['PENDING', 'PREPARING', 'RUNNING', 'CANCEL_REQUESTED'] },
  { label: '成功', value: 'SUCCESS', statuses: ['SUCCESS'] },
  { label: '失败', value: 'FAILED', statuses: ['FAILED', 'TIMED_OUT', 'CANCELLED'] },
]

export default function DashboardPage() {
  const navigate = useNavigate()
  const [recentFilter, setRecentFilter] = useState<RecentFilter>('ALL')
  const plugins = useQuery({ queryKey: queryKeys.plugins.list(), queryFn: listPlugins })
  const builds = useQuery({ queryKey: queryKeys.plugins.builds(), queryFn: () => listPluginBuilds() })
  const jobs = useQuery({ queryKey: queryKeys.jobs.list(), queryFn: () => listJobs() })
  const isLoading = plugins.isLoading || builds.isLoading || jobs.isLoading
  const error = plugins.error ?? builds.error ?? jobs.error
  const summary = useMemo(() => {
    const buildItems = builds.data?.items ?? []
    return {
      pluginCount: plugins.data?.items.length ?? 0,
      buildCount: buildItems.length,
      enabledBuilds: buildItems.filter((item) => item.status === 'ENABLED').length,
      readyBuilds: buildItems.filter((item) => item.status === 'READY').length,
      activeJobs: (jobs.data?.items ?? []).filter((item) =>
        ['PENDING', 'PREPARING', 'RUNNING', 'CANCEL_REQUESTED'].includes(item.status),
      ).length,
      recentFinished: (jobs.data?.items ?? []).filter((item) =>
        ['SUCCESS', 'FAILED', 'CANCELLED', 'TIMED_OUT'].includes(item.status),
      ).length,
      recentSuccesses: (jobs.data?.items ?? []).filter((item) => item.status === 'SUCCESS').length,
    }
  }, [builds.data, jobs.data, plugins.data])

  const successRate = summary.recentFinished > 0
    ? Math.round((summary.recentSuccesses / summary.recentFinished) * 100)
    : null

  const recentItems = useMemo(() => {
    const statuses = RECENT_FILTERS.find((option) => option.value === recentFilter)?.statuses
    const items = jobs.data?.items ?? []
    return statuses === undefined ? items : items.filter((item) => statuses.includes(item.status))
  }, [jobs.data, recentFilter])

  const recentColumns: ColumnsType<Job> = [
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
    { title: '状态', dataIndex: 'status', render: (status: Job['status']) => <StatusTag status={status} /> },
    {
      title: '耗时',
      key: 'duration',
      render: (_, record) => formatDuration(record.created_at, record.started_at, record.finished_at, Date.now()),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      render: (value: string) => <Tooltip title={value}>{formatRelative(value)}</Tooltip>,
    },
  ]

  function retryAll() {
    void Promise.all([plugins.refetch(), builds.refetch(), jobs.refetch()])
  }

  return (
    <div>
      <PageHeader title="概览" subtitle="Service Hub 当前插件、运行环境和任务状态。" />
      {error !== null && error !== undefined && <HubErrorAlert error={dashboardError(error)} onRetry={retryAll} />}
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 6 }} />
      ) : (
        <>
          <div className="kpi-grid">
            <Card className="kpi-card" onClick={() => navigate('/jobs')}>
              <div className="kpi-icon"><PlayCircleOutlined /></div>
              <Statistic title="进行中任务" value={summary.activeJobs} />
            </Card>
            <Card className="kpi-card">
              <div className="kpi-icon"><ThunderboltOutlined /></div>
              <Statistic title="最近成功率" value={successRate === null ? '--' : successRate} suffix={successRate === null ? '' : '%'} />
            </Card>
            <Card className="kpi-card" onClick={() => navigate('/plugins')}>
              <div className="kpi-icon"><CloudServerOutlined /></div>
              <Statistic title="运行环境 Build" value={summary.buildCount} suffix={`已启用 ${summary.enabledBuilds} / 就绪 ${summary.readyBuilds}`} />
            </Card>
            <Card className="kpi-card" onClick={() => navigate('/plugins')}>
              <div className="kpi-icon"><AppstoreOutlined /></div>
              <Statistic title="已注册插件" value={summary.pluginCount} />
            </Card>
          </div>
          <Card style={{ marginTop: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
              <Typography.Title level={4} style={{ margin: 0 }}>最近任务（最多 100 条）</Typography.Title>
              <Segmented
                aria-label="筛选最近任务"
                value={recentFilter}
                onChange={(value) => setRecentFilter(value as RecentFilter)}
                options={RECENT_FILTERS.map(({ label, value }) => ({ label, value }))}
              />
            </div>
            {recentItems.length === 0 ? (
              <Empty description={recentFilter === 'ALL' ? '暂无任务' : '该状态下暂无任务'} style={{ marginTop: 24 }} />
            ) : (
              <Table
                style={{ marginTop: 12 }}
                rowKey="job_id"
                columns={recentColumns}
                dataSource={recentItems}
                pagination={false}
                scroll={{ x: 720 }}
              />
            )}
          </Card>
        </>
      )}
    </div>
  )
}
