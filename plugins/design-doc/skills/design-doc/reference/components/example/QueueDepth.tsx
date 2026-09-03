import { useState } from "preact/hooks";

type Ceiling = [string, string, string, string];

type Props = {
  registers: { ceilings?: Ceiling[] };
  id: string;
};

const BAR = {
  flex: "1 1 0",
  border: "1px solid var(--line)",
  borderRadius: "var(--r-node)",
  background: "var(--card)",
  color: "var(--ink2)",
  padding: "10px 12px",
  textAlign: "left" as const,
  fontSize: "13px",
  lineHeight: 1.3
};

export default function QueueDepth({ registers }: Props) {
  const rows = (registers.ceilings || []).slice(0, 4);
  const [pick, setPick] = useState(0);
  const row = rows[pick];
  if (!row) return <p>This doc declares no ceilings.</p>;
  return (
    <div>
      <div style={{ display: "flex", gap: "8px", alignItems: "flex-end" }}>
        {rows.map((r, i) => (
          <button
            key={r[0]}
            type="button"
            aria-pressed={i === pick}
            onClick={() => setPick(i)}
            style={{
              ...BAR,
              height: 46 + (rows.length - i) * 16 + "px",
              borderColor: i === pick ? "var(--accent)" : "var(--line)",
              color: i === pick ? "var(--accent)" : "var(--ink2)"
            }}
          >
            {r[0]}
          </button>
        ))}
      </div>
      <p style={{ margin: "12px 0 0", fontSize: "15px" }}>
        <b>{row[1]}</b> — {row[2]}
      </p>
      <p style={{ margin: "4px 0 0", fontSize: "14px", color: "var(--ink2)" }}>{row[3]}</p>
    </div>
  );
}
