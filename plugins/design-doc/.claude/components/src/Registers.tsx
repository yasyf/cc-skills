import { useCallback } from 'react';
import { tokens, usePackState } from './host/present';
import type { PackComponentProps } from './host/present';
import { ActionButton, IdChip, List, NoteField, Panel, Pill, Row, SectionLabel, caps, dimText, prose } from './chrome';
import { groupBy, merge } from './maps';

interface Assumption {
  id: string;
  t: string;
  s: 'working' | 'validate';
  b?: string;
  n?: string;
  star?: boolean;
}

interface Decision {
  id: string;
  t: string;
  s: 'resolved' | 'superseded' | 'open';
  r?: string;
  x?: string;
  by?: string;
  round?: number;
}

interface OpenItem {
  g?: string;
  id: string;
  t: string;
}

type Action = 'confirm' | 'challenge';

interface Entry {
  action: Action;
  note?: string;
}

const assumptionTone = { working: 'ok', validate: 'warn' } as const;
const decisionTone = { resolved: 'ok', superseded: 'dim', open: 'warn' } as const;

export function Registers({ block, value, submit, disabled }: PackComponentProps) {
  const t = tokens();
  const assumptions = (block.assumptions as Assumption[] | undefined) ?? [];
  const decisions = (block.decisions as Decision[] | undefined) ?? [];
  const open = (block.open as OpenItem[] | undefined) ?? [];
  const openGroups = (block.openGroups as Record<string, string> | undefined) ?? {};
  const live = block.challengeable === true && !disabled;

  const [pending, setPending] = usePackState<Record<string, Entry>>('pending', {});
  const [notes, setNotes] = usePackState<Record<string, string>>('notes', {});
  const entries = merge((value as { entries?: Record<string, Entry> } | null | undefined)?.entries, pending);

  const noteFor = (id: string) => notes[id] ?? entries[id]?.note ?? '';
  const titleOf = (id: string) => decisions.find((d) => d.id === id)?.t;

  const record = useCallback(
    (id: string, action: Action, note: string) => {
      const entry: Entry = note.trim() ? { action, note: note.trim() } : { action };
      const next = merge(entries, { [id]: entry });
      setPending(next);
      submit({ entries: next });
    },
    [submit, setPending, entries],
  );

  const act = useCallback(
    (id: string, action: Action) => record(id, action, action === 'challenge' ? noteFor(id) : ''),
    [record, notes, entries],
  );

  const commitNote = useCallback(
    (id: string) => {
      const entry = entries[id];
      if (entry) record(id, entry.action, noteFor(id));
    },
    [record, entries, notes],
  );

  const verdict = (id: string) => {
    const entry = entries[id];
    if (!entry) return null;
    if (live) {
      if (entry.action !== 'challenge') return null;
      return (
        <NoteField
          value={noteFor(id)}
          placeholder="What is wrong with it?"
          onChange={(next) => setNotes({ ...notes, [id]: next })}
          onCommit={() => commitNote(id)}
        />
      );
    }
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
        <span style={caps()}>{entry.action === 'confirm' ? 'confirmed' : 'challenged'}</span>
        {entry.note && <p style={dimText()}>{entry.note}</p>}
      </div>
    );
  };

  const actions = (id: string) => {
    if (!live) return null;
    const entry = entries[id];
    return (
      <div style={{ display: 'flex', gap: '0.4rem' }}>
        <ActionButton label="Confirm" active={entry?.action === 'confirm'} onClick={() => act(id, 'confirm')} />
        <ActionButton label="Challenge" active={entry?.action === 'challenge'} onClick={() => act(id, 'challenge')} />
      </div>
    );
  };

  const groups = groupBy(open, (item) => item.g ?? 'open');

  return (
    <Panel title={block.title as string} meta={block.phase as string | undefined}>
      {assumptions.length > 0 && (
        <section style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <SectionLabel>Assumptions</SectionLabel>
          <List>
            {assumptions.map((a) => (
              <Row key={a.id} selected={a.star === true}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem', flexWrap: 'wrap' }}>
                  <span style={{ ...prose(), fontWeight: 600 }}>{a.t}</span>
                  {a.star && (
                    <span aria-label="load-bearing" title="load-bearing" style={{ color: t.accent }}>
                      ★
                    </span>
                  )}
                  <Pill label={a.s} tone={assumptionTone[a.s]} />
                  <IdChip id={a.id} />
                </div>
                {a.b && <p style={dimText()}>{a.b}</p>}
                {a.n && <p style={{ ...dimText(), fontStyle: 'italic' }}>{a.n}</p>}
                {actions(a.id)}
                {verdict(a.id)}
              </Row>
            ))}
          </List>
        </section>
      )}

      {decisions.length > 0 && (
        <section style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <SectionLabel>Decisions</SectionLabel>
          <List>
            {decisions.map((d) => (
              <Row key={d.id}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem', flexWrap: 'wrap' }}>
                  <span style={{ ...prose(), fontWeight: 600 }}>{d.t}</span>
                  <Pill label={d.s} tone={decisionTone[d.s]} />
                  <IdChip id={d.id} />
                  {d.by && (
                    <>
                      <span style={dimText()}>→ replaced by {titleOf(d.by)}</span>
                      <IdChip id={d.by} />
                    </>
                  )}
                  {d.round !== undefined && <span style={caps()}>round {d.round}</span>}
                </div>
                {d.r && <p style={dimText()}>{d.r}</p>}
                {d.x && <p style={dimText()}>{d.x}</p>}
                {actions(d.id)}
                {verdict(d.id)}
              </Row>
            ))}
          </List>
        </section>
      )}

      {open.length > 0 && (
        <section style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <SectionLabel>Open</SectionLabel>
          {Object.entries(groups).map(([key, items]) => (
            <div key={key} style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
              <span style={caps()}>{openGroups[key] ?? key}</span>
              <List>
                {items.map((item) => (
                  <Row key={item.id}>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.45rem', flexWrap: 'wrap' }}>
                      <IdChip id={item.id} />
                      <span style={prose()}>{item.t}</span>
                    </div>
                  </Row>
                ))}
              </List>
            </div>
          ))}
        </section>
      )}
    </Panel>
  );
}
