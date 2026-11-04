---
name: plans
description: The shape of a plan written in plan mode, and the steps for answering a code review that re-enters plan mode. Use before writing or revising any plan, before ExitPlanMode, when the user reviews code (inline diff comments, a numbered list, review-shaped feedback) and re-enters plan mode, and when the user answers a plan with questions instead of approval.
---

# Plans

## Writing a plan

A plan has five sections.

- **Context**: the problem, what prompted it, and the intended outcome, as a
  tight problem statement. Diagnosis beyond what the fixes need goes to
  cc-notes.
- **Approach**: the recommended approach only, as ordered steps naming the
  critical files and the existing utilities to reuse. Every issue the plan
  raises gets a fix step grounded in code actually read, with named files and
  symbols.
- **Potential Pitfalls**: the sharp edges specific to this work, one bullet
  each.
- **Workflow Plan**: required. One line on what the main agent alone does, then
  a `Phase | Shape | Agents | Blocks on | Verification` table over every
  fan-out. Shape is `pipeline`, `parallel` or `loop`; Agents is model and
  effort per the Model Routing table; Blocks on is the upstream phase or `—`;
  Verification is the one gating check. Each row names the worker's
  deliverable and completion condition. Speculative lanes get their own rows
  marked `(speculative)`, worktree-isolated, with the landing verdict in the
  Verification cell. When nothing fans out, one line saying so replaces the
  table.
- **Verification**: exact commands, tests and observable behavior.

The deliverable is the fix, not the finding: a plan that diagnoses without
code-grounded fix steps is not a plan. When a fix step cannot be written because
the code has not been read, do that research in plan mode before `ExitPlanMode`:
delegate it, never defer it or park it as a workflow phase or follow-up. Scope
added mid-planning gets the same treatment before the next attempt; "add a
flag" with no code anchor is unplanned scope. Phase intermediates may be
broken; only the final state must cohere, so skip shims, dual-mode params and
interphase adapters.

## A code review re-enters plan mode

When the user reviews your code and re-enters plan mode:

0. Delegate context-gathering to one `Explore` subagent carrying every cite. It
   greps ±5 lines per cite, reads a whole file only when that is not enough,
   and finds sibling call sites with the same issue. Don't bulk-read cited
   files in the main context.
1. Draft a new plan, not a code change.
2. Inline every comment verbatim with an anchor (`#N`, file:line, or a quote);
   never paraphrase.
3. Cluster into themes when there are more than about five, and extrapolate
   each rule to call sites with the same problem.
4. End with a mapping table `# | file:line | verbatim | cluster`. No comment is
   silently dropped, and every comment or cluster maps to a code-grounded fix
   step in the Approach.
5. Don't implement before approval; an ambiguous comment goes to
   `AskUserQuestion`.

## Questions instead of approval

When the user answers a plan with questions, answer them conversationally, offer
picks through `AskUserQuestion`, and edit the plan only after the user chooses.
