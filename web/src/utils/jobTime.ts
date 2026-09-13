function parseTimestamp(iso: string | null | undefined): number | null {
  if (iso === null || iso === undefined || iso === '') {
    return null
  }
  const time = new Date(iso).getTime()
  return Number.isNaN(time) ? null : time
}

function formatSpan(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  const pad = (value: number) => String(value).padStart(2, '0')
  if (hours > 0) {
    return `${hours} 时 ${pad(minutes)} 分 ${pad(seconds)} 秒`
  }
  return `${minutes} 分 ${pad(seconds)} 秒`
}

/**
 * Derive a human-readable duration without mutating server data: queued jobs
 * count from creation, running jobs from start, and finished jobs between
 * start (or creation) and finish.
 */
export function formatDuration(
  createdAt: string | null | undefined,
  startedAt: string | null | undefined,
  finishedAt: string | null | undefined,
  now: string | number | Date,
): string {
  const created = parseTimestamp(createdAt)
  if (created === null) {
    return '-'
  }
  const started = parseTimestamp(startedAt)
  const finished = parseTimestamp(finishedAt)
  const nowTime =
    typeof now === 'number' ? now : new Date(now).getTime()

  if (finished !== null) {
    return formatSpan(finished - (started ?? created))
  }
  if (started !== null) {
    return formatSpan(nowTime - started)
  }
  return formatSpan(nowTime - created)
}
