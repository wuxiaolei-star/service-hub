import { LoadingOutlined } from '@ant-design/icons'
import { Tag } from 'antd'
import type { BuildStatus, JobStatus } from '../types/api'

const STATUS_META: Record<string, { label: string; color: string; processing?: boolean }> = {
  INSTALLING: { label: '安装中', color: 'blue' },
  READY: { label: '就绪', color: 'green' },
  ENABLED: { label: '已启用', color: 'green' },
  FAILED: { label: '失败', color: 'red' },
  PENDING: { label: '排队中', color: 'blue' },
  PREPARING: { label: '准备中', color: 'blue' },
  RUNNING: { label: '运行中', color: 'cyan', processing: true },
  CANCEL_REQUESTED: { label: '取消请求中', color: 'orange' },
  SUCCESS: { label: '成功', color: 'green' },
  CANCELLED: { label: '已取消', color: 'default' },
  DISABLED: { label: '已禁用', color: 'default' },
  TIMED_OUT: { label: '超时', color: 'red' },
}

export type StatusTagValue = BuildStatus | JobStatus | string

export default function StatusTag({ status }: { status: StatusTagValue }) {
  const meta = STATUS_META[status] ?? { label: '未知状态', color: 'default' }
  return (
    <Tag
      color={meta.color}
      icon={meta.processing ? <LoadingOutlined spin aria-hidden="true" /> : undefined}
      aria-label={`状态：${meta.label}`}
    >
      {meta.label}
    </Tag>
  )
}
