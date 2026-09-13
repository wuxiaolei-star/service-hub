import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, Form, Input, Modal, Select, Space, Table, Typography } from 'antd'
import { useState } from 'react'
import { createUser, listUsers, resetUserPassword, disableUser } from '../api/users'
import { toHubApiError } from '../api/errors'
import HubErrorAlert from '../components/HubErrorAlert'
import PageHeader from '../components/PageHeader'
import { queryKeys } from '../hooks/queryKeys'

interface UserRow {
  id: number
  username: string
  role: string
  is_active: boolean
  must_change_password: boolean
}

export default function UsersPage() {
  const queryClient = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [form] = Form.useForm()

  const users = useQuery({ queryKey: queryKeys.users.list(), queryFn: listUsers })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.users.list() })
  }

  const create = useMutation({
    mutationFn: createUser,
    onSuccess: () => {
      setCreateOpen(false)
      form.resetFields()
      invalidate()
    },
  })
  const disable = useMutation({ mutationFn: disableUser, onSuccess: invalidate })
  const resetPassword = useMutation({ mutationFn: resetUserPassword })

  const columns = [
    { title: 'ID', dataIndex: 'id' },
    { title: '用户名', dataIndex: 'username' },
    { title: '角色', dataIndex: 'role' },
    {
      title: '状态',
      dataIndex: 'is_active',
      render: (value: boolean) => (value ? '启用' : '已停用'),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, row: UserRow) => (
        <Space>
          {row.is_active && (
            <Button size="small" onClick={() => disable.mutate(row.id)}>
              停用
            </Button>
          )}
          <Button
            size="small"
            loading={resetPassword.isPending}
            onClick={() =>
              resetPassword.mutate(row.id, {
                onSuccess: (result) =>
                  Modal.info({
                    title: '一次性新口令（关闭后不再显示）',
                    content: result.password,
                  }),
              })
            }
          >
            重置口令
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="用户管理"
        subtitle="管理员维护操作员账号与角色。"
        extra={
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            新建用户
          </Button>
        }
      />
      {users.error !== null && users.error !== undefined && (
        <HubErrorAlert error={toHubApiError(users.error)} onRetry={() => void users.refetch()} />
      )}
      {create.isError && <HubErrorAlert error={toHubApiError(create.error)} />}
      <Table
        rowKey="id"
        loading={users.isLoading}
        pagination={false}
        dataSource={users.data?.items ?? []}
        columns={columns}
      />
      <Modal
        title="新建用户"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={create.isPending}
        okText="创建"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" onFinish={(values) => create.mutate(values)}>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="password"
            label="初始口令"
            rules={[{ required: true, min: 10, message: '口令至少 10 个字符' }]}
          >
            <Input.Password />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]} initialValue="viewer">
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
      <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>
        重置口令只生成一次性明文，请立即转交本人。
      </Typography.Paragraph>
    </div>
  )
}
