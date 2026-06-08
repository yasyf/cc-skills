# Claude (Anthropic)

Guidance for Claude Opus 4.8 (model id `claude-opus-4-8`), the current Anthropic frontier model. Sourced from Anthropic's prompting best practices and migration guide. Claude Opus 4.8 performs well on existing Opus 4.7 prompts out of the box; the patterns below cover what most often needs tuning.

## Response length and verbosity

Opus 4.8 calibrates response length to how complex it judges the task to be, instead of defaulting to a fixed verbosity. Simple lookups get shorter answers; open-ended analysis gets much longer ones. If your product depends on a fixed style or length, tune the prompt. To reduce verbosity:

```text
Provide concise, focused responses. Skip non-essential context, and keep examples minimal.
```

> "Positive examples showing how Claude can communicate with the appropriate level of concision tend to be more effective than negative examples or instructions that tell the model what not to do."

## Effort parameter

`effort` trades intelligence against token spend and latency. On Opus 4.8 the default is `high` across all surfaces. Levels, per Anthropic:

- **`max`** — "can deliver performance gains in some use cases, but may show diminishing returns from increased token usage. This setting can also sometimes be prone to overthinking." Test it for intelligence-demanding tasks.
- **`xhigh`** — "the best setting for most coding and agentic use cases."
- **`high`** — balances tokens and intelligence; "use a minimum of `high` effort" for most intelligence-sensitive work. Default on 4.8.
- **`medium`** — cost-sensitive work that can trade off some intelligence.
- **`low`** — "Reserve for short, scoped tasks and latency-sensitive workloads that are not intelligence-sensitive."

Opus 4.8 respects effort strictly, especially at the low end: at `low` and `medium` it scopes work to what was asked. That is good for latency and cost, but moderately complex tasks at `low` risk under-thinking.

> "If you observe shallow reasoning on complex problems, raise effort to `high` or `xhigh` rather than prompting around it."

Effort matters more for this model than for any prior Opus, so experiment with it when you upgrade. At `max` or `xhigh`, set a large max output budget (start at 64k tokens) so the model has room to think and act across tool calls and subagents.

## Adaptive thinking

Thinking is off unless you set `thinking: {type: "adaptive"}`. With adaptive thinking on, Claude decides when and how much to think, calibrated by `effort` and query complexity. Higher effort and harder queries elicit more thinking; easy queries get a direct response.

The triggering behavior is steerable. Large or complex system prompts can make the model think more often than you want. To steer it back:

```text
Thinking adds latency and should only be used when it will meaningfully improve answer quality — typically for problems that require multi-step reasoning. When in doubt, respond directly.
```

If you run hard workloads at `medium` and see under-thinking, raise effort first; prompt for more thinking only if you need finer control. Prefer general thinking guidance ("think thoroughly") over hand-written step-by-step plans — Claude's reasoning frequently exceeds what a human would prescribe. You can put `<thinking>` tags inside few-shot examples to show the reasoning pattern, and append "Before you finish, verify your answer against [criteria]" to catch errors.

## Tool use triggering

Opus 4.8 favors reasoning over tool calls, which produces better results in most cases. Tool usage rises with effort:

> "`high` or `xhigh` effort settings show substantially more tool usage in agentic search and coding."

When you want more tool use, raise effort, and describe explicitly when and how to use the tool. If the model is not using your web search tool, say clearly why and how it should.

## Literal instruction following

> "Claude Opus 4.8 interprets prompts literally and explicitly, particularly at lower effort levels. It does not silently generalize an instruction from one item to another, and it does not infer requests you didn't make."

The upside is precision and less thrash, which helps structured extraction and tuned pipelines. The cost: if you want an instruction applied broadly, state the scope. For example, "Apply this formatting to every section, not just the first one."

## Progress updates

Opus 4.8 gives more regular, higher-quality interim updates during long agentic traces. If you added scaffolding to force status messages ("After every 3 tool calls, summarize progress"), remove it. If the updates are not calibrated to your use case, describe what they should look like and give examples.

## Subagent spawning

Opus 4.8 spawns fewer subagents by default, and the behavior is steerable. Give explicit guidance on when subagents are wanted:

```text
Do not spawn a subagent for work you can complete directly in a single response (e.g. refactoring a function you can already see).

Spawn multiple subagents in the same turn when fanning out across items or reading multiple files.
```

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

Opus 4.8 finds bugs better than prior models, with higher recall and precision in Anthropic's internal evals. But a harness tuned for an older model can show *lower measured recall* because the model now follows filter instructions more faithfully:

