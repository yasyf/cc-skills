// The window.CcPresent contract (hostApi 1) a pack sees. Copy verbatim into your pack.

// ThemeTokens is the frozen ui.tokens contract of CSS-variable reference strings.
export interface ThemeTokens {
  readonly bg: string;
  readonly bgSoft: string;
  readonly surface: string;
  readonly surfaceRaised: string;
  readonly text: string;
  readonly dim: string;
  readonly border: string;
  readonly borderStrong: string;
  readonly accent: string;
  readonly accentFg: string;
  readonly ok: string;
  readonly warn: string;
  readonly danger: string;
  readonly focusRing: string;
  readonly radiusSm: string;
  readonly radiusMd: string;
  readonly radiusLg: string;
  readonly fontProse: string;
  readonly fontMono: string;
  readonly trackCaps: string;
}

// PackToast is a pack-raised toast.
export interface PackToast {
  kind: 'info' | 'error';
  text: string;
}

// CcPresentHost is the window.CcPresent surface installed before any pack loads.
export interface CcPresentHost {
  hostApi: 1;
  React: typeof import('react');
  jsxRuntime: typeof import('react/jsx-runtime');
  reactDom: { createPortal: (...args: unknown[]) => unknown };
  ui: {
    Clamped: import('react').ComponentType<{ lines?: number; children?: import('react').ReactNode }>;
    renderMarkdown: (md: string) => string;
    renderInlineMarkdown: (md: string) => string;
    tokens: ThemeTokens;
    toast: (toast: PackToast) => void;
    usePackState: <T>(key: string, initial: T) => [T, (next: T) => void];
  };
}

// PackBlock is the opaque block a component renders.
export interface PackBlock {
  id: string;
  type: string;
  [key: string]: unknown;
}

// PackBlockContext decomposes the block's lifecycle for a pack component.
export interface PackBlockContext {
  closed: boolean;
  roundOver: boolean;
  round: number;
}

// PackComponentProps is what the host calls every pack component with.
export interface PackComponentProps {
  block: PackBlock;
  value: unknown;
  submit: (payload: unknown) => void;
  disabled: boolean;
  context: PackBlockContext;
}

function requireHost(): CcPresentHost {
  const host = window.CcPresent;
  if (!host) {
    throw new Error('cc-present: window.CcPresent unavailable; the host must install it before a pack bundle loads');
  }
  return host;
}

// renderMarkdown turns Markdown into the host's sanitized HTML string.
export function renderMarkdown(md: string): string {
  return requireHost().ui.renderMarkdown(md);
}

// tokens returns the host's frozen ui.tokens.
export function tokens(): ThemeTokens {
  return requireHost().ui.tokens;
}

// toast raises a host toast.
export function toast(t: PackToast): void {
  requireHost().ui.toast(t);
}

// usePackState is the host's ephemeral per-block draft-state hook.
export function usePackState<T>(key: string, initial: T): [T, (next: T) => void] {
  return requireHost().ui.usePackState(key, initial);
}
