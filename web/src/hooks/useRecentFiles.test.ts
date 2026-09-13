import { describe, expect, it, beforeEach, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useRecentFiles } from './useRecentFiles'
import type { FileRecord } from '../types/api'

const record = (fileId: string, name = `${fileId}.nc`): FileRecord => ({
  file_id: fileId,
  name,
  size: 128,
  sha256: 'a'.repeat(64),
  extension: '.nc',
  mime_type: 'application/octet-stream',
  status: 'AVAILABLE',
  created_at: '2026-09-13T08:00:00Z',
})

const STORAGE_KEY = 'service-hub.recent-files.v1'

describe('useRecentFiles', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('records uploads with metadata only and deduplicates by file_id', () => {
    const { result } = renderHook(() => useRecentFiles())

    act(() => {
      result.current.recordUpload(record('file_1'))
      result.current.recordUpload(record('file_1', 'renamed.nc'))
      result.current.recordUpload(record('file_2'))
    })

    expect(result.current.recent).toHaveLength(2)
    expect(result.current.recent[0].file_id).toBe('file_2')
    expect(result.current.recent[1].name).toBe('renamed.nc')

    const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? '[]')
    expect(stored).toHaveLength(2)
    for (const entry of stored) {
      expect(Object.keys(entry)).not.toContain('blob')
      expect(JSON.stringify(entry)).not.toContain('pypkg-bytes')
    }
  })

  it('caps stored records at 50, newest first', () => {
    const { result } = renderHook(() => useRecentFiles())

    act(() => {
      for (let index = 0; index < 60; index += 1) {
        result.current.recordUpload(record(`file_${index}`))
      }
    })

    expect(result.current.recent).toHaveLength(50)
    expect(result.current.recent[0].file_id).toBe('file_59')
    expect(result.current.recent[49].file_id).toBe('file_10')
  })

  it('ignores corrupted localStorage payloads instead of crashing', () => {
    window.localStorage.setItem(STORAGE_KEY, '{not-json')
    const { result } = renderHook(() => useRecentFiles())
    expect(result.current.recent).toEqual([])
  })

  it('drops entries whose stored shape is not public metadata', () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify([{ file_id: 'file_ok', name: 'ok.nc' }, { file_id: 42 }, 'junk']),
    )
    const { result } = renderHook(() => useRecentFiles())
    expect(result.current.recent).toEqual([{ file_id: 'file_ok', name: 'ok.nc' }])
  })

  it('forgetRecord removes one entry', () => {
    const { result } = renderHook(() => useRecentFiles())
    act(() => {
      result.current.recordUpload(record('file_1'))
      result.current.recordUpload(record('file_2'))
    })

    act(() => {
      result.current.forgetRecord('file_1')
    })

    expect(result.current.recent.map((entry) => entry.file_id)).toEqual(['file_2'])
  })

  it('exposes lookup by file id', () => {
    const { result } = renderHook(() => useRecentFiles())
    act(() => {
      result.current.recordUpload(record('file_1'))
    })
    expect(result.current.lookup('file_1')?.name).toBe('file_1.nc')
    expect(result.current.lookup('missing')).toBeUndefined()
    expect(vi.isMockFunction(result.current.lookup)).toBe(false)
  })
})
