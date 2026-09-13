const DEFAULT_DOWNLOAD_BASENAME = 'download'

function hasControlCharacter(name: string): boolean {
  return Array.from(name).some((character) => {
    const codePoint = character.codePointAt(0) ?? 0
    return codePoint <= 31 || codePoint === 127
  })
}

function isUnsafeSegment(name: string): boolean {
  return (
    name === '' ||
    name === '.' ||
    name === '..' ||
    hasControlCharacter(name) ||
    name.includes('/') ||
    name.includes('\\') ||
    name.startsWith('..')
  )
}

function sanitizeSegment(name: string): string {
  return name.trim()
}

function withExtension(basename: string, extension: string): string {
  const normalizedExtension = extension.startsWith('.') ? extension : `.${extension}`
  if (basename.toLowerCase().endsWith(normalizedExtension.toLowerCase())) {
    return basename
  }
  return `${basename}${normalizedExtension}`
}

/**
 * Return a filename that is safe to hand to the browser download attribute.
 * Traversal shapes, separators, control characters, and empty names fall back
 * instead of ever reaching the filesystem.
 */
export function safeDownloadName(name: string, extension: string, fallback: string): string {
  const cleaned = sanitizeSegment(name)
  if (!isUnsafeSegment(cleaned)) {
    return withExtension(cleaned, extension)
  }

  const cleanedFallback = sanitizeSegment(fallback)
  if (!isUnsafeSegment(cleanedFallback)) {
    return withExtension(cleanedFallback, extension)
  }
  return withExtension(DEFAULT_DOWNLOAD_BASENAME, extension)
}

/** Trigger a browser download for the blob and always release the object URL. */
export function saveBlob(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob)
  try {
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = filename
    anchor.rel = 'noopener'
    document.body.appendChild(anchor)
    try {
      anchor.click()
    } finally {
      document.body.removeChild(anchor)
    }
  } finally {
    URL.revokeObjectURL(objectUrl)
  }
}