> "When a review prompt says things like 'only report high-severity issues,' 'be conservative,' or 'don't nitpick,' Claude Opus 4.8 may follow that instruction more faithfully than earlier models did: it may investigate the code just as thoroughly, identify the bugs, and then not report findings it judges to be below your stated bar."

This is a harness effect, not a capability regression. Fix it by making the finding step about coverage and pushing the filter downstream:

```text
Report every issue you find, including ones you are uncertain about or consider low-severity. Do not filter for importance or confidence at this stage - a separate verification step will do that. Your goal here is coverage: it is better to surface a finding that later gets filtered out than to silently drop a real bug. For each finding, include your confidence level and an estimated severity so a downstream filter can rank them.
```

If you do want single-pass self-filtering, set a concrete bar instead of qualitative terms: "report any bugs that could cause incorrect behavior, a test failure, or a misleading result; only omit nits like pure style or naming preferences."

## Frontend default house style

Opus 4.8 has strong design instincts and a persistent default house style: warm cream/off-white backgrounds (~`#F4F1EA`), serif display type, italic word-accents, terracotta/amber accents. It reads well for editorial and portfolio briefs but feels off for dashboards, dev tools, fintech, healthcare, or enterprise apps, and it shows up in slide decks too. Generic instructions ("don't use cream," "make it clean and minimal") tend to shift to a different fixed palette rather than producing variety. Two approaches work:

1. **Specify a concrete alternative** — give an explicit palette, type, and layout spec; the model follows it precisely.
2. **Have the model propose options before building** — "propose 4 distinct visual directions tailored to this brief... Ask the user to pick one, then implement only that direction." This breaks the default and replaces the variety you used to get from `temperature`.

Opus 4.8 needs less frontend prompting than prior models to avoid "AI slop." A short snippet suffices:

```text
<frontend_aesthetics>
NEVER use generic AI-generated aesthetics like overused font families (Inter, Roboto, Arial, system fonts), cliched color schemes (particularly purple gradients on white or dark backgrounds), predictable layouts and component patterns, and cookie-cutter design that lacks context-specific character. Use unique fonts, cohesive colors and themes, and animations for effects and micro-interactions.
</frontend_aesthetics>
```

## Model self-knowledge

To have Claude identify itself and choose model strings correctly:

```text
The assistant is Claude, created by Anthropic. The current model is Claude Opus 4.8.
```

```text
When an LLM is needed, please default to Claude Opus 4.8 unless the user requests otherwise. The exact model string for Claude Opus 4.8 is claude-opus-4-8.
```

## Migration: Opus 4.7 to 4.8

> "There are no breaking API changes for code already running on Claude Opus 4.7."

Swap the model id `claude-opus-4-7` to `claude-opus-4-8` and check these behavior differences:

- **Effort defaults to `high`** across all surfaces, including the Messages API. For coding and high-autonomy work, set `xhigh` explicitly. The token allocation per level also shifts: `medium` allows somewhat more thinking, `high` somewhat less, `xhigh` substantially more — re-baseline if you tuned a level against 4.7.
- **1M context window is the default** with no beta header and no long-context premium (200k on Microsoft Foundry). Remove any context-window beta header.
- **Mid-conversation system messages** are now allowed: Opus 4.8 accepts `role: "system"` messages after a user turn in the `messages` array (earlier models reject them with a 400). Use the top-level `system` field for instructions that apply from the start. You can use mid-conversation system messages to update instructions while preserving prompt-cache hits on earlier turns.
- **Refusal `stop_details` are now publicly documented.** On a refusal the model identifies the refusal category alongside the existing `refusal` stop reason. No beta header, no opt-out — verify your stop-reason handling reads `stop_details`.
- **Lower prompt-caching minimum** (1,024 tokens) — short prompts that could not cache on 4.7 now can, with no code changes.

### Coming from 4.6 or earlier

Apply the **4.7 migration steps first** — they include breaking changes the 4.8 upgrade alone does not cover:

- **Sampling parameters rejected.** Setting `temperature`, `top_p`, or `top_k` to any non-default value returns a 400. Omit them; guide behavior through prompting. `temperature = 0` never guaranteed identical outputs anyway.
- **Manual extended thinking gone.** `thinking: {type: "enabled", budget_tokens: N}` returns a 400. Switch to `thinking: {type: "adaptive"}` and control depth with `effort`. Adaptive thinking is off by default; set it explicitly to enable.
- **New tokenizer.** Opus 4.7 introduced a new tokenizer that can use roughly 1x to 1.35x as many tokens for the same text (up to ~35% more). Re-budget `max_tokens` and re-test any client-side token estimates.
- **Prefill removed** (carried from 4.6): prefilling the last assistant message returns a 400. Use structured outputs, system-prompt instructions, or `output_config.format` instead.
