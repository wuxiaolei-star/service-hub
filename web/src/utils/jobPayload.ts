import type {
  JobCreateRequest,
  PluginInput,
  PluginManifest,
  PluginParameter,
  RuntimeType,
} from '../types/api'

export interface JobValidationError {
  field: string
  code:
    | 'REQUIRED'
    | 'MIN'
    | 'MAX'
    | 'OPTION'
    | 'DUPLICATE'
    | 'TYPE'
    | 'COUNT'
    | 'UNSUPPORTED_TYPE'
    | 'MISSING_RUNTIME'
  message: string
}

export interface JobRequestInput {
  values: Record<string, unknown>
  inputs: Record<string, string | string[]>
  runtimeType?: RuntimeType | null
  runtimeRequired?: boolean
}

const SUPPORTED_PARAMETER_TYPES = new Set([
  'string',
  'integer',
  'number',
  'boolean',
  'enum',
  'string_list',
])

function isNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function validateParameter(
  parameter: PluginParameter,
  raw: unknown,
  errors: JobValidationError[],
): void {
  if (!SUPPORTED_PARAMETER_TYPES.has(parameter.type)) {
    errors.push({
      field: parameter.name,
      code: 'UNSUPPORTED_TYPE',
      message: `参数 ${parameter.label} 的类型 ${String(parameter.type)} 暂不支持，请改用 JSON 模式并手动确认。`,
    })
    return
  }

  const isEmpty = raw === undefined || raw === null || raw === ''
  if (isEmpty) {
    const hasDefault = parameter.default !== undefined && parameter.default !== null
    if (parameter.required && !hasDefault) {
      errors.push({
        field: parameter.name,
        code: 'REQUIRED',
        message: `参数 ${parameter.label} 为必填项`,
      })
    }
    return
  }

  switch (parameter.type) {
    case 'integer':
    case 'number': {
      if (!isNumber(raw)) {
        errors.push({ field: parameter.name, code: 'TYPE', message: `参数 ${parameter.label} 必须是数字` })
        return
      }
      const integer = parameter.type === 'integer'
      if (integer && !Number.isInteger(raw)) {
        errors.push({ field: parameter.name, code: 'TYPE', message: `参数 ${parameter.label} 必须是整数` })
        return
      }
      if (parameter.min !== null && parameter.min !== undefined && raw < parameter.min) {
        errors.push({
          field: parameter.name,
          code: 'MIN',
          message: `参数 ${parameter.label} 不能小于 ${parameter.min}`,
        })
      }
      if (parameter.max !== null && parameter.max !== undefined && raw > parameter.max) {
        errors.push({
          field: parameter.name,
          code: 'MAX',
          message: `参数 ${parameter.label} 不能大于 ${parameter.max}`,
        })
      }
      break
    }
    case 'boolean':
      if (typeof raw !== 'boolean') {
        errors.push({ field: parameter.name, code: 'TYPE', message: `参数 ${parameter.label} 必须是布尔值` })
      }
      break
    case 'enum':
      if (typeof raw !== 'string' || !parameter.options?.includes(raw)) {
        errors.push({
          field: parameter.name,
          code: 'OPTION',
          message: `参数 ${parameter.label} 必须是给定的选项之一`,
        })
      }
      break
    case 'string_list': {
      if (
        !Array.isArray(raw) ||
        raw.some((item) => typeof item !== 'string' || item === '')
      ) {
        errors.push({
          field: parameter.name,
          code: 'TYPE',
          message: `参数 ${parameter.label} 必须是非空字符串列表`,
        })
        return
      }
      if (new Set(raw).size !== raw.length) {
        errors.push({
          field: parameter.name,
          code: 'DUPLICATE',
          message: `参数 ${parameter.label} 不能有重复项`,
        })
      }
      break
    }
    default:
      if (typeof raw !== 'string') {
        errors.push({ field: parameter.name, code: 'TYPE', message: `参数 ${parameter.label} 必须是字符串` })
      }
      break
  }
}

function validateInput(input: PluginInput, value: unknown, errors: JobValidationError[]): void {
  const isEmptySingle = value === undefined || value === null || value === ''
  const values = Array.isArray(value) ? value : isEmptySingle ? [] : [value]
  const invalidItems = values.some((item) => typeof item !== 'string' || item === '')

  if (input.required && (isEmptySingle || values.length === 0)) {
    errors.push({
      field: input.name,
      code: 'REQUIRED',
      message: `输入 ${input.label} 为必填项`,
    })
    return
  }
  if (invalidItems) {
    errors.push({
      field: input.name,
      code: 'TYPE',
      message: `输入 ${input.label} 必须是文件 ID 或文件 ID 列表`,
    })
    return
  }
  if (input.type === 'files') {
    if (input.min_count !== null && input.min_count !== undefined && values.length < input.min_count) {
      errors.push({
        field: input.name,
        code: 'COUNT',
        message: `输入 ${input.label} 至少需要 ${input.min_count} 个文件`,
      })
    }
    if (input.max_count !== null && input.max_count !== undefined && values.length > input.max_count) {
      errors.push({
        field: input.name,
        code: 'COUNT',
        message: `输入 ${input.label} 最多接受 ${input.max_count} 个文件`,
      })
    }
  }
}

/** Validate form values against the manifest and return every violation. */
export function validateJobRequest(
  manifest: PluginManifest,
  input: JobRequestInput,
): JobValidationError[] {
  const errors: JobValidationError[] = []
  for (const parameter of manifest.parameters) {
    validateParameter(parameter, input.values[parameter.name], errors)
  }
  for (const inputSpec of manifest.inputs) {
    validateInput(inputSpec, input.inputs[inputSpec.name], errors)
  }
  if (input.runtimeRequired === true && input.runtimeType === undefined) {
    errors.push({
      field: 'runtime_type',
      code: 'MISSING_RUNTIME',
      message: '请选择运行时',
    })
  }
  return errors
}

/** Build the canonical JobCreateRequest from validated form input. */
export function buildJobRequest(
  manifest: PluginManifest,
  input: JobRequestInput,
): JobCreateRequest {
  const params: Record<string, unknown> = {}
  for (const parameter of manifest.parameters) {
    const raw = input.values[parameter.name]
    if (raw !== undefined) {
      params[parameter.name] = raw
    } else if (parameter.default !== undefined && parameter.default !== null) {
      params[parameter.name] = parameter.default
    }
  }
  return {
    plugin_id: manifest.plugin.id,
    version: manifest.plugin.version,
    runtime_type: input.runtimeType ?? null,
    inputs: input.inputs,
    params,
  }
}

export type ParseJsonRequestResult =
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; reason: string }

export function parseJsonRequest(text: string): ParseJsonRequestResult {
  let parsed: unknown
  try {
    parsed = JSON.parse(text) as unknown
  } catch {
    return { ok: false, reason: 'JSON 格式无效' }
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return { ok: false, reason: '请求必须是 JSON 对象' }
  }
  return { ok: true, value: parsed as Record<string, unknown> }
}
