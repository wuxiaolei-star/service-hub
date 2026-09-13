import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, test, vi } from 'vitest'
import LoginPage from './LoginPage'
import { login } from '../api/auth'

vi.mock('../api/auth', () => ({
  login: vi.fn(),
  logout: vi.fn(),
  fetchMe: vi.fn(),
}))

const mockedLogin = vi.mocked(login)

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/login']}>
        <LoginPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('LoginPage', () => {
  beforeEach(() => {
    mockedLogin.mockClear()
  })

  test('renders username and password fields with a submit button', () => {
    renderPage()
    expect(screen.getByLabelText(/用户名/)).toBeInTheDocument()
    expect(screen.getByLabelText(/口令/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '登 录' })).toBeInTheDocument()
  })

  test('submits credentials and reports server errors', async () => {
    const user = userEvent.setup()
    mockedLogin.mockRejectedValueOnce({ code: 'INVALID_CREDENTIALS', message: '用户名或口令错误' })

    renderPage()
    await user.type(screen.getByLabelText(/用户名/), 'admin')
    await user.type(screen.getByLabelText(/口令/), 'wrong-password')
    await user.click(screen.getByRole('button', { name: '登 录' }))

    await waitFor(() => expect(mockedLogin).toHaveBeenCalledWith('admin', 'wrong-password'))
    expect(await screen.findByText('用户名或口令错误')).toBeInTheDocument()
  })

  it('navigates to the dashboard after a successful login', async () => {
    mockedLogin.mockResolvedValue({
      actor_type: 'user',
      id: 1,
      username: 'admin',
      role: 'admin',
      must_change_password: false,
    })
    renderPage()
    const user = userEvent.setup()
    await user.type(screen.getByLabelText(/用户名/), 'admin')
    await user.type(screen.getByLabelText(/口令/), 'correct-password')
    await user.click(screen.getByRole('button', { name: '登 录' }))
    await waitFor(() => expect(mockedLogin).toHaveBeenCalledTimes(1))
  }, 15000)
})
