# OpenAI (GPT-5.x)

The latest frontier family is GPT-5.x, with GPT-5.5 the newest model. Treat the newest model as a **new family to tune for, not a drop-in replacement** for an older GPT-5 model. Start fresh and re-tune reasoning effort, verbosity, and prompt stacks instead of carrying old settings forward.

## Reasoning effort

`reasoning_effort` controls how much the model reasons before answering. Levels: `none`, `low`, `medium`, `high`, `xhigh`. On GPT-5.5 the default is `medium`.

| Level | Use for |
|-------|---------|
| `none`/`low` | Execution-heavy workloads — start here. |
| `medium` | Balanced default on GPT-5.5; the starting point for research-heavy work. |
| `high` | Reserve for tasks that truly require stronger reasoning. |
| `xhigh` | Avoid as a default unless your evals show a clear benefit. |

**Higher effort is not automatically better.** With conflicting instructions, weak stopping criteria, or open-ended tool access, higher effort produces overthinking, unnecessary searching, and output-quality regressions. Fix the prompt before raising effort.

**Effort is a last-mile knob.** Before raising it, add `<completeness_contract>`, `<verification_loop>`, and `<tool_persistence_rules>` to the prompt. Those structural fixes resolve most "the model stopped early / missed items" failures that people otherwise reach for higher effort to solve. Default to `none` or `low` for execution tasks; reserve `medium`/`high` for research-heavy work.

## Verbosity

`text.verbosity` controls answer length independently of reasoning depth. Set it to `low` for concise responses. **Starting at `low` is often a better default** than letting verbosity ride high; escalate only when a task needs more detail. Keep reasoning effort and verbosity separate: a model can reason at high effort and still answer briefly.

## Outcome-first prompt structure

Structure agent instructions around the outcome, not a procedure. Suggested sections:

```
Role           — who the model is acting as
Goal           — the outcome to achieve
Success criteria — how to know the goal is met
Constraints    — hard boundaries and non-negotiables
Output         — exact format of the deliverable
Stop rules     — when to stop and return
```

Keep each section concise. Add detail only where it changes behavior. Stop rules matter most for agentic loops: without them, the model keeps calling tools.

## Put when-to-call logic in tool descriptions

The tool description is a durable contract; the developer prompt is not. Put most tool-specific guidance in the **tool description itself**: what the tool does, when to use it, required inputs, side effects, retry safety, and common error modes. A model with a vague tool description and a long prompt full of usage rules calls tools worse than one with a self-describing tool and a short prompt.

## Tool preambles for UX

Preambles improve chat UX by showing status before the final response. The model states what it is about to check or do, calls the tool, then continues from that same assistant state once results arrive. Use them for multi-step agentic flows where the user is watching; they improve perceived responsiveness without changing the underlying work.

## Give the model validation tools

Let the model self-check by giving it tools that validate its own output: a linter, a schema validator, a test runner, a search to confirm a fact. Pair this with `<verification_loop>` so the model runs the check before finalizing. Self-validation against a real tool beats asking the model to "double-check" in prose.

## Prompt caching: stable prefix

To maximize cache hits, keep stable content at the **beginning** of the request and per-request, user-specific context near the **end**. System instructions, tool definitions, and few-shot examples go up top so the prefix stays identical across calls; the per-request query goes last. Track `usage.prompt_tokens_details.cached_tokens` to confirm cache hits on repeated traffic.

This ordering also matches the long-context rule (reference material up top, query at the bottom), so the two strategies reinforce each other.

## Phase parameter (multi-step Responses flows)

In multi-step Responses API flows, preserve the `phase` field on assistant output items when managing state manually. Use `phase: "commentary"` for intermediate user-visible updates (preambles, working notes) and `phase: "final_answer"` for the completed answer, so preambles are not mistaken for the final output. Keep `phase` on assistant items only; do not add it to user messages. Preserving it matters most when combining reasoning effort, preambles, or repeated tool calls; dropping it degrades long-running task quality.

## Image detail

The image `detail` level trades fidelity against token cost:

