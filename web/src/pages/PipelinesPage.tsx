import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Form, Input, Modal, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'
import {
  createPipeline,
  executePipeline,
  listPipelineRuns,
  listPipelines,
} from '../api/automation'
import type {
  PipelineExecuteResponse,
  PipelineRow,
  PipelineRunRow,
  PipelineRunState,
  PipelineStep,
} from '../api/automation'
import { toHubApiError } from '../api/errors'
import HubErrorAlert from '../components/HubErrorAlert'
import PageHeader from '../components/PageHeader'
import { queryKeys } from '../hooks/queryKeys'
import { formatDateTime } from '../utils/format'

const RUN_STATE_COLORS: Record<PipelineRunState, string> = {
  PENDING: 'default',
  RUNNING: 'processing',
  SUCCEEDED: 'success',
  FAILED: 'error',
}

function RunStateTag({ state }: { state: PipelineRunState }) {
  return <Tag color={RUN_STATE_COLORS[state]}>{state}</Tag>
}

const DEFAULT_STEPS_TEMPLATE = `[
  {
    "plugin_id": "",
    "version": "",
    "runtime_type": "docker",
    "inputs": {},
    "params": {}
  }
]`

function PipelineRunsTable({ pipelineId }: { pipelineId: number }) {
  const runs = useQuery({
    queryKey: queryKeys.pipelines.runs(pipelineId),
    queryFn: () => listPipelineRuns(pipelineId),
  })

  if (runs.error !== null && runs.error !== undefined) {
    return <HubErrorAlert error={toHubApiError(runs.error)} onRetry={() => void runs.refetch()} />
  }

  const columns: ColumnsType<PipelineRunRow> = [
    { title: '运行 ID', dataIndex: 'id', ellipsis: true },
    {
      title: '状态',
      dataIndex: 'state',
      render: (state: PipelineRunRow['state']) => <RunStateTag state={state} />,
    },
    {
      title: '当前步骤',
      dataIndex: 'current_step',
      render: (value: number | null) => (value === null ? '-' : value),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      render: (value: string) => formatDateTime(value),
    },
  ]

  return (
    <Table
      rowKey="id"
      size="small"
      loading={runs.isLoading}
      pagination={false}
      dataSource={runs.data?.items ?? []}
      columns={columns}
      locale={{ emptyText: '暂无运行记录' }}
    />
  )
}

export default function PipelinesPage() {
  const queryClient = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [form] = Form.useForm()
  const [stepsText, setStepsText] = useState(DEFAULT_STEPS_TEMPLATE)
  const [stepsInvalid, setStepsInvalid] = useState(false)
  const [lastRun, setLastRun] = useState<PipelineExecuteResponse | null>(null)

  const pipelines = useQuery({ queryKey: queryKeys.pipelines.list(), queryFn: listPipelines })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.pipelines.all() })
  }

  const execute = useMutation({
    mutationFn: (pipelineId: number) => executePipeline(pipelineId),
    onSuccess: (result) => {
      setLastRun(result)
      invalidate()
    },
  })
  const create = useMutation({
    mutationFn: (request: { name: string; steps: PipelineStep[] }) => createPipeline(request),
    onSuccess: () => {
      setCreateOpen(false)
      form.resetFields()
      setStepsText(DEFAULT_STEPS_TEMPLATE)
      invalidate()
    },
  })

  const columns: ColumnsType<PipelineRow> = [
    { title: '名称', dataIndex: 'name' },
    {
      title: '步数',
      key: 'steps',
      render: (_, row) => row.steps.length,
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, row) => (
        <Button
          size="small"
          loading={execute.isPending && execute.variables === row.id}
          onClick={() => execute.mutate(row.id)}
        >
          执行
        </Button>
      ),
    },
  ]

  const handleCreate = (values: { name: string }) => {
    let steps: PipelineStep[]
    try {
      const parsed: unknown = JSON.parse(stepsText)
      if (!Array.isArray(parsed)) {
        throw new Error('steps 必须是数组')
      }
      steps = parsed as PipelineStep[]
    } catch {
      setStepsInvalid(true)
      return
    }
    setStepsInvalid(false)
    create.mutate({ name: values.name, steps })
  }

  return (
    <div>
      <PageHeader
        title="管道"
        subtitle="把多个插件步骤串成一次执行。"
        extra={
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            新建管道
          </Button>
        }
      />
      {pipelines.error !== null && pipelines.error !== undefined && (
        <HubErrorAlert
          error={toHubApiError(pipelines.error)}
          onRetry={() => void pipelines.refetch()}
        />
      )}
      {lastRun !== null && (
        <Alert
          type="success"
          showIcon
          closable
          style={{ marginBottom: 16 }}
          message="管道执行已提交"
          description={`run_id: ${lastRun.run_id} · job_id: ${lastRun.job_id} · state: ${lastRun.state}`}
          onClose={() => setLastRun(null)}
        />
      )}
      <Table
        rowKey="id"
        loading={pipelines.isLoading}
        pagination={false}
        dataSource={pipelines.data?.items ?? []}
        columns={columns}
        locale={{ emptyText: '暂无管道' }}
        expandable={{
          expandedRowRender: (row) => <PipelineRunsTable pipelineId={row.id} />,
        }}
      />
      <Modal
        title="新建管道"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={create.isPending}
        okText="创建"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="步骤（JSON）" required>
            <Input.TextArea
              aria-label="步骤 JSON"
              rows={10}
              value={stepsText}
              onChange={(event) => {
                setStepsText(event.target.value)
                setStepsInvalid(false)
              }}
            />
          </Form.Item>
        </Form>
        {stepsInvalid && (
          <Alert type="error" showIcon role="alert" message="JSON 格式无效" />
        )}
      </Modal>
    </div>
  )
}
