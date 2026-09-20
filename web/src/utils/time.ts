import { formatDateTime } from './format'

/**
 * Render an ISO timestamp as a coarse relative time ("3 小时前"), falling
 * back to the absolute timestamp for anything older than a week, invalid
 * values, or timestamps in the future.
 */
export function formatRelative(iso: string | null | undefined, now: number = Date.now()): string {
  if (iso === null || iso === undefined || iso === '') {
    return '-'
  }
  const time = new Date(iso).getTime()
  if (Number.isNaN(time)) {
    return '-'
  }
  const diff = now - time
  if (diff <= 0) {
    return formatDateTime(iso)
  }
  const minute = 60_000
  const hour = 3_600_000
  const day = 86_400_000
  if (diff < minute) {
    return '刚刚'
  }
  if (diff < hour) {
    return `${Math.floor(diff / minute)} 分钟前`
  }
  if (diff < day) {
    return `${Math.floor(diff / hour)} 小时前`
  }
  if (diff < 7 * day) {
    return `${Math.floor(diff / day)} 天前`
  }
  return formatDateTime(iso)
}
