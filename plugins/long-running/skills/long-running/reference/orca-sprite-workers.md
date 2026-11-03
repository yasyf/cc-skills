# Orca Sprite workers

This guide is the drive's copy of monorepo's `.agents/skills/orca`, which stays in monorepo. Drive changes land here, since the owner ruled at 3:02 AM Nov 2 that no drive tooling is added to monorepo. Its scripts are this skill's `scripts/worker-launch.sh`, `scripts/orca-gc` and `scripts/coordinator-waiter.sh`, and `worker-launch.sh` and `coordinator-waiter.sh` are on PATH through the plugin's `bin/`.

Load the version-matched guides first: `orca skills get orca-cli` and
`orca skills get orchestration`. This skill defines the repository's worker
entrypoint and source-return contract.

## Launch a worker

Implementation and test workers default to an unused preinstalled Sprite agents
workspace. The launcher claims it exclusively, refreshes the requested ref
shallowly, prepares the agent, and attaches the native Task in one invocation.
When no compatible unused workspace is ready, it creates the lane's workspace.
Each allocation starts a finite background fill toward `orca.pool.ready`, which
`tools/cc-remote/config.yaml` sets to two unused workspaces, so two launches in a
row both claim warm. Claimed workers stay claimed after completion;
reuse of a retained agent requires an explicit new native Dispatch.

A Sprite lane name has at most 55 lowercase letters, digits, and dashes, and
starts with a letter or digit:

```sh
WORKER_RUN="<owned-run-id>" WORKER_REF=dev WORKER_CHECKOUT="<monorepo checkout>" \
  WORKER_CCI_URL="<cci linux_amd64 archive URL>" WORKER_CCI_SHA256="<its SHA-256>" \
  worker-launch.sh "<lane>" "<model>" "<effort>" "<brief.md>"
```

Run this from the terminal that already owns the named native Run. The script
checks that binding before allocation, calls `cc-remote orca prepare --warm`
once, then starts the prepared idle worker with one native `worker-start` on
the Sprite's Orca server, reusing its prepared terminal and worktree. Prepare
copies the complete brief to the VM and records its hash, length, and actual
checkout HEAD. The first task is a pointer telling the worker to read and
verify that file.

Before `worker-start`, the script installs cci on the Sprite from
`WORKER_CCI_URL` and checks it against `WORKER_CCI_SHA256`. A failed install
leaves the prepared worker idle. With `WORKER_RELAY_SPONSOR` set to a Sprite
that holds an enrolled cci service key, the script then joins the new Sprite to
this Mac's default relay mesh for the mesh's routed drives, with a 24-hour
delegation and this Mac's node as owner, and starts `cci supervise` as a Sprite
service so the worker's records push without a manual `cci relay push`. The key
travels from the sponsor to the new Sprite over stdin and is never written to
the Mac. Without the join, a worker's cci records stay on its Sprite. A failed join prints a warning, sets
`relay` to `failed` in the launch line, and still starts the worker.

Prepare starts the Sprite's Orca runtime through its `orca` CLI, and Orca
1.4.224 serves it on its managed server, `orcad`. The prepared task records that
server's build as `managedServer`, read from the ready event `orca serve --json`
prints. The script starts nothing when the field is missing, which means Orca
fell back to Electron. Orca's reason is the `[serve]` line in
`~/.cc-remote/orca/serve.log` on the Sprite.

With `WORKER_CC_REMOTE` and `CC_REMOTE_CONFIG` unset, the launcher takes both
the CLI and the config from its own checkout. It finds that checkout from the
script's path, never from PATH or the caller's directory:

| Unset variable | The launcher uses |
| --- | --- |
| `WORKER_CC_REMOTE` | `tools/cc-remote/bin/cc-remote`, the wrapper that runs the release `tools/versions.toml` pins through `mise x` |
| `CC_REMOTE_CONFIG` | `tools/cc-remote/config.yaml` |

A long-lived session's PATH, a stale checkout elsewhere, and a newer Homebrew
install do not change that choice. The first launch after a pin change installs
the pinned release. When mise cannot run it, the launcher refuses before
allocation and prints mise's own message.

