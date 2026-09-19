import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Input, Space, message } from 'antd'
import { getJobLogs } from '../api/jobs'
import type { JobLogLine } from '../hooks/useJobLogs'
import { saveBlob } from '../utils/download'

interface JobLogViewerProps {
  events: JobLogLine[]
  isLoading: boolean
  /** Job key used to fetch and download the full log; download is hidden when absent. */
  jobKey?: string
}

const DOWNLOAD_PAGE_LIMIT = 200

function formatLine(event: JobLogLine): string {
  const prefix =
    event.type === 'progress'
      ? `[进度 ${event.percent ?? 0}%] `
      : `[${event.level ?? 'INFO'}] `
  return `${prefix}${event.message}`
}

function lineClass(event: JobLogLine): string | undefined {
  if (event.level === 'ERROR') {
    return 'log-line-error'
  }
  if (event.level === 'WARNING') {
    return 'log-line-warning'
  }
  return undefined
}

function isLogLine(value: unknown): value is JobLogLine {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  return typeof (value as Record<string, unknown>).message === 'string'
}

/**
 * Render runner events as plain text inside a scrolling log pane. Auto
 * scrolling follows the tail until the operator scrolls away, and a button
 * resumes following. A keyword input filters the rendered lines and the
 * download button assembles the full log from cursor pages into one file.
 */
export default function JobLogViewer({ events, isLoading, jobKey }: JobLogViewerProps) {
  const containerRef = useRef<HTMLPreElement>(null)
  const [following, setFollowing] = useState(true)
  const [keyword, setKeyword] = useState('')
  const [downloading, setDownloading] = useState(false)

  const trimmedKeyword = keyword.trim().toLowerCase()
  const visibleEvents = useMemo(
    () =>
      trimmedKeyword === ''
        ? events
        : events.filter((event) => formatLine(event).toLowerCase().includes(trimmedKeyword)),
    [events, trimmedKeyword],
  )

  useEffect(() => {
    const container = containerRef.current
    if (container === null || !following) {
      return
    }
    container.scrollTop = container.scrollHeight
  }, [visibleEvents, following])

  function handleScroll() {
    const container = containerRef.current
    if (container === null) {
      return
    }
    const atBottom = container.scrollTop + container.clientHeight >= container.scrollHeight - 40
    setFollowing(atBottom)
  }

  function resumeFollow() {
    setFollowing(true)
    const container = containerRef.current
    if (container !== null) {
      container.scrollTop = container.scrollHeight
    }
  }

  async function downloadAll() {
    if (jobKey === undefined) {
      return
    }
    setDownloading(true)
    try {
      const lines: string[] = []
      let cursor = 0
      for (;;) {
        const page = await getJobLogs(jobKey, cursor, DOWNLOAD_PAGE_LIMIT)
        for (const raw of page.items) {
          if (isLogLine(raw)) {
            lines.push(formatLine(raw))
          }
        }
        if (page.next_cursor === null) {
          break
        }
        cursor = page.next_cursor
      }
      const blob = new Blob([lines.length > 0 ? `${lines.join('\n')}\n` : ''], {
        type: 'text/plain;charset=utf-8',
      })
      saveBlob(blob, `${jobKey}.log`)
    } catch {
      message.error('日志下载失败')
    } finally {
      setDownloading(false)
    }
  }

  if (isLoading) {
    return <p>日志加载中…</p>
  }

  return (
    <div className="job-log-viewer">
      <Space style={{ marginBottom: 8 }} wrap>
        <Input
          allowClear
          aria-label="日志关键字过滤"
          placeholder="按关键字过滤日志"
          style={{ width: 240 }}
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
        />
        <Button
          disabled={jobKey === undefined}
          loading={downloading}
          onClick={() => void downloadAll()}
        >
          下载日志
        </Button>
        {!following && (
          <Button size="small" onClick={resumeFollow}>
            恢复自动滚动
          </Button>
        )}
      </Space>
      <pre
        ref={containerRef}
        onScroll={handleScroll}
        aria-label="任务日志"
        style={{ maxHeight: 420, overflow: 'auto', background: '#0f172a', color: '#e2e8f0', padding: 12 }}
      >
        {visibleEvents.length === 0
          ? events.length > 0
            ? '暂无匹配日志'
            : '暂无日志'
          : visibleEvents.map((event, index) => (
              <span key={index} className={lineClass(event)}>
                {formatLine(event)}
                {'\n'}
              </span>
            ))}
      </pre>
    </div>
  )
}
