import { Card, Typography } from 'antd'
import { createBrowserRouter } from 'react-router-dom'
import AppLayout from '../layouts/AppLayout'
import NotFoundPage from '../pages/NotFoundPage'

function placeholderPage(title: string) {
  return (
    <Card>
      <Typography.Title level={2}>{title}</Typography.Title>
    </Card>
  )
}

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: placeholderPage('欢迎使用 Service Hub') },
      { path: 'plugins', element: placeholderPage('插件') },
      { path: 'files', element: placeholderPage('文件') },
      { path: 'jobs/new', element: placeholderPage('新建任务') },
      { path: 'jobs', element: placeholderPage('任务') },
      { path: 'jobs/:jobId', element: placeholderPage('任务详情') },
      { path: 'system', element: placeholderPage('系统') },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])
