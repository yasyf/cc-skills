import { useCallback } from 'react';
import { toast, tokens, usePackState } from './host/present';
import type { PackComponentProps } from './host/present';
import { ActionButton, List, NoteField, Panel, Pill, Row, TextField, caps, dimText, prose } from './chrome';

interface Option {
  id: string;
  label: string;
  consequence: string;
  recommended?: boolean;
  pros?: string[];
  cons?: string[];
}

interface Escape {
  label?: string;
  placeholder?: string;
}

interface Answer {
  choice?: string;
  defer?: { title: string; why?: string };
}

function Bullets({ heading, items }: { heading: string; items: string[] }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
      <span style={caps()}>{heading}</span>
      <ul style={{ margin: 0, paddingLeft: '1.1rem' }}>
        {items.map((item) => (
          <li key={item} style={dimText()}>
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function Fork({ block, value, submit, disabled }: PackComponentProps) {
  const t = tokens();
  const options = block.options as Option[];
  const escape = (block.escape as Escape | undefined) ?? {};
  const round = block.round as number | undefined;
  const decides = block.decides as string | undefined;
  const decidesTitle = block.decidesTitle as string | undefined;
  const answer = (value as Answer | null | undefined) ?? {};

  const [detailFor, setDetailFor] = usePackState<string>('detailFor', '');
  const [deferTitle, setDeferTitle] = usePackState<string>('deferTitle', answer.defer?.title ?? '');
  const [deferWhy, setDeferWhy] = usePackState<string>('deferWhy', answer.defer?.why ?? '');
  const [deferring, setDeferring] = usePackState<boolean>('deferring', false);

  const choose = useCallback((id: string) => submit({ choice: id }), [submit]);

  const defer = useCallback(() => {
    const title = deferTitle.trim();
    if (!title) {
      toast({ kind: 'error', text: 'Name the open question before deferring it' });
      return;
    }
    const why = deferWhy.trim();
    submit({ defer: why ? { title, why } : { title } });
    toast({ kind: 'info', text: 'Added to the open list' });
  }, [submit, deferTitle, deferWhy]);

  const settles = decidesTitle ? `settles "${decidesTitle}"` : decides ? `decides ${decides}` : null;
  const meta = [round !== undefined ? `round ${round}` : null, settles].filter(Boolean).join(' · ');

  return (
    <Panel title={block.question as string} meta={meta || undefined}>
      <List>
        {options.map((option) => {
          const selected = answer.choice === option.id;
          const showing = detailFor === option.id;
          const details = (option.pros?.length ?? 0) + (option.cons?.length ?? 0) > 0;
          return (
            <Row key={option.id} selected={selected}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem', flexWrap: 'wrap' }}>
                <span style={{ ...prose(), fontWeight: 600 }}>{option.label}</span>
                {option.recommended && <Pill label="recommended" tone="accent" />}
              </div>
              <p style={dimText()}>{option.consequence}</p>
              {showing && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                  {option.pros && option.pros.length > 0 && <Bullets heading="For" items={option.pros} />}
                  {option.cons && option.cons.length > 0 && <Bullets heading="Against" items={option.cons} />}
                </div>
              )}
              {disabled && selected && <span style={caps()}>chosen</span>}
              <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
                {!disabled && (
                  <ActionButton
                    label={selected ? 'Chosen' : 'Choose'}
                    active={selected}
                    onClick={() => choose(option.id)}
                  />
                )}
                {details && (
                  <ActionButton
                    label={showing ? 'Hide detail' : 'Detail'}
                    onClick={() => setDetailFor(showing ? '' : option.id)}
                  />
                )}
              </div>
            </Row>
          );
        })}
      </List>

      {answer.defer ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
          <span style={caps()}>deferred to the open list</span>
          <span style={{ ...prose(), fontWeight: 600 }}>{answer.defer.title}</span>
          {answer.defer.why && <p style={dimText()}>{answer.defer.why}</p>}
        </div>
      ) : (
        !disabled &&
        (deferring ? (
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: '0.45rem',
              padding: '0.6rem 0.7rem',
              border: `1px dashed ${t.border}`,
              borderRadius: t.radiusMd,
            }}
          >
            <span style={caps()}>{escape.label ?? 'Add to open list'}</span>
            <TextField
              value={deferTitle}
              placeholder="Name the open question"
              onChange={setDeferTitle}
            />
            <NoteField
              value={deferWhy}
              placeholder={escape.placeholder ?? 'What has to be true before this can be decided?'}
              onChange={setDeferWhy}
            />
            <div style={{ display: 'flex', gap: '0.4rem' }}>
              <ActionButton label="Add" primary onClick={defer} />
              <ActionButton label="Cancel" onClick={() => setDeferring(false)} />
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex' }}>
            <ActionButton label={escape.label ?? 'Add to open list'} onClick={() => setDeferring(true)} />
          </div>
        ))
      )}
    </Panel>
  );
}
