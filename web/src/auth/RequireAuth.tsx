import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Navigate, Outlet } from 'react-router-dom'
import { Spin } from 'antd'
import { fetchMe } from '../api/auth'
import { queryKeys } from '../hooks/queryKeys'

/**
 * Guard every management route: unauthenticated visitors are redirected to
 * the login page while the identity query result is cached for the shell.
 */
export default function RequireAuth() {
  const queryClient = useQueryClient()
  const me = useQuery({
    queryKey: queryKeys.auth.me(),
    queryFn: fetchMe,
    retry: false,
  })

  if (me.isPending) {
    return (
      <div style={{ display: 'grid', placeItems: 'center', minHeight: '100vh' }}>
        <Spin size="large" aria-label="身份校验中" />
      </div>
    )
  }

  if (me.isError) {
    void queryClient.removeQueries({ queryKey: queryKeys.auth.me() })
    return <Navigate replace to="/login" />
  }

  return <Outlet context={me.data} />
}
