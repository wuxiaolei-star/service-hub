import { describe, expect, it, vi, afterEach } from 'vitest'
import { saveBlob, safeDownloadName } from './download'

describe('safeDownloadName', () => {
  it('keeps a normal name and appends the extension once', () => {
    expect(safeDownloadName('结果.zip', '.zip', 'download.bin')).toBe('结果.zip')
    expect(safeDownloadName('结果', '.zip', 'download.bin')).toBe('结果.zip')
  })

  it.each([
    ['../escape.zip'],
    ['a/b.zip'],
    ['a\\b.zip'],
    ['..'],
    [''],
    ['\x07control.zip'],
    ['\x1f[2J.zip'],
  ])('falls back instead of using unsafe name %s', (name) => {
    expect(safeDownloadName(name, '.zip', 'fallback.zip')).toBe('fallback.zip')
  })

  it('uses the fallback itself when it is unsafe too', () => {
    expect(safeDownloadName('', '.zip', '../bad')).toBe('download.zip')
  })

  it('does not duplicate the extension for dotted names', () => {
    expect(safeDownloadName('result.2024.tar', '.tar', 'download.bin')).toBe('result.2024.tar')
  })
})

describe('saveBlob', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('creates an anchor, clicks it, and always revokes the object URL', () => {
    const objectUrl = 'blob:mock-url'
    const createObjectURL = vi.fn(() => objectUrl)
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL,
      revokeObjectURL,
    })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    saveBlob(new Blob(['x']), 'result.zip')

    expect(createObjectURL).toHaveBeenCalledTimes(1)
    expect(click).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).toHaveBeenCalledWith(objectUrl)

    click.mockRestore()
  })

  it('revokes the object URL even when the click throws', () => {
    const objectUrl = 'blob:mock-url'
    const createObjectURL = vi.fn(() => objectUrl)
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL,
      revokeObjectURL,
    })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {
      throw new Error('click blocked')
    })

    expect(() => saveBlob(new Blob(['x']), 'result.zip')).toThrow('click blocked')
    expect(revokeObjectURL).toHaveBeenCalledWith(objectUrl)
    expect(document.querySelector('a[download="result.zip"]')).not.toBeInTheDocument()
  })
})
