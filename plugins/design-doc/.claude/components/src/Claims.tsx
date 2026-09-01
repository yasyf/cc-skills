import { useCallback } from 'react';
import { usePackState } from './host/present';
import type { PackComponentProps } from './host/present';
import { ActionButton, IdChip, List, NoteField, Panel, Row, caps, dimText, prose } from './chrome';
import { merge } from './maps';

interface Claim {
  id: string;
  label: string;
  because?: string;
  ifFalse?: string;
}

interface Verdict {
  verdict: string;
  correction?: string;
}

const defaultVerdicts = ['holds', 'partly', 'wrong'];

export function Claims({ block, value, submit, disabled }: PackComponentProps) {
  const claims = block.claims as Claim[];
  const labels = (block.verdicts as string[] | undefined) ?? defaultVerdicts;
  const [pending, setPending] = usePackState<Record<string, Verdict>>('pending', {});
  const [corrections, setCorrections] = usePackState<Record<string, string>>('corrections', {});
  const verdicts = merge((value as { verdicts?: Record<string, Verdict> } | null | undefined)?.verdicts, pending);

  const correctionFor = (id: string) => corrections[id] ?? verdicts[id]?.correction ?? '';

  const record = useCallback(
    (id: string, verdict: string, correction: string) => {
      const entry: Verdict = correction.trim() ? { verdict, correction: correction.trim() } : { verdict };
      const next = merge(verdicts, { [id]: entry });
      setPending(next);
      submit({ verdicts: next });
    },
    [submit, setPending, verdicts],
  );

  const pick = useCallback(
    (id: string, verdict: string) => record(id, verdict, verdict === labels[0] ? '' : correctionFor(id)),
    [record, labels, corrections, verdicts],
  );

  const commitCorrection = useCallback(
    (id: string) => {
      const entry = verdicts[id];
      if (entry) record(id, entry.verdict, correctionFor(id));
    },
    [record, verdicts, corrections],
  );

  const answered = claims.filter((c) => verdicts[c.id] !== undefined).length;

  return (
    <Panel title={block.prompt as string} meta={`${answered} of ${claims.length} answered`}>
      <List>
        {claims.map((claim) => {
          const entry = verdicts[claim.id];
          const needsCorrection = entry !== undefined && entry.verdict !== labels[0];
          return (
            <Row key={claim.id} selected={entry !== undefined}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.45rem', flexWrap: 'wrap' }}>
                <IdChip id={claim.id} />
                <span style={{ ...prose(), fontWeight: 600 }}>{claim.label}</span>
              </div>
              {claim.because && <p style={dimText()}>{claim.because}</p>}
              {claim.ifFalse && <p style={{ ...dimText(), fontStyle: 'italic' }}>If this is wrong — {claim.ifFalse}</p>}
              {disabled ? (
                entry && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
                    <span style={caps()}>{entry.verdict}</span>
                    {entry.correction && <p style={dimText()}>{entry.correction}</p>}
                  </div>
                )
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                  <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
                    {labels.map((label) => (
                      <ActionButton
                        key={label}
                        label={label}
                        active={entry?.verdict === label}
                        onClick={() => pick(claim.id, label)}
                      />
                    ))}
                  </div>
                  {needsCorrection && (
                    <NoteField
                      value={correctionFor(claim.id)}
                      placeholder="In your words, what is actually true?"
                      onChange={(next) => setCorrections({ ...corrections, [claim.id]: next })}
                      onCommit={() => commitCorrection(claim.id)}
                    />
                  )}
                </div>
              )}
            </Row>
          );
        })}
      </List>
    </Panel>
  );
}
