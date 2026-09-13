import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Button, Card, Popconfirm, Space, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { listPlugins } from '../api/plugins'
import { toHubApiError } from '../api/errors'
import HubErrorAlert from '../components/HubErrorAlert'
import PageHeader from '../components/PageHeader'
import StatusTag from '../components/StatusTag'
import UploadPanel from '../components/UploadPanel'
import { useBuildToggle } from '../hooks/useBuildToggle'
import { usePluginInstall } from '../hooks/usePluginInstall'
import { queryKeys } from '../hooks/queryKeys'
import type { PluginSummary } from '../types/api'
import PluginDetailDrawer from './PluginDetailDrawer'

export default function PluginsPage() {
  const [drawerPluginId, setDrawerPluginId] = useState<string | null>(null)
  const plugins = useQuery({ queryKey: queryKeys.plugins.list(), queryFn: listPlugins })
  const { install, progress, trackedBuild } = usePluginInstall()
  const toggle = useBuildToggle()

  const columns: ColumnsType<PluginSummary> = [
    { title: '插件 ID', dataIndex: 'id' },
    { title: '名称', dataIndex: 'name' },
    { title: '分类', dataIndex: 'category', render: (value: string | null) => value ?? '-' },
    {
      title: '最新版本',
      dataIndex: 'latest_version',
      render: (value: string | null) => value ?? '-',
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, record) => (
        <Button size="small" onClick={() => setDrawerPluginId(record.id)}>
          查看详情
        </Button>
      ),
    },
  ]

  const installing = trackedBuild !== undefined && trackedBuild.status === 'INSTALLING'

  return (
    <div>
      <PageHeader
        title="插件管理"
        subtitle="安装发布者构建的 .pypkg 插件包，并管理各版本的运行环境 Build。"
      />
      <Card style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <UploadPanel
            accept=".pypkg"
            uploading={install.isPending || installing}
            progress={progress}
            hint="仅接受发布者构建的 .pypkg 文件；上传完成后 Build 需要经过安装校验才会就绪。"
            beforeUpload={(file) => {
              install.mutate({ packageFile: file })
              return false
            }}
          />
          {trackedBuild !== undefined && (
            <Space wrap>
              <span>最近安装 Build：</span>
              <StatusTag status={trackedBuild.status} />
              <code>{trackedBuild.build_id}</code>
              {trackedBuild.status === 'READY' && (
                <Button
                  size="small"
                  loading={toggle.isPending}
                  onClick={() => toggle.mutate({ build: trackedBuild, action: 'enable' })}
                >
                  启用
                </Button>
              )}
              {trackedBuild.status === 'ENABLED' && (
                <Popconfirm
                  title="停用该 Build？"
                  description="停用后将无法用它创建新任务。"
                  okText="确定"
                  cancelText="取消"
                  onConfirm={() => toggle.mutate({ build: trackedBuild, action: 'disable' })}
                >
                  <Button size="small" loading={toggle.isPending}>
                    停用
                  </Button>
                </Popconfirm>
              )}
            </Space>
          )}
          {trackedBuild !== undefined && trackedBuild.status === 'FAILED' && (
            <HubErrorAlert
              error={{
                code: 'BUILD_FAILED',
                message: trackedBuild.error_summary ?? 'Build 安装失败',
                details: { build_id: trackedBuild.build_id },
              }}
            />
          )}
          {install.isError && <HubErrorAlert error={toHubApiError(install.error)} />}
          {toggle.isError && <HubErrorAlert error={toHubApiError(toggle.error)} />}
        </Space>
      </Card>
      {plugins.error !== null && plugins.error !== undefined && (
        <HubErrorAlert error={toHubApiError(plugins.error)} onRetry={() => void plugins.refetch()} />
      )}
      <Table
        rowKey="id"
        loading={plugins.isLoading}
        pagination={false}
        dataSource={plugins.data?.items ?? []}
        columns={columns}
      />
      <PluginDetailDrawer
        pluginId={drawerPluginId}
        onClose={() => setDrawerPluginId(null)}
      />
    </div>
  )
}
