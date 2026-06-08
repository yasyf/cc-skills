---
name: llm-prompts
description: Research-backed, provider-agnostic guidance for writing LLM prompts and agent instructions — positive framing, XML structure, contrastive examples, outcome-first agentic contracts, and provider tuning knobs. Use when writing or revising a system prompt, agent or tool instructions, a tool description, or few-shot examples; or when the user asks to improve a prompt.
---

# Writing LLM Prompts

Prompt engineering is the wording of a single instruction. Context engineering is the architecture around it — tools, memory, sub-agents, retrieval, how context is layered and ordered. For production agents the architecture matters more than any one phrasing. The principles below are provider-agnostic; per-model knobs live in `reference/`.

## Core principles

Seven research-backed rules govern every prompt. Each traces to a finding in `reference/research.md`.

### 1. Positive framing over negation

Tell the model what TO do, not what NOT to do. Models handle negation poorly at the token level, and instruction-following degrades with negative framing. Replace a `<forbidden_patterns>` block with `<preferred_patterns>`. When a negative constraint is unavoidable (safety, data loss), pair it with the positive alternative and the reason.

```text
# Avoid
Do NOT bundle multiple facts. NEVER include unsupported claims.

# Prefer
Each claim is atomic — one verifiable fact, grounded in the source.
This lets downstream systems validate each claim independently.
```

### 2. Contrastive examples over separated good/bad

Pair correct and incorrect side by side in labeled `<example>` tags, each with a one-line reason. Contrastive pairs beat good-only or bad-only sets. A standalone `<bad_examples>` block with no adjacent correct counterpart is counterproductive.

```text
<examples>
<example label="incomplete">
"Revenue increased by 50%"
Missing: whose revenue, which timeframe — not verifiable alone.
</example>
<example label="self-contained">
"Acme's Q3 2025 revenue increased by 47% year over year"
Entity, metric, timeframe, baseline all present.
</example>
</examples>
```

### 3. Clear language over emphasis markers

Replace `CRITICAL:`, `IMPORTANT:`, `NEVER:` with a plain statement that explains *why* the constraint exists. Emphasis markers overtrigger modern models and waste reasoning tokens. Reserve ALL CAPS for genuine safety boundaries (deletion, payment, irreversible side effects).

```text
# Avoid
IMPORTANT: Each question must ask for ONE value. NEVER ask for comparisons.

# Prefer
Each question asks for one value that can be looked up directly. A question
comparing two values is analytical, not a lookup — it needs interpretation.
```

### 4. Match complexity to model capability

Strong models do worse with over-constrained prompts; instruction-following degrades as the rule list grows. Write the shortest prompt that produces the correct output. Add a constraint only after you observe the specific failure it prevents — never speculatively.

### 5. Reasoning first, structure second

Keep the reasoning step in free text. A JSON or constrained schema on the reasoning step drops accuracy sharply by forcing answer-before-reasoning ordering. Let the model reason freely, then capture structure in a separate step: a structured-output call, a follow-up request, or a dedicated `format` field that runs after the reasoning.

### 6. Success criteria over procedural checklists

State what a correct output looks like and let the model self-verify. Append "Before finishing, verify your answer against these criteria" rather than prescribing PASS/FAIL gates. Reasoning models verify natively; step-by-step gates over-constrain the reasoning and can lower quality.

```text
# Avoid: procedural gate
Step 1: identify the entity — if generic, SKIP.
Step 2: check if permanent — if study-specific, SKIP.

# Prefer: declarative criteria
<success_criteria>
A claim is extractable when it names a specific entity, describes a permanent
property (not study-specific), and would be independently citable.
</success_criteria>
```

### 7. Documents at top, query at bottom

For long-context prompts, put reference material first and the instruction or query last. All major providers confirm this ordering; one reports up to 30% quality improvement from it.

## What changed for the latest models

### Outcome-first prompting

State the destination, not the route. Define the expected outcome, success criteria, allowed side effects, evidence rules, and output shape; let the model pick the path. Reserve step-by-step procedures for cases where the path itself matters (a fixed audit order, a required tool sequence).

```text
Resolve the request end to end.
Success: the decision follows from the provided data; any allowed action is
completed before responding; the reply includes completed_actions and blockers.
Allowed side effects: read-only lookups, and the single action named above.
```

### Effort and verbosity are tuning knobs, not the main lever

Reasoning effort, verbosity, and thinking depth tune the same prompt — they are not the primary quality lever. When output is incomplete or under-verified, add *structure* first: a completeness contract, a verification loop, tool-persistence rules. Raise effort only after the prompt itself is sound, and re-baseline cost and latency at the new level.

### Literal instruction-following and explicit scope

Modern models follow instructions literally and will not silently generalize one item's rule to all items. State scope explicitly: "apply this to every section, not just the first." On a mid-conversation change, say which earlier rules still hold:

```text
<task_update>
For the next response only: [instruction].
All earlier instructions still apply unless they conflict with this update.
</task_update>
```

