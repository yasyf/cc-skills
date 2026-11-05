import { useEffect, useRef } from 'react';
import type { PackComponentProps } from './host/present';
import { mount } from './vendor/arch-map.js';

export function Arch({ block }: PackComponentProps) {
  const host = useRef<HTMLDivElement>(null);
  const doc = block.doc;
  useEffect(() => {
    const drawn = mount(host.current, doc, {});
    return () => drawn.destroy();
  }, [doc]);
  return <div ref={host} />;
}
