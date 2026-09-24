import { Empty } from 'antd'
import type { JobStatsBucket } from '../types/api'

interface DurationTrendChartProps {
  buckets: JobStatsBucket[]
}

const WIDTH = 760
const HEIGHT = 260
const PAD_LEFT = 48
const PAD_RIGHT = 44
const PAD_TOP = 16
const PAD_BOTTOM = 28
const TICK_COUNT = 4

const PLOT_WIDTH = WIDTH - PAD_LEFT - PAD_RIGHT
const PLOT_HEIGHT = HEIGHT - PAD_TOP - PAD_BOTTOM
const PLOT_BOTTOM = PAD_TOP + PLOT_HEIGHT

interface Point {
  x: number
  y: number
}

function niceMax(value: number): number {
  if (value <= 0) return 1
  const exponent = Math.floor(Math.log10(value))
  const magnitude = 10 ** exponent
  const residual = value / magnitude
  const factor = residual <= 1 ? 1 : residual <= 2 ? 2 : residual <= 5 ? 5 : 10
  return factor * magnitude
}

function durationUnit(maxMs: number): { divisor: number; label: string } {
  if (maxMs < 1000) return { divisor: 1, label: 'ms' }
  if (maxMs < 60_000) return { divisor: 1000, label: 's' }
  if (maxMs < 3_600_000) return { divisor: 60_000, label: 'min' }
  return { divisor: 3_600_000, label: 'h' }
}

function formatTickLabel(value: number, divisor: number): string {
  const scaled = value / divisor
  if (divisor === 1) return String(Math.round(scaled))
  if (scaled >= 10) return String(Math.round(scaled))
  return scaled.toFixed(1).replace(/\.0$/, '')
}

function ticks(max: number): number[] {
  const top = niceMax(max)
  return Array.from({ length: TICK_COUNT + 1 }, (_, index) => (top * index) / TICK_COUNT)
}

function centerX(index: number, count: number): number {
  const slot = PLOT_WIDTH / count
  return PAD_LEFT + slot * (index + 0.5)
}

function yFor(value: number, max: number): number {
  if (max <= 0) return PLOT_BOTTOM
  return PLOT_BOTTOM - (value / max) * PLOT_HEIGHT
}

function dateLabel(date: string): string {
  // The contract emits `YYYY-MM-DD`; trimming the year keeps the axis compact.
  return date.slice(5)
}

function splitSegments(points: (Point | null)[]): Point[][] {
  const segments: Point[][] = []
  let current: Point[] = []
  for (const point of points) {
    if (point === null) {
      if (current.length > 0) {
        segments.push(current)
        current = []
      }
    } else {
      current.push(point)
    }
  }
  if (current.length > 0) segments.push(current)
  return segments
}

function linePoint(
  bucket: JobStatsBucket,
  index: number,
  count: number,
  max: number,
  field: 'p50_ms' | 'p95_ms',
): Point | null {
  const value = bucket[field]
  if (value === null) return null
  return { x: centerX(index, count), y: yFor(value, max) }
}

const AXIS_TEXT_STYLE = { fill: 'var(--app-text-secondary)', fontSize: 10 }

