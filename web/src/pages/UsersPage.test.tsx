import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, test, vi } from 'vitest'
import UsersPage from './UsersPage'
import { getUserUsage, listUsers } from '../api/users'
import type { UserRow } from '../api/users'

vi.mock('../api/users', () => ({
  createUser: vi.fn(),
  listUsers: vi.fn(),
  disableUser: vi.fn(),
  resetUserPassword: vi.fn(),
  getUserUsage: vi.fn(),
}))

const mockedListUsers = vi.mocked(listUsers)
const mockedGetUserUsage = vi.mocked(getUserUsage)

const userRow: UserRow = {
  id: 7,
  username: 'operator-a',
  role: 'operator',
  is_active: true,
  must_change_password: false,
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <UsersPage />
    </QueryClientProvider>,
  )
}

describe('UsersPage', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  test('shows per-user storage usage in the usage column', async () => {
    mockedListUsers.mockResolvedValue({ items: [userRow] })
    mockedGetUserUsage.mockResolvedValue({ total_bytes: 12, file_count: 1, active_jobs: 0 })

    renderPage()

    expect(await screen.findByText('operator-a')).toBeInTheDocument()
    expect(await screen.findByText('12 B')).toBeInTheDocument()
    expect(screen.getByText(/1 个文件/)).toBeInTheDocument()
    expect(mockedGetUserUsage).toHaveBeenCalledWith(7)
  })

  test('shows a dash while the usage query is loading', async () => {
    mockedListUsers.mockResolvedValue({ items: [userRow] })
    mockedGetUserUsage.mockReturnValue(new Promise(() => {}))

    renderPage()

    expect(await screen.findByText('operator-a')).toBeInTheDocument()
    expect(screen.getByText('-')).toBeInTheDocument()
  })
})
