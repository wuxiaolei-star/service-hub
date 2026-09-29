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
    details?: unknown
  }
}

function isDetailsRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isHubApiError(value: unknown): value is HubApiError {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const candidate = value as Record<string, unknown>
  return typeof candidate.code === 'string' && typeof candidate.message === 'string'
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

/**
 * 面向控制台用户的错误码文案：后端 message 面向 API 调用方，这里对少数
 * 错误码给出更贴近操作场景的提示。仅覆盖 message，code/details 保留供排查。
 * 插件契约类错误（如作业 error_summary 的 PLUGIN_OUTPUT_MISSING: 前缀）是
 * 插件输出契约信息，保持原文，不在这里翻译。
 */
const FRIENDLY_ERROR_MESSAGES: Record<string, string> = {
  PLUGIN_CONCURRENCY_LIMIT: '该插件正在运行的作业已达到并发上限（可能包含定时调度），请稍后重试',
}

export function toHubApiError(reason: unknown): HubApiError {
  const error = toRawHubApiError(reason)
  const friendly = FRIENDLY_ERROR_MESSAGES[error.code]
  return friendly === undefined ? error : { ...error, message: friendly }
}

function toRawHubApiError(reason: unknown): HubApiError {
  if (axios.isAxiosError(reason)) {
    const payload = reason.response?.data
    if (isErrorEnvelope(payload)) {
      const details = isDetailsRecord(payload.error.details) ? payload.error.details : undefined
      return {
        code: payload.error.code,
        message: payload.error.message,
        ...(details === undefined ? {} : { details }),
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

  if (isHubApiError(reason)) {
    return reason
  }

  if (reason instanceof Error) {
    return { code: 'UNKNOWN_ERROR', message: reason.message }
  }

  return { code: 'UNKNOWN_ERROR', message: '请求失败' }
}
