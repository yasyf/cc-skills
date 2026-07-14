---
name: codex
description: Get a second opinion from OpenAI Codex CLI on difficult debugging, code analysis, or architecture problems, run a code/diff review (finder or adversarial-refuter passes over a diff or working tree), run a security review/audit or verification of security-sensitive code (auth, input validation, crypto, secrets), diagnose a bug, hand it a well-scoped, decision-light change to existing code (large net-new code stays on Claude — opus, fable if crucial), generate images (logos, mascots, banners, illustrations) with Codex's $imagegen skill, or offload rote throwaway work (one-off scripts, data munging) where code quality doesn't matter and nothing can go wrong. Use when reviewing code or a diff for defects, when auditing or verifying security-sensitive code, when diagnosing a bug, when stuck after multiple attempts, for a fully specified edit or clearly-bounded build, when asked to generate an image, or for disposable bulk work. Runs inline in the caller's context — safe to invoke from the main conversation, subagents, and workflows alike; workflow stages that must route to codex by agent type spawn the codex-wrapper agent this plugin ships.
allowed-tools: Bash(cat:*, codex:*, codex-ask:*, echo:*, ls:*), Read, Grep, Glob
effort: medium
---

# Codex CLI

Get a second perspective from OpenAI's Codex CLI when stuck on difficult problems,
run a code/diff review, security review/audit, or bug diagnosis, hand it a
well-scoped edit or clearly-bounded implementation, use its built-in `$imagegen` skill to generate
images, or offload rote throwaway work.

Every codex call in this skill runs through `codex-ask`, the executable this
plugin ships (a plugin's `bin/` rides the Bash tool's PATH while the plugin
is enabled). The script pins `-c model=gpt-5.6-sol
-c model_reasoning_effort=xhigh -c service_tier=fast`, runs
`--sandbox danger-full-access`, feeds the plugin's `AGENTS.md` via
`-c developer_instructions` (see Browser Access below), unsets
`OPENAI_API_KEY` so codex always authenticates via the ChatGPT-plan OAuth
login (the ambient key is billing-capped and never mounts the hosted
`image_gen` tool), and keeps every temp file on an absolute scratch path —
the flags, auth, and paths are the script's job, so invoke it rather than
hand-rolling a `codex exec` line.
The fast tier is mandatory on every variant; without it, xhigh prompts can
run 10–30+ minutes and get abandoned. Keep questions bounded and specific:
a narrow question returns in ~2 minutes, an open-ended design essay does
not. Model Variants and Escalation below covers the two sanctioned
deviations.

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
- Well-scoped, decision-light changes to existing code -- the change is fully
  specifiable up front: a scoped edit, a signature change, threading a parameter
  through, a bounded refactor, a well-specified small feature. Bounded
  terminal/shell-heavy execution fits here too. Large amounts of net-new code
  stay on Claude (opus xhigh, fable if crucial) -- sol is much stronger at
  modifying existing code than at authoring a large new subsystem from scratch;
  ambiguous or exploratory builds, decision-dense refactors, and long agentic
  runs stay on opus too, since sol drifts out of scope and fails to converge on
  open-ended work. Production edits are in range at xhigh; review the diff as you
  would any other contributor's.

## Model Variants and Escalation

`gpt-5.6-sol` is the default for every lane. Two sanctioned deviations, at
your discretion per task; every other flag stays put either way:

- **`gpt-5.6-luna`** for two lanes. The rote-throwaway/bulk lane: one-off
  scripts, scratch harnesses, data munging where quality doesn't matter and
  nothing can go wrong. And the **recon lane, which now defaults to luna**:
  enumerations, config/wiring locates, subsystem traces, pattern sweeps, and
  routine chores (diff and log triage, classify, extract, digest, doc lookup),
  where luna matched or beat sonnet-5 on recall with zero false cites at ~3x
  speed and ~80% lower cost (measured 2026-07-14). Exhaustive enumerations get
  a cross-model verify pass -- luna's failure mode is a confident wrong count, and
  a luna/sonnet/sol verifier caught 100% of planted undercounts. Recon stays on
  Claude (sonnet-5 via Explore) when the surface is Claude-only, when coverage
  must sweep >300K tokens in one pass (luna's retrieval cliff -- xhigh doesn't
  move it), or when a silent miss is unrecoverable and no verify pass will run.
  Pass `-m luna`; every other flag stays pinned. Recon on luna stays at the
  default xhigh effort: at `high` it drops whole subsystems on deep traces.
- **Ultra execution mode** (exposed since codex 0.144.0) decomposes the run
  across internal subagents at 3-5x token cost. It is not an escalation rung:
  ultra shows the worst scope discipline of any config -- long unattended
  runs wander onto the wrong work -- so a hard sol miss escalates straight
  to fable. Never start or retry a task on ultra.

`codex-ask` pins `-c service_tier=fast` on every call, regardless of variant
or effort — the tier is a property of the script, not a caller choice.

