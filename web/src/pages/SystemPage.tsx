import { useQuery } from '@tanstack/react-query'
import { Alert, Card, Descriptions, Skeleton } from 'antd'
import { getSystemInfo } from '../api/system'
import HubErrorAlert from '../components/HubErrorAlert'
import PageHeader from '../components/PageHeader'
import { queryKeys } from '../hooks/queryKeys'

function systemError(error: unknown) {
  return error instanceof Error
    ? { code: 'REQUEST_FAILED', message: error.message }
    : { code: 'REQUEST_FAILED', message: '请求失败' }
}

export default function SystemPage() {
  const systemInfo = useQuery({ queryKey: queryKeys.system.info(), queryFn: getSystemInfo })

  return (
    <div>
      <PageHeader title="系统信息" subtitle="当前 Service Hub 实例的只读运行信息。" />
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
