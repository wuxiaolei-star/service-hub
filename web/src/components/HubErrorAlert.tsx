import { Alert, Button, Collapse, Typography } from 'antd'
import type { HubApiError } from '../api/errors'

interface HubErrorAlertProps {
  error: HubApiError
  onRetry?: () => void
}

export default function HubErrorAlert({ error, onRetry }: HubErrorAlertProps) {
  const hasDetails = error.details !== undefined && Object.keys(error.details).length > 0
  const detailsText = hasDetails ? JSON.stringify(error.details, null, 2) : null

  return (
    <Alert
      type="error"
      showIcon
      role="alert"
      message={<span>{error.message}</span>}
      description={
        <div>
          <Typography.Text code>{error.code}</Typography.Text>
          {detailsText !== null && (
            <Collapse
              size="small"
              ghost
              items={[
                {
                  key: 'details',
                  label: '错误详情',
                  children: (
                    <pre aria-label="错误详情内容" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
                      {detailsText}
                    </pre>
                  ),
                },
              ]}
            />
          )}
          {onRetry !== undefined && (
            <Button size="small" aria-label="重试" onClick={onRetry}>
              重试
            </Button>
          )}
        </div>
      }
    />
  )
}
