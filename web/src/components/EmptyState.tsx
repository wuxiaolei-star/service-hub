import type { ReactNode } from 'react'
import { Empty } from 'antd'

export default function EmptyState({ description }: { description: ReactNode }) {
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description} />
}
