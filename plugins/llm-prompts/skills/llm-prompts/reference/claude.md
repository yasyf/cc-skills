# Claude (Anthropic)

Guidance for Claude Opus 5 (model id `claude-opus-5`), the current Anthropic frontier Opus. Sourced from Anthropic's Opus 5 prompting guide and migration guide. Claude Opus 5 performs well out of the box on existing Opus 4.8 prompts; the patterns below cover what most often needs tuning.

## Response length and verbosity

Opus 5's default user-facing responses run longer than prior Opus models'. The `effort` parameter controls how much the model *thinks*, not how much it *says*: lowering effort can reduce thinking volume without reliably shortening the visible response. To control response length, prompt for it explicitly:

```text
Keep responses focused, brief, and concise. Keep disclaimers and caveats short, and spend most of the response on the main answer. When asked to explain something, give a high-level summary unless an in-depth explanation is specifically requested.
```

In a long system prompt, pair that with a short reminder near the end (`<tone_preference>Keep outputs reasonably concise.</tone_preference>`). Positive examples of the concision you want still beat instructions about what not to do.

## Written deliverable length

Separate from conversational verbosity, files Opus 5 writes to disk (reports, Markdown docs, summaries) are often longer than on prior models. If your product includes Claude-authored documents, calibrate explicitly:

```text
Match the length of written documents to what the task needs: cover the substance, but do not pad with filler sections, redundant summaries, or boilerplate.
```

## Effort parameter

`effort` trades intelligence against token spend and latency. On Opus 5 the default is `high` on the Claude API and Claude Code. Per Anthropic:

- **`max`** — test it where maximum capability matters more than token spend; "can deliver gains on the most demanding tasks but may show diminishing returns from increased token usage and can be prone to overthinking on simpler ones."
- **`xhigh`** — step up here "for demanding coding and agentic work."
- **`high`** — the default; start here and adjust from your evals.
- **`low` / `medium`** — "produce strong quality at a fraction of the tokens and latency of higher settings." Use them liberally as the primary control for token cost and response time wherever quality holds.

If you carried an effort default over from a prior model, re-run an effort sweep on your own evals rather than trusting the old setting. At `xhigh` or `max`, set a large max output budget (start at 64k tokens) so the model has room to think and act across tool calls and subagents. Note the new coupling: `xhigh` and `max` require thinking enabled (see below).

## Thinking defaults and disabling

Thinking is **on by default**: a request with no `thinking` field runs with adaptive thinking. On Opus 4.8 the same request ran without thinking, so this flips for workloads that never set the field. `max_tokens` remains a hard limit on total output, thinking plus response text; revisit it for workloads that ran thinking-off on 4.8.

You can still pass `thinking: {type: "disabled"}`, but only at effort `high` or below — combining it with `xhigh` or `max` returns a 400, validated on every request. Prefer keeping thinking on and lowering effort instead: for most tasks, thinking enabled at `low` effort beats thinking disabled at similar cost.

With thinking disabled, two artifacts can occasionally appear in visible output: tool calls written as plain text, where the call never runs and the leaked text pollutes agentic history, and internal XML tags such as `<thinking>`. If your system prompt tells the model not to think or reason, remove that rule — it increases tag leakage. For integrations that must keep thinking disabled, one combined instruction mitigates both:

```text
When you use a tool, you may say a brief sentence first. If no tool can express what the user asked for, say so instead of guessing. Do not include internal or system XML tags in your response.
```

Instructions that name thinking tags specifically are less effective than this general form.

## Task scope

Opus 5 can expand the scope of a task — adding steps that weren't requested or applying its own judgment about what the task should be. For narrow tasks, constrain scope explicitly:

```text
Deliver what was asked, at the scope intended. Make routine judgment calls yourself, and check in only when different readings of the request would lead to materially different work. If the request seems mistaken or a better approach exists, say so in a sentence and continue with the task as asked rather than quietly narrowing, widening, or transforming it. Finish the whole task, and stop short of actions that are clearly beyond what was asked.
```

## Over-verification and self-correction

Opus 5 verifies its own work and catches its own mistakes without being told to. Remove carried-over verification scaffolding ("include a final verification step," "use a subagent to verify," "double-check your answer") — these compound with the model's own behavior, causing over-verification that wastes tokens with no quality gain.

The model also narrates corrections to its earlier statements more than prior models. To limit that in user-facing products:

```text
Only correct an earlier statement when the error would change the user's code, conclusions, or decisions. State corrections plainly and briefly, then continue the task. For slips that change nothing for the user, make the fix and move on without noting it.
```

