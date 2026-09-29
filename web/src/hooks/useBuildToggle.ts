import { useMutation, useQueryClient } from '@tanstack/react-query'
import { deprecatePluginBuild, disablePluginBuild, enablePluginBuild } from '../api/plugins'
import { queryKeys } from './queryKeys'
import type { PluginBuild } from '../types/api'

type BuildToggleAction = 'enable' | 'disable' | 'deprecate'

/**
 * Enable, disable, or deprecate one Build and keep every cached view of it
 * consistent. The mutation response is authoritative, so it is written straight
 * into the Build detail cache before list invalidation.
 */
export function useBuildToggle() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ build, action }: { build: PluginBuild; action: BuildToggleAction }) => {
      switch (action) {
        case 'enable':
          return enablePluginBuild(build.build_id)
        case 'disable':
          return disablePluginBuild(build.build_id)
        case 'deprecate':
          return deprecatePluginBuild(build.build_id)
      }
    },
    onSuccess: (updated: PluginBuild) => {
      queryClient.setQueryData(queryKeys.plugins.build(updated.build_id), updated)
      void queryClient.invalidateQueries({ queryKey: queryKeys.plugins.builds() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.plugins.list() })
    },
  })
}
