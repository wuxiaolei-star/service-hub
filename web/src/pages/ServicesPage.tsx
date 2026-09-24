import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'
import { fetchMe } from '../api/auth'
import { toHubApiError } from '../api/errors'
import type { HubApiError } from '../api/errors'
import {
  createService,
  deleteService,
  getServiceLogs,
  listServices,
  restartService,
  startService,
  stopService,
  updateService,
} from '../api/services'
import type {
  ServiceCreateRequest,
  ServiceMount,
  ServiceRow,
} from '../api/services'
import HubErrorAlert from '../components/HubErrorAlert'
import PageHeader from '../components/PageHeader'
import { queryKeys } from '../hooks/queryKeys'

const DEFAULT_TAIL = 200

/**
 * service-manager outages surface as 502 from the gateway; give them a
 * human-readable hint instead of the raw proxy message.
 */
function withServiceManagerHint(error: HubApiError): HubApiError {
  if (error.status === 502) {
    return { ...error, message: 'service-manager 不可用' }
  }
  return error
}

function DesiredStateTag({ state }: { state: ServiceRow['desired_state'] }) {
  return state === 'RUNNING' ? (
    <Tag color="success">RUNNING</Tag>
  ) : (
    <Tag>STOPPED</Tag>
  )
}

function RuntimeStateTag({ state }: { state: string }) {
  if (state === 'running') {
    return <Tag color="success">{state}</Tag>
  }
  if (state === 'exited') {
    return <Tag color="default">{state}</Tag>
  }
  return <Tag color="warning">{state}</Tag>
}

function parseEnvText(text: string): Record<string, string> | null {
  const env: Record<string, string> = {}
  for (const rawLine of text.split('\n')) {
    const line = rawLine.trim()
    if (line === '') {
      continue
    }
    const separator = line.indexOf('=')
    if (separator <= 0) {
      return null
    }
    env[line.slice(0, separator).trim()] = line.slice(separator + 1).trim()
  }
  return env
}

function parseMountsText(text: string): ServiceMount[] | null {
  const mounts: ServiceMount[] = []
  for (const rawLine of text.split('\n')) {
    const line = rawLine.trim()
    if (line === '') {
      continue
    }
    const parts = line.split(':').map((part) => part.trim())
    if (parts.length < 2 || parts[0] === '' || parts[1] === '') {
      return null
    }
    mounts.push({ source: parts[0], target: parts[1], read_only: parts[2] === 'ro' })
  }
  return mounts
}

interface ServiceFormValues {
  name: string
  image: string
  ports?: { host: string; container: string }[]
  command?: string
}

