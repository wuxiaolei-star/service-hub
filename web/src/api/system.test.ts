import { describe, expect, test } from 'vitest'
import { parseHubMetrics } from './system'

describe('parseHubMetrics', () => {
  test('parses plain gauge lines and ignores help/type comments', () => {
    const text = [
      '# HELP hub_files_total Stored files.',
      '# TYPE hub_files_total gauge',
      'hub_files_total 3',
      'hub_files_bytes_total 123',
      'hub_jobs_active 1',
    ].join('\n')

    expect(parseHubMetrics(text)).toEqual({
      hub_files_total: 3,
      hub_files_bytes_total: 123,
      hub_jobs_active: 1,
    })
  })

  test('flattens labeled lines into dotted keys and accumulates repeated labels', () => {
    const text = [
      'hub_jobs_total{status="PENDING"} 1',
      'hub_jobs_total{status="SUCCESS"} 2',
      'hub_jobs_total{status="PENDING"} 4',
    ].join('\n')

    expect(parseHubMetrics(text)).toEqual({
      'hub_jobs_total.PENDING': 5,
      'hub_jobs_total.SUCCESS': 2,
    })
  })

  test('returns an empty object for empty or comment-only input', () => {
    expect(parseHubMetrics('')).toEqual({})
    expect(parseHubMetrics('# HELP hub_files_total Stored files.\n# TYPE hub_files_total gauge\n')).toEqual({})
  })

  test('parses float values', () => {
    expect(parseHubMetrics('hub_uptime_seconds 42.1')).toEqual({ hub_uptime_seconds: 42.1 })
  })
})
