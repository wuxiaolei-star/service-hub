import type { ReactNode } from 'react'
import { Space, Typography } from 'antd'

interface PageHeaderProps {
  title: string
  subtitle?: string
  extra?: ReactNode
}

export default function PageHeader({ title, subtitle, extra }: PageHeaderProps) {
  return (
    <header className="page-header">
      <div>
        <Typography.Title level={3} style={{ marginBottom: 0 }}>
          {title}
        </Typography.Title>
        {subtitle !== undefined && (
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0, marginTop: 4 }}>
            {subtitle}
          </Typography.Paragraph>
        )}
      </div>
      {extra !== undefined && <Space>{extra}</Space>}
    </header>
  )
}
