import { createBrowserRouter } from 'react-router-dom'
import AppLayout from '../layouts/AppLayout'
import DashboardPage from '../pages/DashboardPage'
import FilesPage from '../pages/FilesPage'
import JobDetailPage from '../pages/JobDetailPage'
import JobsPage from '../pages/JobsPage'
import NewJobPage from '../pages/NewJobPage'
import NotFoundPage from '../pages/NotFoundPage'
import PluginsPage from '../pages/PluginsPage'
import SystemPage from '../pages/SystemPage'

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: 'plugins', element: <PluginsPage /> },
      { path: 'files', element: <FilesPage /> },
      { path: 'jobs/new', element: <NewJobPage /> },
      { path: 'jobs', element: <JobsPage /> },
      { path: 'jobs/:jobId', element: <JobDetailPage /> },
      { path: 'system', element: <SystemPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])
