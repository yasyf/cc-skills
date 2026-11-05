// Alias target for `react`: re-exports the host's React so the pack shares the
// SPA's single instance. tsc still type-checks imports against @types/react.

const host = window.CcPresent;
if (!host) {
  throw new Error('cc-present: window.CcPresent unavailable; a pack bundle loaded before the host installed it');
}
const React = host.React;

export default React;
export const { createElement, Fragment, useCallback, useEffect, useMemo, useRef, useState } = React;
