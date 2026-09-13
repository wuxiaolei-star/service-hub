import { pollWhileBuildActive, pollWhileJobActive } from './polling'

describe('pollWhileBuildActive', () => {
  test.each([
    ['INSTALLING', 2000],
    ['READY', false],
    ['ENABLED', false],
    ['FAILED', false],
  ] as const)('returns %s polling interval for %s builds', (status, expected) => {
    expect(pollWhileBuildActive(status)).toBe(expected)
  })
})

describe('pollWhileJobActive', () => {
  test.each([
    ['PENDING', 2000],
    ['PREPARING', 2000],
    ['RUNNING', 2000],
    ['CANCEL_REQUESTED', 2000],
    ['SUCCESS', false],
    ['FAILED', false],
    ['CANCELLED', false],
    ['TIMED_OUT', false],
  ] as const)('returns %s polling interval for %s jobs', (status, expected) => {
    expect(pollWhileJobActive(status)).toBe(expected)
  })
})
