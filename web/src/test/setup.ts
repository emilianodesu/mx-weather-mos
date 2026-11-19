import '@testing-library/jest-dom';

// Polyfill ResizeObserver for Recharts ResponsiveContainer in jsdom
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (typeof window !== 'undefined' && !window.ResizeObserver) {
  window.ResizeObserver = ResizeObserverStub;
}
