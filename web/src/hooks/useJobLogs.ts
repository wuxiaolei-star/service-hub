import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { getJobLogs } from '../api/jobs'
import { queryKeys } from './queryKeys'

export interface JobLogLine {
  type: 'log' | 'progress'
  message: string
  level?: string
  percent?: number
}

const LOG_PAGE_LIMIT = 200
const POLL_INTERVAL_MS = 2000

function isJobLogLine(value: unknown): value is JobLogLine {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const candidate = value as Record<string, unknown>
  return typeof candidate.message === 'string'
}

interface UseJobLogsResult {
  events: JobLogLine[]
  exhausted: boolean
  isLoading: boolean
}

/**
 * Accumulate runner event pages in cursor order. Pages are keyed by cursor,
 * so a refetch of the same page is suppressed instead of being appended
 * twice. Polling continues while the job is active and stops at terminal
 * state (active=false) once the log is exhausted; unmount cancels queries
 * through TanStack Query. `enabled` lets callers park the polling entirely
 * (e.g. while the SSE log stream is healthy).
 */
export function useJobLogs(jobId: string, active: boolean, enabled = true): UseJobLogsResult {
  const [events, setEvents] = useState<JobLogLine[]>([])
  const [cursor, setCursor] = useState(0)
  const [exhausted, setExhausted] = useState(false)
  const appliedPageRef = useRef<string | null>(null)

  const query = useQuery({
    queryKey: queryKeys.jobs.logs(jobId, cursor, LOG_PAGE_LIMIT),
    queryFn: () => getJobLogs(jobId, cursor, LOG_PAGE_LIMIT),
    enabled: enabled && (active || !exhausted),
    refetchInterval: active ? POLL_INTERVAL_MS : false,
  })

  const data = query.data
  useEffect(() => {
    if (data === undefined) {
      return
    }
    const items: JobLogLine[] = []
    for (const raw of data.items) {
      if (isJobLogLine(raw)) {
        items.push(raw)
      }
    }
    const signature = `${cursor}:${data.items.length}:${
      items[0]?.message ?? ''
    }:${items[items.length - 1]?.message ?? ''}`
    if (appliedPageRef.current === signature) {
      return
    }
    appliedPageRef.current = signature
    if (items.length > 0) {
      setEvents((current) => [...current, ...items])
    }
    if (data.next_cursor === null) {
      setExhausted(true)
    } else {
      setCursor(data.next_cursor)
    }
  }, [data, cursor])

  return { events, exhausted, isLoading: query.isLoading }
}
