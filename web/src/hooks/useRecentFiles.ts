import { useCallback, useEffect, useState } from 'react'
import type { FileRecord } from '../types/api'

const STORAGE_KEY = 'service-hub.recent-files.v1'
const MAX_RECORDS = 50

interface StoredRecord {
  file_id: string
  name: string
  size?: number
  extension?: string | null
  created_at?: string
}

function isStoredRecord(value: unknown): value is StoredRecord {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const candidate = value as Record<string, unknown>
  return typeof candidate.file_id === 'string' && typeof candidate.name === 'string'
}

function loadRecords(): StoredRecord[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (raw === null) {
      return []
    }
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) {
      return []
    }
    return parsed.filter(isStoredRecord).slice(0, MAX_RECORDS)
  } catch {
    // Corrupted local preferences must never break the console.
    return []
  }
}

function persist(records: StoredRecord[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(records))
  } catch {
    // Storage quota or privacy mode: keep working in memory only.
  }
}

/**
 * Client-side cache of the operator's recently used files. Only public
 * metadata (ids and names) is stored, never payloads or tokens.
 */
export function useRecentFiles() {
  const [recent, setRecent] = useState<StoredRecord[]>(loadRecords)

  useEffect(() => {
    persist(recent)
  }, [recent])

  const recordUpload = useCallback((record: FileRecord) => {
    setRecent((current) => {
      const entry: StoredRecord = {
        file_id: record.file_id,
        name: record.name,
        ...(record.size === undefined ? {} : { size: record.size }),
        ...(record.extension === undefined ? {} : { extension: record.extension }),
        ...(record.created_at === undefined ? {} : { created_at: record.created_at }),
      }
      const deduped = current.filter((item) => item.file_id !== record.file_id)
      return [entry, ...deduped].slice(0, MAX_RECORDS)
    })
  }, [])

  const forgetRecord = useCallback((fileId: string) => {
    setRecent((current) => current.filter((item) => item.file_id !== fileId))
  }, [])

  const lookup = useCallback(
    (fileId: string) => recent.find((item) => item.file_id === fileId),
    [recent],
  )

  return { recent, recordUpload, forgetRecord, lookup }
}
