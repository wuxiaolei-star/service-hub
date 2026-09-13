import { useMutation, useQueryClient } from '@tanstack/react-query'
import { disablePluginBuild, enablePluginBuild } from '../api/plugins'
import { queryKeys } from './queryKeys'
import type { PluginBuild } from '../types/api'

type BuildToggleAction = 'enable' | 'disable'

/**
 * Enable or disable one Build and keep every cached view of it consistent.
 * The mutation response is authoritative, so it is written straight into the
 * Build detail cache before list invalidation.
 */
export function useBuildToggle() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ build, action }: { build: PluginBuild; action: BuildToggleAction }) =>
      action === 'enable'
        ? enablePluginBuild(build.build_id)
        : disablePluginBuild(build.build_id),
    onSuccess: (updated: PluginBuild) => {
      queryClient.setQueryData(queryKeys.plugins.build(updated.build_id), updated)
      void queryClient.invalidateQueries({ queryKey: queryKeys.plugins.builds() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.plugins.list() })
    },
  })
}
