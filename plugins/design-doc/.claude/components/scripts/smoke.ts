// Author-time bundle self-test: stub window.CcPresent with real React, import the
// built bundle, and assert its default export shape. Run via `bun scripts/smoke.ts`.

import * as React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';

(globalThis as { window?: unknown }).window = {
  CcPresent: {
    hostApi: 1,
    React,
    jsxRuntime,
    reactDom: { createPortal: () => null },
    ui: {
      Clamped: () => null,
      renderMarkdown: (md: string) => md,
      renderInlineMarkdown: (md: string) => md,
      tokens: {},
      toast: () => {},
      usePackState: <T>(_key: string, initial: T) => [initial, () => {}],
    },
  },
};

const mod = (await import('../dist/pack.js')) as {
  default?: { hostApi?: unknown; blocks?: Record<string, unknown> };
};

const def = mod.default;
const problems: string[] = [];
if (!def) {
  problems.push('missing default export');
} else {
  if (def.hostApi !== 1) problems.push(`hostApi = ${String(def.hostApi)}, want 1`);
  const blocks = def.blocks ?? {};
  for (const name of ['registers', 'claims', 'fork']) {
    if (typeof blocks[name] !== 'function') {
      problems.push(`blocks.${name} is ${typeof blocks[name]}, want function`);
    }
  }
}

if (problems.length > 0) {
  console.error('pack smoke failed:');
  for (const p of problems) console.error('  -', p);
  process.exit(1);
}

console.log('pack smoke ok: default export { hostApi: 1, blocks: { registers, claims, fork } }');
