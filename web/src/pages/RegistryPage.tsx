import { useMutation, useQuery } from '@tanstack/react-query'
import { Button, Space, Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { downloadRegistryBuild, listRegistryPlugins } from '../api/registry'
import type { RegistryBuildItem } from '../api/registry'
import { toHubApiError } from '../api/errors'
import EmptyState from '../components/EmptyState'
import HubErrorAlert from '../components/HubErrorAlert'
import PageHeader from '../components/PageHeader'
import StatusTag from '../components/StatusTag'
import { queryKeys } from '../hooks/queryKeys'
import { saveBlob } from '../utils/download'

export default function RegistryPage() {
  const registry = useQuery({ queryKey: queryKeys.registry.list(), queryFn: listRegistryPlugins })

  const download = useMutation({
    mutationFn: async (item: RegistryBuildItem) => {
      const blob = await downloadRegistryBuild(item.build_key)
      return { blob, item }
    },
    onSuccess: ({ blob, item }) => {
      saveBlob(blob, `${item.plugin_id}-${item.version}-${item.runtime_type}.pypkg`)
    },
  })

  const columns: ColumnsType<RegistryBuildItem> = [
    {
      title: '插件',
      dataIndex: 'plugin_name',
      render: (value: string, record) => (
        <Space direction="vertical" size={0}>
          <span>{value}</span>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {record.plugin_id}
          </Typography.Text>
        </Space>
      ),
    },
    { title: '版本', dataIndex: 'version' },
    { title: '运行时', dataIndex: 'runtime_type' },
    { title: '架构', dataIndex: 'target_arch' },
    { title: '状态', dataIndex: 'status', render: (value: string) => <StatusTag status={value} /> },
    {
      title: '操作',
      key: 'actions',
      render: (_, record) => (
        <Button
          size="small"
          loading={download.isPending && download.variables?.build_key === record.build_key}
          onClick={() => download.mutate(record)}
        >
          下载
        </Button>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="插件仓库"
        subtitle="浏览全部插件 Build 并下载 .pypkg 安装包；下载动作需要发布者及以上角色。"
      />
      {registry.error !== null && registry.error !== undefined && (
        <HubErrorAlert
          error={toHubApiError(registry.error)}
          onRetry={() => void registry.refetch()}
        />
      )}
      {download.isError && download.error !== null && download.error !== undefined && (
        <HubErrorAlert error={toHubApiError(download.error)} />
      )}
      <Table
        rowKey="build_key"
        loading={registry.isLoading}
        pagination={false}
        dataSource={registry.data?.items ?? []}
        columns={columns}
        locale={{ emptyText: <EmptyState description="仓库中暂无可下载的 Build" /> }}
      />
    </div>
  )
}
