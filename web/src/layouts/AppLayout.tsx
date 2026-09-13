import {
  AppstoreOutlined,
  CloudOutlined,
  FileOutlined,
  HddOutlined,
  PlusCircleOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { Layout, Menu, Tag, Typography } from 'antd'
import type { MenuProps } from 'antd'
import { Link, Outlet, useLocation } from 'react-router-dom'

const menuItems: MenuProps['items'] = [
  { key: '/', icon: <AppstoreOutlined />, label: <Link to="/">概览</Link> },
  { key: '/plugins', icon: <CloudOutlined />, label: <Link to="/plugins">插件</Link> },
  { key: '/files', icon: <FileOutlined />, label: <Link to="/files">文件</Link> },
  { key: '/jobs/new', icon: <PlusCircleOutlined />, label: <Link to="/jobs/new">新建任务</Link> },
  { key: '/jobs', icon: <HddOutlined />, label: <Link to="/jobs">任务</Link> },
  { key: '/system', icon: <SettingOutlined />, label: <Link to="/system">系统</Link> },
]

function selectedMenuKey(pathname: string) {
  if (pathname.startsWith('/jobs/')) return pathname === '/jobs/new' ? '/jobs/new' : '/jobs'
  return menuItems?.some((item) => item?.key === pathname) ? pathname : ''
}

export default function AppLayout() {
  const { pathname } = useLocation()

  return (
    <Layout className="app-shell">
      <Layout.Sider className="app-sider" breakpoint="lg" collapsedWidth={64} collapsible>
        <Link className="brand" to="/" aria-label="Service Hub">
          <span className="brand-mark">S</span>
          <Typography.Text className="brand-name">Service Hub</Typography.Text>
        </Link>
        <nav aria-label="主导航">
          <Menu mode="inline" selectedKeys={[selectedMenuKey(pathname)]} items={menuItems} />
        </nav>
      </Layout.Sider>
      <Layout>
        <Layout.Header className="app-header">
          <Typography.Text>服务状态</Typography.Text>
          <Tag color="success">健康状态待接入</Tag>
        </Layout.Header>
        <Layout.Content className="app-content">
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  )
}
