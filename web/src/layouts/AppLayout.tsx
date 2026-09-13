import {
  AppstoreOutlined,
  CloudOutlined,
  CloudDownloadOutlined,
  FileOutlined,
  HddOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  PartitionOutlined,
  PlusCircleOutlined,
  ScheduleOutlined,
  SettingOutlined,
  UserOutlined,
  KeyOutlined,
  FileSearchOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge, Button, Layout, Menu, Space, Tag, Typography } from 'antd'
import type { MenuProps } from 'antd'
import { useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { getHealth } from '../api/system'
import { fetchMe, logout } from '../api/auth'
import { queryKeys } from '../hooks/queryKeys'

const menuItems: MenuProps['items'] = [
  { key: '/', icon: <AppstoreOutlined />, label: <Link to="/">概览</Link> },
  { key: '/plugins', icon: <CloudOutlined />, label: <Link to="/plugins">插件</Link> },
  { key: '/registry', icon: <CloudDownloadOutlined />, label: <Link to="/registry">仓库</Link> },
  { key: '/files', icon: <FileOutlined />, label: <Link to="/files">文件</Link> },
  { key: '/jobs/new', icon: <PlusCircleOutlined />, label: <Link to="/jobs/new">新建任务</Link> },
  { key: '/jobs', icon: <HddOutlined />, label: <Link to="/jobs">任务</Link> },
  { key: '/schedules', icon: <ScheduleOutlined />, label: <Link to="/schedules">定时任务</Link> },
  { key: '/pipelines', icon: <PartitionOutlined />, label: <Link to="/pipelines">管道</Link> },
  { key: '/system', icon: <SettingOutlined />, label: <Link to="/system">系统</Link> },
  { key: '/admin/users', icon: <UserOutlined />, label: <Link to="/admin/users">用户</Link> },
  { key: '/admin/api-keys', icon: <KeyOutlined />, label: <Link to="/admin/api-keys">API Key</Link> },
  { key: '/admin/audit', icon: <FileSearchOutlined />, label: <Link to="/admin/audit">审计</Link> },
]

function selectedMenuKey(pathname: string) {
  if (pathname.startsWith('/jobs/')) return pathname === '/jobs/new' ? '/jobs/new' : '/jobs'
  return menuItems?.some((item) => item?.key === pathname) ? pathname : ''
}

export default function AppLayout() {
  const { pathname } = useLocation()
  const [collapsed, setCollapsed] = useState(false)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const me = useQuery({ queryKey: queryKeys.auth.me(), queryFn: fetchMe, retry: false })
  const logoutMutation = useMutation({
    mutationFn: logout,
    onSuccess: () => {
      queryClient.clear()
      navigate('/login', { replace: true })
    },
  })
  const health = useQuery({
    queryKey: queryKeys.system.health(),
    queryFn: getHealth,
    refetchInterval: 10_000,
  })
  const healthLabel = health.data?.status === 'UP' ? '服务正常' : health.isError ? '服务离线' : '状态未知'
  const healthStatus = health.data?.status === 'UP' ? 'success' : health.isError ? 'error' : 'default'

  return (
    <Layout className="app-shell">
      <Layout.Sider
        className="app-sider"
        theme="light"
        breakpoint="lg"
        collapsedWidth={64}
        collapsible
        collapsed={collapsed}
        trigger={null}
        onBreakpoint={setCollapsed}
      >
        <Link className="brand" to="/" aria-label="Service Hub">
          <span className="brand-mark">S</span>
          <Typography.Text className="brand-name">Service Hub</Typography.Text>
        </Link>
        <nav id="main-navigation" aria-label="主导航">
          <Menu theme="light" mode="inline" selectedKeys={[selectedMenuKey(pathname)]} items={menuItems} />
        </nav>
        <Button
          className="sider-toggle"
          type="text"
          aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
          aria-controls="main-navigation"
          aria-expanded={!collapsed}
          icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
          onClick={() => setCollapsed((value) => !value)}
        />
      </Layout.Sider>
      <Layout>
        <Layout.Header className="app-header">
          <Typography.Text type="secondary">服务状态</Typography.Text>
          <Space>
            <Tag color={healthStatus}><Badge status={healthStatus} />{healthLabel}</Tag>
            {me.data !== undefined && (
              <Typography.Text>
                {me.data.username}（{me.data.role}）
              </Typography.Text>
            )}
            <Button
              size="small"
              loading={logoutMutation.isPending}
              onClick={() => logoutMutation.mutate()}
            >
              登出
            </Button>
          </Space>
        </Layout.Header>
        <Layout.Content className="app-content">
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  )
}
