// Alias target for `react/jsx-runtime`: re-exports the host's automatic-runtime
// factories so compiled JSX uses the host's React.

const host = window.CcPresent;
if (!host) {
  throw new Error('cc-present: window.CcPresent unavailable; a pack bundle loaded before the host installed it');
}

export const jsx = host.jsxRuntime.jsx;
export const jsxs = host.jsxRuntime.jsxs;
export const Fragment = host.jsxRuntime.Fragment;
