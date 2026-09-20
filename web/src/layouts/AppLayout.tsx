import {
  AppstoreOutlined,
  DeploymentUnitOutlined,
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
import { Badge, Breadcrumb, Button, Layout, Menu, Space, Typography } from 'antd'
import type { MenuProps } from 'antd'
import type { ReactNode } from 'react'
import { useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { getHealth } from '../api/system'
import { fetchMe, logout } from '../api/auth'
import { queryKeys } from '../hooks/queryKeys'

const menuItems: MenuProps['items'] = [
  {
    type: 'group',
    label: '总览',
    children: [{ key: '/', icon: <AppstoreOutlined />, label: <Link to="/">概览</Link> }],
  },
  {
    type: 'group',
    label: '资产',
    children: [
      { key: '/plugins', icon: <CloudOutlined />, label: <Link to="/plugins">插件</Link> },
      { key: '/registry', icon: <CloudDownloadOutlined />, label: <Link to="/registry">仓库</Link> },
      { key: '/files', icon: <FileOutlined />, label: <Link to="/files">文件</Link> },
    ],
  },
  {
    type: 'group',
    label: '运行',
    children: [
      { key: '/jobs', icon: <HddOutlined />, label: <Link to="/jobs">任务</Link> },
      { key: '/schedules', icon: <ScheduleOutlined />, label: <Link to="/schedules">定时任务</Link> },
      { key: '/pipelines', icon: <PartitionOutlined />, label: <Link to="/pipelines">管道</Link> },
      { key: '/services', icon: <DeploymentUnitOutlined />, label: <Link to="/services">服务</Link> },
    ],
  },
  {
    type: 'group',
    label: '管理',
    children: [
      { key: '/system', icon: <SettingOutlined />, label: <Link to="/system">系统</Link> },
      { key: '/admin/users', icon: <UserOutlined />, label: <Link to="/admin/users">用户</Link> },
      { key: '/admin/api-keys', icon: <KeyOutlined />, label: <Link to="/admin/api-keys">API Key</Link> },
      { key: '/admin/audit', icon: <FileSearchOutlined />, label: <Link to="/admin/audit">审计</Link> },
    ],
  },
]

const ROUTE_TITLES: Record<string, string> = {
  '/': '总览',
  '/plugins': '插件',
  '/registry': '仓库',
  '/files': '文件',
  '/jobs': '任务',
  '/jobs/new': '新建任务',
  '/schedules': '定时任务',
  '/pipelines': '管道',
  '/services': '服务',
  '/system': '系统',
  '/admin/users': '用户',
  '/admin/api-keys': 'API Key',
  '/admin/audit': '审计',
}

function breadcrumbItems(pathname: string) {
  const items: { title: ReactNode }[] = [{ title: <Link to="/">总览</Link> }]
  if (pathname === '/') return items
  if (pathname.startsWith('/jobs/')) {
    items.push({ title: <Link to="/jobs">任务</Link> })
    items.push({ title: pathname === '/jobs/new' ? '新建任务' : '任务详情' })
    return items
  }
  const title = ROUTE_TITLES[pathname]
  if (title !== undefined) items.push({ title })
  return items
}

function selectedMenuKey(pathname: string) {
  if (pathname.startsWith('/jobs/')) return '/jobs'
  return menuItems?.some((group) =>
    group?.type === 'group' && Array.isArray(group.children)
      ? group.children.some((item) => item?.key === pathname)
      : false,
  )
    ? pathname
    : ''
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
    refetchInterval: (query) => (query.state.error !== null ? 60_000 : 30_000),
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
        {collapsed ? (
          <Button
            className="sider-new-job"
            type="primary"
            aria-label="新建任务"
            icon={<PlusCircleOutlined />}
            onClick={() => navigate('/jobs/new')}
          />
        ) : (
          <Button
            className="sider-new-job"
            type="primary"
            icon={<PlusCircleOutlined />}
            onClick={() => navigate('/jobs/new')}
          >
            新建任务
          </Button>
        )}
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
          <Breadcrumb items={breadcrumbItems(pathname)} />
          <Space>
            <Typography.Text type="secondary">服务状态</Typography.Text>
            <Badge status={healthStatus} text={healthLabel} />
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