export default function DurationTrendChart({ buckets }: DurationTrendChartProps) {
  const count = buckets.length
  const hasData = buckets.some(
    (bucket) => bucket.count > 0 || bucket.p50_ms !== null || bucket.p95_ms !== null,
  )
  if (!hasData) {
    return <Empty description="暂无任务耗时数据" />
  }

  const countMax = Math.max(0, ...buckets.map((bucket) => bucket.count))
  const durMax = Math.max(
    0,
    ...buckets.flatMap((bucket) =>
      [bucket.p50_ms, bucket.p95_ms].filter((value): value is number => value !== null),
    ),
  )
  const hasDuration = durMax > 0

  const countAxisMax = niceMax(countMax)
  const durAxisMax = niceMax(durMax)
  const duration = durationUnit(durAxisMax)

  const countTicks = ticks(countAxisMax)
  const durationTicks = hasDuration ? ticks(durAxisMax) : []
  const gridTicks = hasDuration ? durationTicks : countTicks
  const gridMax = hasDuration ? durAxisMax : countAxisMax

  const p50Points = buckets.map((bucket, index) => linePoint(bucket, index, count, durAxisMax, 'p50_ms'))
  const p95Points = buckets.map((bucket, index) => linePoint(bucket, index, count, durAxisMax, 'p95_ms'))
  const p50Segments = splitSegments(p50Points)
  const p95Segments = splitSegments(p95Points)

  const slot = PLOT_WIDTH / count
  const barWidth = Math.min(slot * 0.6, 26)
  const labelStride = Math.max(1, Math.ceil(count / 8))

  return (
    <div>
      <div
        data-testid="duration-trend-legend"
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 16,
          marginBottom: 8,
          color: 'var(--app-text-secondary)',
          fontSize: 12,
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 10, height: 10, borderRadius: 2, background: 'var(--app-accent)', display: 'inline-block' }} />
          成功任务数
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 10, height: 10, borderRadius: 2, background: 'var(--app-border)', display: 'inline-block' }} />
          任务总量
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <svg width="14" height="4" aria-hidden="true">
            <line x1="0" y1="2" x2="14" y2="2" stroke="var(--app-accent)" strokeWidth="2" />
          </svg>
          P50 耗时
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <svg width="14" height="4" aria-hidden="true">
            <line x1="0" y1="2" x2="14" y2="2" stroke="var(--app-accent)" strokeWidth="2" strokeDasharray="3 2" />
          </svg>
          P95 耗时
        </span>
      </div>
      <svg
        role="img"
        aria-label="任务耗时趋势图"
        data-testid="duration-trend-chart"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        style={{ width: '100%', height: 'auto', display: 'block' }}
      >
        {gridTicks.map((tick) => (
          <line
            key={`grid-${tick}`}
            x1={PAD_LEFT}
            x2={WIDTH - PAD_RIGHT}
            y1={yFor(tick, gridMax)}
            y2={yFor(tick, gridMax)}
            style={{ stroke: 'var(--app-border)' }}
            strokeWidth="1"
          />
        ))}
        <line
          x1={PAD_LEFT}
          x2={WIDTH - PAD_RIGHT}
          y1={PLOT_BOTTOM}
          y2={PLOT_BOTTOM}
          style={{ stroke: 'var(--app-border)' }}
          strokeWidth="1"
        />

        {hasDuration &&
          durationTicks.map((tick) => (
            <text
              key={`dur-${tick}`}
              x={PAD_LEFT - 8}
              y={yFor(tick, durAxisMax) + 4}
              textAnchor="end"
              style={AXIS_TEXT_STYLE}
            >
              {formatTickLabel(tick, duration.divisor)}
              {duration.label}
            </text>
          ))}

        {countTicks.map((tick) => (
          <text
            key={`count-${tick}`}
            x={WIDTH - PAD_RIGHT + 8}
            y={yFor(tick, countAxisMax) + 4}
            textAnchor="start"
            style={AXIS_TEXT_STYLE}
          >
            {String(Math.round(tick))}
          </text>
        ))}

        {buckets.map((bucket, index) => {
          const cx = centerX(index, count)
          const totalHeight = countAxisMax > 0 ? (bucket.count / countAxisMax) * PLOT_HEIGHT : 0
          const successHeight =
            countAxisMax > 0 ? (bucket.success_count / countAxisMax) * PLOT_HEIGHT : 0
          return (
            <g key={bucket.date}>
              <rect
                x={cx - barWidth / 2}
                y={PLOT_BOTTOM - totalHeight}
                width={barWidth}
                height={totalHeight}
                rx={2}
                style={{ fill: 'var(--app-border)' }}
                data-testid={`total-bar-${index}`}
              />
              <rect
                x={cx - barWidth / 2}
                y={PLOT_BOTTOM - successHeight}
                width={barWidth}
                height={successHeight}
                rx={2}
                style={{ fill: 'var(--app-accent)' }}
                data-testid={`success-bar-${index}`}
              />
            </g>
          )
        })}

        {p50Segments.map((segment, index) => (
          <polyline
            key={`p50-seg-${index}`}
            points={segment.map((point) => `${point.x},${point.y}`).join(' ')}
            fill="none"
            strokeWidth="2"
            style={{ stroke: 'var(--app-accent)' }}
            data-testid="duration-p50-line"
          />
        ))}
        {p95Segments.map((segment, index) => (
          <polyline
            key={`p95-seg-${index}`}
            points={segment.map((point) => `${point.x},${point.y}`).join(' ')}
            fill="none"
            strokeWidth="2"
            strokeDasharray="4 4"
            style={{ stroke: 'var(--app-accent)', strokeOpacity: 0.45 }}
            data-testid="duration-p95-line"
          />
        ))}

        {p50Points.map((point, index) =>
          point === null ? null : (
            <circle
              key={`p50-pt-${index}`}
              cx={point.x}
              cy={point.y}
              r="3"
              style={{ fill: 'var(--app-accent)' }}
              data-testid="p50-point"
            />
          ),
        )}
        {p95Points.map((point, index) =>
          point === null ? null : (
            <circle
              key={`p95-pt-${index}`}
              cx={point.x}
              cy={point.y}
              r="3"
              style={{ fill: 'var(--app-accent)', fillOpacity: 0.45 }}
              data-testid="p95-point"
            />
          ),
        )}

        {buckets.map((bucket, index) =>
          index % labelStride === 0 || index === count - 1 ? (
            <text
              key={`x-${index}`}
              x={centerX(index, count)}
              y={HEIGHT - 8}
              textAnchor="middle"
              style={AXIS_TEXT_STYLE}
            >
              {dateLabel(bucket.date)}
            </text>
          ) : null,
        )}
      </svg>
    </div>
  )
}
