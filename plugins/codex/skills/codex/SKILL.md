---
name: codex
description: Get a second opinion from OpenAI Codex CLI on difficult debugging, code analysis, or architecture problems, run a code/diff review (finder or adversarial-refuter passes over a diff or working tree), run a security review/audit or verification of security-sensitive code (auth, input validation, crypto, secrets), diagnose a bug, hand it a well-scoped edit to existing code (little net-new code), generate images (logos, mascots, banners, illustrations) with Codex's $imagegen skill, or offload rote throwaway work (one-off scripts, data munging) where code quality doesn't matter and nothing can go wrong. Use when reviewing code or a diff for defects, when auditing or verifying security-sensitive code, when diagnosing a bug, when stuck after multiple attempts, for a fully specified edit to existing code, when asked to generate an image, or for disposable bulk work. Runs inline in the caller's context — safe to invoke from the main conversation, subagents, and workflows alike; workflow stages that must route to codex by agent type spawn the codex-wrapper agent this plugin ships.
allowed-tools: Bash(cat:*, codex:*, echo:*, ls:*), Read, Grep, Glob
effort: medium
---

# Codex CLI

Get a second perspective from OpenAI's Codex CLI when stuck on difficult problems,
run a code/diff review, security review/audit, or bug diagnosis, hand it a
well-scoped edit to existing code, use its built-in `$imagegen` skill to generate
images, or offload rote throwaway work.

Every `codex exec` in this skill pins `-c model=gpt-5.6-sol
-c model_reasoning_effort=xhigh -c service_tier=fast`. The fast tier is
mandatory — never drop it or offer a non-fast variant, whatever the model;
without it, xhigh prompts can run 10–30+ minutes and get abandoned. Keep
questions bounded and specific: a narrow question returns in ~2 minutes, an
open-ended design essay does not. Model Variants and Escalation below covers
the two sanctioned deviations.

## When to Use

- Code/diff review — sweeping a diff or codebase for bugs, correctness issues, or
  cleanups, including finder and adversarial-refuter passes. This is the review
  lane per the Models table; the synthesis/accept-reject pass over findings stays
  with the caller (fable).
- Security review/audit and verification of security-sensitive code — auth, input
  validation, file paths, crypto, secrets. The primary security-verification lane
  per the Models table: implementing that code stays on fable, this lane checks
  the result, and the synthesis/accept-reject pass over findings stays with the
  caller (fable).
- Bug diagnosis — the first stop; escalate to fable only when Codex's answer
  misses.
- After 2+ failed approaches to the same problem
- Debugging subtle bugs (off-by-one, race conditions, state corruption)
- Analyzing complex algorithms against specifications
- Understanding unfamiliar code patterns, protocols, or file formats
- When a fresh perspective would break a deadlock
- Generating images -- logos, mascots, banners, illustrations -- via `$imagegen`
  (see Generating Images below)
- Rote, throwaway work -- one-off scripts, scratch harnesses, bulk data munging --
  where code quality doesn't matter and nothing can go wrong. Codex's flat-rate
  plan makes this effectively free; keep the output out of production paths.
- Well-scoped edits to existing code -- the change is fully specifiable up front
  and adds little net-new code (a refactor, a signature change, threading a
  parameter through). Production edits are in range at xhigh; review the diff as
  you would any other contributor's.

## Model Variants and Escalation

`gpt-5.6-sol` is the default for every lane. Two sanctioned deviations, at
your discretion per task; every other flag stays put either way:

- **`gpt-5.6-luna`** for the rote-throwaway/bulk lane only -- one-off scripts,
  scratch harnesses, data munging where quality doesn't matter and nothing can
  go wrong. Swap the `-c model=` value; keep the rest of the recipe.
- **Ultra execution mode** is the escalation rung between sol at xhigh and
  fable: it decomposes the run across internal subagents for the hardest
  problems. The codex CLI does not expose it yet (0.144.1: not a
  reasoning-effort value, no feature flag), so until it does, a hard sol miss
  escalates straight to fable. Once it lands, it is the retry rung -- slow and
  expensive; never start a task there.

`-c service_tier=fast` is non-negotiable on every exec, regardless of variant
or effort.

## From Workflows and Subagents (the codex-wrapper agent)

Since 0.10.0 this skill runs inline — no `context: fork` — so `Skill(codex)`
works identically from the main conversation, subagents, and workflow steps.
(Through 0.9.0 the skill forked, and a schema-bound caller leaked its
`StructuredOutput` tool into the fork; a fork ending its turn there had its
answer discarded as a bare "Skill execution completed" stub — claude-code#75559.
Inline execution removes the fork and the relay, so the failure mode is
structurally gone.)

