import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import DurationTrendChart from './DurationTrendChart'
import type { JobStatsBucket } from '../types/api'

const bucket = (overrides: Partial<JobStatsBucket> = {}): JobStatsBucket => ({
  date: '2026-09-11',
  count: 10,
  success_count: 8,
  p50_ms: 1200,
  p95_ms: 8000,
  ...overrides,
})

describe('DurationTrendChart', () => {
  it('renders bars and duration lines for populated buckets', () => {
    render(
      <DurationTrendChart
        buckets={[
          bucket(),
          bucket({ date: '2026-09-12', p50_ms: null, p95_ms: null }),
        ]}
      />,
    )

    expect(screen.getByTestId('duration-trend-chart')).toBeInTheDocument()
    expect(screen.getAllByTestId(/^total-bar-/).length).toBe(2)
    expect(screen.getAllByTestId(/^success-bar-/).length).toBe(2)
    // The null day is skipped, so only one point per percentile is drawn.
    expect(screen.getAllByTestId('p50-point').length).toBe(1)
    expect(screen.getAllByTestId('p95-point').length).toBe(1)
    expect(screen.getByTestId('duration-trend-legend')).toBeInTheDocument()
  })

  it('breaks the duration line across a day with null percentiles', () => {
    render(
      <DurationTrendChart
        buckets={[
          bucket({ date: '2026-09-11' }),
          bucket({ date: '2026-09-12', p50_ms: null, p95_ms: null }),
          bucket({ date: '2026-09-13' }),
        ]}
      />,
    )

    // Two contiguous segments split by the null day in the middle.
    expect(screen.getAllByTestId('duration-p50-line').length).toBe(2)
    expect(screen.getAllByTestId('duration-p95-line').length).toBe(2)
    expect(screen.getAllByTestId('p50-point').length).toBe(2)
  })

  it('renders Empty when there are no buckets', () => {
    render(<DurationTrendChart buckets={[]} />)
    expect(screen.getByText('暂无任务耗时数据')).toBeInTheDocument()
  })

  it('renders Empty when every bucket is all zero', () => {
    render(
      <DurationTrendChart
        buckets={[bucket({ count: 0, success_count: 0, p50_ms: null, p95_ms: null })]}
      />,
    )
    expect(screen.getByText('暂无任务耗时数据')).toBeInTheDocument()
  })
})