cc-remote prepares a Sprite only when the CLI is the exact release of the
config's `orca.bootstrap_helper`; a newer patch release fails like an older
one. The CLI pin in `tools/versions.toml` and `mise.toml`, the helper in
`config.yaml`, and `bootstrapHelper` in `agents-payload.json` therefore move
in one pin change. Before allocation, the launcher compares the `version` the
selected CLI prints with `bootstrapHelper.version` and refuses a difference,
naming both. cc-remote makes the same check itself, but a warm claim reaches
it after the spare is claimed, and that spare then stays claimed as failed.

`WORKER_CC_REMOTE` selects another CLI by path or PATH name, and
`CC_REMOTE_CONFIG` selects another config file. Set them for a deliberate pair,
such as a runner that keeps one release while its checkout moves. Against the
checkout's config, a CLI override still has to match the helper pin. Under a
config from outside the checkout, the launcher compares nothing and
cc-remote's own check decides.

When a freshly installed signed release stalls on its first launch on the Mac,
recover it yourself. This is an installation recovery, never a question for the
owner. Verify that the selected executable is the signed release, remove
`com.apple.quarantine` from that one file, and retry its version command. For
the wrapper, the executable is `cc-remote` inside the directory
`mise where cc-remote@<pinned-version>` prints:

```sh
exe="$(readlink -f "<selected-executable>")"
codesign --verify --strict --check-notarization --verbose=2 \
  -R='notarized and certificate leaf[subject.OU] = "SXKCTF23Q2"' "$exe" &&
  xattr -d com.apple.quarantine "$exe"
"<selected-cc-remote>" version
```

`xattr` prints `No such xattr` when the attribute is already gone. Never pass
`-r`, name a directory, or change Gatekeeper's system policy.

The launcher passes no `--config`. It exports `CC_REMOTE_CONFIG` to cc-remote
as an absolute path. An unset variable becomes the checkout's config, and a
relative value resolves from the caller's directory. The selected config names
the repository the Sprite checks out. `WORKER_REPO`
selects only a local Orca repository; setting it on the Sprite route refuses
before an attempt or allocation. The launcher does not map a local repo ID to
a remote repository.

Supply an explicit binary/config pair like this:

```sh
WORKER_CC_REMOTE=/absolute/path/to/cc-remote \
CC_REMOTE_CONFIG=/absolute/path/to/config.yaml \
WORKER_RUN="<owned-run-id>" WORKER_REF=dev WORKER_CHECKOUT="<monorepo checkout>" \
  WORKER_CCI_URL="<cci linux_amd64 archive URL>" WORKER_CCI_SHA256="<its SHA-256>" \
  worker-launch.sh "<lane>" opus xhigh "<brief.md>"
```

The wrapper runs a preflight before `orca prepare`, with or without `--warm`:
`payload-url validate` checks the payload metadata against the inventory
without fetching the payload archives onto the Mac. A launch through another
CLI skips it. Recipe-picker enablement is separate and does not gate this
direct CLI route.

`WORKER_REF` names a remote branch or tag and passes through unchanged; when
omitted, the native config supplies its ref. `WORKER_BASE_BRANCH=origin/dev`
belongs to the explicit local worktree route. Do not pass it as the remote
branch, strip arbitrary prefixes, or replace the requested ref with a commit ID from
a Mac prelookup. The prepared record's `baseCommit` is the actual VM base. Setting
`WORKER_BASE_BRANCH` on the remote route refuses; `WORKER_REF` and
`WORKER_EXPECT_BASE` refuse on the local route.

Ordinary branch movement is valid. Set `WORKER_EXPECT_BASE` only when the task
requires an exact base. Supply a full 40- or 64-character lowercase commit ID. The
launcher compares it after prepare; a mismatch retains the idle prepared worker
and stops before `worker-start`. This comparison does not select the checkout.
Prepare has no separate exact-commit checkout option, and a branch or tag name
is still a ref when its spelling looks hexadecimal.