export default function ServicesPage() {
  const queryClient = useQueryClient()
  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<ServiceRow | null>(null)
  const [envText, setEnvText] = useState('')
  const [mountsText, setMountsText] = useState('')
  const [parseError, setParseError] = useState<string | null>(null)
  const [logTarget, setLogTarget] = useState<ServiceRow | null>(null)
  const [tailText, setTailText] = useState(String(DEFAULT_TAIL))
  const [form] = Form.useForm<ServiceFormValues>()

  const services = useQuery({ queryKey: queryKeys.services.list(), queryFn: listServices })
  const me = useQuery({ queryKey: queryKeys.auth.me(), queryFn: fetchMe, retry: false })
  const isAdmin = me.data?.role === 'admin'

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.services.list() })
  }

  const create = useMutation({ mutationFn: createService, onSuccess: closeEditor })
  // updateService takes (name, request); adapt it to the single-variable shape
  // react-query expects, otherwise the whole payload lands in the name slot.
  const update = useMutation({
    mutationFn: (variables: { name: string; request: ServiceCreateRequest }) =>
      updateService(variables.name, variables.request),
    onSuccess: closeEditor,
  })
  const start = useMutation({ mutationFn: startService, onSuccess: invalidate })
  const stop = useMutation({ mutationFn: stopService, onSuccess: invalidate })
  const restart = useMutation({ mutationFn: restartService, onSuccess: invalidate })
  const remove = useMutation({ mutationFn: deleteService, onSuccess: invalidate })

  const tail = Number.parseInt(tailText, 10)
  const logs = useQuery({
    queryKey: queryKeys.services.logs(logTarget?.name ?? '', Number.isFinite(tail) ? tail : DEFAULT_TAIL),
    queryFn: () => getServiceLogs(logTarget!.name, tail),
    enabled: logTarget !== null && Number.isFinite(tail) && tail > 0,
  })

  function closeEditor() {
    setEditorOpen(false)
    setEditing(null)
    setParseError(null)
  }

  function openCreate() {
    setEditing(null)
    form.resetFields()
    setEnvText('')
    setMountsText('')
    setParseError(null)
    setEditorOpen(true)
  }

  function openEdit(row: ServiceRow) {
    setEditing(row)
    form.resetFields()
    form.setFieldsValue({ name: row.name, image: row.image })
    setEnvText('')
    setMountsText('')
    setParseError(null)
    setEditorOpen(true)
  }

  const handleSubmit = (values: ServiceFormValues) => {
    const env = parseEnvText(envText)
    if (env === null) {
      setParseError('环境变量格式无效，每行应为 k=v')
      return
    }
    const mounts = parseMountsText(mountsText)
    if (mounts === null) {
      setParseError('挂载格式无效，每行应为 source:target[:ro]')
      return
    }
    const command = (values.command ?? '')
      .split(',')
      .map((part) => part.trim())
      .filter((part) => part.length > 0)
    const request: ServiceCreateRequest = {
      name: editing !== null ? editing.name : values.name,
      image: values.image,
      ports: (values.ports ?? []).map((port) => ({
        host: Number(port.host),
        container: Number(port.container),
      })),
      env,
      mounts,
      ...(command.length > 0 ? { command } : {}),
    }
    if (editing !== null) {
      update.mutate({ name: editing.name, request })
    } else {
      create.mutate(request)
    }
  }

  const columns: ColumnsType<ServiceRow> = [
    { title: '名称', dataIndex: 'name' },
    { title: '镜像', dataIndex: 'image' },
    {
      title: '期望状态',
      dataIndex: 'desired_state',
      render: (state: ServiceRow['desired_state']) => <DesiredStateTag state={state} />,
    },
    {
      title: '实际状态',
      key: 'runtime_state',
      render: (_, row) => {
        const state = row.runtime?.state ?? null
        return state === null ? <span>-</span> : <RuntimeStateTag state={state} />
      },
    },
    {
      title: '健康',
      key: 'health',
      render: (_, row) => <span>{row.runtime?.health ?? '-'}</span>,
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, row) => {
        // 启动用于未运行的容器（含 service-manager 不可见时）；停止/重启仅对
        // 正在运行的容器展示，期望状态单独以 Tag 呈现。
        const running = row.runtime?.state === 'running'
        return (
          <Space size={4} wrap>
            {!running && (
              <Button
                size="small"
                loading={start.isPending && start.variables === row.name}
                onClick={() => start.mutate(row.name)}
              >
                启动
              </Button>
            )}
            {running && (
              <Button
                size="small"
                loading={stop.isPending && stop.variables === row.name}
                onClick={() => stop.mutate(row.name)}
              >
                停止
              </Button>
            )}
            {running && (
              <Button
                size="small"
                loading={restart.isPending && restart.variables === row.name}
                onClick={() => restart.mutate(row.name)}
              >
                重启
              </Button>
            )}
            <Button size="small" onClick={() => setLogTarget(row)}>
              日志
            </Button>
            <Button size="small" onClick={() => openEdit(row)}>
              编辑
            </Button>
            {isAdmin && (
              <Popconfirm
                title={`删除服务 ${row.name}？`}
                okText="确定"
                cancelText="取消"
                onConfirm={() => remove.mutate(row.name)}
              >
                <Button danger size="small">
                  删除
                </Button>
              </Popconfirm>
            )}
          </Space>
        )
      },
    },
  ]

  const actionError = start.error ?? stop.error ?? restart.error ?? remove.error

  return (
    <div>
      <PageHeader
        title="服务管理"
        subtitle="部署并管理长期运行的服务容器。"
        extra={
          <Button type="primary" onClick={openCreate}>
            部署服务
          </Button>
        }
      />
      {services.error !== null && services.error !== undefined && (
        <HubErrorAlert
          error={withServiceManagerHint(toHubApiError(services.error))}
          onRetry={() => void services.refetch()}
        />
      )}
      {actionError !== null && actionError !== undefined && (
        <HubErrorAlert
          error={withServiceManagerHint(toHubApiError(actionError))}
          onRetry={invalidate}
        />
      )}
      {(create.isError || update.isError) && (
        <HubErrorAlert
          error={withServiceManagerHint(toHubApiError(create.error ?? update.error))}
        />
      )}
      <Table
        rowKey="name"
        loading={services.isLoading}
        pagination={false}
        dataSource={services.data?.items ?? []}
        columns={columns}
        locale={{ emptyText: '暂无服务' }}
      />
      <Modal
        title={editing !== null ? `编辑服务：${editing.name}` : '部署服务'}
        open={editorOpen}
        onCancel={closeEditor}
        onOk={() => form.submit()}
        confirmLoading={create.isPending || update.isPending}
        okText={editing !== null ? '保存' : '部署'}
        cancelText="取消"
        width={640}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input disabled={editing !== null} placeholder="例如：tile-server" />
          </Form.Item>
          <Form.Item name="image" label="镜像" rules={[{ required: true }]}>
            <Input placeholder="例如：ghcr.io/example/tiles:1.0" />
          </Form.Item>
          <Form.Item label="端口映射" required>
            <Form.List name="ports">
              {(fields, { add, remove: removePort }) => (
                <>
                  {fields.map((field) => (
                    <Space key={field.key} align="baseline" style={{ display: 'flex' }}>
                      <Form.Item
                        name={[field.name, 'host']}
                        label="宿主端口"
                        rules={[
                          { required: true, message: '请输入宿主端口' },
                          { pattern: /^\d+$/, message: '端口必须是数字' },
                        ]}
                      >
                        <Input style={{ width: 120 }} />
                      </Form.Item>
                      <Form.Item
                        name={[field.name, 'container']}
                        label="容器端口"
                        rules={[
                          { required: true, message: '请输入容器端口' },
                          { pattern: /^\d+$/, message: '端口必须是数字' },
                        ]}
                      >
                        <Input style={{ width: 120 }} />
                      </Form.Item>
                      <Button size="small" onClick={() => removePort(field.name)}>
                        移除
                      </Button>
                    </Space>
                  ))}
                  <Button type="dashed" onClick={() => add()}>
                    添加端口
                  </Button>
                </>
              )}
            </Form.List>
          </Form.Item>
          <Form.Item label="环境变量（每行 k=v）" required>
            <Input.TextArea
              aria-label="环境变量"
              rows={3}
              value={envText}
              placeholder={'KEY=value\nTZ=Asia/Shanghai'}
              onChange={(event) => {
                setEnvText(event.target.value)
                setParseError(null)
              }}
            />
          </Form.Item>
          <Form.Item label="挂载（每行 source:target[:ro]）" required>
            <Input.TextArea
              aria-label="挂载"
              rows={3}
              value={mountsText}
              placeholder={'/data:/data:ro\n/cache:/cache'}
              onChange={(event) => {
                setMountsText(event.target.value)
                setParseError(null)
              }}
            />
          </Form.Item>
          <Form.Item name="command" label="启动命令（逗号分隔，可空）">
            <Input placeholder="例如：python,-u,server.py" />
          </Form.Item>
        </Form>
        {parseError !== null && <Alert type="error" showIcon role="alert" message={parseError} />}
      </Modal>
      <Modal
        title={logTarget !== null ? `日志：${logTarget.name}` : '日志'}
        open={logTarget !== null}
        onCancel={() => setLogTarget(null)}
        footer={
          <Button onClick={() => setLogTarget(null)}>关闭</Button>
        }
        width={720}
      >
        <Space style={{ marginBottom: 12 }}>
          <Typography.Text>尾部行数</Typography.Text>
          <Input
            aria-label="日志行数"
            style={{ width: 120 }}
            value={tailText}
            onChange={(event) => setTailText(event.target.value)}
          />
          <Button onClick={() => void logs.refetch()}>刷新</Button>
        </Space>
        {logs.error !== null && logs.error !== undefined ? (
          <HubErrorAlert
            error={withServiceManagerHint(toHubApiError(logs.error))}
            onRetry={() => void logs.refetch()}
          />
        ) : (
          <pre
            aria-label="服务日志"
            style={{
              maxHeight: 360,
              overflow: 'auto',
              margin: 0,
              padding: 12,
              background: 'var(--app-bg-subtle)',
              whiteSpace: 'pre-wrap',
            }}
          >
            {logs.isLoading ? '加载中…' : (logs.data ?? '')}
          </pre>
        )}
      </Modal>
    </div>
  )
}
