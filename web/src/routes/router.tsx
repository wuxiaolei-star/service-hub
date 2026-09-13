import { Card, Typography } from 'antd'
import { createBrowserRouter } from 'react-router-dom'
import AppLayout from '../layouts/AppLayout'
import DashboardPage from '../pages/DashboardPage'
import NotFoundPage from '../pages/NotFoundPage'
import SystemPage from '../pages/SystemPage'

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
      { index: true, element: <DashboardPage /> },
      { path: 'plugins', element: placeholderPage('插件') },
      { path: 'files', element: placeholderPage('文件') },
      { path: 'jobs/new', element: placeholderPage('新建任务') },
      { path: 'jobs', element: placeholderPage('任务') },
      { path: 'jobs/:jobId', element: placeholderPage('任务详情') },
      { path: 'system', element: <SystemPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])
