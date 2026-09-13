import { InboxOutlined } from '@ant-design/icons'
import { Progress, Typography, Upload } from 'antd'
import type { UploadRequestOption } from 'rc-upload/lib/interface'

interface UploadPanelProps {
  accept?: string
  beforeUpload: (file: File) => boolean | void
  uploading: boolean
  progress: number
  hint?: string
}

export default function UploadPanel({
  accept,
  beforeUpload,
  uploading,
  progress,
  hint,
}: UploadPanelProps) {
  return (
    <div>
      <Upload.Dragger
        accept={accept}
        disabled={uploading}
        showUploadList={false}
        multiple={false}
        beforeUpload={(file) => beforeUpload(file as unknown as File) === false ? false : true}
        customRequest={(options: UploadRequestOption) => {
          options.onSuccess?.({})
        }}
        aria-label="文件上传区域"
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">{uploading ? '正在上传…' : '点击或拖拽文件到此处上传'}</p>
        {hint !== undefined && (
          <Typography.Text type="secondary">
            {hint}
          </Typography.Text>
        )}
      </Upload.Dragger>
      {uploading && (
        <Progress
          percent={progress}
          size="small"
          status={progress >= 100 ? 'normal' : 'active'}
          aria-label="上传进度"
        />
      )}
    </div>
  )
}