## Browser Access

`codex-ask` feeds the plugin's `AGENTS.md` to codex as developer
instructions on every call. That file bans raw
browser launches (Chrome under the old `workspace-write` seatbelt died at
`RegisterApplication`, spraying `.ips` crash reports; unsandboxed it opens
windows on the user's desktop) and routes all browser/DOM verification through
the `agent-browser` CLI in the `codex` namespace. Nothing for you to do —
there is no warm-up step; the agent-browser daemon auto-starts from inside
codex now that the sandbox is `danger-full-access`. Routing every call
through `codex-ask` is what keeps the feed and the sandbox pin in place; a
hand-rolled `codex exec` line is how they get lost.

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

### Step 2: Ask via codex-ask

Pipe the question through `codex-ask`. The script handles the mechanics
that used to be recipe steps: it mktemps the question/reply/log files in a
fresh `mktemp -d` directory, runs the pinned exec, redirects the JSONL
event stream (banner, echoed prompt, progress trace) into the log, and
prints the `REPLY_FILE:`/`LOG_FILE:` lines — plus a log tail on failure —
so only those reach the conversation. To group the files in your session
scratchpad instead, pass `-s <dir>` with its absolute path; the script
rejects a relative path or one inside the repository, because a scratch dir
in the working tree gets committed by auto-snapshot. Give the Bash call a 10-minute
timeout: xhigh on the fast tier typically returns in ~2 minutes but can run
longer.

```bash
codex-ask - <<'QUESTION'
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
```

In place of `-` (stdin), `codex-ask` also takes a file path or literal
text, so a short question can go inline:
`codex-ask "Explain the JPEG progressive AC refinement algorithm"`. Every
form writes the question file, so the exchange keeps a durable record
either way.

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
`codex-ask` unsets `OPENAI_API_KEY` for exactly this reason, so the login
state from `codex login status` is what counts. Two defenses, use both:

1. **Pass `--image`** so codex cannot draw -- it adds `--disable shell_tool`,
   and with no shell codex either calls `image_gen` or reports the tool
   missing.
2. **Tell it to fail loudly**: "If the image_gen tool is unavailable, reply
   IMAGE_GEN_UNAVAILABLE and stop."

With the shell disabled, codex cannot write into your repo. Generations land in
`$CODEX_HOME/generated_images/` (default `~/.codex/generated_images/`); have the
reply list the saved paths, then copy and post-process the files yourself.

```bash
codex-ask --image - <<'PROMPT'
Use $imagegen to create a square 1024x1024 logo for [project]: [subject], flat
illustration, bold clean shapes, on a solid bright-green background (it will be
chroma-keyed out locally). If the image_gen tool is unavailable, reply
IMAGE_GEN_UNAVAILABLE and stop. End your reply with the absolute path of the
saved file on its own line.
PROMPT
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

**No output**: read the tail of the `LOG_FILE:` JSONL — the failing event is
in the last lines

**Result is a bare "Skill execution completed"**: you are running a stale cached
version (0.9.0 or earlier, when this skill ran `context: fork` and schema-bound
subagent callers hit a relay bug — claude-code#75559). Since 0.10.0 the skill
runs inline and this cannot happen: run `claude plugin update codex@skills`.

**Two codex calls stomped or cross-read each other's files, or temp files
appeared inside the repo**: a hand-rolled `codex exec` used fixed,
`$$`-suffixed, or repo-relative paths. `codex-ask` mktemps fresh absolute
paths per call, so neither can happen through it — route the call through
the script.

**Timeout**: Exec mode never prompts; `--sandbox danger-full-access` runs generated commands unsandboxed without approval (the old `workspace-write` seatbelt crashed GUI launches like browsers). `codex-ask` pins `-c service_tier=fast`, so a call dragging past a few minutes means the question is unbounded — broad open-ended prompts are the usual cause.

**Codex launched Chrome / browser windows appeared**: a hand-rolled `codex exec` bypassed `codex-ask`, dropping the `-c developer_instructions` feed that carries the browser rules (agent-browser only, `codex` namespace). Route the call through `codex-ask`, which feeds it on every call.

**"Not inside a trusted directory"**: `codex exec` refuses to run outside a git repository — `git init` first, or pass codex-ask's `--skip-git-repo-check` flag (it goes before the question argument).

**IMAGE_GEN_UNAVAILABLE**: codex is signed in with an API key (`codex login status`), not a ChatGPT plan — the hosted tool never mounts. Use the fallback CLI from Generating Images instead.

**Images not in the repo**: expected — with `--disable shell_tool` codex can't write into the workspace; generations stay in `$CODEX_HOME/generated_images/` and copying them in is your job.

**Solid box behind a "transparent" logo**: chroma-key removal was skipped -- gpt-image-2 has no native transparency; use the `remove_chroma_key.py` step.
