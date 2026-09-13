import { useEffect, useState } from 'react'
import type { JobLogLine } from './useJobLogs'

export type { JobLogLine }

interface StreamMessageEvent {
  data: unknown
}

/** Minimal structural surface of the native EventSource used by the hook. */
export interface StreamEventSource {
  close(): void
  onmessage: ((event: StreamMessageEvent) => void) | null
  onerror: ((event: unknown) => void) | null
  addEventListener(type: string, listener: (event: StreamMessageEvent) => void): void
}

export type EventSourceFactory = (url: string) => StreamEventSource

let eventSourceFactory: EventSourceFactory | null = null

/**
 * Replace the EventSource constructor used by useJobLogStream. Pass null to
 * restore the native constructor. Tests inject a fake because jsdom does not
 * ship EventSource.
 */
export function setEventSourceFactory(factory: EventSourceFactory | null): void {
  eventSourceFactory = factory
}

function defaultFactory(url: string): StreamEventSource | null {
  const ctor = (globalThis as { EventSource?: new (url: string) => StreamEventSource })
    .EventSource
  if (ctor === undefined) {
    return null
  }
  return new ctor(url)
}

function streamUrl(jobId: string): string {
  return `/api/v1/jobs/${encodeURIComponent(jobId)}/logs/stream`
}

interface SseFrame {
  event: string
  data: string
}

/** Parse one raw `event: X\ndata: Y` frame as delivered over the wire. */
function parseSseFrame(raw: string): SseFrame | null {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of raw.split('\n')) {
    const cleanLine = line.endsWith('\r') ? line.slice(0, -1) : line
    if (cleanLine.startsWith('event:')) {
      event = cleanLine.slice('event:'.length).trim()
    } else if (cleanLine.startsWith('data:')) {
      dataLines.push(cleanLine.slice('data:'.length).trimStart())
    }
  }
  if (dataLines.length === 0) {
    return null
  }
  return { event, data: dataLines.join('\n') }
}

function isJobLogLine(value: unknown): value is JobLogLine {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const candidate = value as Record<string, unknown>
  return (
    (candidate.type === 'log' || candidate.type === 'progress') &&
    typeof candidate.message === 'string'
  )
}

interface UseJobLogStreamResult {
  events: JobLogLine[]
  exhausted: boolean
  isLoading: boolean
}

/**
 * Consume the SSE log stream of one job while it is active. Log frames are
 * accumulated in arrival order; the `end` event, a connection error, a missing
 * EventSource implementation, or `active` becoming false all close the
 * stream. `exhausted` marks the end of the stream so callers can fall back to
 * cursor polling for a final full pass.
 */
export function useJobLogStream(jobId: string, active: boolean): UseJobLogStreamResult {
  const [previousJobId, setPreviousJobId] = useState(jobId)
  const [events, setEvents] = useState<JobLogLine[]>([])
  const [exhausted, setExhausted] = useState(false)
  const [isLoading, setIsLoading] = useState(false)

  if (previousJobId !== jobId) {
    setPreviousJobId(jobId)
    setEvents([])
    setExhausted(false)
    setIsLoading(false)
  }

  useEffect(() => {
    if (!active) {
      return
    }

    setIsLoading(true)
    const url = streamUrl(jobId)
    const source = eventSourceFactory !== null ? eventSourceFactory(url) : defaultFactory(url)
    if (source === null) {
      // EventSource is unavailable (e.g. jsdom): degrade to cursor polling.
      setIsLoading(false)
      setExhausted(true)
      return
    }

    let closed = false
    const finish = () => {
      if (closed) {
        return
      }
      closed = true
      source.close()
      setIsLoading(false)
      setExhausted(true)
    }

    const handleNamedEvent =
      (name: string) =>
      (event: StreamMessageEvent): void => {
        if (name === 'end') {
          finish()
          return
        }
        if ((name !== 'log' && name !== 'progress') || typeof event.data !== 'string') {
          return
        }
        try {
          const parsed: unknown = JSON.parse(event.data)
          if (isJobLogLine(parsed)) {
            setIsLoading(false)
            setEvents((current) => [...current, parsed])
          }
        } catch {
          // Ignore malformed payloads instead of tearing down the stream.
        }
      }

    const handleMessage = (event: StreamMessageEvent): void => {
      if (typeof event.data !== 'string') {
        return
      }
      const frame = parseSseFrame(event.data)
      if (frame === null) {
        // Plain JSON without an event line behaves like an unnamed log event.
        handleNamedEvent('log')(event)
        return
      }
      handleNamedEvent(frame.event)({ data: frame.data })
    }

    source.onmessage = handleMessage
    source.onerror = () => {
      finish()
    }
    source.addEventListener('log', handleNamedEvent('log'))
    source.addEventListener('progress', handleNamedEvent('progress'))
    source.addEventListener('end', handleNamedEvent('end'))

    return () => {
      closed = true
      source.close()
    }
  }, [jobId, active])

  return { events, exhausted, isLoading }
}