The `model` parameter on `Agent`/`Task` calls and workflow `agent()` steps
still takes only Claude models, so a workflow stage that should BE a codex
call spawns agent type `codex:codex-wrapper` (the `subagent_type` of an
`Agent`/`Task` call, or `agentType` on a workflow `agent()` step) with the
full self-contained question — or pointers to gather plus the questions to
answer — as the prompt. The wrapper is also the lane for keeping a big
context gather (a large diff, many files) out of your own window: it reads,
composes, runs the same pinned `codex exec`, and returns Codex's answer
verbatim.

## Workflow

### Step 1: Gather Context

Before invoking Codex, collect all relevant context using Read, Grep, and Glob.
Build a comprehensive question with:

- Clear problem statement with the specific error or symptom
- Complete functions (never truncated snippets)
- What has already been tried and why it failed
- Specific questions to answer

### Step 2: Write Question and Invoke Codex

Write the question to a mktemp-unique path in your session scratchpad
directory (the path listed in your system prompt) — never /tmp, which is
shared across sessions, so fixed or `$$`-suffixed names there get clobbered
or cross-read by parallel codex runs — then write the question, run codex,
and print the reply path in ONE Bash call so the variables resolve
consistently. Give the call a 10-minute timeout: xhigh on the fast tier
typically returns in ~2 minutes but can run longer.

```bash
S=<your scratchpad directory>  # from your system prompt; S=$(mktemp -d) if none is listed
Q=$(mktemp "$S/codex-q-XXXXXX"); R=$(mktemp "$S/codex-r-XXXXXX")
cat <<'QUESTION' > "$Q"
I have a [component] that fails with [specific error].

Here is the full function:
```
[paste complete code]
```

Key observations:
1. [What works]
2. [What fails]
3. [When it fails]

What has been tried:
- [approach 1 and why it failed]
- [approach 2 and why it failed]

Questions:
1. [specific question]
2. [specific question]
QUESTION

cat "$Q" | codex exec -c model=gpt-5.6-sol -c model_reasoning_effort=xhigh -c service_tier=fast -o "$R" --sandbox workspace-write
echo "REPLY_FILE: $R"
```

### Step 3: Evaluate the Reply

Read the reply file printed on the `REPLY_FILE:` line. The file persists as a
durable record of the exchange.

Evaluate suggestions critically. Codex is helpful but not infallible -- it can occasionally misinterpret specifications. Always verify against authoritative sources before applying.

The codex skill never absorbs a surprise. If the reply invalidates the premise
of your question or changes the task's shape -- the bug isn't where you said,
the spec means something else, the fix belongs in a different layer -- stop
rather than improvising a detour: surface the finding with 2-4 concrete options
and let the user (or the fable orchestrator that delegated to you) pick. See
AGENTS.md § Ask Before Assuming.

## Alternative: Direct Piping

For shorter questions:
```bash
echo "Explain the JPEG progressive AC refinement algorithm" | codex exec -c model=gpt-5.6-sol -c model_reasoning_effort=xhigh -c service_tier=fast --sandbox workspace-write
```

The file-based pattern is better for debugging because you can refine the question and keep a record.

## Response Format

For diagnosis, review, and second-opinion calls, return a structured summary:

```
## Codex Analysis

**Problem:** <1 sentence>
**Codex Findings:**
1. <finding with assessment: agree/disagree/needs-verification>
2. <finding with assessment>

**Recommended Actions:**
- <concrete next step based on verified findings>

**Confidence:** <high/medium/low based on how well Codex understood the problem>
```

