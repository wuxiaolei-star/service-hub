import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider } from 'antd'
import { RouterProvider } from 'react-router-dom'
import { router } from './routes/router'
import { ThemeModeProvider } from './theme/ThemeModeProvider'
import { useThemeMode } from './theme/themeMode'
import { createAppTheme } from './theme/theme'

const queryClient = new QueryClient()

function ThemedApp() {
  const { mode } = useThemeMode()
  return (
    <ConfigProvider theme={createAppTheme(mode)}>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} future={{ v7_startTransition: true }} />
      </QueryClientProvider>
    </ConfigProvider>
  )
}

export default function App() {
  return (
    <ThemeModeProvider>
      <ThemedApp />
    </ThemeModeProvider>
  )
}
