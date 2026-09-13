import type { HealthResponse, SystemInfoResponse } from '../types/api'
import { apiClient } from './client'

export async function getHealth(): Promise<HealthResponse> {
  const response = await apiClient.get<HealthResponse>('/system/health')
  return response.data
}

export async function getSystemInfo(): Promise<SystemInfoResponse> {
  const response = await apiClient.get<SystemInfoResponse>('/system/info')
  return response.data
}

export async function getMetrics(): Promise<string> {
  const response = await apiClient.get<string>('/system/metrics', { responseType: 'text' })
  return response.data
}

const METRICS_LINE_PATTERN =
  /^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)$/

function metricKey(name: string, labels: string | undefined): string {
  if (labels === undefined) {
    return name
  }
  const pairs = labels.slice(1, -1).split(',')
  const values = pairs.map((pair) => {
    const separator = pair.indexOf('=')
    const raw = separator === -1 ? '' : pair.slice(separator + 1).trim()
    return raw.replace(/^"(.*)"$/, '$1')
  })
  return [name, ...values].join('.')
}

/**
 * Parse the Prometheus text exposition served by /system/metrics.
 * Plain gauges keep their metric name; labeled series are flattened to
 * `name.labelValue` keys (values of identical keys are summed).
 */
export function parseHubMetrics(text: string): Record<string, number> {
  const result: Record<string, number> = {}
  for (const rawLine of text.split('\n')) {
    const line = rawLine.trim()
    if (line === '' || line.startsWith('#')) {
      continue
    }
    const match = METRICS_LINE_PATTERN.exec(line)
    if (match === null) {
      continue
    }
    const [, name, labels, valueText] = match
    const value = Number(valueText)
    if (Number.isNaN(value)) {
      continue
    }
    const key = metricKey(name, labels)
    result[key] = (result[key] ?? 0) + value
  }
  return result
}
