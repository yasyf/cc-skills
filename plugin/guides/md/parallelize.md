## Parallelize Independent Work

Batch and fan out cheap independent reads, greps, outlines, and network calls, plus source edits in separate worktrees or lanes; a single message carrying several independent tool calls is the first choice. Local builds, test suites, repo-wide scans or indexing, benchmarks, and similar compiler- or IO-heavy jobs share a host budget: one such job at a time across a root coordinator and every descendant lane, including workers and nested coordinators, with the root coordinator recording who holds the slot, and at most two compiler or test workers on the host at once. Right before starting one, check load average against core count, idle CPU, and free memory; when the host is saturated, defer every optional job and let CI grade the work; a necessary local reproduction waits for real headroom. CI grades tests; run a suite locally only to reproduce a red CI run or for a check no pipeline grades. Use no timed retry loops around git or stack writes and no separate fetch, rebase, or restack before a ship or submit that already does it; read a refusal and retry only when its cause changes. When budget is unavailable, queue the resource-intensive lane and continue cheap work; waiting on host budget is correct scheduling. Never stop, restart, suspend, signal, or reprioritize existing sessions, their processes, or anyone else's jobs to free capacity; use lower priority (`nice`, background QoS) only for disposable local jobs you create yourself in this task. Pick the surface by scale:

- **Batch tool calls in one message** — the cheapest parallelism and the most missed. Independent reads, greps, globs, and read-only Bash go in a *single* message, never one per turn.
- **Parallel subagent calls in one message** — ad-hoc independent investigations: "explore X while I check Y", multi-file reviews, independent edits. One message, N `Agent` tool uses, results gathered in parallel.
- **Dynamic workflow** — default for substantive multi-step work, with or without ultracode: this guide is standing opt-in to the `Workflow` tool, and ultracode only raises the scale ceiling. The script holds the loop, branching, and intermediate results. Cleanups, sweeps, and refactors never run inline in the main agent — each one fans out as a dynamic workflow. See CLAUDE.md `## Plan Execution & Orchestration`. Workflows are also the cheapest surface, and stage granularity is why: a `pipeline()` stage returns a *value* and the next stage is a fresh agent whose context is only that value, so the condense-and-respawn discipline below is enforced by the script instead of left to a delegate's judgment. `schema` makes the handoff structured by construction. A workflow whose stages each run hundreds of turns forfeits all of this — split the stage, don't grow it.
- **Named team** — long-running peers needing agent-to-agent handoffs mid-run, via `TeamCreate`. Sized for a handful of peers; a teammate's own subagents are foreground-only, so an N-unit sweep inside a team delegates to a workflow instead of nesting `Agent` calls.

Single-step exception: one task, no parallel sibling, no follow-on → one subagent call is fine.

**Bound a delegate's life, not just its scope.** A delegate's cost grows with the *square* of its turn count: its context grows by roughly a thousand tokens per tool call and every call re-reads the whole thing, so a turn at 200 costs several times the same turn at 20. Past ~50 tool calls a delegate spends more re-reading its own history than doing the work.

At ~50 tool calls, a delegate stops and hands off rather than pressing on: it returns findings, open questions, and the next concrete step — never its transcript, never a narrated log of what it tried. The caller respawns a fresh delegate with that summary as its entire context. Splitting one 200-turn agent into four 50-turn ones costs roughly half as much and loses nothing a real handoff carries; the fresh prefix each respawn pays for is noise against the saving. This is a budget, not a deadline — a delegate that finishes in ten turns finishes, and one mid-edit at turn 50 completes the edit first, because an interrupted mutation costs more to reconstruct than the turns it saves. A delegate that keeps going past the budget records why in its handoff.

## Worker model defaults

Roots run Opus 5.5. Fable is for exceptional cases only: the most sensitive
local implementation, using the Mac's existing interactive authentication. Ordinary
Claude workers and subdesks use Opus or Sonnet per the routing table; Opus is the
default for Claude implementation workers. Auth, migrations, concurrency, or error-prone code
alone does not qualify for Fable. Preserve explicitly requested models and
effort within these roles. Never use Fable as a general fallback or set
`fallbackModel`.

Set the model explicitly on worker definitions or dispatches so ordinary
workers never inherit Fable.