## Progress updates

Opus 5 narrates readily during agentic work — it announces what it's about to do, and per-message output in agentic sessions runs longer than prior models'. Describe the cadence and shape you want rather than relying on defaults. To tune narration down:

```text
Before your first tool call, say in one sentence what you're about to do. While working, give a brief update only when you find something important or change direction. When you finish, lead with the outcome: your first sentence should answer "what happened" or "what did you find," with supporting detail after it for readers who want it.
```

To tune narration up or restyle it, the same lever runs the other way: describe what updates should look like and give positive examples.

## Subagent spawning

Opus 5 delegates to subagents **more readily** than prior models; Opus 4.8 had the opposite lean and spawned fewer. It coordinates agent teams well, with effective writer-verifier patterns and few overwrite collisions, but delegation multiplies cost and time on small tasks. Give explicit guidance on what warrants delegation, or set deterministic spawn caps:

```text
Delegate to a subagent only for large tasks that are genuinely independent and parallelizable, such as a wide multi-file investigation. Do not delegate work you can finish yourself in a handful of tool calls, and do not use subagents to verify or double-check your own work. If one subagent can complete the task, use one rather than several, and keep spawn counts low.
```

## Tool use triggering

When you want more tool use, describe explicitly when and how to use the tool — if the model is not using your web search tool, say clearly why and how it should. Vision work in particular is strongest when the model has tools to iteratively analyze, crop, and verify; tool access is a more cost-effective lever there than thinking alone.

## Default-to-action vs hold

Claude follows instructions precisely and benefits from explicit direction to act. "Can you suggest some changes" often yields suggestions, not edits. To get action, say "Change this function..." or "Make these edits...". To set the default in the system prompt:

```text
<default_to_action>
By default, implement changes rather than only suggesting them. If the user's intent is unclear, infer the most useful likely action and proceed, using tools to discover any missing details instead of guessing. Try to infer the user's intent about whether a tool call (e.g., file edit or read) is intended or not, and act accordingly.
</default_to_action>
```

To make the model hold back until explicitly asked:

```text
<do_not_act_before_instructions>
Do not jump into implementation or change files unless clearly instructed to make changes. When the user's intent is ambiguous, default to providing information, doing research, and providing recommendations rather than taking action. Only proceed with edits, modifications, or implementations when the user explicitly requests them.
</do_not_act_before_instructions>
```

These models are more responsive to the system prompt than earlier ones, so prompts written to reduce undertriggering may now overtrigger. Dial back aggressive language: "Use this tool when..." beats "CRITICAL: You MUST use this tool when...".

## Parallel tool calls

Claude runs independent tool calls in parallel well without prompting. To push the success rate to ~100% or tune aggression:

```text
<use_parallel_tool_calls>
If you intend to call multiple tools and there are no dependencies between the tool calls, make all of the independent tool calls in parallel. Prioritize calling tools simultaneously whenever the actions can be done in parallel rather than sequentially. For example, when reading 3 files, run 3 tool calls in parallel to read all 3 files into context at the same time. Maximize use of parallel tool calls where possible to increase speed and efficiency. However, if some tool calls depend on previous calls to inform dependent values like the parameters, do NOT call these tools in parallel and instead call them sequentially. Never use placeholders or guess missing parameters in tool calls.
</use_parallel_tool_calls>
```

To reduce parallelism: "Execute operations sequentially with brief pauses between each step to ensure stability."

## Investigate before answering (hallucination control)

To keep code answers grounded:

```text
<investigate_before_answering>
Never speculate about code you have not opened. If the user references a specific file, you MUST read the file before answering. Make sure to investigate and read relevant files BEFORE answering questions about the codebase. Never make any claims about code before investigating unless you are certain of the correct answer - give grounded and hallucination-free answers.
</investigate_before_answering>
```

## Code-review harnesses (precision/recall)

Opus 5 reviews code with high precision and recall: it finds real bugs at a high rate per pass, and its additional findings are mostly real issues rather than false positives. Accuracy holds at lower effort settings, which supports a fast pass at review time and a more thorough pass later.

The literal-filter caveat from 4.8 still applies:

> "If your review prompt says 'only report high-severity issues' or 'be conservative,' the model may follow that instruction literally and report less; ask it to report everything and filter in a separate pass instead."

Make the finding step about coverage and push the filter downstream:

```text
Report every issue you find, including ones you are uncertain about or consider low-severity. Do not filter for importance or confidence at this stage - a separate verification step will do that. Your goal here is coverage: it is better to surface a finding that later gets filtered out than to silently drop a real bug. For each finding, include your confidence level and an estimated severity so a downstream filter can rank them.
```

