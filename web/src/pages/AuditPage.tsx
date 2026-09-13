import { useQuery } from '@tanstack/react-query'
import { Button, Input, Space, Table } from 'antd'
import { useState } from 'react'
import { listAuditLogs } from '../api/users'
import { toHubApiError } from '../api/errors'
import HubErrorAlert from '../components/HubErrorAlert'
import PageHeader from '../components/PageHeader'
import { queryKeys } from '../hooks/queryKeys'
import { formatDateTime } from '../utils/format'

export default function AuditPage() {
  const [action, setAction] = useState('')
  const [actorName, setActorName] = useState('')

  const audit = useQuery({
    queryKey: [...queryKeys.audit.list(), action, actorName],
    queryFn: () =>
      listAuditLogs({
        ...(action === '' ? {} : { action }),
        ...(actorName === '' ? {} : { actor_name: actorName }),
        limit: 200,
      }),
  })

  const columns = [
    { title: '时间', dataIndex: 'at', render: (value: string | null) => formatDateTime(value) },
    { title: '操作者', dataIndex: 'actor_name' },
    { title: '动作', dataIndex: 'action' },
    {
      title: '对象',
      key: 'resource',
      render: (_: unknown, row: { resource_type: string | null; resource_id: string | null }) =>
        row.resource_type === null ? '-' : `${row.resource_type}:${row.resource_id ?? '-'}`,
    },
    {
      title: '结果',
      dataIndex: 'result',
      render: (value: string) => (value === 'ok' ? '成功' : value === 'denied' ? '拒绝' : value),
    },
    { title: 'IP', dataIndex: 'ip', render: (value: string | null) => value ?? '-' },
  ]

  return (
    <div>
      <PageHeader title="审计日志" subtitle="最近 200 条写操作与登录记录。" />
      <Space style={{ marginBottom: 12 }}>
        <Input
          placeholder="按动作过滤，如 job.create"
          value={action}
          onChange={(event) => setAction(event.target.value)}
          style={{ width: 240 }}
          aria-label="按动作过滤"
        />
        <Input
          placeholder="按操作者过滤"
          value={actorName}
          onChange={(event) => setActorName(event.target.value)}
          style={{ width: 200 }}
          aria-label="按操作者过滤"
        />
        <Button
          onClick={() => {
            setAction('')
            setActorName('')
          }}
        >
          清除过滤
        </Button>
      </Space>
      {audit.error !== null && audit.error !== undefined && (
        <HubErrorAlert error={toHubApiError(audit.error)} onRetry={() => void audit.refetch()} />
      )}
      <Table
        rowKey="id"
        loading={audit.isLoading}
        pagination={false}
        dataSource={audit.data?.items ?? []}
        columns={columns}
        locale={{ emptyText: '暂无记录' }}
      />
    </div>
  )
}
