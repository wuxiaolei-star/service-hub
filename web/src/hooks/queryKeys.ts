type QueryFilters = Record<string, string | number | boolean | null | undefined>

export const queryKeys = {
  auth: {
    me: () => ['auth', 'me'] as const,
  },
  system: {
    health: () => ['system', 'health'] as const,
    info: () => ['system', 'info'] as const,
    metrics: () => ['system', 'metrics'] as const,
  },
  plugins: {
    list: (filters: QueryFilters = {}) => ['plugins', 'list', filters] as const,
    detail: (pluginId: string) => ['plugins', 'detail', pluginId] as const,
    version: (pluginId: string, version: string) =>
      ['plugins', 'detail', pluginId, 'version', version] as const,
    builds: (filters: QueryFilters = {}) => ['plugin-builds', 'list', filters] as const,
    build: (buildId: string) => ['plugin-builds', 'detail', buildId] as const,
  },
  files: {
    list: (filters: QueryFilters = {}) => ['files', 'list', filters] as const,
    detail: (fileId: string) => ['files', 'detail', fileId] as const,
    download: (fileId: string) => ['files', 'download', fileId] as const,
  },
  users: {
    list: (filters: QueryFilters = {}) => ['users', 'list', filters] as const,
    usage: (userId: number) => ['users', 'usage', userId] as const,
  },
  apiKeys: {
    list: (filters: QueryFilters = {}) => ['api-keys', 'list', filters] as const,
  },
  audit: {
    list: (filters: QueryFilters = {}) => ['audit-logs', 'list', filters] as const,
  },
  jobs: {
    list: (filters: QueryFilters = {}) => ['jobs', 'list', filters] as const,
    detail: (jobId: string) => ['jobs', 'detail', jobId] as const,
    logs: (jobId: string, cursor: number, limit: number) =>
      ['jobs', 'logs', jobId, { cursor, limit }] as const,
    outputs: (jobId: string) => ['jobs', 'outputs', jobId] as const,
  },
}
