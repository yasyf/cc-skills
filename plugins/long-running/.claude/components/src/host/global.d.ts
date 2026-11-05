import type { CcPresentHost } from './present';

declare global {
  interface Window {
    CcPresent?: CcPresentHost;
  }
}
