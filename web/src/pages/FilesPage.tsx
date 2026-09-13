import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Button, Card, Input, Space, Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { downloadFile, getFileMetadata, listFiles, uploadFile } from '../api/files'
import { toHubApiError } from '../api/errors'
import EmptyState from '../components/EmptyState'
import HubErrorAlert from '../components/HubErrorAlert'
import PageHeader from '../components/PageHeader'
import UploadPanel from '../components/UploadPanel'
import { queryKeys } from '../hooks/queryKeys'
import { useRecentFiles } from '../hooks/useRecentFiles'
import type { FileRecord } from '../types/api'
import { formatDateTime, formatFileSize } from '../utils/format'
import { safeDownloadName, saveBlob } from '../utils/download'

interface RowRecord {
  key: string
  file_id: string
  name: string
  size: number | null
  created_at: string | null
  extension: string | null
  origin: 'server' | 'local'
}

function toRow(file: FileRecord): RowRecord {
  return {
    key: file.file_id,
    file_id: file.file_id,
    name: file.name,
    size: file.size,
    created_at: file.created_at,
    extension: file.extension,
    origin: 'server',
  }
}

function localToRow(entry: { file_id: string; name: string; size?: number; created_at?: string }): RowRecord {
  return {
    key: entry.file_id,
    file_id: entry.file_id,
    name: entry.name,
    size: entry.size ?? null,
    created_at: entry.created_at ?? null,
    extension: null,
    origin: 'local',
  }
}

export default function FilesPage() {
  const queryClient = useQueryClient()
  const { recent, recordUpload, forgetRecord, lookup } = useRecentFiles()
  const [lookupId, setLookupId] = useState('')
  const [lookupError, setLookupError] = useState(false)

  const files = useQuery({ queryKey: queryKeys.files.list(), queryFn: listFiles })

  const metadataLookup = useMutation({
    mutationFn: getFileMetadata,
    onSuccess: (record) => {
      setLookupError(false)
      recordUpload(record)
      void queryClient.invalidateQueries({ queryKey: queryKeys.files.list() })
    },
    onError: () => {
      setLookupError(true)
    },
  })

  const upload = useMutation({
    mutationFn: ({ file }: { file: File }) => uploadFile(file),
    onSuccess: (record) => {
      recordUpload(record)
      void queryClient.invalidateQueries({ queryKey: queryKeys.files.list() })
    },
  })

  const download = useMutation({
    mutationFn: async (row: RowRecord) => {
      const blob = await downloadFile(row.file_id)
      let name = row.name
      let extension = row.extension
      try {
        const metadata = await queryClient.fetchQuery({
          queryKey: queryKeys.files.detail(row.file_id),
          queryFn: () => getFileMetadata(row.file_id),
        })
        name = metadata.name
        extension = metadata.extension
      } catch {
        // Metadata is only a naming aid; the blob download itself must survive.
      }
      return { blob, name, extension }
    },
    onSuccess: ({ blob, name, extension }) => {
      saveBlob(blob, safeDownloadName(name, extension ?? '', 'download.bin'))
    },
  })

  const serverIds = new Set((files.data?.items ?? []).map((item) => item.file_id))
  const localOnly = recent
    .filter((entry) => !serverIds.has(entry.file_id))
    .map(localToRow)
  const rows: RowRecord[] = [
    ...(files.data?.items ?? []).map(toRow),
    ...localOnly,
  ]

  const columns: ColumnsType<RowRecord> = [
    {
      title: '文件名',
      dataIndex: 'name',
      render: (value: string, record) => <a onClick={() => download.mutate(record)}>{value}</a>,
    },
    {
      title: '文件 ID',
      dataIndex: 'file_id',
      render: (value: string) => <Typography.Text copyable={{ text: value }}>{value}</Typography.Text>,
    },
    { title: '大小', dataIndex: 'size', render: (value: number | null) => (value === null ? '-' : formatFileSize(value)) },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      render: (value: string | null) => (value === null ? '-' : formatDateTime(value)),
    },
    {
      title: '来源',
      dataIndex: 'origin',
      render: (value: RowRecord['origin']) => (value === 'local' ? '本地记录' : '服务器'),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, record) => (
        <Space>
          <Button size="small" loading={download.isPending} onClick={() => download.mutate(record)}>
            下载
          </Button>
          {record.origin === 'local' && (
            <Button size="small" onClick={() => forgetRecord(record.file_id)}>
              移除记录
            </Button>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="文件管理"
        subtitle="上传输入文件、按 ID 查找并安全下载。"
      />
      <Card style={{ marginBottom: 16 }}>
        <UploadPanel
          uploading={upload.isPending}
          progress={upload.variables ? 100 : 0}
          hint="上传成功后文件会出现在下方列表，并记录到本地最近文件。"
          beforeUpload={(file) => {
            upload.mutate({ file })
            return false
          }}
        />
        {upload.isError && <HubErrorAlert error={toHubApiError(upload.error)} />}
        {download.isError && <HubErrorAlert error={toHubApiError(download.error)} />}
      </Card>
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space.Compact style={{ width: '100%', maxWidth: 480 }}>
          <Input
            aria-label="按文件 ID 查找"
            placeholder="输入文件 ID，例如 file_1a2b"
            value={lookupId}
            onChange={(event) => setLookupId(event.target.value)}
            onPressEnter={() => lookupId !== '' && metadataLookup.mutate(lookupId.trim())}
          />
          <Button
            type="primary"
            loading={metadataLookup.isPending}
            onClick={() => lookupId !== '' && metadataLookup.mutate(lookupId.trim())}
          >
            查找
          </Button>
        </Space.Compact>
        {lookupError && (
          <Typography.Text type="warning" style={{ marginLeft: 12 }}>
            查找失败，已保留本地记录
          </Typography.Text>
        )}
        {lookupId !== '' && lookup(lookupId.trim()) !== undefined && (
          <Typography.Text type="secondary" style={{ marginLeft: 12 }}>
            本地记录：{lookup(lookupId.trim())?.name}
          </Typography.Text>
        )}
      </Card>
      {files.error !== null && files.error !== undefined && (
        <HubErrorAlert error={toHubApiError(files.error)} onRetry={() => void files.refetch()} />
      )}
      <Card title="服务器最近 100 条记录，本地最近文件补充显示">
        <Table
          rowKey="key"
          loading={files.isLoading}
          pagination={false}
          dataSource={rows}
          columns={columns}
          locale={{ emptyText: <EmptyState description="暂无文件" /> }}
        />
      </Card>
    </div>
  )
}