Preserve the selected model and effort. `WORKER_SERVICE_TIER` passes an explicit
Codex service tier unchanged; it refuses with a Claude model. The fast tier is
for incident lanes only: `WORKER_SERVICE_TIER=fast` refuses unless
`WORKER_INCIDENT=1`. Without a tier, the launcher passes no service-tier flag,
and codex runs on the standard tier.

Use `WORKER_LOCAL=1` for a local coordinator, which runs Opus, or for root-authorized
extremely sensitive work, the one case for Fable:

```sh
WORKER_RUN="<owned-run-id>" WORKER_LOCAL=1 \
  worker-launch.sh "<lane>" opus xhigh "<brief.md>"
```

An unset or empty `WORKER_LOCAL` selects Sprite; any other nonempty value
refuses. The local route starts Claude only and refuses Codex models. Remote
Fable refuses before prepare; it never turns into a local launch or a different
model. Local Claude keeps the Mac's existing interactive authentication and
task-scoped bypass behavior.

Start only the Model Context Protocol servers named in the task's allowlist;
`ccx`, `ccn`, and other CLIs need none. On Sprite, `WORKER_MCP_CONFIG` passes an
inline JSON object for Claude or an inline TOML table for Codex to prepare's
`--mcp-config`; it is not a Mac file path. On the local route, the value follows
Claude's `--mcp-config` syntax.

Remote Claude and Codex use dedicated API keys from cc-remote's configured
callbacks. Claude uses normal API environment authentication; its ordinary tool
processes may inherit those keys. For Codex, the pinned CLI excludes
`OPENAI_API_KEY`, `CODEX_API_KEY`, `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, and
`CLAUDE_CODE_OAUTH_TOKEN` from built-in shell tools. The actor and its native hooks
retain the environment needed for API calls. These exclusions provide no process
or filesystem isolation under bypass mode.

The config also hands every worker a TypeSafe key as `TYPESAFE_API_KEY`, read
from the Keychain item `spawnllm-jev-api-key`. capt-hook's `evt.decide` reads it
on an API actor. `tools/cc-remote/bin/sync-decision-keys` fills that item and
`spawnllm-openai-api-key` from SSM through the `plat-prod` and `civ2-bake`
profiles; hooks on the Mac read both items at call time. Rerun it after either
key rotates.

Keep API credentials in the remote actor and its permitted descendants.
Actor-local hook evaluators may pass selected keys to short-lived inference
children. Keep keys out of resident hook daemons, images, artifacts, logs, shared
files, and global configuration. Do not set API-key variables globally on the Mac,
replace local interactive authentication, or copy OAuth refresh tokens or whole
authentication caches to a worker.

Each brief claims `<brief-stem>.worker` beside the brief. For
`/briefs/lane-a.md`, that is `/briefs/lane-a.worker`. An existing directory,
link, failed attempt, or legacy `<brief-stem>.dispatch.json` refuses replay.
The directory records `request.json` with the requested route, model, effort,
tier, ref, optional expected base, and local brief hash/bytes.

The remote route retains the raw `allocation.json`, `prepare.err`, and
`prepare.status`. It validates the requested lane, actual workspace, requested
ref, allocation kind, task HEAD, agent, brief, environment, terminal, and
`<repoId>::<projectRoot>` worktree before extracting the task into
`prepared.json`. It then runs `worker-start` with `--on` the prepared
environment, `--worktree id:<worktreeId>`, and `--terminal` the prepared
terminal. Both routes retain `worker-start.json`, `worker-start.err`, and
`worker-start.status`. Keep these files on every outcome.

Success requires `worker-start` exit zero and a receipt reading `ok`, state
`ready`, the owned Run, and printable Task and Dispatch IDs. IDs in a failed
receipt do not make it ready. Success prints one JSON line with the fields
`state`, `task`, `dispatch`, `terminal`, and `worktree`.

On Sprite, the JSON also names `lane`, the actual `workspace`, and `allocation`
(`warm` or `created`). `terminal` is `prepared.terminal` and `worktree` is
`prepared.projectRoot`, the checkout on the VM. Use that actual workspace for
later `cc-remote orca collect` calls. A failed or unproven start prints no success JSON
and retains the attempt without retry or cleanup.

## Prepare and attach separately

Use the configured signed CLI and YAML described above when preparation and
first-task attachment need separate inspection. This explicit
fresh Sprite agents example returns a plain prepared task:

```sh
"<selected-cc-remote>" orca prepare "<lane>" --config "<configured-yaml>" \
  --provider sprites --profile agents --agent claude \
  --model claude-opus-5-5 --effort xhigh --ref "<branch-or-tag>" \
  --brief-file "<complete-brief.md>" >"<new-prepared.json>"