For well-scoped edits and image generation, skip the structure: return Codex's
answer verbatim in the exact shape the caller asked for (e.g. "reply with ONLY
the edited function"). Don't wrap a bare artifact in the Analysis boilerplate —
the caller wants the artifact, not a report on it.

## Generating Images ($imagegen)

Codex ships a built-in `$imagegen` skill backed by a hosted `image_gen` tool
(model gpt-image-2). Mentioning `$imagegen` anywhere in the prompt loads the
skill; it works with non-interactive `codex exec`.

**Availability:** the hosted tool mounts only when codex is signed in with a
ChatGPT plan -- check `codex login status`. With API-key auth it never mounts,
and codex will quietly fake the image by drawing it with PIL/ImageMagick instead.
Two defenses, use both:

1. **Pass `--disable shell_tool`** so codex cannot draw -- with no shell it
   either calls `image_gen` or reports the tool missing.
2. **Tell it to fail loudly**: "If the image_gen tool is unavailable, reply
   IMAGE_GEN_UNAVAILABLE and stop."

With the shell disabled, codex cannot write into your repo. Generations land in
`$CODEX_HOME/generated_images/` (default `~/.codex/generated_images/`); have the
reply list the saved paths, then copy and post-process the files yourself.

```bash
S=<your scratchpad directory>  # from your system prompt; S=$(mktemp -d) if none is listed
Q=$(mktemp "$S/codex-q-XXXXXX"); R=$(mktemp "$S/codex-r-XXXXXX")
cat <<'PROMPT' > "$Q"
Use $imagegen to create a square 1024x1024 logo for [project]: [subject], flat
illustration, bold clean shapes, on a solid bright-green background (it will be
chroma-keyed out locally). If the image_gen tool is unavailable, reply
IMAGE_GEN_UNAVAILABLE and stop. End your reply with the absolute path of the
saved file on its own line.
PROMPT

cat "$Q" | codex exec -c model=gpt-5.6-sol -c model_reasoning_effort=xhigh -c service_tier=fast -o "$R" --disable shell_tool --sandbox workspace-write
echo "REPLY_FILE: $R"
```

Then place and post-process yourself (read the path from the `REPLY_FILE:` line):

```bash
uv run --with pillow "${CODEX_HOME:-$HOME/.codex}/skills/.system/imagegen/scripts/remove_chroma_key.py" \
  --input <saved path> --out assets/logo.png --auto-key border --soft-matte --despill
```

Confirm with `ls`, then view each file with Read (it renders images) and iterate
with a refined prompt if needed.

Model limits to design around:

1. **No native transparency** -- gpt-image-2 always paints a background. Generate
   on a solid chroma-key background and remove it locally with the bundled
   `remove_chroma_key.py` helper (as above; it needs only Pillow, hence
   `uv run --with pillow`).
2. **Fixed native sizes** -- 1024x1024, 1536x1024 (landscape), 1024x1536
   (portrait). For other ratios, ask for the content composed in a known band
   and crop locally: `sips -c H W <src> --out <dst>` on macOS, or ImageMagick
   `magick <src> -gravity center -crop WxH+0+0 +repage <dst>`.
3. **Text renders accurately** -- names and taglines inside images come out
   right; quote the exact strings in the prompt.
4. **Style drifts across sessions** -- generate related images in ONE codex
   session so characters and palette stay consistent.
5. **Fallback CLI for API-key machines** --
   `${CODEX_HOME:-$HOME/.codex}/skills/.system/imagegen/scripts/image_gen.py`
   (requires `OPENAI_API_KEY`) offers explicit sizes and
   `gpt-image-1.5 --background transparent` for native transparency. Run it
   directly; no codex session needed.

## Tips

1. **Provide complete code** -- don't truncate functions. Codex needs full context.
2. **Be specific** -- "Why does Huffman decoding fail after 1477 blocks in AC refinement scan?" not "Why does this fail?"
3. **Include the spec** -- if debugging against a standard, mention the relevant spec sections.
4. **Verify suggestions** -- Codex is helpful but not infallible. Always verify against authoritative sources.
5. **Iterate if needed** -- if the first response doesn't solve the problem, create a new question with additional context from what you learned.

## Common Issues

**"stdin is not a terminal"**: Use `codex exec` not bare `codex`

**No output**: Check that `-o` flag has a valid path

**Result is a bare "Skill execution completed"**: you are running a stale cached
version (0.9.0 or earlier, when this skill ran `context: fork` and schema-bound
subagent callers hit a relay bug — claude-code#75559). Since 0.10.0 the skill
runs inline and this cannot happen: run `claude plugin update codex@skills`.

**Two codex calls stomped or cross-read each other's files**: the question/reply
files were written outside the session scratchpad (`/tmp` is shared across
sessions, and even `$$`-suffixed names there collide when PIDs are reused).
Keep the recipe's `mktemp` paths rooted in your scratchpad directory.

**Timeout**: Exec mode never prompts; `--sandbox workspace-write` lets generated commands write files without approval (`--full-auto` is the deprecated spelling of the same thing). If a call drags past a few minutes, check the `-c service_tier=fast` flag is present and the question is bounded — broad open-ended prompts are the usual cause.

**"Not inside a trusted directory"**: `codex exec` refuses to run outside a git repository — `git init` first, or pass `--skip-git-repo-check`.

**IMAGE_GEN_UNAVAILABLE**: codex is signed in with an API key (`codex login status`), not a ChatGPT plan — the hosted tool never mounts. Use the fallback CLI from Generating Images instead.

**Images not in the repo**: expected — with `--disable shell_tool` codex can't write into the workspace; generations stay in `$CODEX_HOME/generated_images/` and copying them in is your job.

**Solid box behind a "transparent" logo**: chroma-key removal was skipped -- gpt-image-2 has no native transparency; use the `remove_chroma_key.py` step.
