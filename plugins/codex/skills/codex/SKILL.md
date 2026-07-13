---
name: codex
description: Get a second opinion from OpenAI Codex CLI on difficult debugging, code analysis, or architecture problems, run a code/diff review (finder or adversarial-refuter passes over a diff or working tree), run a security review/audit or verification of security-sensitive code (auth, input validation, crypto, secrets), diagnose a bug, hand it a well-scoped edit or clearly-bounded implementation task (net-new code included when the boundaries are crisp), generate images (logos, mascots, banners, illustrations) with Codex's $imagegen skill, or offload rote throwaway work (one-off scripts, data munging) where code quality doesn't matter and nothing can go wrong. Use when reviewing code or a diff for defects, when auditing or verifying security-sensitive code, when diagnosing a bug, when stuck after multiple attempts, for a fully specified edit or clearly-bounded build, when asked to generate an image, or for disposable bulk work. Runs inline in the caller's context — safe to invoke from the main conversation, subagents, and workflows alike; workflow stages that must route to codex by agent type spawn the codex-wrapper agent this plugin ships.
allowed-tools: Bash(cat:*, codex:*, echo:*, ls:*), Read, Grep, Glob
effort: medium
---

# Codex CLI

Get a second perspective from OpenAI's Codex CLI when stuck on difficult problems,
run a code/diff review, security review/audit, or bug diagnosis, hand it a
well-scoped edit or clearly-bounded implementation, use its built-in `$imagegen` skill to generate
images, or offload rote throwaway work.

Every `codex exec` in this skill pins `-c model=gpt-5.6-sol
-c model_reasoning_effort=xhigh -c service_tier=fast`, runs
`--sandbox danger-full-access`, and feeds the plugin's `AGENTS.md` via
`-c developer_instructions` (see Browser Access below). The fast tier is
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
- Well-scoped edits and clearly-bounded implementation -- the change is fully
  specifiable up front: a refactor, a signature change, threading a parameter
  through, or net-new code whose boundaries are crisp. Bounded terminal/shell-heavy
  execution fits here too. Ambiguous or exploratory builds, large multi-file
  refactors, and long agentic runs stay on Claude (opus xhigh) -- sol drifts
  out of scope on open-ended work. Production edits are in range at xhigh;
  review the diff as you would any other contributor's.

## Model Variants and Escalation

`gpt-5.6-sol` is the default for every lane. Two sanctioned deviations, at
your discretion per task; every other flag stays put either way:

- **`gpt-5.6-luna`** for the rote-throwaway/bulk lane only -- one-off scripts,
  scratch harnesses, data munging where quality doesn't matter and nothing can
  go wrong. Swap the `-c model=` value; keep the rest of the recipe.
- **Ultra execution mode** (exposed since codex 0.144.0) decomposes the run
  across internal subagents at 3-5x token cost. It is not an escalation rung:
  ultra shows the worst scope discipline of any config -- long unattended
  runs wander onto the wrong work -- so a hard sol miss escalates straight
  to fable. Never start or retry a task on ultra.

`-c service_tier=fast` is non-negotiable on every exec, regardless of variant
or effort.

## Browser Access

