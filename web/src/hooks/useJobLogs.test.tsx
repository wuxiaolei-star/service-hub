import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useJobLogs } from './useJobLogs'
import { getJobLogs } from '../api/jobs'
import type { JobLogResponse } from '../types/api'

vi.mock('../api/jobs', () => ({ getJobLogs: vi.fn() }))

const mockedGetJobLogs = vi.mocked(getJobLogs)

const logItem = (message: string) => ({ type: 'log' as const, level: 'INFO', message })

function renderHookWith(jobId: string, active: boolean) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return renderHook(() => useJobLogs(jobId, active), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  })
}

describe('useJobLogs', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('accumulates pages in order and stops when exhausted and inactive', async () => {
    mockedGetJobLogs
      .mockResolvedValueOnce({
        items: [logItem('first'), logItem('second')],
        next_cursor: 2,
      } as JobLogResponse)
      .mockResolvedValueOnce({
        items: [logItem('third')],
        next_cursor: null,
      } as JobLogResponse)

    const { result } = renderHookWith('job-1', false)

    await waitFor(() => expect(result.current.events.map((event) => event.message)).toEqual([
      'first',
      'second',
      'third',
    ]))
    expect(result.current.exhausted).toBe(true)
    expect(mockedGetJobLogs).toHaveBeenCalledTimes(2)
    expect(mockedGetJobLogs).toHaveBeenNthCalledWith(1, 'job-1', 0, 200)
    expect(mockedGetJobLogs).toHaveBeenNthCalledWith(2, 'job-1', 2, 200)
  })

  it('keeps polling while the job is active even after catching up', async () => {
    mockedGetJobLogs.mockResolvedValue({
      items: [logItem('first')],
      next_cursor: null,
    } as JobLogResponse)

    const { result, rerender } = renderHookWith('job-1', true)
    await waitFor(() => expect(result.current.events).toHaveLength(1))
    const callsAfterFirstPage = mockedGetJobLogs.mock.calls.length
    expect(callsAfterFirstPage).toBeGreaterThanOrEqual(1)

    rerender({ jobId: 'job-1', active: false })
    await waitFor(() => expect(result.current.exhausted).toBe(true))
  })

  it('suppresses duplicate appends when the same page is refetched', async () => {
    mockedGetJobLogs.mockResolvedValue({
      items: [logItem('first')],
      next_cursor: null,
    } as JobLogResponse)

    const { result } = renderHookWith('job-1', true)
    await waitFor(() => expect(result.current.events).toHaveLength(1))

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50))
    })
    expect(result.current.events).toHaveLength(1)
  })

  it('stops fetching after unmount', async () => {
    mockedGetJobLogs.mockResolvedValue({
      items: [logItem('first')],
      next_cursor: null,
    } as JobLogResponse)

    const { unmount } = renderHookWith('job-1', true)
    await waitFor(() => expect(mockedGetJobLogs).toHaveBeenCalled())
    const calls = mockedGetJobLogs.mock.calls.length
    unmount()
    await new Promise((resolve) => setTimeout(resolve, 30))
    expect(mockedGetJobLogs.mock.calls.length).toBe(calls)
  })
})
