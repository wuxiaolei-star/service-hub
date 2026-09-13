import axios from 'axios'

export interface HubApiError {
  code: string
  message: string
  details?: Record<string, unknown>
  status?: number
}

interface ErrorEnvelope {
  success: false
  error: {
    code: string
    message: string
    details?: Record<string, unknown>
  }
}

function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== 'object' || value === null) {
    return false
  }

  const envelope = value as Record<string, unknown>
  if (envelope.success !== false || typeof envelope.error !== 'object' || envelope.error === null) {
    return false
  }

  const error = envelope.error as Record<string, unknown>
  return typeof error.code === 'string' && typeof error.message === 'string'
}

export function toHubApiError(reason: unknown): HubApiError {
  if (axios.isAxiosError(reason)) {
    const payload = reason.response?.data
    if (isErrorEnvelope(payload)) {
      return {
        code: payload.error.code,
        message: payload.error.message,
        ...(payload.error.details === undefined ? {} : { details: payload.error.details }),
        ...(reason.response?.status === undefined ? {} : { status: reason.response.status }),
      }
    }

    if (reason.response === undefined) {
      return { code: 'NETWORK_ERROR', message: reason.message || '网络连接失败' }
    }

    return {
      code: 'HTTP_ERROR',
      message: reason.message || '请求失败',
      status: reason.response.status,
    }
  }

  if (reason instanceof Error) {
    return { code: 'UNKNOWN_ERROR', message: reason.message }
  }

  return { code: 'UNKNOWN_ERROR', message: '请求失败' }
}
