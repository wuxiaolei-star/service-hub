import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setEventSourceFactory, useJobLogStream } from './useJobLogStream'
import type { StreamEventSource } from './useJobLogStream'

class FakeEventSource implements StreamEventSource {
  onmessage: ((event: { data: unknown }) => void) | null = null
  onerror: ((event: unknown) => void) | null = null
  closed = false

  private readonly listeners = new Map<string, (event: { data: unknown }) => void>()

  addEventListener(type: string, listener: (event: { data: unknown }) => void): void {
    this.listeners.set(type, listener)
  }

  close(): void {
    this.closed = true
  }

  /** Deliver a raw wire-format frame through the unnamed-message channel. */
  emitMessage(data: string): void {
    this.onmessage?.({ data })
  }

  /** Deliver a parsed named event the way a native EventSource would. */
  emitNamed(type: string, data: unknown): void {
    this.listeners.get(type)?.({ data })
  }

  fail(): void {
    this.onerror?.(new Event('error'))
  }
}

const sources: FakeEventSource[] = []
let lastUrl = ''

function installFake(): void {
  setEventSourceFactory((url: string) => {
    lastUrl = url
    const source = new FakeEventSource()
    sources.push(source)
    return source
  })
}

function renderStream(jobId = 'job-1', active = true) {
  return renderHook(({ id, enabled }: { id: string; enabled: boolean }) => useJobLogStream(id, enabled), {
    initialProps: { id: jobId, enabled: active },
  })
}

describe('useJobLogStream', () => {
  beforeEach(() => {
    sources.length = 0
    lastUrl = ''
  })

  afterEach(() => {
    setEventSourceFactory(null)
  })

  it('accumulates raw SSE log frames delivered through onmessage', () => {
    installFake()
    const { result } = renderStream()

    expect(lastUrl).toBe('/api/v1/jobs/job-1/logs/stream')

    act(() => {
      sources[0].emitMessage('event: log\ndata: {"type":"log","message":"step 1"}\n\n')
    })
    act(() => {
      sources[0].emitMessage('event: log\ndata: {"type":"progress","message":"half","percent":50}\n\n')
    })

    expect(result.current.events).toEqual([
      { type: 'log', message: 'step 1' },
      { type: 'progress', message: 'half', percent: 50 },
    ])
    expect(result.current.isLoading).toBe(false)
    expect(result.current.exhausted).toBe(false)
  })

  it('closes and reports exhausted when the end event arrives', () => {
    installFake()
    const { result } = renderStream()

    act(() => {
      sources[0].emitMessage('event: end\ndata: {}\n\n')
    })

    expect(result.current.exhausted).toBe(true)
    expect(sources[0].closed).toBe(true)
  })

  it('handles native-style named events via addEventListener', () => {
    installFake()
    const { result } = renderStream()

    act(() => {
      sources[0].emitNamed('log', JSON.stringify({ type: 'log', level: 'INFO', message: 'named' }))
    })

    expect(result.current.events).toEqual([{ type: 'log', level: 'INFO', message: 'named' }])

    act(() => {
      sources[0].emitNamed('end', '{}')
    })

    expect(result.current.exhausted).toBe(true)
    expect(sources[0].closed).toBe(true)
  })

  it('closes the stream when active turns false', () => {
    installFake()
    const { rerender } = renderStream()

    rerender({ id: 'job-1', enabled: false })

    expect(sources[0].closed).toBe(true)
  })

  it('degrades to exhausted when EventSource is unavailable', () => {
    vi.stubGlobal('EventSource', undefined)

    const { result } = renderStream()

    expect(result.current.exhausted).toBe(true)
    expect(result.current.events).toEqual([])
    expect(result.current.isLoading).toBe(false)
    vi.unstubAllGlobals()
  })

  it('closes and reports exhausted when the connection errors', () => {
    installFake()
    const { result } = renderStream()

    act(() => {
      sources[0].fail()
    })

    expect(result.current.exhausted).toBe(true)
    expect(sources[0].closed).toBe(true)
  })

  it('ignores malformed frames without tearing down the stream', () => {
    installFake()
    const { result } = renderStream()

    act(() => {
      sources[0].emitMessage('event: log\ndata: not-json\n\n')
    })
    act(() => {
      sources[0].emitMessage('event: log\ndata: {"type":"log","message":"recovered"}\n\n')
    })

    expect(result.current.events).toEqual([{ type: 'log', message: 'recovered' }])
    expect(result.current.exhausted).toBe(false)
  })
})
