import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import HubErrorAlert from './HubErrorAlert'
import type { HubApiError } from '../api/errors'

const error: HubApiError = {
  code: 'PLUGIN_NOT_FOUND',
  message: '插件不存在',
  details: { plugin_id: 'missing_plugin' },
}

describe('HubErrorAlert', () => {
  it('shows the server message and error code', () => {
    render(<HubErrorAlert error={error} />)
    expect(screen.getByText('插件不存在')).toBeInTheDocument()
    expect(screen.getByText(/PLUGIN_NOT_FOUND/)).toBeInTheDocument()
  })

  it('keeps error details collapsed until expanded', async () => {
    const user = userEvent.setup()
    render(<HubErrorAlert error={error} />)

    expect(screen.queryByText(/missing_plugin/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /错误详情/ }))
    expect(screen.getByText(/missing_plugin/)).toBeInTheDocument()
  })

  it('omits the details toggle when details are absent', () => {
    render(<HubErrorAlert error={{ code: 'NETWORK_ERROR', message: '网络连接失败' }} />)
    expect(screen.queryByRole('button', { name: /错误详情/ })).not.toBeInTheDocument()
  })

  it('renders a retry action when provided', async () => {
    const user = userEvent.setup()
    const onRetry = vi.fn()
    render(<HubErrorAlert error={error} onRetry={onRetry} />)

    await user.click(screen.getByRole('button', { name: '重试' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('shows the friendly concurrency-limit copy while keeping the error code', () => {
    render(
      <HubErrorAlert
        error={{
          code: 'PLUGIN_CONCURRENCY_LIMIT',
          message: '该插件正在运行的作业已达到并发上限（可能包含定时调度），请稍后重试',
          status: 409,
        }}
      />,
    )
    expect(screen.getByText('该插件正在运行的作业已达到并发上限（可能包含定时调度），请稍后重试')).toBeInTheDocument()
    expect(screen.getByText(/PLUGIN_CONCURRENCY_LIMIT/)).toBeInTheDocument()
  })
})