```

Prepare starts the selected agent and stops at native idle without sending its
task. Require command exit zero, `prepared:true`, the requested model/effort,
actual `baseCommit`, and exact brief hash and length before attachment. For a
Codex assignment, preserve its requested agent kind, model, effort, and explicit
service tier. Keep API credentials in the configured process-only callbacks.

From the coordinator that already owns the Run, attach once with the prepared
record's fields and a new receipt file:

```sh
orca orchestration worker-start --run "<owned-run-id>" --on "<environment>" \
  --worktree "id:<worktreeId>" --terminal "<terminal>" --task-title "<lane>" \
  --spec "Lane <lane>: read the whole brief at <brief.path>, verify its SHA-256 is <brief.sha256> and its size is <brief.bytes> bytes, then follow it exactly." \
  --json >"<new-receipt.json>"
```

This reuses the prepared terminal and worktree and creates the native Task with
a pointer to the complete brief. Pass no agent, model, effort, or service tier,
so the prepared process keeps its own. The ready receipt names the Task and
Dispatch. The existing inbox owner handles their messages; do not send a
second task pointer or start another consumer. On accepted completion, retain
the session and use the [source-return and collection contract](#return-source-for-root-review).
Retain receipts on every outcome. A fresh-create failure can run the normal
CLI rollback; report the actual remaining resources and never replay an
ambiguous attempt.

### First worker on a recorded workspace

Version `0.15.0` adds prepare-only `--existing` for a first worker on the exact
recorded workspace and retained checkout:

```sh
"<selected-cc-remote>" orca prepare "<recorded-workspace>" --existing \
  --config "<same-config>" --provider sprites --profile agents --agent claude \
  --model claude-opus-5-5 --effort xhigh \
  --brief-file "<complete-brief.md>" >"<new-prepared.json>"
