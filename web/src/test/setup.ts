import '@testing-library/jest-dom/vitest'

// jsdom lacks matchMedia/ResizeObserver, which antd responsive grids require.
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}

if (typeof window !== 'undefined' && typeof window.ResizeObserver !== 'function') {
  class ResizeObserverMock {
    observe(): void {}

    unobserve(): void {}

    disconnect(): void {}
  }
  window.ResizeObserver = ResizeObserverMock as unknown as typeof ResizeObserver
}
