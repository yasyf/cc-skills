# Google (Gemini)

*Carried over from prior guidance; not refreshed against new 2026 source docs this pass.*

Guidance for Gemini 2.5 and Gemini 3 models.

## Reasoning depth

- Set reasoning depth with the `thinkingLevel` enum (`minimal` / `low` / `medium` / `high`) on Gemini 3. It replaces the older token-budget knob.
- Gemini 3 Pro cannot disable thinking entirely. Even at the lowest level it produces some internal reasoning, so plan for that latency and token cost.

## Instructions and examples

- Few-shot examples can replace instructions. When the examples are clear enough, drop the prose rules and let the examples carry the task. Google's docs recommend removing instructions that the examples already demonstrate.
- Do not add explicit step-by-step reasoning instructions to thinking models. The thinking mode generates its own reasoning; prescriptive "think step by step" prompting works against it.

## Sampling

- Keep the default temperature of 1.0 for Gemini 3 models. Lowering it does not reliably improve quality and can degrade reasoning.

## Context ordering

- Place all context first, query last. For long-context prompts, put reference material at the top and the instruction or question at the bottom.

## Tone

- Remove emotional appeals and flattery. Google's docs state these no longer improve performance and can worsen output. Replace them with plain, specific instructions.

## Date and knowledge cutoff

- For Gemini 3 Flash, add the explicit current date and the model's knowledge-cutoff in the system instructions. This grounds time-sensitive answers.

## Multi-turn function calling

- Pass thought signatures back in multi-turn function-calling conversations. They preserve reasoning continuity across turns; dropping them breaks the model's chain of reasoning between tool calls.

## Structured output with tools

- Structured output and tool use can be combined. You can request structured output while the model also uses tools such as Google Search, Code Execution, and File Search.
