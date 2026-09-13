import { useEffect, useRef, useState } from 'react'
import { Button } from 'antd'
import type { JobLogLine } from '../hooks/useJobLogs'

interface JobLogViewerProps {
  events: JobLogLine[]
  isLoading: boolean
}

function formatLine(event: JobLogLine): string {
  const prefix =
    event.type === 'progress'
      ? `[进度 ${event.percent ?? 0}%] `
      : `[${event.level ?? 'INFO'}] `
  return `${prefix}${event.message}`
}

/**
 * Render runner events as plain text inside a scrolling log pane. Auto
 * scrolling follows the tail until the operator scrolls away, and a button
 * resumes following.
 */
export default function JobLogViewer({ events, isLoading }: JobLogViewerProps) {
  const containerRef = useRef<HTMLPreElement>(null)
  const [following, setFollowing] = useState(true)

  useEffect(() => {
    const container = containerRef.current
    if (container === null || !following) {
      return
    }
    container.scrollTop = container.scrollHeight
  }, [events, following])

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

  if (isLoading) {
    return <p>日志加载中…</p>
  }

  return (
    <div className="job-log-viewer">
      {!following && (
        <Button size="small" onClick={resumeFollow}>
          恢复自动滚动
        </Button>
      )}
      <pre
        ref={containerRef}
        onScroll={handleScroll}
        aria-label="任务日志"
        style={{ maxHeight: 420, overflow: 'auto', background: '#0f172a', color: '#e2e8f0', padding: 12 }}
      >
        {events.length === 0 ? '暂无日志' : events.map((event) => formatLine(event)).join('\n')}
      </pre>
    </div>
  )
}