If you do want single-pass self-filtering, set a concrete bar instead of qualitative terms: "report any bugs that could cause incorrect behavior, a test failure, or a misleading result; only omit nits like pure style or naming preferences."

## Frontend default house style

An Opus 4.8-era observation, not yet re-documented for Opus 5 — re-validate before assuming it carries over: 4.8 had a persistent default house style (warm cream/off-white `#F4F1EA` backgrounds, serif display type, italic word-accents, terracotta/amber accents) that suited editorial briefs and clashed with dashboards, dev tools, and enterprise apps. Generic instructions ("don't use cream") shift the model to a different fixed palette rather than producing variety. Two approaches work on either model:

1. **Specify a concrete alternative** — give an explicit palette, type, and layout spec; the model follows it precisely.
2. **Have the model propose options before building** — "propose 4 distinct visual directions tailored to this brief... Ask the user to pick one, then implement only that direction." This breaks the default and replaces the variety you used to get from `temperature`.

A short anti-slop snippet still suffices:

```text
<frontend_aesthetics>
NEVER use generic AI-generated aesthetics like overused font families (Inter, Roboto, Arial, system fonts), cliched color schemes (particularly purple gradients on white or dark backgrounds), predictable layouts and component patterns, and cookie-cutter design that lacks context-specific character. Use unique fonts, cohesive colors and themes, and animations for effects and micro-interactions.
</frontend_aesthetics>
```

## Model self-knowledge

To have Claude identify itself and choose model strings correctly:

```text
The assistant is Claude, created by Anthropic. The current model is Claude Opus 5.
```

```text
When an LLM is needed, please default to Claude Opus 5 unless the user requests otherwise. The exact model string for Claude Opus 5 is claude-opus-5.
```

## Migration: Opus 4.8 to 5

Swap the model id `claude-opus-4-8` to `claude-opus-5` (a fixed, dateless ID like its predecessor; pricing is unchanged at $5/$25 per MTok) and handle two breaking changes:

- **Thinking on by default.** Requests without a `thinking` field now run with adaptive thinking. Revisit `max_tokens` (a hard limit on thinking plus response text), or pass `thinking: {type: "disabled"}` to preserve the old behavior — subject to the next item.
- **Disabling thinking is capped at `high` effort.** `thinking: {type: "disabled"}` with effort `xhigh` or `max` returns a 400, enforced independently on every request. Re-enable thinking or lower the effort.

Recommended follow-ups:

- **Re-run an effort sweep** on your own evals instead of carrying a tuned setting over; `low`/`medium` are strong cost controls, and `max` is worth testing where capability outweighs spend. Raise `max_tokens` to ≥64k at `xhigh`/`max`.
- **Consider `fallbacks: "default"`** (beta header `server-side-fallback-2026-07-01`): Opus 5's cybersecurity safety classifiers can refuse with the cyber category, and default-mode fallback re-runs refused requests on a recommended model (e.g. Opus 4.8) automatically. Keep handling `stop_reason: "refusal"` either way.
- **Cache shorter prompts** — the minimum cacheable prompt drops to 512 tokens (from 1,024 on 4.8), with no code changes required.
- **Change tools mid-conversation** (beta header `mid-conversation-tool-changes-2026-07-01`) — add or remove tools between turns without invalidating prompt-cache hits on earlier turns.
- **Re-tune length prompts and remove verification scaffolding** — see the verbosity, deliverable-length, and over-verification sections above.
- **Priority Tier is not supported on Opus 5** (Opus 4.8 keeps it) — plan committed capacity separately.

### Coming from 4.7 or earlier

Apply the earlier breaking changes first — the 4.8→5 delta alone does not cover them:

- **Sampling parameters rejected.** Setting `temperature`, `top_p`, or `top_k` to any non-default value returns a 400. Omit them; guide behavior through prompting. `temperature = 0` never guaranteed identical outputs anyway.
- **Manual extended thinking gone.** `thinking: {type: "enabled", budget_tokens: N}` returns a 400. Control depth with `effort`.
- **New tokenizer** (introduced with 4.7) can use roughly 1x to 1.35x as many tokens for the same text. Re-budget `max_tokens` and re-test any client-side token estimates.
- **Prefill removed** (carried from 4.6): prefilling the last assistant message returns a 400. Use structured outputs, system-prompt instructions, or `output_config.format` instead.
- 4.8 additions carry forward: mid-conversation `role: "system"` messages, 1M-token default context window with no long-context premium, and documented refusal `stop_details`.