| Value | Use for |
|-------|---------|
| `high` | Standard high-fidelity reading of dense text or fine visual detail. |
| `original` | Large, dense, or spatially sensitive images — computer use, OCR, click accuracy. |
| `low` | When speed and cost matter more than exact detail. |

Specify the level explicitly rather than leaving it unset; when unset, GPT-5.5 preserves more detail (uses `original`).

## Reusable XML contract blocks

Paste these GPT-5.x prompt-guidance blocks into agent instructions as needed. They encode the behaviors above as explicit contracts. Add `<completeness_contract>` + `<verification_loop>` + `<tool_persistence_rules>` **before** raising reasoning effort.

```xml
<output_contract>
- Return exactly the sections requested, in the requested order.
- If the prompt defines a preamble, analysis block, or working section, do not treat it as extra output.
- Apply length limits only to the section they are intended for.
- If a format is required (JSON, Markdown, SQL, XML), output only that format.
</output_contract>
```

```xml
<verbosity_controls>
- Prefer concise, information-dense writing.
- Avoid repeating the user's request.
- Keep progress updates brief.
- Do not shorten the answer so aggressively that required evidence, reasoning, or completion checks are omitted.
</verbosity_controls>
```

```xml
<instruction_priority>
- User instructions override default style, tone, formatting, and initiative preferences.
- Safety, honesty, privacy, and permission constraints do not yield.
- If a newer user instruction conflicts with an earlier one, follow the newer instruction.
- Preserve earlier instructions that do not conflict.
</instruction_priority>
```

```xml
<tool_persistence_rules>
- Use tools whenever they materially improve correctness, completeness, or grounding.
- Do not stop early when another tool call is likely to materially improve correctness or completeness.
- Keep calling tools until:
  (1) the task is complete, and
  (2) verification passes (see <verification_loop>).
- If a tool returns empty or partial results, retry with a different strategy.
</tool_persistence_rules>
```

```xml
<dependency_checks>
- Before taking an action, check whether prerequisite discovery, lookup, or memory retrieval steps are required.
- Do not skip prerequisite steps just because the intended final action seems obvious.
- If the task depends on the output of a prior step, resolve that dependency first.
</dependency_checks>
```

```xml
<parallel_tool_calling>
- When multiple retrieval or lookup steps are independent, prefer parallel tool calls to reduce wall-clock time.
- Do not parallelize steps that have prerequisite dependencies or where one result determines the next action.
- After parallel retrieval, pause to synthesize the results before making more calls.
- Prefer selective parallelism: parallelize independent evidence gathering, not speculative or redundant tool use.
</parallel_tool_calling>
```

```xml
<completeness_contract>
- Treat the task as incomplete until all requested items are covered or explicitly marked [blocked].
- Keep an internal checklist of required deliverables.
- For lists, batches, or paginated results:
  - determine expected scope when possible,
  - track processed items or pages,
  - confirm coverage before finalizing.
- If any item is blocked by missing data, mark it [blocked] and state exactly what is missing.
</completeness_contract>
```

```xml
<empty_result_recovery>
If a lookup returns empty, partial, or suspiciously narrow results:
- do not immediately conclude that no results exist,
- try at least one or two fallback strategies,
  such as:
  - alternate query wording,
  - broader filters,
  - a prerequisite lookup,
  - or an alternate source or tool,
- Only then report that no results were found, along with what you tried.
</empty_result_recovery>
```

```xml
<verification_loop>
Before finalizing:
- Check correctness: does the output satisfy every requirement?
- Check grounding: are factual claims backed by the provided context or tool outputs?
- Check formatting: does the output match the requested schema or style?
- Check safety and irreversibility: if the next step has external side effects, ask permission first.
</verification_loop>
```

```xml
<missing_context_gating>
- If required context is missing, do NOT guess.
- Prefer the appropriate lookup tool when the missing context is retrievable; ask a minimal clarifying question only when it is not.
- If you must proceed, label assumptions explicitly and choose a reversible action.
</missing_context_gating>
```
