import { useQuery } from '@tanstack/react-query'
import { Alert, Card, Col, Descriptions, Row, Skeleton, Statistic } from 'antd'
import { getMetrics, getSystemInfo, parseHubMetrics } from '../api/system'
import { toHubApiError } from '../api/errors'
import HubErrorAlert from '../components/HubErrorAlert'
import PageHeader from '../components/PageHeader'
import { queryKeys } from '../hooks/queryKeys'
import { formatFileSize } from '../utils/format'

const METRICS_POLL_INTERVAL_MS = 15_000

function systemError(error: unknown) {
  return error instanceof Error
    ? { code: 'REQUEST_FAILED', message: error.message }
    : { code: 'REQUEST_FAILED', message: '请求失败' }
}

export default function SystemPage() {
  const systemInfo = useQuery({ queryKey: queryKeys.system.info(), queryFn: getSystemInfo })
  const metrics = useQuery({
    queryKey: queryKeys.system.metrics(),
    queryFn: getMetrics,
    refetchInterval: METRICS_POLL_INTERVAL_MS,
  })

  const metricsError =
    metrics.error !== null && metrics.error !== undefined ? toHubApiError(metrics.error) : undefined
  const metricsForbidden = metricsError?.status === 403
  const parsedMetrics = metrics.data !== undefined ? parseHubMetrics(metrics.data) : undefined

  return (
    <div>
      <PageHeader title="系统信息" subtitle="当前 Service Hub 实例的只读运行信息。" />
      {!metricsForbidden && (
        <Card title="运行指标" style={{ marginBottom: 16 }}>
          {metrics.isLoading && <Skeleton active paragraph={{ rows: 2 }} />}
          {metricsError !== undefined && !metricsForbidden && (
            <HubErrorAlert error={metricsError} onRetry={() => void metrics.refetch()} />
          )}
          {parsedMetrics !== undefined && (
            <Row gutter={[16, 16]}>
              <Col xs={12} md={8} xl={4}>
                <Statistic title="文件总数" value={parsedMetrics['hub_files_total'] ?? 0} />
              </Col>
              <Col xs={12} md={8} xl={4}>
                <Statistic
                  title="存储字节"
                  value={formatFileSize(parsedMetrics['hub_files_bytes_total'] ?? 0)}
                />
              </Col>
              <Col xs={12} md={8} xl={4}>
                <Statistic title="活动任务" value={parsedMetrics['hub_jobs_active'] ?? 0} />
              </Col>
              <Col xs={12} md={8} xl={4}>
                <Statistic title="用户数" value={parsedMetrics['hub_users_total'] ?? 0} />
              </Col>
              <Col xs={12} md={8} xl={4}>
                <Statistic title="活跃会话" value={parsedMetrics['hub_sessions_active'] ?? 0} />
              </Col>
            </Row>
          )}
        </Card>
      )}
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message="V1 当前未启用身份认证"
        description="请仅通过受信任网络或 Nginx 反向代理暴露此管理界面；认证能力将在后续版本加入。"
      />
      {systemInfo.isLoading && <Skeleton active paragraph={{ rows: 4 }} />}
      {systemInfo.error !== null && systemInfo.error !== undefined && (
        <HubErrorAlert error={systemError(systemInfo.error)} onRetry={() => void systemInfo.refetch()} />
      )}
      {systemInfo.data !== undefined && (
        <Card>
          <Descriptions column={{ xs: 1, sm: 2 }} bordered>
            <Descriptions.Item label="Hub 版本">{systemInfo.data.hub_version}</Descriptions.Item>
            <Descriptions.Item label="主机架构">{systemInfo.data.platform.os} / {systemInfo.data.platform.arch}</Descriptions.Item>
            <Descriptions.Item label="Python 版本">{systemInfo.data.python_version}</Descriptions.Item>
            <Descriptions.Item label="部署模式">{systemInfo.data.deployment_mode}</Descriptions.Item>
          </Descriptions>
        </Card>
      )}
    </div>
  )
}
