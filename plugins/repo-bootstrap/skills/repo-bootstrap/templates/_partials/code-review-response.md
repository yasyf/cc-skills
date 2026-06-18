## Code Review Response (Plan Re-Entry)

When the user reviews code you wrote and re-enters plan mode — whether by leaving inline diff comments, pasting a numbered list of issues, or otherwise sending review-shaped feedback after a recent edit cycle — you MUST:

0. **Delegate context-gathering to a subagent.** Spawn one `Explore` subagent with every cite (file:line + the user's verbatim comment text). Instruct it to, per cite, `Grep` the file with ~5 lines of context either side of the cited line (`-B 5 -A 5`), and only escalate to a full `Read` when the ±5-line window is insufficient (e.g. the comment refers to a function defined further up). Have it also surface sibling call sites with the same issue (Grep across the module). Use the subagent's digest as your source of truth when drafting the plan. Do NOT bulk-`Read` the cited files yourself in the main turn — it bloats the main context window before you've even started writing the plan.
1. **Draft a new plan**, not a code change. Plan-mode re-entry is the user asking "let's align on what you'll do next," not "go fix it."
2. **Inline every comment verbatim** in the plan. Each comment gets a short anchor (`#N`, the file:line if provided, or a quoted excerpt) plus the user's exact wording in a blockquote or `*"…"*` italics. Do not paraphrase. The user must be able to scan the plan and see every comment they wrote reproduced exactly.
3. **Cluster when many.** If there are more than ~5 comments, group them into themes (e.g. "T1 — Guards against impossible states") and list every verbatim trigger per theme. Address every cited line *and* extrapolate the rule to other call sites that have the same problem.
4. **Map every comment.** Maintain a "verbatim feedback table" near the end of the plan with one row per comment: `# | file:line | verbatim | cluster`. No comment may be silently dropped.
5. **Do NOT start implementing** before the plan is approved via `ExitPlanMode`. Delegating reads via #0 is fine; editing source is not.

The canonical shape is the `Overarching themes` table + per-cluster `**#N (verbatim):** *"…"*` anchors + final mapping table. When a comment is ambiguous, ask via `AskUserQuestion` rather than guessing.

### Plan follow-up questions

After you write a plan, the user may respond with questions ("why this approach?", "what about X?", "did you consider Y?") rather than approval. In that case you MUST NOT edit the plan to bake in answers. Instead:

1. **Answer the question conversationally** in your text response — explain the reasoning, the tradeoffs, and what you'd recommend.
2. **Propose options via `AskUserQuestion`** — one question per ambiguity, each with 2–4 concrete options the user can pick from. Batch related questions into one `AskUserQuestion` call.
3. **Wait for the user's choice** before editing the plan. The plan edit then reflects the user's pick, not your assumption.

Editing the plan first robs the user of the choice and forces them to diff the plan to find what you decided. Surface the decision point first.
