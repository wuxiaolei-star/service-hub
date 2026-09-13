import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, Button, Card, Input, Radio, Select, Space, Typography } from 'antd'
import { createJob } from '../api/jobs'
import { getPlugin, listPlugins, listPluginBuilds } from '../api/plugins'
import { toHubApiError } from '../api/errors'
import HubErrorAlert from '../components/HubErrorAlert'
import PageHeader from '../components/PageHeader'
import PluginParameterForm from '../components/PluginParameterForm'
import { queryKeys } from '../hooks/queryKeys'
import { useRecentFiles } from '../hooks/useRecentFiles'
import type { PluginManifest, RuntimeType } from '../types/api'
import {
  buildJobRequest,
  parseJsonRequest,
  validateJobRequest,
  type JobValidationError,
} from '../utils/jobPayload'

type RequestMode = 'form' | 'json'

const UNSUPPORTED_MESSAGE =
  '该插件声明了暂不支持的可视化参数类型，请切换到 JSON 模式手动构造请求。'

function errorsToMap(errors: JobValidationError[]): Map<string, string> {
  const map = new Map<string, string>()
  for (const error of errors) {
    if (!map.has(error.field)) {
      map.set(error.field, error.message)
    }
  }
  return map
}

export default function NewJobPage() {
  const navigate = useNavigate()
  const { recent } = useRecentFiles()
  const [pluginId, setPluginId] = useState<string | null>(null)
  const [version, setVersion] = useState<string | null>(null)
  const [runtimeType, setRuntimeType] = useState<RuntimeType | undefined>(undefined)
  const [paramValues, setParamValues] = useState<Record<string, unknown>>({})
  const [inputValues, setInputValues] = useState<Record<string, string | string[]>>({})
  const [mode, setMode] = useState<RequestMode>('form')
  const [jsonText, setJsonText] = useState('')
  const [submitted, setSubmitted] = useState(false)

  const plugins = useQuery({ queryKey: queryKeys.plugins.list(), queryFn: listPlugins })
  const detail = useQuery({
    queryKey: queryKeys.plugins.detail(pluginId ?? 'none'),
    queryFn: () => getPlugin(pluginId as string),
    enabled: pluginId !== null,
  })
  const builds = useQuery({
    queryKey: queryKeys.plugins.builds({ plugin_id: pluginId ?? 'none' }),
    queryFn: () => listPluginBuilds(pluginId ?? undefined),
    enabled: pluginId !== null,
  })

  const manifest: PluginManifest | undefined = useMemo(() => {
    const versions = detail.data?.versions ?? []
    const selected =
      versions.find((item) => item.version === version) ?? versions[0]
    return selected?.manifest
  }, [detail.data, version])

  useEffect(() => {
    if (manifest === undefined) {
      return
    }
    setParamValues((current) => {
      const next = { ...current }
      for (const parameter of manifest.parameters) {
        if (next[parameter.name] === undefined && parameter.default !== undefined && parameter.default !== null) {
          next[parameter.name] = parameter.default
        }
      }
      return next
    })
  }, [manifest])

  const enabledRuntimes = useMemo(() => {
    const runtimes = new Set<RuntimeType>()
    for (const build of builds.data?.items ?? []) {
      if (build.status === 'ENABLED') {
        runtimes.add(build.runtime_type)
      }
    }
    return runtimes
  }, [builds.data])

  const runtimeRequired = enabledRuntimes.size > 1
  const effectiveRuntime: RuntimeType | undefined =
    runtimeType !== undefined && enabledRuntimes.has(runtimeType)
      ? runtimeType
      : enabledRuntimes.size === 1
        ? [...enabledRuntimes][0]
        : undefined

  const validationErrors = useMemo(() => {
    if (mode === 'json' || manifest === undefined) {
      return []
    }
    return validateJobRequest(manifest, {
      values: paramValues,
      inputs: inputValues,
      runtimeType: effectiveRuntime,
      runtimeRequired,
    })
  }, [manifest, paramValues, inputValues, effectiveRuntime, runtimeRequired, mode])

  const requestPreview = useMemo(() => {
    if (manifest === undefined) {
      return null
    }
    if (mode === 'json') {
      const parsed = parseJsonRequest(jsonText)
      return parsed.ok ? parsed.value : null
    }
    return buildJobRequest(manifest, {
      values: paramValues,
      inputs: inputValues,
      runtimeType: effectiveRuntime,
    })
  }, [manifest, mode, jsonText, paramValues, inputValues, effectiveRuntime])

  const jsonParsed =
    mode === 'json' && jsonText.trim() !== '' ? parseJsonRequest(jsonText) : null

  const create = useMutation({
    mutationFn: createJob,
    onSuccess: (job) => {
      navigate(`/jobs/${job.job_id}`)
    },
  })

  function submit() {
    if (manifest === undefined) {
      return
    }
    setSubmitted(true)
    if (mode === 'json') {
      const parsed = parseJsonRequest(jsonText)
      if (!parsed.ok) {
        return
      }
      create.mutate(parsed.value as unknown as Parameters<typeof create.mutate>[0])
      return
    }
    if (validationErrors.length > 0) {
      return
    }
    create.mutate(
      buildJobRequest(manifest, {
        values: paramValues,
        inputs: inputValues,
        runtimeType: effectiveRuntime,
      }),
    )
  }

  const blockingUnsupported =
    manifest !== undefined &&
    manifest.parameters.some(
      (parameter) =>
        parameter.type !== 'string' &&
        parameter.type !== 'integer' &&
        parameter.type !== 'number' &&
        parameter.type !== 'boolean' &&
        parameter.type !== 'enum' &&
        parameter.type !== 'string_list',
    )

  const versionOptions = (detail.data?.versions ?? []).map((item) => ({
    value: item.version,
    label: item.version,
  }))

  return (
    <div>
      <PageHeader
        title="新建任务"
        subtitle="按插件 Manifest 驱动创建一次性 Job。"
      />
      <Card style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <div>
            <label htmlFor="job-plugin">插件</label>
            <Select
              id="job-plugin"
              style={{ width: '100%', maxWidth: 480 }}
              placeholder="选择插件"
              value={pluginId}
              options={(plugins.data?.items ?? []).map((item) => ({
                value: item.id,
                label: `${item.name}（${item.id}）`,
              }))}
              onChange={(next) => {
                setPluginId(next)
                setVersion(null)
                setParamValues({})
                setInputValues({})
                setRuntimeType(undefined)
                setSubmitted(false)
              }}
            />
          </div>
          {versionOptions.length > 1 && (
            <div>
              <label htmlFor="job-version">版本</label>
              <Select
                id="job-version"
                style={{ width: '100%', maxWidth: 240 }}
                value={version ?? versionOptions[0]?.value}
                options={versionOptions}
                onChange={(next) => setVersion(next)}
              />
            </div>
          )}
          {manifest !== undefined && (
            <div>
              <label>运行时</label>
              <div>
                {enabledRuntimes.size === 0 ? (
                  <Typography.Text type="warning">该版本当前没有已启用的 Build，无法创建任务。</Typography.Text>
                ) : (
                  <Radio.Group
                    value={effectiveRuntime}
                    onChange={(event) => setRuntimeType(event.target.value as RuntimeType)}
                    disabled={!runtimeRequired}
                    options={[...enabledRuntimes].map((runtime) => ({
                      value: runtime,
                      label: runtime === 'conda-pack' ? 'conda-pack' : 'docker',
                    }))}
                  />
                )}
              </div>
            </div>
          )}
        </Space>
      </Card>

      {manifest !== undefined && (
        <>
          <Card title="输入文件" style={{ marginBottom: 16 }}>
            <Space direction="vertical" style={{ width: '100%' }} size={12}>
              {manifest.inputs.map((input) => (
                <div key={input.name}>
                  <label htmlFor={`input-${input.name}`}>
                    {input.label}
                    {input.required && <span aria-hidden="true"> *</span>}
                  </label>
                  {input.type === 'files' ? (
                    <Input
                      id={`input-${input.name}`}
                      placeholder="输入文件 ID 列表，用逗号分隔（如 file_1,file_2）"
                      value={Array.isArray(inputValues[input.name]) ? (inputValues[input.name] as string[]).join(',') : ''}
                      onChange={(event) =>
                        setInputValues((current) => ({
                          ...current,
                          [input.name]: event.target.value
                            .split(',')
                            .map((item) => item.trim())
                            .filter((item) => item !== ''),
                        }))
                      }
                    />
                  ) : (
                    <Input
                      id={`input-${input.name}`}
                      value={typeof inputValues[input.name] === 'string' ? (inputValues[input.name] as string) : ''}
                      onChange={(event) =>
                        setInputValues((current) => ({ ...current, [input.name]: event.target.value.trim() }))
                      }
                    />
                  )}
                  {recent.length > 0 && (
                    <Select
                      style={{ width: '100%', maxWidth: 480, marginTop: 8 }}
                      placeholder="从最近文件中选择"
                      value={null}
                      options={recent.map((entry) => ({
                        value: entry.file_id,
                        label: `${entry.name}（${entry.file_id}）`,
                      }))}
                      onChange={(next) =>
                        setInputValues((current) => {
                          const selectedId = String(next)
                          return {
                            ...current,
                            [input.name]:
                              input.type === 'files'
                                ? [
                                    ...(Array.isArray(current[input.name])
                                      ? (current[input.name] as string[])
                                      : []),
                                    selectedId,
                                  ]
                                : selectedId,
                          }
                        })
                      }
                    />
                  )}
                </div>
              ))}
            </Space>
          </Card>

          <Card
            title="参数与请求"
            style={{ marginBottom: 16 }}
            extra={
              <Radio.Group
                value={mode}
                onChange={(event) => setMode(event.target.value as RequestMode)}
                options={[
                  { value: 'form', label: '表单模式' },
                  { value: 'json', label: 'JSON 模式' },
                ]}
                optionType="button"
              />
            }
          >
            {blockingUnsupported && (
              <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={UNSUPPORTED_MESSAGE} />
            )}
            {mode === 'form' ? (
              <PluginParameterForm
                parameters={manifest.parameters}
                values={paramValues}
                errors={errorsToMap(validationErrors)}
                onChange={(name, value) =>
                  setParamValues((current) => ({ ...current, [name]: value }))
                }
              />
            ) : (
              <div>
                <label htmlFor="job-json">JSON 请求</label>
                <Input.TextArea
                  id="job-json"
                  rows={8}
                  value={jsonText}
                  onChange={(event) => setJsonText(event.target.value)}
                />
                {jsonParsed !== null && !jsonParsed.ok && (
                  <Alert type="error" showIcon style={{ marginTop: 8 }} message={jsonParsed.reason} />
                )}
              </div>
            )}
            {requestPreview !== null && (
              <>
                <Typography.Title level={5} style={{ marginTop: 16 }}>
                  请求预览
                </Typography.Title>
                <pre data-testid="request-preview" style={{ whiteSpace: 'pre-wrap' }}>
                  {JSON.stringify(requestPreview, null, 2)}
                </pre>
              </>
            )}
          </Card>
        </>
      )}

      {submitted && validationErrors.length > 0 && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message="请求未通过校验"
          description={validationErrors.map((error) => error.message).join('；')}
        />
      )}
      {create.isError && <HubErrorAlert error={toHubApiError(create.error)} onRetry={() => create.reset()} />}

      <Button
        type="primary"
        size="large"
        disabled={manifest === undefined || enabledRuntimes.size === 0}
        loading={create.isPending}
        onClick={submit}
      >
        创建任务
      </Button>
    </div>
  )
}
