import { describe, expect, it } from 'vitest'
import { formatDuration } from './jobTime'

const CREATED = '2026-09-13T09:00:00Z'
const STARTED = '2026-09-13T09:00:05Z'
const FINISHED = '2026-09-13T09:01:35Z'
const NOW = '2026-09-13T09:02:05Z'

describe('formatDuration', () => {
  it('shows elapsed queue time before start', () => {
    expect(formatDuration(CREATED, null, null, NOW)).toBe('2 分 05 秒')
  })

  it('shows running duration from start', () => {
    expect(formatDuration(CREATED, STARTED, null, NOW)).toBe('2 分 00 秒')
  })

  it('shows finished duration between start and finish', () => {
    expect(formatDuration(CREATED, STARTED, FINISHED, NOW)).toBe('1 分 30 秒')
  })

  it('falls back to created time when finish has no start', () => {
    expect(formatDuration(CREATED, null, FINISHED, NOW)).toBe('1 分 35 秒')
  })

  it('renders a dash for invalid or missing timestamps', () => {
    expect(formatDuration('not-a-time', STARTED, FINISHED, NOW)).toBe('-')
    expect(formatDuration(null, null, null, NOW)).toBe('-')
  })

  it('renders hours for long durations', () => {
    const finished = '2026-09-13T11:02:05Z'
    expect(formatDuration(CREATED, STARTED, finished, NOW)).toBe('2 时 02 分 00 秒')
  })
})
