import { describe, expect, it } from 'vitest'
import { buildJobRequest, parseJsonRequest, validateJobRequest } from './jobPayload'
import type { PluginManifest, PluginParameter } from '../types/api'

const parameter = (overrides: Partial<PluginParameter>): PluginParameter => ({
  name: 'param',
  label: '参数',
  type: 'string',
  required: false,
  default: null,
  ...overrides,
})

const manifest = (parameters: PluginParameter[]): PluginManifest => ({
  spec_version: '1.0',
  plugin: { id: 'nc_to_shp', name: 'NC 转 Shapefile', version: '1.0.0', tags: [] },
  sdk: { version: '1.0' },
  runtime: { type: 'process', python: { version: '3.12' } },
  entrypoint: { module: 'nc_to_shp_plugin.main', function: 'run' },
  parameters,
  inputs: [
    {
      name: 'source_nc',
      label: 'NC 文件',
      type: 'file',
      required: true,
      extensions: ['.nc'],
    },
  ],
  outputs: [],
  execution: { timeout: 3600, concurrency: 1 },
  environment_variables: { required: [] },
  healthcheck: { enabled: true, type: 'import' },
})

describe('buildJobRequest', () => {
  it('produces the exact NC payload', () => {
    const request = buildJobRequest(
      manifest([
        parameter({ name: 'group_name', label: 'Group', default: '1' }),
        parameter({
          name: 'metrics',
          label: '指标',
          type: 'string_list',
          default: ['depth', 'stage'],
        }),
      ]),
      {
        values: { group_name: '1', metrics: ['depth', 'stage'] },
        inputs: { source_nc: 'file_123' },
        runtimeType: 'docker',
      },
    )

    expect(request).toEqual({
      plugin_id: 'nc_to_shp',
      version: '1.0.0',
      runtime_type: 'docker',
      inputs: { source_nc: 'file_123' },
      params: { group_name: '1', metrics: ['depth', 'stage'] },
    })
  })
})

describe('validateJobRequest', () => {
  it('rejects missing required parameters and file inputs with field errors', () => {
    const errors = validateJobRequest(
      manifest([parameter({ name: 'group_name', label: 'Group', required: true })]),
      { values: {}, inputs: {}, runtimeType: 'docker' },
    )
    expect(errors).toContainEqual(expect.objectContaining({ field: 'group_name' }))
    expect(errors).toContainEqual(expect.objectContaining({ field: 'source_nc' }))
  })

  it('validates integer/number min and max bounds', () => {
    const errors = validateJobRequest(
      manifest([
        parameter({ name: 'count', label: '数量', type: 'integer', default: 1, min: 1, max: 3 }),
      ]),
      { values: { count: 9 }, inputs: { source_nc: 'file_1' }, runtimeType: 'docker' },
    )
    expect(errors).toContainEqual(expect.objectContaining({ field: 'count' }))
  })

  it('validates enum options and string_list duplicates', () => {
    const errors = validateJobRequest(
      manifest([
        parameter({
          name: 'pick',
          label: '选项',
          type: 'enum',
          required: true,
          options: ['a', 'b'],
        }),
        parameter({ name: 'flag', label: '开关', type: 'boolean', default: false }),
        parameter({ name: 'tags', label: '标签', type: 'string_list', default: ['x'] }),
      ]),
      {
        values: { pick: 'c', flag: true, tags: ['x', 'x'] },
        inputs: { source_nc: 'file_1' },
        runtimeType: 'docker',
      },
    )
    expect(errors).toContainEqual(expect.objectContaining({ field: 'pick' }))
    expect(errors).toContainEqual(expect.objectContaining({ field: 'tags' }))
  })

  it('blocks unsupported parameter types instead of silently dropping them', () => {
    const errors = validateJobRequest(
      manifest([parameter({ name: 'weird', label: '未知', type: 'datetime' as never })]),
      { values: { weird: '2026-01-01' }, inputs: { source_nc: 'file_1' }, runtimeType: 'docker' },
    )
    expect(errors).toContainEqual(
      expect.objectContaining({ field: 'weird', code: 'UNSUPPORTED_TYPE' }),
    )
  })

  it('supports repeated file inputs as arrays with min_count', () => {
    const multiManifest = manifest([])
    multiManifest.inputs = [
      {
        name: 'shapes',
        label: '图形',
        type: 'files',
        required: true,
        extensions: ['.shp'],
        min_count: 2,
      },
    ]
    const errors = validateJobRequest(multiManifest, {
      values: {},
      inputs: { shapes: ['file_1', 'file_2'] },
      runtimeType: null,
    })
    expect(errors).toEqual([])
  })

  it('reports a missing runtime selection when both runtimes exist', () => {
    const errors = validateJobRequest(manifest([]), {
      values: {},
      inputs: {},
      runtimeType: undefined,
      runtimeRequired: true,
    })
    expect(errors).toContainEqual(expect.objectContaining({ field: 'runtime_type' }))
  })
})

describe('parseJsonRequest', () => {
  it('parses a valid JSON object payload', () => {
    const parsed = parseJsonRequest('{"plugin_id":"nc_to_shp"}')
    expect(parsed).toEqual({ ok: true, value: { plugin_id: 'nc_to_shp' } })
  })

  it('rejects invalid JSON and non-object payloads', () => {
    expect(parseJsonRequest('{nope')).toEqual({ ok: false, reason: 'JSON 格式无效' })
    expect(parseJsonRequest('[1,2]')).toEqual({ ok: false, reason: '请求必须是 JSON 对象' })
  })
})
