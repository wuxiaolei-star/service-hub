import { Tag } from 'antd'
import type { BuildStatus, JobStatus } from '../types/api'

const STATUS_META: Record<string, { label: string; color: string }> = {
  INSTALLING: { label: '安装中', color: 'processing' },
  READY: { label: '就绪', color: 'cyan' },
  ENABLED: { label: '已启用', color: 'success' },
  FAILED: { label: '失败', color: 'error' },
  PENDING: { label: '排队中', color: 'default' },
  PREPARING: { label: '准备中', color: 'processing' },
  RUNNING: { label: '运行中', color: 'processing' },
  CANCEL_REQUESTED: { label: '取消请求中', color: 'warning' },
  SUCCESS: { label: '成功', color: 'success' },
  CANCELLED: { label: '已取消', color: 'default' },
  TIMED_OUT: { label: '超时', color: 'error' },
}

export type StatusTagValue = BuildStatus | JobStatus | string

export default function StatusTag({ status }: { status: StatusTagValue }) {
  const meta = STATUS_META[status] ?? { label: '未知状态', color: 'default' }
  return (
    <Tag color={meta.color} aria-label={`状态：${meta.label}`}>
      {meta.label}
    </Tag>
  )
}
