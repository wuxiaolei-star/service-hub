const FILE_SIZE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB'] as const

/** Format a byte count as a compact human-readable size string. */
export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return '-'
  }
  let value = bytes
  let unitIndex = 0
  while (value >= 1024 && unitIndex < FILE_SIZE_UNITS.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  const formatted =
    unitIndex === 0 ? String(value) : value.toFixed(value >= 100 ? 0 : value >= 10 ? 1 : 2)
  return `${formatted} ${FILE_SIZE_UNITS[unitIndex]}`
}

/**
 * Compact display form of a job ID: long IDs keep a 14-char prefix so table
 * columns stay readable; the full ID remains copyable next to it.
 */
export function shortJobId(jobId: string): string {
  return jobId.length <= 14 ? jobId : jobId.slice(0, 14)
}

/** Render an ISO timestamp in the local timezone, or a dash for null/invalid values. */
export function formatDateTime(iso: string | null | undefined): string {
  if (iso === null || iso === undefined || iso === '') {
    return '-'
  }
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) {
    return '-'
  }
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}
