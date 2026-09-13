import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'
import { getPluginBuild, installPlugin } from '../api/plugins'
import { pollWhileBuildActive } from './polling'
import { queryKeys } from './queryKeys'
import type { PluginBuild, UploadProgressHandler } from '../types/api'

interface InstallMutationInput {
  packageFile: File
  onProgress?: UploadProgressHandler
}

/**
 * Install one `.pypkg` and follow its Build through INSTALLING to a terminal
 * state. The upload response alone never determines the final status.
 */
export function usePluginInstall() {
  const queryClient = useQueryClient()
  const [buildId, setBuildId] = useState<string | null>(null)
  const [progress, setProgress] = useState(0)

  const install = useMutation({
    mutationFn: async ({ packageFile, onProgress }: InstallMutationInput) => {
      const reportProgress: UploadProgressHandler = (percent) => {
        setProgress(percent)
        onProgress?.(percent)
      }
      const build = await installPlugin(packageFile, reportProgress)
      return build
    },
    onSuccess: (build: PluginBuild) => {
      setProgress(100)
      setBuildId(build.build_id)
      void queryClient.invalidateQueries({ queryKey: queryKeys.plugins.list() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.plugins.builds() })
    },
  })

  const buildQuery = useQuery({
    queryKey: queryKeys.plugins.build(buildId ?? 'pending-install'),
    queryFn: () => getPluginBuild(buildId as string),
    enabled: buildId !== null,
    refetchInterval: (query) => pollWhileBuildActive(query.state.data?.status ?? 'INSTALLING'),
  })

  const trackedStatus = buildQuery.data?.status
  useEffect(() => {
    if (trackedStatus !== undefined && trackedStatus !== 'INSTALLING') {
      void queryClient.invalidateQueries({ queryKey: queryKeys.plugins.builds() })
    }
  }, [trackedStatus, queryClient])

  const reset = useCallback(() => {
    setBuildId(null)
    setProgress(0)
  }, [])

  return {
    install,
    progress,
    trackedBuild: buildQuery.data,
    reset,
  }
}
