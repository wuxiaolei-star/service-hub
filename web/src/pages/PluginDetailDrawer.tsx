import { useQuery } from '@tanstack/react-query'
import { Button, Drawer, Popconfirm, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { getPlugin, listPluginBuilds } from '../api/plugins'
import HubErrorAlert from '../components/HubErrorAlert'
import StatusTag from '../components/StatusTag'
import { useBuildToggle } from '../hooks/useBuildToggle'
import { queryKeys } from '../hooks/queryKeys'
import { toHubApiError } from '../api/errors'
import type { PluginBuild } from '../types/api'

interface PluginDetailDrawerProps {
  pluginId: string | null
  onClose: () => void
}

export default function PluginDetailDrawer({ pluginId, onClose }: PluginDetailDrawerProps) {
  const detail = useQuery({
    queryKey: queryKeys.plugins.detail(pluginId ?? 'none'),
    queryFn: () => getPlugin(pluginId as string),
    enabled: pluginId !== null,
  })
  const builds = useQuery({
    queryKey: queryKeys.plugins.builds({ plugin_id: pluginId ?? 'none' }),
    queryFn: () => listPluginBuilds(pluginId ?? undefined),
    enabled: pluginId !== null,
  })
  const toggle = useBuildToggle()

  const buildColumns: ColumnsType<PluginBuild> = [
    {
      title: 'Build ID',
      dataIndex: 'build_id',
      ellipsis: true,
      render: (value: string) => (
        <Typography.Text copyable={{ text: value }}>{value}</Typography.Text>
      ),
    },
    { title: '版本', dataIndex: 'version' },
    {
      title: '运行时 / 平台',
      key: 'runtime',
      render: (_, record) => (
        <Space size={4}>
          <Tag>{record.runtime_type}</Tag>
          <Tag>{`${record.target_os}/${record.target_arch}`}</Tag>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      render: (status: PluginBuild['status']) => <StatusTag status={status} />,
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, record) => (
        <Space>
          {record.status === 'READY' && (
            <Button
              size="small"
              loading={toggle.isPending}
              onClick={() => toggle.mutate({ build: record, action: 'enable' })}
            >
              启用
            </Button>
          )}
          {record.status === 'ENABLED' && (
            <Popconfirm
              title="停用该 Build？"
              description="停用后将无法用它创建新任务。"
              okText="确定"
              cancelText="取消"
              onConfirm={() => toggle.mutate({ build: record, action: 'disable' })}
            >
              <Button size="small" loading={toggle.isPending}>
                停用
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  const version = detail.data?.versions[0]

  return (
    <Drawer
      title={`插件详情：${detail.data?.name ?? pluginId ?? ''}`}
      open={pluginId !== null}
      onClose={onClose}
      width={720}
    >
      {detail.error !== null && detail.error !== undefined && (
        <HubErrorAlert
          error={toHubApiError(detail.error)}
          onRetry={() => void detail.refetch()}
        />
      )}
      {detail.data !== undefined && version !== undefined && (
        <>
          <Typography.Title level={5}>入口与运行时</Typography.Title>
          <Typography.Paragraph>
            入口 {version.manifest.entrypoint.module}:{version.manifest.entrypoint.function}
            ，Python {version.manifest.runtime.python.version}，SDK {version.sdk_version}
          </Typography.Paragraph>
          {version.manifest.parameters.length > 0 && (
            <>
              <Typography.Title level={5}>参数</Typography.Title>
              <Table
                rowKey="name"
                size="small"
                pagination={false}
                dataSource={version.manifest.parameters}
                columns={[
                  { title: '名称', dataIndex: 'name' },
                  { title: '标签', dataIndex: 'label' },
                  { title: '类型', dataIndex: 'type' },
                  {
                    title: '必填',
                    dataIndex: 'required',
                    render: (value: boolean) => (value ? '是' : '否'),
                  },
                  {
                    title: '默认值',
                    dataIndex: 'default',
                    render: (value: unknown) =>
                      value === null || value === undefined ? '-' : String(value),
                  },
                ]}
              />
            </>
          )}
          <Typography.Title level={5}>运行环境 Build</Typography.Title>
          <Table
            rowKey="build_id"
            size="small"
            loading={builds.isLoading}
            pagination={false}
            dataSource={builds.data?.items ?? []}
            columns={buildColumns}
          />
        </>
      )}
    </Drawer>
  )
}