Every exec feeds `${CLAUDE_PLUGIN_ROOT}/AGENTS.md` to codex as developer
instructions via `-c developer_instructions="$(cat ...)"`. That file bans raw
browser launches (Chrome under the old `workspace-write` seatbelt died at
`RegisterApplication`, spraying `.ips` crash reports; unsandboxed it opens
windows on the user's desktop) and routes all browser/DOM verification through
the `agent-browser` CLI in the `codex` namespace. Nothing for you to do —
there is no warm-up step; the agent-browser daemon auto-starts from inside
codex now that the sandbox is `danger-full-access`. Never drop the
`-c developer_instructions` flag or swap the sandbox back.

`--sandbox danger-full-access` means codex-generated commands run unsandboxed
on this machine — a standing, user-sanctioned choice for these lanes; don't
"harden" it back to `workspace-write`.

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
directory when your system prompt lists one; when none is listed, create a
fresh directory with `mktemp -d`. Those are the only two options — never
invent a directory (a repo-relative name like `.claude-scratch/` lands in
the working tree and gets committed by auto-snapshot), and never use fixed
or `$$`-suffixed paths, which collide across parallel codex runs. Write the
question, run codex, and print the reply path in ONE Bash call so the
variables resolve consistently. Give the call a 10-minute timeout: xhigh on
the fast tier typically returns in ~2 minutes but can run longer. The exec
redirects its event stream (banner, echoed prompt, progress trace) into a
JSONL log file; only the `REPLY_FILE:`/`LOG_FILE:` lines — or a failure
tail — reach the conversation.

```bash
S=<your scratchpad directory>  # absolute path from your system prompt; none listed → S=$(mktemp -d). Never a made-up or repo-relative dir.
Q=$(mktemp "$S/codex-q-XXXXXX") && R=$(mktemp "$S/codex-r-XXXXXX") || exit 1
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

cat "$Q" | codex exec -c model=gpt-5.6-sol -c model_reasoning_effort=xhigh -c service_tier=fast -c developer_instructions="$(cat "${CLAUDE_PLUGIN_ROOT}/AGENTS.md")" -o "$R" --json --color never --sandbox danger-full-access > "$Q.log" 2>&1 || tail -20 "$Q.log"
echo "REPLY_FILE: $R"; echo "LOG_FILE: $Q.log"
```

### Step 3: Evaluate the Reply

Read the reply file printed on the `REPLY_FILE:` line. The file persists as a
durable record of the exchange. If the reply file is empty or missing, read
the tail of the `LOG_FILE:` JSONL — the failing event is in the last lines.

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
S=<your scratchpad directory>  # absolute path from your system prompt; none listed → S=$(mktemp -d)
R=$(mktemp "$S/codex-r-XXXXXX") || exit 1
echo "Explain the JPEG progressive AC refinement algorithm" | codex exec -c model=gpt-5.6-sol -c model_reasoning_effort=xhigh -c service_tier=fast -c developer_instructions="$(cat "${CLAUDE_PLUGIN_ROOT}/AGENTS.md")" -o "$R" --json --color never --sandbox danger-full-access > "$R.log" 2>&1 || tail -20 "$R.log"
cat "$R"
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
S=<your scratchpad directory>  # absolute path from your system prompt; none listed → S=$(mktemp -d). Never a made-up or repo-relative dir.
Q=$(mktemp "$S/codex-q-XXXXXX") && R=$(mktemp "$S/codex-r-XXXXXX") || exit 1
cat <<'PROMPT' > "$Q"
Use $imagegen to create a square 1024x1024 logo for [project]: [subject], flat
illustration, bold clean shapes, on a solid bright-green background (it will be
chroma-keyed out locally). If the image_gen tool is unavailable, reply
IMAGE_GEN_UNAVAILABLE and stop. End your reply with the absolute path of the
saved file on its own line.
PROMPT

cat "$Q" | codex exec -c model=gpt-5.6-sol -c model_reasoning_effort=xhigh -c service_tier=fast -c developer_instructions="$(cat "${CLAUDE_PLUGIN_ROOT}/AGENTS.md")" -o "$R" --disable shell_tool --json --color never --sandbox danger-full-access > "$Q.log" 2>&1 || tail -20 "$Q.log"
echo "REPLY_FILE: $R"; echo "LOG_FILE: $Q.log"
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

**No output**: Check that the `-o` flag has a valid path, then read the tail
of the `LOG_FILE:` JSONL — the failing event is in the last lines

**Result is a bare "Skill execution completed"**: you are running a stale cached
version (0.9.0 or earlier, when this skill ran `context: fork` and schema-bound
subagent callers hit a relay bug — claude-code#75559). Since 0.10.0 the skill
runs inline and this cannot happen: run `claude plugin update codex@skills`.

**Two codex calls stomped or cross-read each other's files**: the question/reply
files used fixed or `$$`-suffixed names (PIDs recycle, so parallel runs collide)
or a shared invented directory. Keep the recipe's `mktemp` paths — the scratchpad
from your system prompt, else a fresh `mktemp -d` — and never a repo-relative dir.

**Timeout**: Exec mode never prompts; `--sandbox danger-full-access` runs generated commands unsandboxed without approval (the old `workspace-write` seatbelt crashed GUI launches like browsers). If a call drags past a few minutes, check the `-c service_tier=fast` flag is present and the question is bounded — broad open-ended prompts are the usual cause.

**Codex launched Chrome / browser windows appeared**: the `-c developer_instructions` feed was dropped from the invocation — it carries the browser rules (agent-browser only, `codex` namespace). Restore the flag exactly as in the Step 2 recipe.

**"Not inside a trusted directory"**: `codex exec` refuses to run outside a git repository — `git init` first, or pass `--skip-git-repo-check`.

**IMAGE_GEN_UNAVAILABLE**: codex is signed in with an API key (`codex login status`), not a ChatGPT plan — the hosted tool never mounts. Use the fallback CLI from Generating Images instead.

**Images not in the repo**: expected — with `--disable shell_tool` codex can't write into the workspace; generations stay in `$CODEX_HOME/generated_images/` and copying them in is your job.

**Solid box behind a "transparent" logo**: chroma-key removal was skipped -- gpt-image-2 has no native transparency; use the `remove_chroma_key.py` step.