```

Do not pass `--ref`; this mode preserves the checkout and reports its actual
base. It requires the recorded provider/profile/machine, an absent task, and no
prior Orca runtime. A busy or previously claimed task refuses before another
first launch. A completed worker is retained and reused through a new native
Dispatch under the version-matched reuse contract.

Normal resume preparation still runs and may repair changed tools or configure
services. Verify those effects before calling a measurement preinstalled. Report
one-time setup, same-identity cold-to-agent readiness, and native Task completion
separately. `--existing` preserves the retained checkout of the named workspace.
The canonical `--warm` route refreshes the requested shallow ref on an unused
pool workspace. Recipe-picker selection remains separate.

## Keep one Run owner and one inbox consumer

The launch inherits the existing native coordinator identity. A missing or
different Run binding is a refusal; do not create or rebind a Run, open another
coordinator terminal, or start another reader to make the launch pass.

The selected home-inbox owner keeps its existing cursor. Process every message
in a delivered batch before acknowledging it; a wake filter does not discard
other messages in that batch. Do not start another consuming check beside that
owner. A worker reads only its own Dispatch mailbox using the exact terminal
identity in its live preamble.

Route follow-ups through the saved home Dispatch ID:

```sh
orca orchestration send --to "dispatch:<dispatch-id>" --subject "Follow-up" --body "<guidance>" --json
orca orchestration reply --id "<original-home-question-id>" --body "<answer>" --json
```

A reply uses the original home relay question ID. Sending guidance does not
answer an `ask`, and durable enqueue does not prove consumption. For questions
and completion, match authoritative `from_handle` to `dispatch:<dispatch-id>`
and cross-check the payload Task/Dispatch IDs. Workers use their live preamble
for `ask`, status, heartbeat, checks, and `worker_done`; copied IDs or terminal
titles confer no authority. An ask timeout resumes the same message ID.

An `orca terminal send --enter` that warns no turn start was observed, with the
prompt visible on screen in the composer or under queued messages, was
delivered: never resend it; at most reissue it with `--retry-request <id>`.

## Diagnose the saved Dispatch

Start with the native receipt and the exact home Dispatch:

```sh
orca orchestration worker-show --dispatch "<dispatch-id>" --json
orca orchestration worker-read --dispatch "<dispatch-id>" --limit 50 --json
orca orchestration worker-list --run "<owned-run-id>" --include-remote --json
```

Follow returned pagination cursors. Preserve `live`, `exited`, and
`unverifiable` as distinct observations; contact loss or an empty read does not
prove a worker stopped. Keep raw receipts when input was accepted but the
outcome is unknown. Do not replay prepare/attach, type into a remote terminal,
or substitute a local launch. The existing orca-doctor assumes local terminals
and worktrees; it is not the remote default's diagnostic path.

A host running Orca's managed server installs no agent hook, so `agentWait`
stays unset for its workers and `worker-read` falls back to the terminal with
`fallbackReason: session_not_reported`. A null `agentWait` on a remote worker is
therefore no evidence that it is working. Read its rendered screen through the
server the receipt names:

```sh
orca terminal read --terminal "<handle>" --screen --environment "<server.name>" --json
```

Without `--environment` the handle reads `terminal_handle_stale`. A host that
does not answer reads `remote_runtime_unavailable`: record the worker as
unreachable, never as stopped and never as stuck. `agentWait` on a local
terminal can stay set after its prompt was answered, so it is a reason to read
the screen and never authority to answer one.

## End a done lane

The coordinator ends a local lane once it decides the lane is done: the deliverable is
on disk, the lane was stood down, or another lane superseded it. It does not
message the lane to send `worker_done` first. One call ends it:

```sh
scripts/orca-gc --run <run> --done <ctx>
```

`--done` runs `orca orchestration worker-stop` on the Dispatch, which fences it
and closes an Orca-owned agent terminal. When Orca already revoked the
Dispatch, so worker-stop reads `dispatch_inactive` and the Dispatch would read
`dispatched` forever, `--done` runs `worker-abandon` instead, which settles it
as failed. Any terminal still open is closed without waiting for an idle
prompt, and then the worktree is removed when it is clean and its HEAD is on
origin or landed. A worktree with uncommitted or unpushed work stays. Add `--dry-run` to print the plan first. A Dispatch that
already settled through `worker_done` gets the same sweep without the stop:
`orca-gc --run <run> --dispatch <ctx>` closes its idle terminal and reclaims
its worktree.

When `orca worktree rm` answers with an error such as `runtime_unavailable`
but the directory is gone, gc counts the worktree as removed. The Orca error
prints on its own `error` line, and a dropped runtime connection needs no
second gc pass.

When Orca no longer tracks the worktree, `orca worktree rm` answers
`selector_not_found` while the directory is still on disk. gc then removes it
with `git worktree remove` after the same checks, and keeps and names it when
git refuses.

A settled Dispatch that Orca records no workspace for, such as one that failed
before its worktree resolved, is kept and named on its own `keep` line. gc
makes no Orca, git, or cc-remote call for it.

Only the coordinator that coordinates the Run ends its lanes. A lane never stops
another Dispatch, and nobody uses `pkill`, signals, `run-stop`, or bulk
terminal closes. The captain-hook sessions guard allows a bare
`orca orchestration worker-stop --dispatch <ctx>` only from that root.

A settled Sprite worker frees its Sprite through the same `orca-gc` call. The
Sprite must be one whose cc-remote task record names the Dispatch's worktree
and terminal, and whose workspace record still exists. `orca-gc` runs
`cc-remote destroy` on it and removes its Orca environment. It does this only
once no unsettled Dispatch shares the workspace, the terminal reads idle twice,
and the checkout, read over ssh, is clean with its HEAD on origin or landed;
`--done` skips the idle check. A Sprite with no matching task record is left
alone.

A lane that ended has its Sprite stopped without the coordinator. Every five minutes
the lane supervisor runs `lr stop-sprites`, which picks each retired registry lane
placed on a Sprite whose Orca dispatch reads completed or failed. When the Run
lists no dispatch for the lane, its `done` record or a withdrawal must have
retired it. A lane that is live or held, or whose dispatch is unsettled, keeps its
Sprite.

For each ended lane, lr runs `cc-remote orca stop <lane>` once with the
`WORKER_CC_REMOTE` and `CC_REMOTE_CONFIG` of `runner.json`'s `orca.sprite.env`.
That stop closes the forward and stops every service, so the Sprite goes cold
with its disk kept, and it refuses a warm pool workspace's own name. The gc pause
below does not hold it. A stop that fails posts one `SPRITE-STOP-FAILED` blocker
to root with the error, and lr never runs that lane's stop again.

gc is paused until `~/.local/state/orca-gc/resumed` exists, or
`$XDG_STATE_HOME/orca-gc/resumed` when that variable is set. A paused gc prints
one `skip` line and changes nothing. Resume it with
`mkdir -p "${XDG_STATE_HOME:-$HOME/.local/state}/orca-gc" && touch "${XDG_STATE_HOME:-$HOME/.local/state}/orca-gc/resumed"`,
and pause it again with `rm "${XDG_STATE_HOME:-$HOME/.local/state}/orca-gc/resumed"`. A running pass reads
the pause before each candidate and again right before each worker-stop,
release, terminal close, worktree removal, or Sprite destroy. It names every
action it skips and lets an operation it already started finish. `--dry-run`
ignores the pause.

Until gc runs, separately authorized new work may reuse a proven existing
worker through a new native Dispatch. Use
the version-matched reuse contract and new lifecycle IDs. Never automatically
retry the old task or restart its session.

## Write the complete VM brief

The brief must stand alone inside the VM. Include the exact task and source
scope, requested branch/tag policy, constraints and owner rulings, verification,
output paths, and the source-return contract below. Do not paste a Mac-only lane
brief unchanged when it assumes local paths, local stack metadata, or worker PR
shipping. Remote workers return source; the coordinator integrates and ships it.

A standard Sprite startup does not eagerly initialize WordNet. When the task
needs it in place beforehand, copy the setup command from `AGENTS.md` under
"Remote workspaces" into the brief. Leave it out of every other brief.

Prepare copies the complete brief unchanged outside the checkout and records
its absolute VM path, SHA-256, and byte count. The launcher sends only a pointer
telling the worker to read and verify that file.
Never shorten the full brief, invent a receipt, or substitute its Mac path.

Workers read their Dispatch follow-ups at natural checkpoints and immediately
before completion. They process every row in the returned batch, acknowledge its
`deliveryId` with `check --terminal <their-handle> --ack <delivery-id>`, and then
read the next batch. `replayed:true` means the previous batch is still
unacknowledged. Later guidance stays queued until that batch is acknowledged. After
processing the queued guidance, they send one honest explicit-outcome
`worker_done` and idle.

## Return source for root review

For a source task, leave HEAD at the actual prepared `baseCommit` and source
edits uncommitted. Do not stage, commit, push, open a PR, close the coordinator task,
or clean up resources. Produce a complete patch containing every tracked and
untracked change with file modes, and a strict collection report beside the VM
brief, outside the checkout. Use regular files, not links or an archive.

The report is one JSON document with only these fields:

| Field | Value |
| --- | --- |
| `schemaVersion` | `1` |
| `baseCommit` | Actual prepared checkout commit, unchanged at completion |
| `patch` | Actual absolute patch `path`, lowercase `sha256`, and integer `bytes` |
| `files` | Every changed repository-relative `path` with its actual lowercase `sha256`, or `deleted:true` for a deletion |

Do not put extra provenance keys in this strict report. Report unrun tests and
other task facts through native status/completion. Include real modified paths
and the actual report path in `worker_done` metadata.

The coordinator collects the two named files into a new local directory:

```sh
"<selected-cc-remote>" orca collect "<workspace>" --config "<same-config>" \
  --report-file "<vm-brief-dir>/report.json" \
  --patch-file "<vm-brief-dir>/source.patch" --output "<new-local-directory>"
