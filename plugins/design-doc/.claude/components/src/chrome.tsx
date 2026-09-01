import type { CSSProperties, ReactNode } from 'react';
import { tokens } from './host/present';

export type Tone = 'dim' | 'ok' | 'warn' | 'danger' | 'accent';

export function caps(): CSSProperties {
  const t = tokens();
  return {
    fontFamily: t.fontMono,
    fontSize: '0.7rem',
    letterSpacing: t.trackCaps,
    textTransform: 'uppercase',
    color: t.dim,
  };
}

export function prose(size = '0.9rem'): CSSProperties {
  return { fontFamily: tokens().fontProse, fontSize: size };
}

export function dimText(): CSSProperties {
  const t = tokens();
  return { ...prose('0.85rem'), color: t.dim, margin: 0 };
}

export function Panel({
  title,
  meta,
  children,
}: {
  title: string;
  meta?: string;
  children: ReactNode;
}) {
  const t = tokens();
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.85rem',
        width: '100%',
        boxSizing: 'border-box',
        padding: '1rem',
        background: t.surface,
        color: t.text,
        border: `1px solid ${t.border}`,
        borderRadius: t.radiusLg,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '0.5rem' }}>
        <div style={{ ...prose('1rem'), fontWeight: 600 }}>{title}</div>
        {meta && <span style={caps()}>{meta}</span>}
      </div>
      {children}
    </div>
  );
}

export function SectionLabel({ children }: { children: ReactNode }) {
  const t = tokens();
  return (
    <div style={{ ...caps(), paddingBottom: '0.3rem', borderBottom: `1px solid ${t.border}` }}>{children}</div>
  );
}

export function IdChip({ id }: { id: string }) {
  const t = tokens();
  return (
    <span
      style={{
        fontFamily: t.fontMono,
        fontSize: '0.72rem',
        padding: '0.1rem 0.35rem',
        borderRadius: t.radiusSm,
        border: `1px solid ${t.border}`,
        color: t.dim,
        whiteSpace: 'nowrap',
      }}
    >
      {id}
    </span>
  );
}

export function Pill({ label, tone = 'dim' }: { label: string; tone?: Tone }) {
  const t = tokens();
  const color = tone === 'ok' ? t.ok : tone === 'warn' ? t.warn : tone === 'danger' ? t.danger : tone === 'accent' ? t.accent : t.dim;
  return (
    <span
      style={{
        fontFamily: t.fontMono,
        fontSize: '0.68rem',
        letterSpacing: t.trackCaps,
        textTransform: 'uppercase',
        padding: '0.1rem 0.4rem',
        borderRadius: t.radiusSm,
        border: `1px solid ${color}`,
        color,
        whiteSpace: 'nowrap',
      }}
    >
      {label}
    </span>
  );
}

export function ActionButton({
  label,
  active = false,
  disabled = false,
  primary = false,
  onClick,
}: {
  label: string;
  active?: boolean;
  disabled?: boolean;
  primary?: boolean;
  onClick: () => void;
}) {
  const t = tokens();
  const filled = active || primary;
  return (
    <button
      type="button"
      disabled={disabled}
      aria-pressed={active}
      onClick={onClick}
      style={{
        padding: '0.35rem 0.7rem',
        fontFamily: t.fontProse,
        fontSize: '0.8rem',
        borderRadius: t.radiusMd,
        border: `1px solid ${filled ? t.accent : t.border}`,
        background: filled ? t.accent : t.surface,
        color: filled ? t.accentFg : t.text,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.55 : 1,
        whiteSpace: 'nowrap',
      }}
    >
      {label}
    </button>
  );
}

export function NoteField({
  value,
  placeholder,
  disabled = false,
  rows = 2,
  onChange,
  onCommit,
}: {
  value: string;
  placeholder?: string;
  disabled?: boolean;
  rows?: number;
  onChange: (next: string) => void;
  onCommit?: () => void;
}) {
  const t = tokens();
  return (
    <textarea
      rows={rows}
      value={value}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      onBlur={onCommit}
      style={{
        width: '100%',
        boxSizing: 'border-box',
        resize: 'vertical',
        padding: '0.4rem 0.55rem',
        fontFamily: t.fontProse,
        fontSize: '0.85rem',
        color: t.text,
        background: t.bg,
        border: `1px solid ${t.border}`,
        borderRadius: t.radiusMd,
      }}
    />
  );
}

export function TextField({
  value,
  placeholder,
  disabled = false,
  onChange,
}: {
  value: string;
  placeholder?: string;
  disabled?: boolean;
  onChange: (next: string) => void;
}) {
  const t = tokens();
  return (
    <input
      type="text"
      value={value}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      style={{
        width: '100%',
        boxSizing: 'border-box',
        padding: '0.4rem 0.55rem',
        fontFamily: t.fontProse,
        fontSize: '0.85rem',
        color: t.text,
        background: t.bg,
        border: `1px solid ${t.border}`,
        borderRadius: t.radiusMd,
      }}
    />
  );
}

export function Row({ children, selected = false }: { children: ReactNode; selected?: boolean }) {
  const t = tokens();
  return (
    <li
      style={{
        listStyle: 'none',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.4rem',
        padding: '0.6rem 0.7rem',
        borderRadius: t.radiusMd,
        border: `1px solid ${selected ? t.accent : t.border}`,
        background: selected ? t.bgSoft : 'transparent',
      }}
    >
      {children}
    </li>
  );
}

export function List({ children }: { children: ReactNode }) {
  return (
    <ul style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', margin: 0, padding: 0 }}>{children}</ul>
  );
}
