import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import ServicesPage from './ServicesPage'
import { fetchMe } from '../api/auth'
import type { MeResponse } from '../api/auth'
import {
  createService,
  deleteService,
  getServiceLogs,
  listServices,
  startService,
} from '../api/services'
import type { ServiceRow } from '../api/services'

vi.mock('../api/services', () => ({
  listServices: vi.fn(),
  createService: vi.fn(),
  updateService: vi.fn(),
  startService: vi.fn(),
  stopService: vi.fn(),
  restartService: vi.fn(),
  deleteService: vi.fn(),
  getServiceLogs: vi.fn(),
}))

vi.mock('../api/auth', () => ({
  fetchMe: vi.fn(),
}))

const mockedList = vi.mocked(listServices)
const mockedCreate = vi.mocked(createService)
const mockedStart = vi.mocked(startService)
const mockedDelete = vi.mocked(deleteService)
const mockedGetLogs = vi.mocked(getServiceLogs)
const mockedFetchMe = vi.mocked(fetchMe)

const adminMe: MeResponse = {
  actor_type: 'user',
  id: 1,
  username: 'admin',
  role: 'admin',
  must_change_password: false,
}

const operatorMe: MeResponse = {
  actor_type: 'user',
  id: 2,
  username: 'operator-a',
  role: 'operator',
  must_change_password: false,
}

const runningRow: ServiceRow = {
  name: 'tile-server',
  image: 'nginx:alpine',
  container_name: 'hub-svc-tile-server',
  desired_state: 'RUNNING',
  runtime: { state: 'running', health: 'healthy', ports: { '8081/tcp': [] } },
}

const stoppedRow: ServiceRow = {
  name: 'query-server',
  image: 'ghcr.io/example/query:1.0',
  container_name: 'hub-svc-query-server',
  desired_state: 'STOPPED',
  runtime: { state: 'exited', health: null, ports: null },
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <ServicesPage />
    </QueryClientProvider>,
  )
}

async function openDeployModal() {
  renderPage()
  const user = userEvent.setup({ delay: null })
  await user.click(await screen.findByRole('button', { name: '部署服务' }))
  return user
}

describe('ServicesPage', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    mockedFetchMe.mockResolvedValue(adminMe)
    mockedList.mockResolvedValue({ items: [] })
  })

  test('renders desired state, runtime state and health for each service', async () => {
    mockedList.mockResolvedValue({ items: [runningRow, stoppedRow] })

    renderPage()

    expect(await screen.findByText('tile-server')).toBeInTheDocument()
    expect(screen.getByText('nginx:alpine')).toBeInTheDocument()
    expect(screen.getByText('RUNNING')).toBeInTheDocument()
    expect(screen.getByText('running')).toBeInTheDocument()
    expect(screen.getByText('healthy')).toBeInTheDocument()
    expect(screen.getByText('query-server')).toBeInTheDocument()
    expect(screen.getByText('STOPPED')).toBeInTheDocument()
    expect(screen.getByText('exited')).toBeInTheDocument()
  })

  test('hides the delete action for non-admin users', async () => {
    mockedFetchMe.mockResolvedValue(operatorMe)
    mockedList.mockResolvedValue({ items: [runningRow] })

    renderPage()

    expect(await screen.findByText('tile-server')).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /删\s*除/ })).not.toBeInTheDocument(),
    )
  })

  test(
    'deletes a service after the admin confirms the popconfirm',
    async () => {
      mockedList.mockResolvedValue({ items: [runningRow] })
      mockedDelete.mockResolvedValue(undefined)

      renderPage()
      const user = userEvent.setup({ delay: null })

      await user.click(await screen.findByRole('button', { name: /删\s*除/ }))
      await user.click(await screen.findByRole('button', { name: /确\s*定/ }, { timeout: 15000 }))

      await waitFor(() =>
        expect(mockedDelete).toHaveBeenCalledWith('tile-server', expect.anything()),
      )
    },
    20000,
  )

  test('starts a stopped service through the lifecycle endpoint', async () => {
    mockedList.mockResolvedValue({ items: [stoppedRow] })
    mockedStart.mockResolvedValue({ name: 'query-server', runtime: null })

    renderPage()
    const user = userEvent.setup({ delay: null })

    await user.click(await screen.findByRole('button', { name: /启\s*动/ }))

    await waitFor(() =>
      expect(mockedStart).toHaveBeenCalledWith('query-server', expect.anything()),
    )
  })

  test('rejects malformed environment lines instead of submitting', async () => {
    const user = await openDeployModal()

    await user.type(screen.getByLabelText('名称'), 'tile-server')
    await user.type(screen.getByLabelText('镜像'), 'nginx:alpine')
    await user.type(screen.getByLabelText('环境变量'), 'NOT_A_PAIR')
    await user.click(screen.getByRole('button', { name: /^部\s*署$/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('环境变量格式无效，每行应为 k=v')
    expect(mockedCreate).not.toHaveBeenCalled()
  })

  test('rejects malformed mount lines instead of submitting', async () => {
    const user = await openDeployModal()

    await user.type(screen.getByLabelText('名称'), 'tile-server')
    await user.type(screen.getByLabelText('镜像'), 'nginx:alpine')
    await user.type(screen.getByLabelText('挂载'), '/srv/data')
    await user.click(screen.getByRole('button', { name: /^部\s*署$/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      '挂载格式无效，每行应为 source:target[:ro]',
    )
    expect(mockedCreate).not.toHaveBeenCalled()
  })

  test(
    'deploys with parsed environment, mounts and command',
    async () => {
      mockedCreate.mockResolvedValue(runningRow)

      const user = await openDeployModal()

      await user.type(screen.getByLabelText('名称'), 'tile-server')
      await user.type(screen.getByLabelText('镜像'), 'nginx:alpine')
      await user.type(screen.getByLabelText('环境变量'), 'TZ=Asia/Shanghai')
      await user.type(screen.getByLabelText('挂载'), '/srv/data:/data:ro')
      await user.type(screen.getByLabelText('启动命令（逗号分隔，可空）'), 'python,-u,server.py')
      await user.click(screen.getByRole('button', { name: /^部\s*署$/ }))

      await waitFor(
        () =>
          expect(mockedCreate).toHaveBeenCalledWith(
            {
              name: 'tile-server',
              image: 'nginx:alpine',
              ports: [],
              env: { TZ: 'Asia/Shanghai' },
              mounts: [{ source: '/srv/data', target: '/data', read_only: true }],
              command: ['python', '-u', 'server.py'],
            },
            expect.anything(),
          ),
        { timeout: 15000 },
      )
    },
    20000,
  )

  test('loads the bounded log tail in a modal', async () => {
    mockedList.mockResolvedValue({ items: [runningRow] })
    mockedGetLogs.mockResolvedValue('line-1\nline-2')

    renderPage()
    const user = userEvent.setup({ delay: null })

    await user.click(await screen.findByRole('button', { name: /日\s*志/ }))

    await waitFor(() => expect(mockedGetLogs).toHaveBeenCalledWith('tile-server', 200))
    expect(await screen.findByLabelText('服务日志')).toHaveTextContent('line-1')
  })

  test('explains a 502 as an unavailable service-manager', async () => {
    mockedList.mockResolvedValue({ items: [runningRow] })
    mockedGetLogs.mockRejectedValue({
      code: 'SERVICE_MANAGER_UNAVAILABLE',
      message: 'bad gateway',
      status: 502,
    })

    renderPage()
    const user = userEvent.setup({ delay: null })

    await user.click(await screen.findByRole('button', { name: /日\s*志/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('service-manager 不可用')
  })
})