```

Use the same selected CLI/config as prepare. The collector checks the recorded
runtime, unchanged HEAD, report/patch hashes, and bounded status. It does not
prove patch completeness or correctness. Reconcile all status paths, including
untracked files, with the report and full patch; require no status overflow or
staged diff, and verify returned file hashes in isolated review. Apply the
accepted source in a coordinator-owned checkout and ship through normal repo tooling.

## Remote workspaces

The direct worker route above and the repository recipe flow below are separate
interfaces. Keep recipe generation, native picker settings, and global worktree
settings separate from the canonical worker default.

### Repository recipe flow

[cc-remote](https://github.com/yasyf/cc-remote) provisions the machine behind
each `orca.yaml` recipe. Follow its
[remote-workspaces skill](https://github.com/yasyf/cc-remote/blob/main/skills/remote-workspaces/SKILL.md)
for the composer flow and `cc-remote orca wait`, `verify`, and `gone`.
Run `./tools/cc-remote/bin/cc-remote` from the repo root; the wrapper uses the
version in `tools/versions.toml`. Pass `--config tools/cc-remote/config.yaml`
to Sprite commands. `tools/cc-remote/inventory.yaml` pins the captured tools
and plugins. The agents profile checks out the requested ref shallowly.

The inventory's prepare steps bake Orca's managed server into the payload. At
build time they run `orca serve` once, require it to select `orcad`, and keep
the server slot and pinned Node it materialized beside the runtime. Every Sprite
links that directory at `~/.config/orca/orcad-artifacts`, so a lane's first
`orca serve` downloads and copies nothing.

The inventory's `strictKnownMarketplaces` managed setting keeps each plugin
marketplace at the source the inventory registers. This repository's
`.claude/settings.json` declares `captain-hook` with no ref, and without the
allowlist a session start replaces the pinned checkout with that marketplace's
default branch, so the plugin runs ahead of the pinned Captain Hook host. A
marketplace the repository newly declares loads on a Sprite only after the
allowlist names its source.

The Sprite agents profile downloads two private S3 objects: the tools payload
and its resident package archive. `payload-url` checks each source against
`agents-payload.json` and verifies the inventory hash before obtaining a
900-second URL through `tools/artifact.sh`. The VM downloads each archive
and verifies its full SHA-256 and byte count; mounting also checks the tool
fingerprint. The wrapper preserves the caller's stdin and the CLI's JSON
stdout. Diagnostics go to stderr.

Sign in with the existing team AWS profile:

```sh
AWS_CONFIG_FILE=tools/artifact-cache.aws-config aws sso login --profile core-auto
```

The URL broker tries your signed-in `core-auto` profile, then the committed
read-only profile. It presigns the pinned seeds directly, and
`tools/artifact.sh fetch cc-remote-agents-tools` builds the pair from those
seeds on a store miss; no CI step produces it. When the captured tools, the
cc-remote pin, or the Sprites base change, move the pin and inventory first,
then build and publish from this Mac. The build uses your local `sprite` login
and `gh auth token`, the same credentials every create uses, and no CI job or
SSM parameter holds either:

```sh
out="$(mktemp -d "$HOME/.cache/cc-remote-payload.XXXXXX")"
./tools/cc-remote/bin/cc-remote payload build --config tools/cc-remote/config.yaml --profile agents \
  --out "$out/tools.sqfs" --packages-out "$out/packages.tar" >"$out/build.json"
