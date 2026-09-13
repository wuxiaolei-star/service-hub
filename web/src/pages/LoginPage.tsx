import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, Button, Card, Form, Input, Typography } from 'antd'
import { login } from '../api/auth'
import { toHubApiError } from '../api/errors'

interface LoginRequest {
  username: string
  password: string
}

export default function LoginPage() {
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)
  const loginMutation = useMutation({
    mutationFn: (request: LoginRequest) => login(request.username, request.password),
    onSuccess: () => navigate('/', { replace: true }),
    onError: (reason) => setError(toHubApiError(reason).message),
  })

  return (
    <div
      style={{
        display: 'grid',
        placeItems: 'center',
        minHeight: '100vh',
        background: '#f4f8fd',
      }}
    >
      <Card style={{ width: 380 }}>
        <Typography.Title level={3} style={{ textAlign: 'center' }}>
          Service Hub 管理台登录
        </Typography.Title>
        {error !== null && (
          <Alert type="error" showIcon style={{ marginBottom: 12 }} message={error} />
        )}
        <Form
          layout="vertical"
          onFinish={(values) => loginMutation.mutate(values as LoginRequest)}
        >
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input autoComplete="username" />
          </Form.Item>
          <Form.Item
            name="password"
            label="口令"
            rules={[{ required: true, message: '请输入口令' }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            block
            loading={loginMutation.isPending}
          >
            登录
          </Button>
        </Form>
      </Card>
    </div>
  )
}
