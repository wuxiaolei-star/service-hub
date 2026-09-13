import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import StatusTag from './StatusTag'
import type { BuildStatus, JobStatus } from '../types/api'

describe('StatusTag', () => {
  it.each<[BuildStatus, string]>([
    ['INSTALLING', '安装中'],
    ['READY', '就绪'],
    ['ENABLED', '已启用'],
    ['FAILED', '失败'],
  ])('renders build status %s as %s', (status, label) => {
    render(<StatusTag status={status} />)
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it.each<[JobStatus, string]>([
    ['PENDING', '排队中'],
    ['PREPARING', '准备中'],
    ['RUNNING', '运行中'],
    ['CANCEL_REQUESTED', '取消请求中'],
    ['SUCCESS', '成功'],
    ['FAILED', '失败'],
    ['CANCELLED', '已取消'],
    ['TIMED_OUT', '超时'],
  ])('renders job status %s as %s', (status, label) => {
    render(<StatusTag status={status} />)
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('marks unknown status explicitly instead of rendering raw text', () => {
    render(<StatusTag status={'UNKNOWN' as JobStatus} />)
    expect(screen.getByText('未知状态')).toBeInTheDocument()
  })
})
