import { AxiosError, AxiosHeaders } from 'axios'
import { describe, expect, test } from 'vitest'

import { toHubApiError } from './errors'

function envelopeError(code: string, message: string, status: number) {
  return new AxiosError(
    `Request failed with status code ${status}`,
    undefined,
    undefined,
    undefined,
    {
      data: { success: false, error: { code, message } },
      status,
      statusText: 'Conflict',
      headers: {},
      config: { headers: new AxiosHeaders() },
    },
  )
}

describe('friendly error messages', () => {
  test('replaces the concurrency-limit message with user-facing copy', () => {
    const error = toHubApiError(
      envelopeError('PLUGIN_CONCURRENCY_LIMIT', '插件 Build 并发作业数已达上限', 409),
    )

    expect(error).toEqual({
      code: 'PLUGIN_CONCURRENCY_LIMIT',
      message: '该插件正在运行的作业已达到并发上限（可能包含定时调度），请稍后重试',
      status: 409,
    })
  })

  test('keeps plugin contract failure summaries verbatim', () => {
    const summary = 'PLUGIN_OUTPUT_MISSING: result.shp'

    expect(toHubApiError({ code: 'PLUGIN_OUTPUT_MISSING', message: summary })).toEqual({
      code: 'PLUGIN_OUTPUT_MISSING',
      message: summary,
    })
    expect(
      toHubApiError(
        envelopeError(
          'PLUGIN_OUTPUT_REGISTER_FAILED',
          'PLUGIN_OUTPUT_REGISTER_FAILED: 输出注册失败',
          500,
        ),
      ),
    ).toEqual({
      code: 'PLUGIN_OUTPUT_REGISTER_FAILED',
      message: 'PLUGIN_OUTPUT_REGISTER_FAILED: 输出注册失败',
      status: 500,
    })
  })

  test('leaves error codes without a mapping untouched', () => {
    expect(
      toHubApiError(envelopeError('PLUGIN_BUILD_NOT_READY', '插件 Build 尚未 READY, 不能启用', 409)),
    ).toEqual({
      code: 'PLUGIN_BUILD_NOT_READY',
      message: '插件 Build 尚未 READY, 不能启用',
      status: 409,
    })
  })
})
