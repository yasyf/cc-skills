@AGENTS.md

## Claude-Specific Rules

- **Clarify via `AskUserQuestion`, never inline prose** (§ Ask Before Assuming) — concrete picks, up to 4 questions per call, batched.

## Task Tracking

Non-trivial work flows `pending` → `in_progress` → `completed`: `TaskCreate` before starting, `TaskUpdate` as you go. The task list is the source of truth — complete or explicitly defer every task before stopping.

## Plan Execution & Orchestration

Plans you author must specify, and plans you execute must enforce, that substantive work runs as **dynamic workflows** (`Workflow` tool): the script holds the loop, branching, and intermediate results; your context holds only final answers. This section is standing authorization to invoke `Workflow`. Multi-phase work runs as workflows in sequence (understand → implement → verify); read each result before dispatching the next.

Exceptions: trivial single-file edits, single file reads, and single targeted `semble`/`LSP`/`Grep` lookups stay at the main-agent level; a lone ad-hoc investigation gets one subagent (fallbacks: AGENTS.md `## Parallelize Independent Work`).

**Quality patterns**: pick per task — adversarial verify, judge panel, loop-until-dry, multi-modal sweep. Reviews and audits lean thorough; quick checks lean brief.

**Effort**: every workflow agent, subagent, and team peer runs at the **max model/effort level**. Never downgrade to save tokens — the plan was approved at that level of rigor; executors must match it.

**Phase intermediates may be broken.** In a phased plan, only the final state must be coherent. Shims, dual-mode params, and interphase adapters exist to be deleted next phase — skip them.

**Authoring requirement**: every plan must include the `## Workflow Plan` section described in AGENTS.md `## Writing Plans`. A plan without it is incomplete.

**Reusable orchestrations**: save repeatable runs to `.claude/workflows/`; they become `/` commands.
