# {{PROJECT_NAME}} Hook Style Guide

The concrete style rules for `.claude/hooks/` — this repo's capt-hook hooks. Target Python 3.13+. Hooks are Python regardless of the repo's primary language; the root `STYLEGUIDE.md` governs the rest of the tree.

## Core Principles

1. **Never a wrong fire.** A gate or nudge fires only on the shape it can prove offending; an ambiguous shape stays silent. A hook that misfires trains the agent to ignore it — and every other hook with it.
2. **Narrowest condition that captures the correction.** Match the offending shape, not the neighborhood. One concern per hook file.
3. **Functional over imperative.** Compose, chain, and return. Comprehensions and the walrus (`:=`) over loops and accumulators.
4. **Type everything.** `from __future__ import annotations` in every module. Never widen a typed slot to `Any` to quiet the checker.
5. **Fail fast, fail loud.** No defensive coding, no fallbacks, no guards against impossible states. A conservative fallthrough (`return`/no-fire) is the hook contract, not defensiveness; a failure that needs diagnosing raises.
6. **Match surrounding code.** Follow this guide first, then the file you're in.
7. **Flat over nested.** Early returns; nesting past three levels is a smell.

## Hook Shape

Pick the narrowest primitive: `nudge` for one-shot advice, `gate` for a stop check, `hook(block=True)` for always-on enforcement. The message cites the rule it enforces and says what to do instead — an agent reading it cold should know the next command to run. Message text is load-bearing once anything external matches on it; change it minimally and deliberately.

## Code Organization

Module order runs the docstring, imports, constants, helpers, condition classes, then registrations last. Module-level `UPPER_SNAKE_CASE` constants sit immediately after imports.

No leading underscores on classes, constants, or module-level helpers. Reserve a leading underscore for a private instance attribute.

## Comments & Docstrings

Code documents itself through names and types. The exceptions: TODOs, non-obvious workarounds — and decision rationale. A helper whose branches encode a policy (what fires, what stays silent, why that's safe) carries a docstring stating each lane; that prose is load-bearing. A docstring that restates the signature is deleted on sight.

## Testing

Every registration carries inline `tests={...}`: at least one `Input(...)` firing on the offending shape and one `Allow()` on a benign neighbor. Prove them with `uvx capt-hook test` before the hook goes live. Shapes that classify against the filesystem belong in pytest with a `tmp_path` tree, when the repo carries a pytest setup; everything else stays inline. Strict assertions — a test that can't fail uncovers nothing.
