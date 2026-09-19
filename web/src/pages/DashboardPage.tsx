import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Col, Empty, Row, Skeleton, Statistic, Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { listJobs } from '../api/jobs'
import { listPluginBuilds, listPlugins } from '../api/plugins'
import HubErrorAlert from '../components/HubErrorAlert'
import PageHeader from '../components/PageHeader'
import StatusTag from '../components/StatusTag'
import { queryKeys } from '../hooks/queryKeys'
import type { Job } from '../types/api'
import { formatDateTime } from '../utils/format'

function dashboardError(error: unknown) {
  return error instanceof Error
    ? { code: 'REQUEST_FAILED', message: error.message }
    : { code: 'REQUEST_FAILED', message: '请求失败' }
}

const jobColumns: ColumnsType<Job> = [
  { title: '任务 ID', dataIndex: 'job_id', ellipsis: true },
  { title: '插件', dataIndex: 'plugin_id' },
  { title: '版本', dataIndex: 'version' },
  { title: '状态', dataIndex: 'status', render: (status: Job['status']) => <StatusTag status={status} /> },
  { title: '创建时间', dataIndex: 'created_at', render: (value: string) => formatDateTime(value) },
]

export default function DashboardPage() {
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
    }
  }, [builds.data, jobs.data, plugins.data])

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
          <Row gutter={[16, 16]}>
            <Col xs={24} sm={12} xl={6}><Card><Statistic title="已注册插件" value={summary.pluginCount} /></Card></Col>
            <Col xs={24} sm={12} xl={6}><Card><Statistic title="运行环境 Build" value={summary.buildCount} /></Card></Col>
            <Col xs={24} sm={12} xl={6}><Card><Statistic title="可用 Build" value={`${summary.enabledBuilds + summary.readyBuilds}`} suffix={`已启用 ${summary.enabledBuilds} / 就绪 ${summary.readyBuilds}`} /></Card></Col>
            <Col xs={24} sm={12} xl={6}><Card><Statistic title="进行中任务" value={summary.activeJobs} /></Card></Col>
          </Row>
          <Card style={{ marginTop: 16 }}>
            <Typography.Title level={4}>最近任务（最多 100 条）</Typography.Title>
            {(jobs.data?.items.length ?? 0) === 0 ? <Empty description="暂无任务" /> : <Table rowKey="job_id" columns={jobColumns} dataSource={jobs.data?.items} pagination={false} scroll={{ x: 720 }} />}
          </Card>
        </>
      )}
    </div>
  )
}
