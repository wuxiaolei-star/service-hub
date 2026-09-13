import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Alert, Button, Form, Input, Modal, Select, Table, Typography } from 'antd'
import {
  createApiKey,
  listApiKeys,
  revokeApiKey,
  type ApiKeyRow,
} from '../api/users'
import { toHubApiError } from '../api/errors'
import HubErrorAlert from '../components/HubErrorAlert'
import PageHeader from '../components/PageHeader'
import { queryKeys } from '../hooks/queryKeys'

export default function ApiKeysPage() {
  const queryClient = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [plaintext, setPlaintext] = useState<string | null>(null)
  const [form] = Form.useForm()

  const keys = useQuery({ queryKey: queryKeys.apiKeys.list(), queryFn: listApiKeys })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.apiKeys.list() })
  }

  const create = useMutation({
    mutationFn: createApiKey,
    onSuccess: (result) => {
      setCreateOpen(false)
      setPlaintext(result.key)
      form.resetFields()
      invalidate()
    },
  })
  const revoke = useMutation({ mutationFn: revokeApiKey, onSuccess: invalidate })

  const columns = [
    { title: '名称', dataIndex: 'name' },
    { title: '前缀', dataIndex: 'key_prefix' },
    { title: '角色', dataIndex: 'role' },
    {
      title: '状态',
      dataIndex: 'revoked',
      render: (revoked: boolean) => (revoked ? '已吊销' : '有效'),
    },
    {
      title: '最近使用',
      dataIndex: 'last_used_at',
      render: (value: string | null) => value ?? '-',
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, row: ApiKeyRow) =>
        !row.revoked && (
          <Button danger size="small" onClick={() => revoke.mutate(row.id)}>
            吊销
          </Button>
        ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="API Key 管理"
        subtitle="为业务系统与 hubctl 签发机器账号凭证。"
        extra={
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            新建 Key
          </Button>
        }
      />
      {keys.error !== null && keys.error !== undefined && (
        <HubErrorAlert error={toHubApiError(keys.error)} onRetry={() => void keys.refetch()} />
      )}
      {create.isError && <HubErrorAlert error={toHubApiError(create.error)} />}
      <Table
        rowKey="id"
        loading={keys.isLoading}
        pagination={false}
        dataSource={keys.data?.items ?? []}
        columns={columns}
      />
      {plaintext !== null && (
        <Alert
          type="success"
          showIcon
          style={{ marginTop: 12 }}
          message="Key 只显示这一次，请立即保存"
          description={<Typography.Text copyable>{plaintext}</Typography.Text>}
          closable
          onClose={() => setPlaintext(null)}
        />
      )}
      <Modal
        title="新建 API Key"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={create.isPending}
        okText="创建"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" onFinish={(values) => create.mutate(values)}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="例如：业务系统A" />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]} initialValue="operator">
            <Select
              options={[
                { value: 'viewer', label: 'viewer（只读）' },
                { value: 'operator', label: 'operator（操作）' },
                { value: 'publisher', label: 'publisher（发布）' },
                { value: 'admin', label: 'admin（管理）' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
