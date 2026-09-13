import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'
import {
  createSchedule,
  deleteSchedule,
  disableSchedule,
  enableSchedule,
  listSchedules,
} from '../api/automation'
import type { ScheduleCreateRequest, ScheduleRow } from '../api/automation'
import { toHubApiError } from '../api/errors'
import HubErrorAlert from '../components/HubErrorAlert'
import PageHeader from '../components/PageHeader'
import { queryKeys } from '../hooks/queryKeys'
import { formatDateTime } from '../utils/format'

export default function SchedulesPage() {
  const queryClient = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [form] = Form.useForm()

  const schedules = useQuery({ queryKey: queryKeys.schedules.list(), queryFn: listSchedules })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.schedules.list() })
  }

  const create = useMutation({
    mutationFn: (request: ScheduleCreateRequest) => createSchedule(request),
    onSuccess: () => {
      setCreateOpen(false)
      form.resetFields()
      invalidate()
    },
  })
  const enable = useMutation({
    mutationFn: (scheduleId: number) => enableSchedule(scheduleId),
    onSuccess: invalidate,
  })
  const disable = useMutation({
    mutationFn: (scheduleId: number) => disableSchedule(scheduleId),
    onSuccess: invalidate,
  })
  const remove = useMutation({
    mutationFn: (scheduleId: number) => deleteSchedule(scheduleId),
    onSuccess: invalidate,
  })

  const columns: ColumnsType<ScheduleRow> = [
    { title: '名称', dataIndex: 'name' },
    { title: '插件', dataIndex: 'plugin_id' },
    { title: '版本', dataIndex: 'version' },
    { title: '间隔（分钟）', dataIndex: 'interval_minutes' },
    {
      title: '下次运行',
      dataIndex: 'next_run_at',
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: '最近 Job',
      dataIndex: 'last_job_id',
      render: (value: string | null) => value ?? '-',
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      render: (enabled: boolean) =>
        enabled ? <Tag color="success">启用中</Tag> : <Tag>已停用</Tag>,
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, row) => (
        <Space>
          {row.enabled ? (
            <Button
              size="small"
              loading={disable.isPending && disable.variables === row.id}
              onClick={() => disable.mutate(row.id)}
            >
              停用
            </Button>
          ) : (
            <Button
              size="small"
              loading={enable.isPending && enable.variables === row.id}
              onClick={() => enable.mutate(row.id)}
            >
              启用
            </Button>
          )}
          <Popconfirm
            title="删除该定时任务？"
            okText="确定"
            cancelText="取消"
            onConfirm={() => remove.mutate(row.id)}
          >
            <Button danger size="small">
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="定时任务"
        subtitle="按固定间隔重复执行插件任务。"
        extra={
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            新建定时任务
          </Button>
        }
      />
      {schedules.error !== null && schedules.error !== undefined && (
        <HubErrorAlert
          error={toHubApiError(schedules.error)}
          onRetry={() => void schedules.refetch()}
        />
      )}
      {create.isError && <HubErrorAlert error={toHubApiError(create.error)} />}
      {remove.isError && <HubErrorAlert error={toHubApiError(remove.error)} />}
      <Table
        rowKey="id"
        loading={schedules.isLoading}
        pagination={false}
        dataSource={schedules.data?.items ?? []}
        columns={columns}
        locale={{ emptyText: '暂无定时任务' }}
      />
      <Modal
        title="新建定时任务"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={create.isPending}
        okText="创建"
        cancelText="取消"
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) =>
            create.mutate({
              name: values.name,
              plugin_id: values.plugin_id,
              version: values.version,
              runtime_type: values.runtime_type,
              interval_minutes: values.interval_minutes,
              inputs: {},
              params: {},
            })
          }
        >
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="plugin_id" label="插件 ID" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="version" label="版本" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="runtime_type" label="运行时" rules={[{ required: true }]} initialValue="docker">
            <Select
              options={[
                { value: 'docker', label: 'docker（容器）' },
                { value: 'conda-pack', label: 'conda-pack（进程）' },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="interval_minutes"
            label="间隔（分钟）"
            rules={[{ required: true, message: '请输入执行间隔' }]}
            initialValue={60}
          >
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