version="$(./tools/cc-remote/bin/cc-remote version)"
curl -fsSL -o "$out/cc-remote-bootstrap-linux-amd64.tar.gz" \
  "https://github.com/yasyf/cc-remote/releases/download/v$version/cc-remote_${version}_linux_amd64.tar.gz"
store() {
  aws s3api put-object --profile civ2-bake --bucket frg-ci-cache \
    --key "cache-seeds/cc-remote-agents-tools/sources/$(shasum -a 256 "$out/$1" | cut -d' ' -f1)/$1" \
    --body "$out/$1" --if-none-match '*' --checksum-algorithm SHA256
}
store tools.sqfs
store packages.tar
store cc-remote-bootstrap-linux-amd64.tar.gz
```

A 412 answer means that seed is already stored. Copy the digests and sizes
from `build.json` and the helper archive into `agents-payload.json` and
`config.yaml`. Then run `./tools/cc-remote/payload-url <member> <sha256> <bytes>`
for `tools`, `packages`, and `bootstrap-helper`, and fetch one byte from each
URL it prints. Open the pin PR only after all three answer 206: CI reads no
seed, so a pin whose seeds are missing passes CI and breaks every Sprite create.

After editing the Sprite config, regenerate its recipe:

```sh
./tools/cc-remote/bin/cc-remote orca recipes --binary ./tools/cc-remote/bin/cc-remote --config tools/cc-remote/config.yaml --orca-yaml orca.yaml
```

Keep the generated name, description, and lifecycle commands intact:
`orca wait` compares the complete entry. The `(default)` badge identifies
that config's default provider/profile; it does not select a global Orca
default. Pass `--binary ./tools/cc-remote/bin/cc-remote` and
`--config tools/cc-remote/config.yaml` when running `orca wait`.

Namespace uses regular Compute through `tools/cc-remote/namespace.yaml`, which
reads the same `tools/cc-remote/inventory.yaml` as the Sprite config. A
Namespace machine takes its tools from an image baked from that inventory, one
image per profile. After an inventory change, rebuild both images and put each
printed digest reference in that profile's `machine.namespace.image`:

```sh
./tools/cc-remote/bin/cc-remote images build --config tools/cc-remote/namespace.yaml --profile agents
./tools/cc-remote/bin/cc-remote images build --config tools/cc-remote/namespace.yaml --profile stack
```

Create refuses a machine whose image was baked from a different inventory.
Running workspaces and finite automatic lease renewal have been verified.
Suspend/Wake remains unsupported at the tested US endpoint. The repository
recipe remains Sprite; native picker enablement stays separate.

## Layout

A prepared VM checkout is the worker's source workspace, not the coordinator's local
worktree. Give each editing lane its own checkout. Keep the brief, report,
patch, and native attempt receipts outside source checkouts.

Local Orca lane worktrees use
`~/.orca/workspaces/monorepo/monorepo/<name>`. The coordinator integrates accepted
remote patches in an isolated local branch and one native GitHub stack, using
`ccx vcs stack new` and [submit-pr](https://github.com/Forge-AI/monorepo/blob/dev/.agents/skills/submit-pr/SKILL.md). The coordinator owns PR
registration, pushes, and queue decisions; the remote source worker does not
inherit a Mac lane's shipping obligations.

## Models

Root orchestrators and Claude implementation workers run Opus; subdesks use
Opus or Sonnet per the routing table. Codex review, diagnosis, sweeps and
shell-heavy lanes run `gpt-6.1-sol` at `xhigh` on the standard tier; only an
incident lane adds `WORKER_SERVICE_TIER=fast` with `WORKER_INCIDENT=1`. Fable is
for exceptional cases only: local, for the most sensitive implementation, using
the Mac's existing interactive authentication. Auth, migrations, concurrency, or
error-prone code alone does not qualify for Fable. Preserve explicitly requested
models and effort within these roles. Never use Fable as a general fallback or
set `fallbackModel`.

| Alias | Model ID | Placement |
| --- | --- | --- |
| `opus` | `claude-opus-5-5` | Root orchestrators and the Claude implementation default |
| `sonnet` | `claude-sonnet-5-5` | Explicitly selected Claude worker |
| `fable` | `claude-fable-5-1` | Exceptional: explicit local, most sensitive implementation only |
| `gpt-6.1-sol` | `gpt-6.1-sol` | Codex review, diagnosis, sweeps and shell-heavy lanes on the standard tier; incident lanes add the fast tier |

On the remote route, full `claude-*` and `gpt-*` model IDs pass through unchanged
to Claude and Codex respectively; other prefixes refuse. The local route accepts
Claude only. Preserve requested effort and Codex service tier; the coordinator's model
does not select a worker's model. The launcher passes effort through unchanged.