## XML tag structure

XML tags constrain structure without constraining reasoning, and outperform JSON/YAML for organizing a prompt. Use descriptive, self-documenting tag names.

Top-level structural tags:

```text
<instruction> the task </instruction>
<context> reference material, placed before the instruction for long inputs </context>
<output_format> exact shape of the expected output </output_format>
<examples> contrastive pairs </examples>
```

Content-specific tags carry meaning in the name: `<success_criteria>`, `<filtering_criteria>` (include/exclude with reasons), `<action_rules>` (when to append/merge/add/drop), `<preferred_patterns>`, `<key_constraints>` (constraints with their reasons).

The example pattern is always contrastive — correct and incorrect adjacent, each with a reason:

```text
<examples>
<example label="non-atomic">
"Company laid off 30% of staff and the CEO says all is well"
Two independent facts bundled — neither is separately verifiable.
</example>
<example label="atomic">
"Company laid off 30% of its staff in 2025"
One entity, one action, one timeframe.
</example>
</examples>
```

Self-check tags state the test declaratively, not as a procedure:

```text
<merge_test>
Two claims describe the same fact when they would produce the same headline.
Same headline, different wording → merge. Two headlines → keep separate.
</merge_test>
```

## Agentic and tool-use patterns

For agents, encode behavior as reusable XML contract blocks rather than prose. Each is one focused block; full text is in `reference/openai.md`.

- `<tool_persistence_rules>` — keep calling tools until the task is complete and verification passes; retry on empty/partial results.
- `<completeness_contract>` — track an internal checklist; treat the task as incomplete until every item is covered or marked `[blocked]`.
- `<verification_loop>` — before finalizing, check correctness, grounding, formatting, and irreversible side effects.
- `<empty_result_recovery>` — on empty or suspiciously narrow results, try one or two fallback strategies before concluding nothing exists.
- `<dependency_checks>` — resolve prerequisite lookups before acting; don't skip them because the final action seems obvious.
- `<parallel_tool_calling>` — parallelize independent lookups; run dependent steps sequentially.

Claude's latest models default to action and parallelize well: they make independent tool calls simultaneously, and respond to "investigate before answering" by reading referenced files before claiming anything about code. They favor reasoning over tool calls by default, so raising effort is the lever that increases tool usage. The full Claude blocks are in `reference/claude.md`.

## Provider knobs at a glance

| Provider | Reasoning / effort (default) | Verbosity | Thinking default |
|---|---|---|---|
| Anthropic Opus 4.8 | `effort`: `low`–`max`, default `high`; set `xhigh` for coding/agentic | calibrated to task complexity; prompt to constrain | adaptive thinking **off** unless `thinking: {type: "adaptive"}` |
| OpenAI GPT-5.5 | `reasoning_effort`: `none`–`xhigh`, default `medium` | `verbosity`, default `medium`; `low` is often a good start | internal reasoning; no explicit CoT prompt |
| Google Gemini 3 | `thinkingLevel`: `minimal`–`high`; Pro can't fully disable thinking | — | thinking on; keep `temperature` at `1.0` |

Defer per-model detail to `reference/`.

## Common pitfalls

1. **Emphasis markers instead of reasoning.** `CRITICAL:`/`IMPORTANT:`/`NEVER` overtrigger modern models. Explain why the constraint exists.
2. **`<forbidden_patterns>` instead of `<preferred_patterns>`.** State what to do; pair any necessary negation with the positive alternative.
3. **Separated bad/good examples.** Always contrastive pairs, correct and incorrect adjacent, each with a reason.
4. **Aggressive tone.** Authoritative and direct, not demanding. "Each claim should be atomic because…" beats "NEVER bundle facts."
5. **Missing context.** Include the essential context; don't assume the model knows the broader task.
6. **Programmatic guards before prompt fixes.** When a model ignores a constraint, read its reasoning to learn why, then fix the prompt. Code-level guards are safety nets, not the primary fix.
7. **Over-constraining strong models.** Shorter, clearer prompts win. Add a constraint only after you observe its failure mode.
8. **JSON-mode for the reasoning step.** Reason in free text first; extract structure in a separate step.
9. **Procedural PASS/FAIL checklists.** State success criteria declaratively; reasoning models verify natively.
10. **"Let's think step by step" on reasoning models.** Models with built-in reasoning do this internally; explicit CoT is counterproductive.
11. **Raising effort to paper over a weak prompt.** Add completeness/verification/tool-persistence structure first; treat effort as the last-mile knob.

## Reference

- `reference/claude.md` — Anthropic Opus 4.x: effort levels, adaptive thinking, parallel-tool and investigate-before-answering blocks, prefill removal.
- `reference/openai.md` — OpenAI GPT-5.x: reasoning effort, verbosity, and the full agentic XML contract blocks.
- `reference/gemini.md` — Google Gemini 3: thinkingLevel, temperature, few-shot-over-instructions, thought signatures.
- `reference/research.md` — the findings table behind the core principles, with sources.
