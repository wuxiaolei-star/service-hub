import type { BuildStatus, JobStatus } from '../types/api'

const POLLING_INTERVAL_MS = 2000

export function pollWhileBuildActive(status: BuildStatus): 2000 | false {
  return status === 'INSTALLING' ? POLLING_INTERVAL_MS : false
}

export function pollWhileJobActive(status: JobStatus): 2000 | false {
  return status === 'PENDING' ||
    status === 'PREPARING' ||
    status === 'RUNNING' ||
    status === 'CANCEL_REQUESTED'
    ? POLLING_INTERVAL_MS
    : false
}
