# llm-prompts

**Stop cargo-culting all-caps rules and outdated chain-of-thought folklore.** Get research-backed, provider-agnostic guidance for system prompts, agent instructions, tool descriptions, and few-shot examples, updated for current Anthropic, OpenAI, and Google models.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install llm-prompts@skills
```

## Use it

### Audit and rewrite a system prompt

Prompts stuffed with `CRITICAL` and `NEVER` rules overtrigger modern models. With `llm-prompts` installed, ask:

```text
Review our agent system prompt and rewrite it for Claude Opus 5 and GPT-5.5
```

The skill applies the core principles traced to `reference/research.md`. It replaces `<forbidden_patterns>` negation with `<preferred_patterns>`, strips `CRITICAL`/`IMPORTANT`/`NEVER` emphasis markers, and shifts from procedural checklists to outcome-first success criteria. It also puts reasoning before structure, matches prompt complexity to model capability, and moves long documents to the top with the query at the bottom. Where examples help, it pairs correct and incorrect cases inside labeled `<example>` tags with a one-line reason for each.

### Wire up agentic tool contracts and provider knobs

Cranking reasoning effort to fix a flaky agent wastes tokens when the prompt itself lacks clear stopping and recovery rules. With `llm-prompts` installed, ask:

```text
Add tool-use guardrails to this agent prompt and check our reasoning settings across providers
```

The skill structures the prompt with agentic XML contract blocks such as `<tool_persistence_rules>`, `<completeness_contract>`, `<verification_loop>`, `<empty_result_recovery>`, `<dependency_checks>`, and `<parallel_tool_calling>`, drawing from `reference/openai.md`, `reference/claude.md`, and `reference/gemini.md`. It also flags eleven common pitfalls, like using `"Let's think step by step"` on reasoning models, where explicit chain-of-thought gives +2.9% at 20-80% more latency. It warns against forcing JSON mode during the reasoning step or raising effort to paper over a weak prompt instead of tuning each provider's effort, verbosity, and thinking knobs.

## What's inside

| Kind | Name | What it does |
| --- | --- | --- |
| Skill | `llm-prompts:llm-prompts` | Research-backed, provider-agnostic guidance for writing and revising LLM prompts, agent or tool instructions, tool descriptions, and few-shot examples, backed by `reference/claude.md`, `reference/openai.md`, `reference/gemini.md`, and `reference/research.md`. |

Part of [cc-skills](../../README.md).
