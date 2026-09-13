const DEFAULT_DOWNLOAD_BASENAME = 'download'
const DEFAULT_DOWNLOAD_EXTENSION = '.bin'

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

function safeExtension(extension: string): string | null {
  const trimmed = extension.trim()
  const suffix = trimmed.startsWith('.') ? trimmed.slice(1) : trimmed
  if (
    suffix === '' ||
    suffix === '.' ||
    suffix === '..' ||
    suffix.includes('.') ||
    suffix.includes('/') ||
    suffix.includes('\\') ||
    hasControlCharacter(suffix)
  ) {
    return null
  }
  return `.${suffix}`
}

function fallbackExtension(fallback: string): string {
  const extensionStart = fallback.lastIndexOf('.')
  if (extensionStart <= 0 || extensionStart === fallback.length - 1) {
    return DEFAULT_DOWNLOAD_EXTENSION
  }
  return safeExtension(fallback.slice(extensionStart)) ?? DEFAULT_DOWNLOAD_EXTENSION
}

function withExtension(basename: string, extension: string): string {
  if (basename.toLowerCase().endsWith(extension.toLowerCase())) {
    return basename
  }
  return `${basename}${extension}`
}

/**
 * Return a filename that is safe to hand to the browser download attribute.
 * Traversal shapes, separators, control characters, and empty names fall back
 * instead of ever reaching the filesystem.
 */
export function safeDownloadName(name: string, extension: string, fallback: string): string {
  const normalizedExtension = safeExtension(extension) ?? fallbackExtension(fallback)
  const cleaned = sanitizeSegment(name)
  if (!isUnsafeSegment(cleaned)) {
    return withExtension(cleaned, normalizedExtension)
  }

  const cleanedFallback = sanitizeSegment(fallback)
  if (!isUnsafeSegment(cleanedFallback)) {
    return withExtension(cleanedFallback, normalizedExtension)
  }
  return withExtension(DEFAULT_DOWNLOAD_BASENAME, normalizedExtension)
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
