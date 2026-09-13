import { act, render, screen } from '@testing-library/react'
import App from '../App'

test('renders the service navigation shell', async () => {
  await act(async () => {
    render(<App />)
  })

  expect(screen.getByText('Service Hub')).toBeInTheDocument()
  expect(screen.getByRole('navigation')).toBeInTheDocument()
  expect(screen.getByText('概览')).toBeInTheDocument()
})
