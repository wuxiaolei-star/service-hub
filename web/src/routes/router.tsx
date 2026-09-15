import { createBrowserRouter } from 'react-router-dom'
import RequireAuth from '../auth/RequireAuth'
import AppLayout from '../layouts/AppLayout'
import DashboardPage from '../pages/DashboardPage'
import ApiKeysPage from '../pages/ApiKeysPage'
import AuditPage from '../pages/AuditPage'
import FilesPage from '../pages/FilesPage'
import LoginPage from '../pages/LoginPage'
import UsersPage from '../pages/UsersPage'
import JobDetailPage from '../pages/JobDetailPage'
import JobsPage from '../pages/JobsPage'
import NewJobPage from '../pages/NewJobPage'
import NotFoundPage from '../pages/NotFoundPage'
import PipelinesPage from '../pages/PipelinesPage'
import PluginsPage from '../pages/PluginsPage'
import RegistryPage from '../pages/RegistryPage'
import SchedulesPage from '../pages/SchedulesPage'
import ServicesPage from '../pages/ServicesPage'
import SystemPage from '../pages/SystemPage'

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    path: '/',
    element: <RequireAuth />,
    children: [
      {
        path: '',
        element: <AppLayout />,
        children: [
          { index: true, element: <DashboardPage /> },
          { path: 'plugins', element: <PluginsPage /> },
          { path: 'registry', element: <RegistryPage /> },
          { path: 'files', element: <FilesPage /> },
          { path: 'jobs/new', element: <NewJobPage /> },
          { path: 'jobs', element: <JobsPage /> },
          { path: 'jobs/:jobId', element: <JobDetailPage /> },
          { path: 'schedules', element: <SchedulesPage /> },
          { path: 'services', element: <ServicesPage /> },
          { path: 'pipelines', element: <PipelinesPage /> },
          { path: 'system', element: <SystemPage /> },
          { path: 'admin/users', element: <UsersPage /> },
          { path: 'admin/api-keys', element: <ApiKeysPage /> },
          { path: 'admin/audit', element: <AuditPage /> },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
])
