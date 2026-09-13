import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import UploadPanel from './UploadPanel'

vi.mock('antd', async (importActual) => {
  const React = await import('react')
  const actual = await importActual<typeof import('antd')>()
  const Dragger = ({
    accept,
    beforeUpload,
    children,
    customRequest,
    disabled,
    ...props
  }: {
    accept?: string
    beforeUpload?: (file: File) => boolean | void
    children?: React.ReactNode
    customRequest?: (options: { file: File; onSuccess?: () => void }) => void
    disabled?: boolean
  } & React.HTMLAttributes<HTMLDivElement>) => {
    const [autoUploaded, setAutoUploaded] = React.useState(false)

    return (
      <div aria-label={props['aria-label']} aria-disabled={disabled}>
        <input
          aria-label="mock-file-input"
          accept={accept}
          disabled={disabled}
          type="file"
          onChange={(event) => {
            const file = event.currentTarget.files?.[0]
            if (file === undefined) {
              return
            }
            const result = beforeUpload?.(file)
            if (result !== false) {
              customRequest?.({ file, onSuccess: () => setAutoUploaded(true) })
            }
          }}
        />
        {children}
        {autoUploaded && <span>auto-uploaded</span>}
      </div>
    )
  }

  return {
    ...actual,
    Upload: {
      ...actual.Upload,
      Dragger,
    },
  }
})

function getFileInput(container: HTMLElement): HTMLInputElement {
  const input = container.querySelector('input[type="file"]')
  expect(input).toBeInstanceOf(HTMLInputElement)
  return input as HTMLInputElement
}

describe('UploadPanel', () => {
  it('passes the accepted file suffixes to the file input', () => {
    const { container } = render(
      <UploadPanel accept=".zip,.tar" beforeUpload={() => {}} uploading={false} progress={0} />,
    )

    expect(getFileInput(container)).toHaveAttribute('accept', '.zip,.tar')
  })

  it('shows upload progress while uploading', () => {
    render(<UploadPanel beforeUpload={() => {}} uploading progress={42} />)

    expect(screen.getByText('正在上传…')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '42')
  })

  it('invokes beforeUpload for the accepted file and prevents Ant auto upload', async () => {
    const user = userEvent.setup()
    const beforeUpload = vi.fn()
    const { container } = render(
      <UploadPanel accept=".zip" beforeUpload={beforeUpload} uploading={false} progress={0} />,
    )
    const file = new File(['zip'], 'plugin.zip', { type: 'application/zip' })

    await user.upload(getFileInput(container), file)

    await waitFor(() => expect(beforeUpload).toHaveBeenCalledTimes(1))
    expect(beforeUpload).toHaveBeenCalledWith(expect.objectContaining({ name: 'plugin.zip' }))
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.queryByText('auto-uploaded')).not.toBeInTheDocument()
  })
})
