import { describe, expect, it } from 'vitest'
import { formatDateTime, formatFileSize } from './format'

describe('formatFileSize', () => {
  it('formats byte counts with compact units', () => {
    expect(formatFileSize(512)).toBe('512 B')
    expect(formatFileSize(1536)).toBe('1.50 KB')
  })

  it('returns a dash for invalid byte counts', () => {
    expect(formatFileSize(-1)).toBe('-')
    expect(formatFileSize(Number.NaN)).toBe('-')
  })
})

describe('formatDateTime', () => {
  it('formats a valid ISO timestamp in the local zh-CN shape', () => {
    expect(formatDateTime('2026-09-13T09:08:07Z')).toMatch(
      /^2026\/09\/13 \d{2}:08:07$/,
    )
  })

  it.each([null, undefined, '', 'not-a-date'])(
    'returns a dash for missing or invalid timestamp %s',
    (value) => {
      expect(formatDateTime(value)).toBe('-')
    },
  )
})
