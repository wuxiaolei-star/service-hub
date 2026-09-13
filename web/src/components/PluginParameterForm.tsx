import { Input, InputNumber, Select, Switch } from 'antd'
import type { PluginParameter } from '../types/api'

interface PluginParameterFormProps {
  parameters: PluginParameter[]
  values: Record<string, unknown>
  errors: Map<string, string>
  onChange: (name: string, value: unknown) => void
}

function controlFor(
  parameter: PluginParameter,
  value: unknown,
  onChange: (value: unknown) => void,
) {
  switch (parameter.type) {
    case 'integer':
      return (
        <InputNumber
          id={`param-${parameter.name}`}
          style={{ width: '100%' }}
          value={typeof value === 'number' ? value : undefined}
          precision={0}
          onChange={(next) => onChange(next ?? undefined)}
        />
      )
    case 'number':
      return (
        <InputNumber
          id={`param-${parameter.name}`}
          style={{ width: '100%' }}
          value={typeof value === 'number' ? value : undefined}
          onChange={(next) => onChange(next ?? undefined)}
        />
      )
    case 'boolean':
      return (
        <Switch
          id={`param-${parameter.name}`}
          checked={value === true}
          onChange={(checked) => onChange(checked)}
        />
      )
    case 'enum':
      return (
        <Select
          id={`param-${parameter.name}`}
          allowClear
          value={typeof value === 'string' ? value : undefined}
          options={(parameter.options ?? []).map((option) => ({ value: option, label: option }))}
          onChange={(next) => onChange(next ?? undefined)}
        />
      )
    case 'string_list':
      return (
        <Select
          id={`param-${parameter.name}`}
          mode="tags"
          allowClear
          open={false}
          value={Array.isArray(value) ? (value as string[]) : []}
          onChange={(next) => onChange(next)}
        />
      )
    default:
      return (
        <Input
          id={`param-${parameter.name}`}
          value={typeof value === 'string' ? value : value === undefined || value === null ? '' : String(value)}
          onChange={(event) => onChange(event.target.value)}
        />
      )
  }
}

/** Render one control per manifest-declared parameter with defaults applied. */
export default function PluginParameterForm({
  parameters,
  values,
  errors,
  onChange,
}: PluginParameterFormProps) {
  if (parameters.length === 0) {
    return null
  }

  return (
    <div className="parameter-form">
      {parameters.map((parameter) => (
        <div key={parameter.name} className="parameter-form-item">
          <label htmlFor={`param-${parameter.name}`}>
            {parameter.label}
            {parameter.required && <span aria-hidden="true"> *</span>}
          </label>
          {controlFor(parameter, values[parameter.name], (value) => onChange(parameter.name, value))}
          {errors.has(parameter.name) && (
            <div role="alert" className="parameter-form-error">
              {errors.get(parameter.name)}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
