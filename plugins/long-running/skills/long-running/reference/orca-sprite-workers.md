# Orca Sprite workers

The drive's variant of the `orca` plugin's `orca` skill. This pack depends on that plugin, which carries `orca-worker-launch.sh`, `orca-reclaim.py` and the `orca-doctor` agent. Drive changes land here. The tools are `lr launch worker`, `lr gc` and `scripts/coordinator-waiter.sh`, which is on PATH through the plugin's `bin/`.

Load the version-matched guides first: `orca skills get orca-cli` and `orca skills get orchestration`. This file defines the repository's worker entrypoint and source-return contract.

## Contents

- [Launch a worker](#launch-a-worker)
- [Choose the CLI, config, ref, tier and route](#choose-the-cli-config-ref-tier-and-route)
- [Keep API credentials out of the wrong places](#keep-api-credentials-out-of-the-wrong-places)
- [Read a launch's receipts](#read-a-launchs-receipts)
- [Keep Sprites current](#keep-sprites-current)
- [Keep a Sprite awake while its agent runs](#keep-a-sprite-awake-while-its-agent-runs)
- [Read Buildkite from a Sprite](#read-buildkite-from-a-sprite)
- [Read the merge queue from a Sprite](#read-the-merge-queue-from-a-sprite)
- [Prepare and attach separately](#prepare-and-attach-separately)
- [Keep one Run owner and one inbox consumer](#keep-one-run-owner-and-one-inbox-consumer)
- [Diagnose the saved Dispatch](#diagnose-the-saved-dispatch)
- [Write the complete VM brief](#write-the-complete-vm-brief)
- [Return source for coordinator review](#return-source-for-coordinator-review)
- [End a done lane](#end-a-done-lane)
- [Remote workspaces](#remote-workspaces)
- [Layout](#layout)
- [Models](#models)

## Launch a worker

Implementation and test workers default to an unused preinstalled Sprite agents workspace. In one invocation the launcher claims it exclusively, refreshes the requested ref shallowly, prepares the agent, and attaches the native Task. With no compatible unused workspace it creates the lane's workspace.

- Each allocation starts a finite background fill toward `orca.pool.ready`, which `tools/cc-remote/config.yaml` sets to two unused workspaces, so two launches in a row both claim warm.
- A claimed worker stays claimed after completion. Reusing a retained agent needs an explicit new native Dispatch.
- A Sprite lane name has at most 55 lowercase letters, digits and dashes, and starts with a letter or digit.

```sh
WORKER_RUN="<owned-run-id>" WORKER_REF=dev WORKER_CHECKOUT="<monorepo checkout>" \
  WORKER_CCI_URL="<cci linux_amd64 archive URL>" WORKER_CCI_SHA256="<its SHA-256>" \
  lr launch worker "<lane>" "<model>" "<effort>" "<brief.md>"
```

Run it from the terminal that already owns the named native Run. The script checks that binding before allocation, calls `cc-remote orca prepare --warm` once, then starts the prepared idle worker with one native `worker-start` on the Sprite's Orca server, reusing the prepared terminal and worktree. Prepare copies the complete brief to the VM and records its hash, length and actual checkout HEAD. The first task is a pointer telling the worker to read and verify that file.

### What the launch does, in order

1. **Carry.** `lr carry` runs on the brief before prepare. It copies each file or directory the brief names whose real path, after symlinks, lies under the drive's state dir (`$DRIVE_DIR`), `~/.local/state/lr-scratch`, `~/.claude/scratch`, `~/.vcache/scratch`, `~/.claude/plans`, `~/handoffs` or `~/.claude/plugins/cache`. A path anywhere else, such as `~/.claude.json` or `~/.config/cc-slack/drive.toml`, stays behind, and the launch names it. A Sprite installs its own `~/.local/bin`, `~/.claude/CLAUDE.md` and `~/.claude/long-running`, so none of them travels. It also copies each `ccn doc show <id>`, `ccn plan show <id>` or `ccn task show <id>` it names, and each cci record it cites, into `<brief>.worker/carried`. A cited record is `cci show <seq>` (any flags, one or more seqs), `record <seq>`, `<seq>@<origin>`, or a bare seq of five or more digits that follows a lane name when that record's lane or addressee is that lane. The Mac reads each one with `cci show --drive <the drive's cci drive>`, which covers records no Sprite's store or the relay holds, such as a dotted `lr.*` kind the owner node keeps local; the copy lands as `cci/record-<seq>.md` and the cited form is rewritten to its path. Entities render from the checkout `WORKER_NOTES_REPO` names, else `WORKER_CHECKOUT`. The brief that prepare copies names those copies under `/home/sprite/drive/carried`, where they land before `worker-start`. A carried file named `brief.md` or `*.brief.md` is followed too: a `brief.md` brings its whole directory, and what that brief names travels with its references rewritten. What cannot travel from a followed brief stays behind and is logged; what cannot travel from the launched brief stays behind with the warning below.
2. **Check the brief's CLIs.** The box must run every drive CLI the carried brief invokes: `ccn`, `cc-notes`, `cci`, `lr`, `gh` and `ccx`. A CLI counts when the brief names it before a space and a lowercase verb.
3. **Install before the agent starts.** `cc-remote orca prepare --before-worker-file` runs the install, so the first Claude session loads the pinned hooks, skills and agents. It installs cci, the pack's `lr` and every tool pin, as [Keep Sprites current](#keep-sprites-current) describes.
4. **Fetch notes.** The lane's `ccn` reads the owner's answers from `refs/cc-notes/*` in the Sprite's checkout. The Mac bundles its own `refs/cc-notes/*` with `git bundle create`, uploads the bundle with `sprite exec --file`, and the checkout fetches every ref from it. `ccn sync --prefix refs/cc-notes/` then fetches only what origin holds past the bundle, where a fetch from scratch took 8 to 12 minutes on the monorepo. The same step runs `git remote set-head origin --auto`, since a Sprite clone has no `origin/HEAD` and `ccx vcs ship` diffs against it. A Sprite holds no GitHub credential, so the Mac's `gh auth token` goes over stdin into the one shell that runs the set-head and `ccn sync`, and is written nowhere. A failed bundle, set-head or fetch keeps its output in `notes.log` and the worker still starts.
5. **Write the drive entry.** For a lane inside a drive, the launch writes `~/.claude/long-running/drives/<drive>.json` on the Sprite, so the pack's hooks that read the drive entry act there as they do on the Mac. It copies the drive, bus, program, ledger, repo, coordinator sessions and parent from this Mac's entry, and sets `host` to `sprite`. `checkout` is the Sprite's checkout, `git_common_dir` is read from that checkout on the box, and `state_dir` is a Sprite-local `~/.claude/scratch/<drive>`. A standing desk, a brief whose `ccx:` line says `role=desk` or `role=watch`, also gets the drive's `lr.json` in that directory with `checkout` set to the Sprite's, so `lr board` and `lr approved` read the drive there as they do on the Mac; a desk launch refuses before anything is prepared when this Mac has no drive entry for it or cannot read that `lr.json` as a JSON object, and refuses before `worker-start` when the copy fails to land, keeping its output in `drive-config.log`. No Sprite gets the drive's `lr.sqlite`, which only the Mac's supervisor writes: the entry step leaves `lr.sqlite.on-mac` in `state_dir`, so `lr lanes`, the board's rows and cards, and every other read of the drive's store there refuse, naming the Mac and asking its coordinator over cci, rather than open an empty registry. Every hook that reads the drive's store warns and passes, except the hand-landing hook, which refuses a monorepo hand landing it cannot check for the owner's tap. The worker's first prompt only points at its brief, so the hooks read the brief that prompt names. `lr request` posts its record from there with no registry check of its own, and the supervisor on the Mac runs the check. No credential, Orca run, terminal or Mac path is written. A failed write keeps its output in `drive-entry.log`, warns, and the worker still starts without the drive's hooks.
6. **Join the relay**, as below.
7. **Start the worker.** First the launch runs the checkout's `tools/cc-remote/before-worker.sh` from the checkout when it is executable: the monorepo's installs the build tools its `mise.lock` pins and the SandSQL stub bindings. A failure warns, keeps its output in `repo-setup.log`, and the worker still starts. After `worker-start` succeeds, the launch creates the `worker-awake` service, as [Keep a Sprite awake](#keep-a-sprite-awake-while-its-agent-runs) describes.
8. **Copy the store, desks only.** A standing desk's Sprite then gets the drive's cci records: the Mac writes `cci relay export` to `store-seed.gz` in the attempt directory, uploads it to `/tmp` on the Sprite with `sprite exec --file`, and starts a `store-seed` service there that runs `cci relay seed`, removes the file and deletes itself; the Mac's side logs to `store-seed.log`. It runs detached because a whole drive applies at a few hundred rows a second, which held the first in-line copy past Orca's `worker-start` timeout. The export follows the relay join, so the copy plus the relay's queue hold every record: the desk's spec says never to run `cci relay sync`, which costs one relay read per origin, and to check on the Mac before reporting a record absent while `sprite-env services list` still shows `store-seed`. cci 0.52.0 keeps a record the copy applies out of the desk's mail when it is stamped before the desk started. Any other lane gets no copy: `cci show <seq>` reads a record its store lacks from the relay's log, and the relay brings every record posted after the join.

**Orca server.** Prepare starts the Sprite's Orca runtime through its `orca` CLI, and Orca 1.4.224 serves it on its managed server, `orcad`. The prepared task records that server's build as `managedServer`, read from the ready event `orca serve --json` prints. A missing field means Orca fell back to Electron, and the script starts nothing; Orca's reason is the `[serve]` line in `~/.cc-remote/orca/serve.log` on the Sprite.

**Publishing notes.** With no credential on the box, a lane's `ccn` writes stay on its Sprite until the supervisor on the Mac publishes them, and its background sync cannot help. Every 30 s, on its own check and never on the tick, the supervisor runs `ccn sync --prefix` on the Sprite of each live lane that posted a record the tick has read, for each ref holding a commit its origin tracking copy lacks, with the token on stdin. A lane being retired is published on the tick before it retires, and one lane's Sprite gets one publish at a time. It costs one `sprite exec` per lane and no process stays on the box. A whole-namespace `ccn sync` is not used: on a Sprite that has drifted behind origin it can run past five minutes or fail on the authority lock.

- Each pass spends at most 60 s starting `ccn sync --prefix` runs and waits at most 5 s on the Sprite's authority lock. The first `ccn` on a checkout imports its records into the managed authority, which took 153 s on the monorepo's 17,935 notes refs and keeps running on the Sprite after `ccn` returns. A pass that finds `ccn` importing or busy, or that is out of time, stops and posts no blocker. The supervisor retries that lane on each 30 s check until a pass finishes, so a handoff in the lane's last record still reaches origin.
- A failed sync posts one blocker to the lane and its coordinator, naming the Sprite and the first line of the error, never the script. It clears on the next clean sync.
- A note a local default keeps off origin, such as one with a `lane-brief` label or a `*.log` attachment, counts as a failure. Publish it with `ccn local unmark <id>`, or keep it deliberately with `ccn local mark <id>`.
- Before a lane retires, the supervisor syncs once more. A failure holds the lane and posts a blocker, as an owed deletion does.
- A stand-down by order retires the lane anyway and posts the blocker, since the order wins. A Sprite no longer listed retires the lane with a blocker saying its unpublished notes are lost.

**Relay join.** `WORKER_RELAY_SPONSOR` names a Sprite that holds an enrolled cci service key. The script joins the new Sprite to this Mac's default relay mesh for the mesh's routed drives, with a 24-hour delegation and this Mac's node as owner. It starts `cci supervise` as a Sprite service, so the worker's records push without a manual `cci relay push`. The key travels from the sponsor to the new Sprite over stdin and is never written to the Mac.

- Without the join, a worker's cci records stay on its Sprite, so a launch with no sponsor refuses before it prepares anything.
- A failed join retries after 5, 15 and 45 seconds; a join that still fails refuses the launch, naming the relay's reason, with no worker started.
- Every five minutes the supervisor joins each live lane whose receipt shows a failed join, stopping at the first refusal. `lr rejoin` runs the same pass at once.

### What refuses the launch

A path the brief's own text names that does not exist, lies outside those directories or is over 64 MiB, or a `ccn` entity that does not render, warns and the lane starts without it. A credential such as `~/.ssh`, `~/.aws` or `~/.iris/credentials.json` lies outside them, so a brief that names one, even to forbid it, launches without it: the caller reads a `WARN` line and a stage. Register rows the launch quoted between `Register slice:` and `slice-hash:` are not carried or checked.

Each refusal names its cause, and nothing is prepared unless noted.

| Cause | Refusal |
| --- | --- |
| A drive CLI the brief invokes is missing on the box | Names each missing CLI; output in `prepare.err`; no lane starts on a box that cannot run its brief |
| `bin/lr.binrun` pins no sha256 tar.gz GitHub release for `linux-x86_64` | Refuses before prepare |
| A malformed tool pin | Refuses before prepare |
| A pin off its digest or version | `prepare` fails; no worker starts |
| Any step of the tool install fails | Names the step, its exit and what it printed, for a launch, a roll and a grader; `prepare.err` keeps the rest |
| No `WORKER_RELAY_SPONSOR` | Refuses before preparing anything |
| A standing desk with no drive entry on this Mac, or whose drive's `lr.json` does not read as a JSON object | Refuses before preparing anything, naming the missing entry or file |
| A standing desk whose `lr.json` fails to land on its Sprite | Refuses before `worker-start`; output in `drive-config.log` |
| A relay join that fails after its retries | Refuses, naming the relay's reason; no worker started |
| Any failure after `prepare` and before `worker-start` | Runs `cc-remote orca stop <lane>`, so the prepared worker frees its Sprite and the lane can launch again; `stop.status` in the attempt directory records it, and the desk reads a launch whose stop exited 0 as one that never started a worker |
| mise cannot run the pinned `cc-remote` | Refuses before allocation, printing mise's own message |
| Selected CLI's `version` differs from `bootstrapHelper.version` | Refuses before allocation, naming both |
| `WORKER_REPO` set on the Sprite route | Refuses before an attempt or allocation |
| `WORKER_BASE_BRANCH` set on the remote route | Refuses |
| `WORKER_REF` or `WORKER_EXPECT_BASE` set on the local route | Refuses |
| `WORKER_SERVICE_TIER` with a Claude model | Refuses |
| `WORKER_SERVICE_TIER=fast` without `WORKER_INCIDENT=1` | Refuses |
| A nonempty `WORKER_LOCAL` other than `1` | Refuses |
| A Codex model on the local route | Refuses |
| Remote Fable | Refuses before prepare; it never turns into a local launch or a different model |
| A model prefix other than `claude-*` or `gpt-*` on the remote route | Refuses |
| An existing `<brief-stem>.worker` directory, link, failed attempt or legacy `<brief-stem>.dispatch.json` | Refuses replay |

A failed cci install leaves the prepared worker idle; `install.log` in the attempt directory keeps its output.

## Choose the CLI, config, ref, tier and route

### CLI and config

With `WORKER_CC_REMOTE` and `CC_REMOTE_CONFIG` unset, the launcher takes both from its own checkout. It finds that checkout from the script's path, never from PATH or the caller's directory.

| Unset variable | The launcher uses |
| --- | --- |
| `WORKER_CC_REMOTE` | `tools/cc-remote/bin/cc-remote`, the wrapper that runs the release `tools/versions.toml` pins through `mise x` |
| `CC_REMOTE_CONFIG` | `tools/cc-remote/config.yaml` |

A long-lived session's PATH, a stale checkout elsewhere, and a newer Homebrew install do not change that choice. The first launch after a pin change installs the pinned release.

The launcher passes no `--config`. It exports `CC_REMOTE_CONFIG` to cc-remote as an absolute path: an unset variable becomes the checkout's config, and a relative value resolves from the caller's directory. The selected config names the repository the Sprite checks out.

**One pin change.** cc-remote prepares a Sprite only when the CLI is the exact release of the config's `orca.bootstrap_helper`; a newer patch release fails like an older one. So the CLI pin in `tools/versions.toml` and `mise.toml`, the helper in `config.yaml` and `bootstrapHelper` in `agents-payload.json` move in one pin change. The launcher compares the `version` the selected CLI prints with `bootstrapHelper.version` before allocation. cc-remote makes the same check, but a warm claim reaches it after the spare is claimed, and that spare then stays claimed as failed.

**Override.** `WORKER_CC_REMOTE` selects another CLI by path or PATH name, and `CC_REMOTE_CONFIG` selects another config file. Set them for a deliberate pair, such as a runner that keeps one release while its checkout moves.

- Against the checkout's config, a CLI override still has to match the helper pin.
- Under a config from outside the checkout, the launcher compares nothing and cc-remote's own check decides.

```sh
WORKER_CC_REMOTE=/absolute/path/to/cc-remote \
CC_REMOTE_CONFIG=/absolute/path/to/config.yaml \
WORKER_RUN="<owned-run-id>" WORKER_REF=dev WORKER_CHECKOUT="<monorepo checkout>" \
  WORKER_CCI_URL="<cci linux_amd64 archive URL>" WORKER_CCI_SHA256="<its SHA-256>" \
  lr launch worker "<lane>" opus high "<brief.md>"
```

The wrapper runs a preflight before `orca prepare`, with or without `--warm`: `payload-url validate` checks the payload metadata against the inventory without fetching the payload archives onto the Mac. A launch through another CLI skips it. Recipe-picker enablement is separate and does not gate this direct CLI route.

**Freshly installed release stalls on the Mac.** Recover it yourself; this is an installation recovery, never a question for the owner. Verify that the selected executable is the signed release, remove `com.apple.quarantine` from that one file, and retry its version command. For the wrapper, the executable is `cc-remote` inside the directory `mise where cc-remote@<pinned-version>` prints.

```sh
exe="$(readlink -f "<selected-executable>")"
codesign --verify --strict --check-notarization --verbose=2 \
  -R='notarized and certificate leaf[subject.OU] = "SXKCTF23Q2"' "$exe" &&
  xattr -d com.apple.quarantine "$exe"
"<selected-cc-remote>" version
```

`xattr` prints `No such xattr` when the attribute is already gone. Never pass `-r`, name a directory, or change Gatekeeper's system policy.

### Ref and base

- `WORKER_REF` names a remote branch or tag and passes through unchanged. When omitted, the native config supplies its ref.
- `WORKER_BASE_BRANCH=origin/dev` belongs to the explicit local worktree route. Do not pass it as the remote branch, strip arbitrary prefixes, or replace the requested ref with a commit ID from a Mac prelookup. The prepared record's `baseCommit` is the actual VM base.
- Ordinary branch movement is valid. Set `WORKER_EXPECT_BASE` only when the task requires an exact base: a full 40- or 64-character lowercase commit ID. The launcher compares it after prepare; a mismatch stops the lane's prepared worker with `cc-remote orca stop` before `worker-start`. It does not select the checkout. Prepare has no exact-commit checkout option, and a branch or tag name is still a ref when its spelling looks hexadecimal.
- `WORKER_REPO` selects a local Orca repository, and the local route requires it. The launcher does not map a local repo ID to a remote repository.

### Model, effort and tier

- Preserve the selected model and effort. The launcher passes effort through unchanged, and the coordinator's model does not select a worker's model.
- `WORKER_SERVICE_TIER` passes an explicit Codex service tier unchanged. The fast tier is for incident lanes only. Without a tier, the launcher passes no service-tier flag and codex runs on the standard tier.
- On the remote route, full `claude-*` and `gpt-*` model IDs pass through unchanged to Claude and Codex respectively.

### Local route

Use `WORKER_LOCAL=1` for a local coordinator, which runs Fable, or for root-authorized extremely sensitive work, the other case for Fable:

```sh
WORKER_RUN="<owned-run-id>" WORKER_LOCAL=1 \
  lr launch worker "<lane>" opus high "<brief.md>"
```

An unset or empty `WORKER_LOCAL` selects Sprite. The local route starts Claude only. Local Claude keeps the Mac's existing interactive authentication and task-scoped bypass behavior.

### MCP servers

Start only the Model Context Protocol servers named in the task's allowlist; `ccx`, `ccn` and other CLIs need none. On Sprite, `WORKER_MCP_CONFIG` passes an inline JSON object for Claude or an inline TOML table for Codex to prepare's `--mcp-config`; it is not a Mac file path. On the local route, the value follows Claude's `--mcp-config` syntax.

## Keep API credentials out of the wrong places

- Remote Claude and Codex use dedicated API keys from cc-remote's configured callbacks. Claude uses normal API environment authentication, and its ordinary tool processes may inherit those keys.
- For Codex, the pinned CLI excludes `OPENAI_API_KEY`, `CODEX_API_KEY`, `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` and `CLAUDE_CODE_OAUTH_TOKEN` from built-in shell tools. The actor and its native hooks retain the environment needed for API calls. These exclusions give no process or filesystem isolation under bypass mode.
- Keep API credentials in the remote actor and its permitted descendants. Actor-local hook evaluators may pass selected keys to short-lived inference children.
- Keep keys out of resident hook daemons, images, artifacts, logs, shared files, and global configuration.
- Do not set API-key variables globally on the Mac, replace local interactive authentication, or copy OAuth refresh tokens or whole authentication caches to a worker.

**TypeSafe key.** The config hands every worker a TypeSafe key as `TYPESAFE_API_KEY`, read from the Keychain item `spawnllm-jev-api-key`. capt-hook's `evt.decide` reads it on an API actor. `tools/cc-remote/bin/sync-decision-keys` fills that item and `spawnllm-openai-api-key` from SSM through the `plat-prod` and `civ2-bake` profiles; hooks on the Mac read both items at call time. Rerun it after either key rotates.

Inject each dedicated key only into its remote worker process, and keep keys out of images, artifacts, logs and shared configuration. Never share OAuth refresh tokens or authentication caches. Keep the Mac on its interactive authentication; never export these keys globally or replace its sign-in. The remote Claude key does not support Fable, so Fable lanes are assigned to the Mac before launch; automatic rerouting does not exist.

## Read a launch's receipts

Each brief claims `<brief-stem>.worker` beside the brief. For `/briefs/lane-a.md`, that is `/briefs/lane-a.worker`. The directory records `request.json` with the requested route, model, effort, tier, ref, optional expected base, and local brief hash and bytes.

- The remote route retains the raw `allocation.json`, `prepare.err` and `prepare.status`. It validates the requested lane, actual workspace, requested ref, allocation kind, task HEAD, agent, brief, environment, terminal and `<repoId>::<projectRoot>` worktree before extracting the task into `prepared.json`. It then runs `worker-start` with `--on` the prepared environment, `--worktree id:<worktreeId>`, and `--terminal` the prepared terminal.
- Both routes retain `worker-start.json`, `worker-start.err` and `worker-start.status`.
- Keep these files on every outcome.

**Success** requires `worker-start` exit zero and a receipt reading `ok`, state `ready`, the owned Run, and printable Task and Dispatch IDs. IDs in a failed receipt do not make it ready. Success prints one JSON line with the fields `state`, `task`, `dispatch`, `terminal` and `worktree`.

On Sprite, the JSON also names `lane`, the actual `workspace` and `allocation` (`warm` or `created`). `terminal` is `prepared.terminal` and `worktree` is `prepared.projectRoot`, the checkout on the VM. Use that actual workspace for later `cc-remote orca collect` calls.

**Retry.** When Orca answers `worker-start` with `runtime_timeout`, `runtime_unavailable`, `remote_runtime_unavailable` or `invalid_runtime_response` and names an `orchestrationRequestId`, the script reissues the same command with `--retry-request <id>` after 5, 15, then 45 seconds, so Orca replays or joins that one request instead of starting a second worker. A failed or unproven start prints no success JSON, names its tries when it made more than one, and retains the attempt without cleanup.

## Keep Sprites current

### Tool pins

The launch installs into the Sprite, before `worker-start`:

- **cci**, from `WORKER_CCI_URL`, checked against `WORKER_CCI_SHA256`.
- **`lr`**: the `linux-x86_64` release that this pack's `bin/lr.binrun` pins, into `~/.local/bin`, checked against that file's SHA-256, so a Sprite lane runs the `lr` the launching drive runs.
- **Each pin in `WORKER_TOOLS`**, which the desk fills from `runner.json`'s `orca.sprite.tools`, so a new Sprite runs the tools the Mac runs without a payload rebuild.
- **A mise setting** that trusts every config under the Sprite's home, `~/.config/mise/conf.d/cc-remote-trust.toml`, so a hook that runs through mise works in a fresh checkout.

A pin is `{kind, name, url, sha256, version}`. Each archive is checked against its SHA-256 before anything moves.

| Kind | Install |
| --- | --- |
| `binary` | Extract the member `name`, at the top of the archive or in a directory below it, to `~/.local/bin/<name>.new`, run it with `--version`, and move it over `~/.local/bin/<name>` only when the last version-shaped word printed, less a leading `v`, is the pinned version |
| `raw` | The download itself is the executable, for a tool whose release ships a bare binary, such as `ccn` |
| `package` | Such as `capt-hookd`. Stage the member, check that `version` reports the pinned `build`, run its `package-install` (which upgrades a live daemon under its own install lock), and check the installed `version` again |
| `plugin` | Such as `long-running@skills`. Unpack into Claude Code's plugin cache at `<marketplace>/<plugin>/<version>.new`, check that tree's `.claude-plugin/plugin.json` version, move it into place, and point that plugin's `installed_plugins.json` entries at it |

Plugin pins:

- The pack's archive is the `long-running_<version>.tar.gz` asset the `long-running pack release` workflow publishes, with `checksums.txt`, on each `long-running--v<version>` tag.
- A `plugin` pin for a plugin the payload lacks, such as `cc-inbox@cc-inbox` from its `cc-inbox_<version>.tar.gz` asset, also adds its entry: a directory marketplace under `~/.local/share/cc-remote/marketplaces/`, the one location the Sprites' managed settings allow, and its `enabledPlugins` key.
- A marketplace the payload holds, such as `skills`, is read-only, so the install replaces its link with a writable copy first. A link fails because Claude refuses a plugin source that leaves its marketplace directory.

The script prints one `installed <name> <version>` line per tool, cci and lr included. `prepare` returns them in the task record, the launch keeps them in `install.log`, and the desk records them for the Sprite when it settles the launch. `lr sprites` prints, from one store read, each Sprite's tools and versions as its last launch or roll installed them, or one line saying none is recorded.

### `lr sprites pin`

`lr sprites pin` moves each pin to the version the Mac runs, read from `installed_plugins.json`, so no one edits the versions by hand.

- A `plugin` pin tracks itself. Any other pin names the plugin it tracks in `follows`, such as `"follows": "cc-context@cc-context"` on `ccx`; a pin without `follows` stays where it is.
- The new URL is the old one with its version replaced.
- The pin moves only after that asset's SHA-256 matches its line in the release's `checksums.txt` or `SHA256SUMS.txt`. `runner.json` is replaced by rename with every other key kept.
- When the Mac's newest version has no release yet, the pin takes the newest version in the Mac's plugin cache that has one, because the pack's release follows its install by a minute or two. A version marked bad is passed over.
- An asset off its checksum stops that pin for the pass and refuses with exit 1; the other pins still move.
- `lr install` runs the same step after its swap. Live Sprites move only when someone runs `lr sprites roll`.

### `lr sprites roll`

`lr sprites roll` moves the Sprites already running onto the same pins. It runs the launch's install, with no brief check, on each Sprite that hosts a live worker of the drive's Run: `--probe <sprite>` first, then the grader Sprites, then the rest in name order, one at a time. It finds a Sprite through the cc-remote task record of each live worker, keyed by worktree and terminal, so a worker on the Mac is never a box. Each Sprite `runner.json` lists in `finder.sprite` or `fidelity.sprite` is a box too, live worker or not: it takes cci, lr and the ccx, ccn and codex-ask pins, the set `lr sprites grader` installs, unless it also hosts a live worker, which takes the whole list.

- Probe a new binary or pin on one Sprite before rolling it to the fleet: run with `--probe`, and `--probe-only` stops after that Sprite so someone can check a real hook there before running the rest. A truncated download rolled straight to every box breaks them all.
- A failure on a pin no earlier box in the roll installed stops the roll, so every later Sprite keeps what it runs, and the pin that failed is marked bad in the drive's store. That first box is the pin's probe: `--probe` when given, else the first box to take it. Only proof about the pin marks it bad: a `refused:` line from the install, a `curl: (22)` naming a 4xx status, or a member missing from the archive; a 5xx is the server's, so it marks nothing bad. Any other failure there stops the roll and marks nothing. One install runs on a box at a time, so a second roll reaching that box waits. A roll whose pins include a version marked bad refuses before it touches any Sprite.
- A failure on a pin an earlier box installed is that box's fault: the roll goes on, tries the box once more after the last one, and ends by naming each box that failed twice, its last error line and the versions it holds, then exits non-zero. Three boxes in a row failing with the same error line stop the roll as a likely pin fault, marking nothing bad; a box that installs between them resets the count. The desk host is never a pin's probe, so a failure there always counts as the box's.
- `--sprite <name>` installs the desk host's pins on that one Sprite alone, the way the roll installs them on `host.sprite`. Use it for a box neither `host.sprite` nor a live worker's, such as a standby landing host. It does not lay out the rest of a host (state dir, gh wrapper, `run.sh`, relay join, record seed), and it takes no `--probe`, `--probe-only` or `--skip`.
- `--skip <sprite>`, repeated or comma-separated, leaves a box alone; the roll names the skipped boxes and never counts them as failures. A `--probe` that `--skip` also names rolls nothing.
- A box `sprite exec` cannot reach, or that times out having installed nothing, is passed over, and the roll ends by naming every such box. A `--probe` it cannot reach stops the roll, since nothing proved the pins.
- A roll does not wait for a lane to go idle. A binary is replaced by rename, so a running process keeps its old file and a new one starts the old or the new, never a part. A grader's `lr grade` runs from the pack copy its job ships, which the roll leaves alone. A plugin's old tree stays where the session loaded it. A lane in mid-turn sees the new hooks at its next hook call, and the new skills only in its next session.
- On a box with a codex-ask service, the install points that service's recipe at a copy of the new binary, `~/.local/share/cc-remote/tools/codex-ask-<version>/codex-ask`, and waits up to 60 seconds to see it running. `supervise.py` swaps on the recipe change, so `sprite-env services restart` is not used and would only rerun the old recipe. `lr sprites` then shows `codex-ask-supervise=<version>`. A running pass or Codex lane is not touched: its worker runs outside the service, and dispatch, `--await` and `--cancel` read the lane's files. A Claude lane parked on the channel `await` tool, if its box has a daemon running, gets one error at the swap while its channel process stays up. A service that never starts stops the roll and marks no pin bad. Other tools' services (`cc-present`, `capt-hookd`) stay as they were.
- On a box with a `cci supervise` service, the install then runs the new `cci serve`, which has that supervisor drain the cci daemon and start the installed build, and prints `installed cci-daemon <version>`, read from the daemon process itself. The swap runs mid-turn: a lane's cci calls fall back to the store while no daemon answers, and its `cci watch` and channel streams reconnect, so every call answers and every record arrives. A box with no cci supervisor runs no cci daemon, and the install starts none and prints `installed cci-daemon none`. A grader, a box `runner.json` lists only in `finder.sprite` or `fidelity.sprite`, gets no daemon from any install, whether `lr sprites roll`, the grader roll after `lr install`, or `lr sprites grader`: where a leftover `cci supervise` still runs, the install leaves its daemon alone and prints a `warning:` line naming it for removal. The roll and `lr sprites grader` pass on each `warning:` line an install prints, as `<box>: warning: …`. `capt-hookd package-install` already moves the capt-hookd daemon onto its build; only its supervisor process keeps the old build, and it serves nothing.
- `lr sprites` adds `its cci daemon runs <old>, not yet moved to cci <new>` to a box whose daemon runs another build than its command, never to one at `cci-daemon=none`, and the host roll installs on such a host again. A cci fix is installed on a box only once its line shows `cci-daemon` at the fix's version.
- The roll also installs on `host.sprite` from `runner.json`, the always-on desk host, which hosts no worker. `lr install` rolls that host right after it moves the pins, so the host follows the Mac without a separate roll. A failure there is logged and does not undo the Mac's install. On a box with a `~/.claude/long-running/current` link, installing the pack moves the link, and a supervisor running there re-executes onto it.

### `lr sprites grader`

`lr sprites grader NAME...` makes each named Sprite a grader the `finder` and `fidelity` desks can list. A grader holds no cc-notes and needs none: the coordinator reads the answers and landed rows a pass needs and puts them in its job directory, so a pass reads the same on any grader. It makes the Sprites at the same time, each in this order:

1. Install the lr, ccx, ccn and codex-ask pins, recorded for `lr sprites roll`. The install script also lays down the cci binary, which nothing on a grader runs.
2. Link this Mac's home to `/home/sprite`, and each of the `landing`, `finder` and `fidelity` checkouts to the Sprite's checkout, so a job's Mac paths resolve there.
3. Fetch the checkout's whole history with a `ccx vcs auth token` for the landing repo.

A grader holds no copy of the drive's store and runs no `cci` service, so a pass holds only what its job directory, Git and the copied register and plan files carry: a read you add comes in the job or stops the pass with an error naming what is missing, and never reads as no rows or a skipped check. It writes its records to `posts.jsonl`. The coordinator publishes those records when the job's files return. The wrapper checks the whole archive before it extracts anything, so an incomplete copy leaves nothing to publish and fails the pass as a lost result for retry. A Python job through `sprite-job.sh` still pushes through the Sprite's relay, so a box named in `judge.sprite` keeps its `cci` service.

This verb does not stop a `cci` service an older `lr sprites grader` started. A Sprite that fails stops at that step alone, and the verb ends by naming every Sprite it did not make. Adding the Sprite to `finder.sprite` or `fidelity.sprite` in `runner.json` is a separate edit.

## Keep a Sprite awake while its agent runs

A Sprite pauses about 30 seconds after its last activity, and a parked agent is not activity. A lane waiting on CI or a Monitor goes warm, then cold, and a cold Sprite has lost its processes.

Once `worker-start` succeeds, the launch creates the `worker-awake` Sprite service, `lr sprites hold`:

- Each minute it reads `/proc` for a `claude` or `codex` process. While it finds one, it refreshes a `worker-awake` task with a 5-minute expiry through the Sprite's task API, the same helper `lr desk-creds serve` uses.
- When the agent exits, the refreshes stop and the task expires, so the Sprite sleeps about 5 minutes later. `lr stop-sprites` and `lr gc` end the agent, so a settled lane's Sprite still sleeps or is destroyed.
- A holder that is stopped deletes its task, so `cc-remote orca stop` does not find the Sprite held.
- A failed read or refresh logs at warn and tries again next minute. A service that fails to start warns on the launch's stderr and in `awake.log` in the attempt directory, and the worker keeps running without a hold.
- A lane already running when this version installs has no holder until it is relaunched; `lr sprites roll` does not add one.

The same service carries the lane's work off the box, so a Sprite the provider loses costs at most 5 minutes of work:

- Every 5 minutes it finds the agent whose environment names `CLAUDE_LONG_RUNNING_LANE` and pushes with that agent's GitHub credential and git identity. A Sprite with no lane agent pushes nothing.
- It walks the home directory three levels deep, skipping dot directories, and takes each git checkout whose origin is on github.com: the drive checkout and any clone the lane made. It records their paths in `~/.local/state/lr/work-checkouts` for `lr gc`.
- For each checkout it stages the whole tree into a scratch index, so `.gitignore` keeps local-only files out and the lane's own index, branch and PRs never move. It writes one commit whose tree is the working tree, whose first parent is HEAD and whose other parents are the local branch tips no remote-tracking ref holds; its message names each branch and tip.
- It pushes that commit to `refs/lr/work/<lane>` on the checkout's origin with `--no-verify`, and keeps a local copy of the ref. A checkout with no edits and nothing unpushed pushes nothing, and neither does one whose tree and parents match its last push.
- A push is plain git. To the monorepo, git first reads GitHub's whole ref advertisement, about 130,000 refs, which takes about 3 seconds, and then sends only the changed objects. A box pushes at most 12 times an hour per checkout, and only while the lane is changing files.
- A relaunched lane's brief names `refs/lr/work/<old lane>` and the fetch that reads it, and a lost lane's partial-work line says what that ref may lack.

**Tunnel.** The Mac also holds an ssh tunnel to each lane's Sprite, opened when the lane starts, and an open tunnel keeps the box running with or without an agent. `lr gc` closes it, with `ssh -O exit` on that lane's own control path, when it keeps a settled lane's Sprite for unsaved work, an unreadable checkout or a refused destroy, so the kept box can sleep. A live dispatch or a busy terminal keeps its tunnel. When the supervisor passed the lane as `--finished` and the Sprite is kept for unsaved work, gc first closes the lane's terminal through the Sprite's Orca environment, after reading it idle again. Closing it ends the agent session and the processes attached to that terminal on the Sprite, which lose no files. The Sprite, its checkout, its stashes and branches, its `refs/lr/work/<lane>` push, and any process detached from the terminal stay for a later gc. With no lane agent left, the box pushes nothing more, and it needs to push nothing more: a lane that has sat idle fifteen minutes has changed nothing since the service's last push. An unreadable checkout, a refused destroy or a busy terminal keeps the terminal open.

**Local disk.** A Sprite's local disk holds 20 GB, about 18.6 GB of it usable, and a grader wrote about 4 GB a day to its home. So each `lr grade` on a Sprite runs `binrun gc` and deletes codex session files older than a day. A failure there is logged and never fails the pass. Those two were 5.5 GB of a 14 GB home after 2.5 days, read on dar-fleet-23.

**Unclean stops.** A Sprite that sits frozen can come back from an unclean reboot instead of resuming, and it then holds whatever had reached the disk. dar-fleet-35 lost 52 loose git objects this way on Nov 4, each left empty, since git does not fsync loose objects by default. So a job's git calls set `core.fsync=committed` with `core.fsyncMethod=batch`, and each job deletes empty loose objects before its fetch, which brings them back.

**Samples.** The same service appends one JSON line every 10 seconds to `/tmp/cc-remote-samples.log`, fsynced, because a stop can reboot the kernel and wipe its counters. `/tmp` is the Sprite's local disk, which survives a stop. Home is `nbd0`, a network disk whose connection can die, and a write to it hangs first, so a sample written there stops before the event it should record.

- Each line has the time, kernel uptime, `MemAvailable`, swap in use, the balloon's net size, the kernel's `oom_kill` count, `/proc/loadavg`, the count of processes waiting on a disk, the top 3 processes by RSS with their command lines, each `claude` or `codex` with its parent, `memory.events` when readable, and any new kernel line at error level or naming `nbd0`.
- The service checks the kernel log every second, and a new such line writes a sample at once, so an error just before a stop reaches `/tmp` rather than waiting out the 10 seconds.
- `cci post` cannot carry these off the box when it matters, because cci's store is on home and hangs with it. The supervisor reads the file instead: its `LANE-LOST` for a stopped Sprite states the last sample before the stop and any kernel error, from one `sprite exec`.
- The file keeps about 2 hours. A failed read writes no line, warns, and leaves the hold running.
- To tell which went first, compare the last line naming an agent with the last line of the kernel's uptime: an agent gone while uptime keeps climbing means the agent died before the box.

## Read the merge queue from a Sprite

`scripts/stack-enqueue --check` and `--status` run `tools/ci trunk`, which fetches the `ci` binary from the ci cache bucket. Adding the aws CLI does not help: a Sprite holds no AWS identity, so the read stops at `Unable to locate credentials`. `ccx vcs pr status <n>` answers the same question with no bucket, from GitHub and the `TRUNK_MERGE_API_KEY` the launch gives each worker. Run it from a checkout of the repository, or the queue reads unknown.

## Read Buildkite from a Sprite

`tools/ci bk` needs the aws CLI or the devbox broker, and a Sprite has neither, so no Buildkite token reaches a Sprite. A lane asks the `bk-desk` lane over cci instead, and the launch spec tells each new worker how:

```sh
cci post --kind ask --to bk-desk "bk build <pipeline> <number>"
```

| Read | Answer body |
|---|---|
| `bk build <pipeline> <number>` | The build's state, branch, commit and URL, then one line per job not passed with its state, exit status and job id |
| `bk jobs <pipeline> <number>` | One line per script job, passed ones included, with its state, job id and name |
| `bk builds <pipeline> [--branch <glob>] [--since <date or RFC3339 time, UTC>]` | Newest first, each build's number, state, branch and creation time, then its whole message, up to 16 KiB of whole builds. The desk reads every page itself and the answer's line says when older builds did not fit |
| `bk annotations <pipeline> <number> [<context>]` | Each annotation's style and context, then its text with the HTML removed, up to 16 KiB; one too long to fit is named with the context that reads it alone |
| `bk log <pipeline> <number> <job id or step key>` | The last 16 KiB of the job's log, with terminal escapes removed. A step key reads that step's current job, and a key that names no job or several answers with their ids instead |
| `bk grep <pipeline> <number> <job id or step key> <regex>` | The lines of the whole log that match, up to 16 KiB |
| `bk api <path>` | The first 16 KiB of a GET inside the organization, its path given without the `/organizations/<org>` prefix and with no flag |

- The answer is a record addressed to the lane that closes the ask, usually within seconds and at worst in about two minutes, since the host's `tools/ci` refreshes its published `ci` about once an hour before it reads. Its line carries the state or size, and `cci show <seq>` prints the body.
- Any other text, a write included, gets a refusal, and nothing runs.
- Ask once per need: `gh pr checks` follows a build's progress, and `bk-desk` is for reading a result. cci treats an identical ask from one lane within ten minutes as the same record: `cci post` prints `(duplicate)`, and the earlier answer stands.
- One desk serves the whole drive. It holds the token in memory, runs one read at a time, and answers a repeated read from memory for a minute, so every lane shares one client path on the organization's shared rate limit.
- An ask no desk answered stays open, which is how a dead desk surfaces.
- A lane cannot reach the desk any other way. The tailnet policy admits only the owner's devices to a `tag:orca` node, so one Sprite cannot reach another or this Mac, and a Sprite's URL needs an organization token that no Sprite holds.

### Run the desk

Run it once per drive, on the desk host. Write `bk.json` beside the host's `lr.json`, naming the Buildkite client, the checkout that holds it, and the command that prints a read-only token:

```json
{
  "cli": "tools/ci bk",
  "checkout": "<checkout on the host>",
  "token_command": ["aws", "ssm", "get-parameter", "--profile", "<profile>", "--region", "<region>", "--name", "<parameter>", "--with-decryption", "--query", "Parameter.Value", "--output", "text"]
}
```

Then start the desk as a Sprite service:

```sh
sprite exec -s <host> -- sprite-env services create bk-desk \
  --cmd /home/sprite/.local/bin/lr --args bk,desk,--config,<lr.json> \
  --needs desk-creds --no-stream
```

- The desk reads the token once at start, so restart the service after rotating it.
- An answer that fails is logged, its ask stays open, and the desk goes on to the next ask. A restart answers any ask from the last hour that is still open.
- It needs the host joined to the relay and nothing else: no tailnet route, no open port and no credential on a lane.

## Read Sentry, Datadog and ai-oncall from a Sprite

A Sprite holds no Sentry or Datadog key and no AWS identity, so a lane asks the `obs-desk` lane, which runs on the Mac, over cci:

```sh
cci post --kind ask --to obs-desk "sentry issue <id>"
```

| Read | Answer body |
|---|---|
| `sentry issue <id, short id or issue URL>` | The issue's state, counts and first and last seen, then its latest event: exception, newest frame first, request and tags |
| `sentry events <id>` | The issue's newest events, one line each with its time, id, title and key tags |
| `sentry search [--period 14d] <query>` | Up to 25 issues matching a Sentry search, newest first, over the last day by default |
| `datadog monitor <id>` | The monitor's state, query and tags, each group not OK and since when, and its message |
| `datadog logs [--from <age>] [--to <age>] <query>` | Up to 100 logs, newest first, over the last hour by default; an age is `90m`, `6h`, `2d` or an RFC3339 time |
| `datadog spans [--from <age>] [--to <age>] <query>` | Up to 100 APM spans, newest first, with service, resource, duration, trace and error |
| `datadog metric [--from <age>] [--to <age>] <query>` | Each series of a metrics query with its points |
| `sentry api <path>`, `datadog api <path>` | The first 16 KiB of one GET under that API |
| `oncall <read verb>` | `tools/ci oncall` with `incidents`, `page status`, `ledger`, `journal read`, `history`, `monitors`, `impact`, `current`, `responder` or `connections list` |

- The desk makes read calls only. Any other text, a resolve, mute, downtime, notebook, `oncall say` or `oncall connect` included, gets a refusal, and nothing runs.
- The answer is a record addressed to the lane that closes the ask, within seconds; `cci show <seq>` prints the body. A repeated read is answered from memory for a minute.
- The keys stay in the desk's memory: never in a file, record, log, brief or command line, and never on a Sprite.

### Run the obs desk

Write `obs.json` beside the drive's `lr.json`, naming each key's SSM parameter, never its value, and the checkout whose `tools/ci` answers oncall reads:

```json
{
  "ssm": {"profile": "<profile>", "region": "<region>"},
  "sentry": {"api": "https://sentry.io/api/0", "org": "<org id or slug>", "token": "<parameter of a token with event:read>"},
  "datadog": {"api": "https://api.datadoghq.com", "api_key": "<parameter>", "app_key": "<parameter>"},
  "oncall": {"cli": "tools/ci oncall", "checkout": "<monorepo checkout>"}
}
```

Then run `lr obs install --config <lr.json>` on the Mac from the installed lr. It writes a launchd agent that runs that lr's `lr obs desk`. The desk reads the three keys from SSM once at start, so restart it after a key rotates or after an SSO sign-in lapse stopped a start. Oncall reads use the profile's live sign-in on every read. A Sentry token that only uploads releases and source maps answers every issue read with HTTP 403; the token needs `event:read`.

## Prepare and attach separately

Use the configured signed CLI and YAML when preparation and first-task attachment need separate inspection. This explicit fresh Sprite agents example returns a plain prepared task:

```sh
"<selected-cc-remote>" orca prepare "<lane>" --config "<configured-yaml>" \
  --provider sprites --profile agents --agent claude \
  --model claude-opus-5-5 --effort high --ref "<branch-or-tag>" \
  --brief-file "<complete-brief.md>" >"<new-prepared.json>"
```

Prepare starts the selected agent and stops at native idle without sending its task. Before attachment, require command exit zero, `prepared:true`, the requested model and effort, the actual `baseCommit`, and the exact brief hash and length. For a Codex assignment, preserve its requested agent kind, model, effort and explicit service tier. Keep API credentials in the configured process-only callbacks.

From the coordinator that already owns the Run, attach once with the prepared record's fields and a new receipt file:

```sh
orca orchestration worker-start --run "<owned-run-id>" --on "<environment>" \
  --worktree "id:<worktreeId>" --terminal "<terminal>" --task-title "<lane>" \
  --spec "Lane <lane>: read the whole brief at <brief.path>, verify its SHA-256 is <brief.sha256> and its size is <brief.bytes> bytes, then follow it exactly." \
  --json >"<new-receipt.json>"
```

- This reuses the prepared terminal and worktree and creates the native Task with a pointer to the complete brief.
- Pass no agent, model, effort or service tier, so the prepared process keeps its own.
- The ready receipt names the Task and Dispatch. The existing inbox owner handles their messages; do not send a second task pointer or start another consumer.
- On accepted completion, retain the session and use the [source-return and collection contract](#return-source-for-coordinator-review).
- Retain receipts on every outcome. A fresh-create failure can run the normal CLI rollback; report the actual remaining resources and never replay an ambiguous attempt.

### First worker on a recorded workspace

Version `0.15.0` adds prepare-only `--existing` for a first worker on the exact recorded workspace and retained checkout:

```sh
"<selected-cc-remote>" orca prepare "<recorded-workspace>" --existing \
  --config "<same-config>" --provider sprites --profile agents --agent claude \
  --model claude-opus-5-5 --effort high \
  --brief-file "<complete-brief.md>" >"<new-prepared.json>"
```

- Do not pass `--ref`; this mode preserves the checkout and reports its actual base.
- It requires the recorded provider, profile and machine, an absent task, and no prior Orca runtime. A busy or previously claimed task refuses before another first launch.
- A completed worker is retained and reused through a new native Dispatch under the version-matched reuse contract.
- Normal resume preparation still runs and may repair changed tools or configure services. Verify those effects before calling a measurement preinstalled. Report one-time setup, same-identity cold-to-agent readiness, and native Task completion separately.
- `--existing` preserves the retained checkout of the named workspace. The canonical `--warm` route refreshes the requested shallow ref on an unused pool workspace. Recipe-picker selection remains separate.

## Keep one Run owner and one inbox consumer

The launch inherits the existing native coordinator identity. A missing or different Run binding is a refusal; do not create or rebind a Run, open another coordinator terminal, or start another reader to make the launch pass.

- The selected home-inbox owner keeps its existing cursor. Process every message in a delivered batch before acknowledging it; a wake filter does not discard other messages in that batch.
- Do not start another consuming check beside that owner.
- A worker reads only its own Dispatch mailbox using the exact terminal identity in its live preamble.

Route follow-ups through the saved home Dispatch ID:

```sh
orca orchestration send --to "dispatch:<dispatch-id>" --subject "Follow-up" --body "<guidance>" --json
orca orchestration reply --id "<original-home-question-id>" --body "<answer>" --json
```

- A reply uses the original home relay question ID. Sending guidance does not answer an `ask`, and durable enqueue does not prove consumption.
- For questions and completion, match authoritative `from_handle` to `dispatch:<dispatch-id>` and cross-check the payload Task and Dispatch IDs. Copied IDs or terminal titles confer no authority.
- Workers use their live preamble for heartbeat, relay acks, checks and `worker_done`, and `cci post` for every ask or report.
- An ask timeout resumes the same message ID.
- An `orca terminal send --enter` that warns no turn start was observed, with the prompt visible on screen in the composer or under queued messages, was delivered: never resend it; at most reissue it with `--retry-request <id>`.

## Diagnose the saved Dispatch

Start with the native receipt and the exact home Dispatch:

```sh
orca orchestration worker-show --dispatch "<dispatch-id>" --json
orca orchestration worker-read --dispatch "<dispatch-id>" --limit 50 --json
orca orchestration worker-list --run "<owned-run-id>" --include-remote --json
```

- Follow returned pagination cursors.
- Preserve `live`, `exited` and `unverifiable` as distinct observations. Contact loss or an empty read does not prove a worker stopped.
- Keep raw receipts when input was accepted but the outcome is unknown.
- Do not replay prepare/attach, type into a remote terminal, or substitute a local launch.
- A Sprite's cci replica can start mid-history. Before calling an order dropped from an audit run on a Sprite, check that the order is absent on the Mac.
- The existing orca-doctor assumes local terminals and worktrees; it is not the remote default's diagnostic path.

**A null `agentWait` is no evidence of work.** A host running Orca's managed server installs no agent hook, so `agentWait` stays unset for its workers and `worker-read` falls back to the terminal with `fallbackReason: session_not_reported`. Read the worker's rendered screen through the server the receipt names:

```sh
orca terminal read --terminal "<handle>" --screen --environment "<server.name>" --json
```

- Without `--environment` the handle reads `terminal_handle_stale`.
- A host that does not answer reads `remote_runtime_unavailable`: record the worker as unreachable, never as stopped and never as stuck.
- `agentWait` on a local terminal can stay set after its prompt was answered, so it is a reason to read the screen and never authority to answer one.

## Write the complete VM brief

The brief must stand alone inside the VM. Include the exact task and source scope, requested branch/tag policy, constraints and owner rulings, verification, output paths, and the source-return contract below. Do not paste a Mac-only lane brief unchanged when it assumes local paths, local stack metadata, or worker PR shipping. Remote workers return source; the coordinator integrates and ships it.

- A standard Sprite startup does not eagerly initialize WordNet. When the task needs it in place beforehand, copy the setup command from `AGENTS.md` under "Remote workspaces" into the brief. Leave it out of every other brief.
- Prepare copies the complete brief unchanged outside the checkout and records its absolute VM path, SHA-256 and byte count. The launcher sends only a pointer telling the worker to read and verify that file. Never shorten the full brief, invent a receipt, or substitute its Mac path.

**What the worker does with follow-ups.** Workers read their Dispatch follow-ups at natural checkpoints and immediately before completion. They process every row in the returned batch, acknowledge its `deliveryId` with `check --terminal <their-handle> --ack <delivery-id>`, and then read the next batch. `replayed:true` means the previous batch is still unacknowledged, and later guidance stays queued until that batch is acknowledged. After processing the queued guidance, they send one honest explicit-outcome `worker_done` and idle.

## Return source for coordinator review

For a source task, leave HEAD at the actual prepared `baseCommit` and source edits uncommitted. Do not stage, commit, push, open a PR, close the coordinator task, or clean up resources. Produce a complete patch containing every tracked and untracked change with file modes, and a strict collection report beside the VM brief, outside the checkout. Use regular files, not links or an archive.

The report is one JSON document with only these fields:

| Field | Value |
| --- | --- |
| `schemaVersion` | `1` |
| `baseCommit` | Actual prepared checkout commit, unchanged at completion |
| `patch` | Actual absolute patch `path`, lowercase `sha256`, and integer `bytes` |
| `files` | Every changed repository-relative `path` with its actual lowercase `sha256`, or `deleted:true` for a deletion |

Put no extra provenance keys in this strict report. Report unrun tests and other task facts through native status/completion. Include real modified paths and the actual report path in `worker_done` metadata.

The coordinator collects the two named files into a new local directory:

```sh
"<selected-cc-remote>" orca collect "<workspace>" --config "<same-config>" \
  --report-file "<vm-brief-dir>/report.json" \
  --patch-file "<vm-brief-dir>/source.patch" --output "<new-local-directory>"
```

- Use the same selected CLI and config as prepare.
- The collector checks the recorded runtime, unchanged HEAD, report and patch hashes, and bounded status. It does not prove patch completeness or correctness.
- Reconcile all status paths, including untracked files, with the report and full patch. Require no status overflow or staged diff, and verify returned file hashes in isolated review.
- Apply the accepted source in a coordinator-owned checkout and ship through normal repo tooling.

## End a done lane

The coordinator ends a local lane once it decides the lane is done: the deliverable is on disk, the lane was stood down, or another lane superseded it. It does not message the lane to send `worker_done` first. One call ends it:

```sh
lr gc --run <run> --done <ctx>
```

- `--done` runs `orca orchestration worker-stop` on the Dispatch, which fences it and closes an Orca-owned agent terminal. When Orca already revoked the Dispatch, so worker-stop reads `dispatch_inactive` and the Dispatch reads `dispatched` forever, `--done` runs `worker-abandon` instead, which settles it as failed.
- Any terminal still open is closed without waiting for an idle prompt. Then the worktree is removed when it is clean and its HEAD is on origin or landed. A head also counts as landed when replaying it onto the parent of a merged PR of its own branch gives exactly that PR's squash tree, so the copy left by a rebase before the merge does not hold a tree. A worktree with uncommitted or unpushed work stays.
- Add `--dry-run` to print the plan first.
- A Dispatch that already settled through `worker_done` gets the same sweep without the stop: `lr gc --run <run> --dispatch <ctx>` closes its idle terminal and reclaims its worktree.

**Who may end a lane.** Only the coordinator that coordinates the Run ends its lanes. A lane never stops another Dispatch, and nobody uses `pkill`, signals, `run-stop`, or bulk terminal closes. The captain-hook sessions guard allows a bare `orca orchestration worker-stop --dispatch <ctx>` only from that coordinator.

### Worktree removal edge cases

| Orca answers | gc does |
| --- | --- |
| `orca worktree rm` errors, such as `runtime_unavailable`, but the directory is gone | Counts the worktree as removed. The Orca error prints on its own `error` line, and a dropped runtime connection needs no second gc pass |
| `selector_not_found` while the directory is still on disk (Orca no longer tracks the worktree) | Removes it with `git worktree remove` after the same checks; keeps and names it when git refuses |
| No workspace recorded for a settled Dispatch, such as one that failed before its worktree resolved | Keeps it and names it on its own `keep` line; makes no Orca, git or cc-remote call for it |

### Free the Sprite

A settled Sprite worker frees its Sprite through the same `lr gc` call. `lr gc` runs `cc-remote destroy` on it and removes its Orca environment, when all of these hold:

- The Sprite's cc-remote task record names the Dispatch's worktree and terminal, and its workspace record still exists. A Sprite with no matching task record is left alone.
- No unsettled Dispatch shares the workspace.
- The terminal reads idle twice (`--done` skips the idle check).
- The checkout, read over ssh, is clean with its HEAD on origin or landed, and so is every other checkout `lr sprites hold` recorded on the Sprite. A clone holding unsaved work keeps the Sprite and names the clone.
- The Sprite's Orca runtime is up. A Sprite whose runtime is down, as it is on any Sprite `lr stop-sprites` stopped, reads as unknown and is kept.

Right before the destroy, gc deletes the `refs/lr/work/*` refs each of those checkouts holds from that checkout's origin. That includes a predecessor's ref a successor fetched. It first reads which of them the origin still has, so a ref already gone is passed over, and a delete that fails keeps the Sprite.

The destroy runs `WORKER_CC_REMOTE` with `CC_REMOTE_CONFIG` when set, otherwise `tools/cc-remote/bin/cc-remote` and `tools/cc-remote/config.yaml` of the checkout gc runs from.

### Stop the Sprite of an ended lane

A lane that ended has its Sprite stopped without the coordinator. Every five minutes the lane supervisor runs `lr stop-sprites`, which picks each retired registry lane placed on a Sprite whose Orca dispatch reads completed or failed. When the Run lists no dispatch for the lane, its `done` record or a withdrawal must have retired it. A lane that is live or held, or whose dispatch is unsettled, keeps its Sprite.

- For each ended lane, lr runs `cc-remote orca stop <lane>` with the `WORKER_CC_REMOTE` and `CC_REMOTE_CONFIG` of `runner.json`'s `orca.sprite.env`. That stop closes the forward and stops every service, so the Sprite goes cold with its disk kept. It refuses a warm pool workspace's own name. The gc pause below does not hold it.
- When a stop fails, lr runs `sprite list` once for the pass and looks for the Sprite named in the lane's launch receipt. A Sprite the list no longer shows closes the stop.
- A Sprite whose storage the provider lost answers 503 `Storage not ready` and never runs again. A cc-remote release that carries yasyf/cc-remote#117 counts that as stopped, so the stop succeeds, closes any open blocker with one `SPRITE-STOPPED` line, and destroys nothing.
- A stop that failed on a 5xx from the Sprites API or a timeout is tried again every pass, one call per stuck Sprite. Its first failure posts one `SPRITE-STOP-FAILED` blocker to the coordinator with the error and what the list said. When the Sprite stops or is gone, one `SPRITE-STOPPED` note closes that blocker.
- A person ends those retries through `lr stop-sprites`, whose blocker line names the flag; it posts one `SPRITE-STOP-LEFT` note. Resolving the blocker does not end them, because a record that only routes the blocker also resolves it.
- Any other failed stop is tried again on the next pass, up to three tries. The third failure posts one `SPRITE-STOP-FAILED` blocker, and lr never runs that lane's stop again.
- Before any stop, lr asks that CLI its `version`. A release older than 0.27.4, the first whose `orca stop` works on a finished lane's Sprite, skips the pass with one warning in the supervisor log. The ended lanes stay unmarked, and lr stops them on the next pass after the checkout's pin reaches 0.27.4.

### Pause and resume gc

gc is paused until `~/.local/state/orca-gc/resumed` exists, or `$XDG_STATE_HOME/orca-gc/resumed` when that variable is set. A paused gc prints one `skip` line and changes nothing. Resume it with
`mkdir -p "${XDG_STATE_HOME:-$HOME/.local/state}/orca-gc" && touch "${XDG_STATE_HOME:-$HOME/.local/state}/orca-gc/resumed"`,
and pause it again with `rm "${XDG_STATE_HOME:-$HOME/.local/state}/orca-gc/resumed"`.

A running pass reads the pause before each candidate and again right before each worker-stop, release, terminal close, worktree removal, or Sprite destroy. It names every action it skips and lets an operation it already started finish. `--dry-run` ignores the pause.

Until gc runs, separately authorized new work may reuse a proven existing worker through a new native Dispatch. Use the version-matched reuse contract and new lifecycle IDs. Never automatically retry the old task or restart its session.

## Remote workspaces

The direct worker route above and the repository recipe flow below are separate interfaces. Keep recipe generation, native picker settings and global worktree settings separate from the canonical worker default.

### Repository recipe flow

[cc-remote](https://github.com/yasyf/cc-remote) provisions the machine behind each `orca.yaml` recipe. Follow its [remote-workspaces skill](https://github.com/yasyf/cc-remote/blob/main/skills/remote-workspaces/SKILL.md) for the composer flow and `cc-remote orca wait`, `verify`, and `gone`.

- Run `./tools/cc-remote/bin/cc-remote` from the repo root; the wrapper uses the version in `tools/versions.toml`.
- Pass `--config tools/cc-remote/config.yaml` to Sprite commands.
- `tools/cc-remote/inventory.yaml` pins the captured tools and plugins. The agents profile checks out the requested ref shallowly.
- The inventory's prepare steps bake Orca's managed server into the payload. At build time they run `orca serve` once, require it to select `orcad`, and keep the server slot and pinned Node it materialized beside the runtime. Every Sprite links that directory at `~/.config/orca/orcad-artifacts`, so a lane's first `orca serve` downloads and copies nothing.
- The inventory's `strictKnownMarketplaces` managed setting keeps each plugin marketplace at the source the inventory registers. This repository's `.claude/settings.json` declares `captain-hook` with no ref, and without the allowlist a session start replaces the pinned checkout with that marketplace's default branch, so the plugin runs ahead of the pinned Captain Hook host. A marketplace the repository newly declares loads on a Sprite only after the allowlist names its source.

After editing the Sprite config, regenerate its recipe:

```sh
./tools/cc-remote/bin/cc-remote orca recipes --binary ./tools/cc-remote/bin/cc-remote --config tools/cc-remote/config.yaml --orca-yaml orca.yaml
```

Keep the generated name, description and lifecycle commands intact: `orca wait` compares the complete entry. The `(default)` badge identifies that config's default provider/profile; it does not select a global Orca default. Pass `--binary ./tools/cc-remote/bin/cc-remote` and `--config tools/cc-remote/config.yaml` when running `orca wait`.

### Build and publish the payload

The Sprite agents profile downloads two private S3 objects: the tools payload and its resident package archive. `payload-url` checks each source against `agents-payload.json` and verifies the inventory hash before obtaining a 900-second URL through `tools/artifact.sh`. The VM downloads each archive and verifies its full SHA-256 and byte count; mounting also checks the tool fingerprint. The wrapper preserves the caller's stdin and the CLI's JSON stdout, and diagnostics go to stderr.

The URL broker, `tools/artifact.sh presign`, reads as the `core-auto` profile in `~/.aws/config` first, and on this Mac that profile assumes its role from the machine identity, so it never expires and needs no sign-in. It is the CI account's machine-sourced profile, the one `CI_AWS_PROFILE` names here; probe the name the broker reads with `AWS_PROFILE=core-auto aws sts get-caller-identity`. Its second rung, the committed `tools/artifact-cache.aws-config`, is an SSO profile that no lane signs in or asks the owner to sign in: a failed probe is a blocker to the coordinator carrying its error. It presigns the pinned seeds directly, and `tools/artifact.sh fetch cc-remote-agents-tools` builds the pair from those seeds on a store miss; no CI step produces it.

When the captured tools, the cc-remote pin, or the Sprites base change, move the pin and inventory first, then build and publish from this Mac. The build uses your local `sprite` sign-in and `gh auth token`, the same credentials every create uses, and no CI job or SSM parameter holds either:

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

A 412 answer means that seed is already stored. Copy the digests and sizes from `build.json` and the helper archive into `agents-payload.json` and `config.yaml`. Then run `./tools/cc-remote/payload-url <member> <sha256> <bytes>` for `tools`, `packages`, and `bootstrap-helper`, and fetch one byte from each URL it prints. Open the pin PR only after all three answer 206: CI reads no seed, so a pin whose seeds are missing passes CI and breaks every Sprite create.

### Namespace images

Namespace uses regular Compute through `tools/cc-remote/namespace.yaml`, which reads the same `tools/cc-remote/inventory.yaml` as the Sprite config. A Namespace machine takes its tools from an image baked from that inventory, one image per profile. After an inventory change, rebuild both images and put each printed digest reference in that profile's `machine.namespace.image`:

```sh
./tools/cc-remote/bin/cc-remote images build --config tools/cc-remote/namespace.yaml --profile agents
./tools/cc-remote/bin/cc-remote images build --config tools/cc-remote/namespace.yaml --profile stack
```

Create refuses a machine whose image was baked from a different inventory. Running workspaces and finite automatic lease renewal have been verified. Suspend/Wake remains unsupported at the tested US endpoint. The repository recipe remains Sprite; native picker enablement stays separate.

## Layout

A prepared VM checkout is the worker's source workspace, not the coordinator's local worktree. Give each editing lane its own checkout. Keep the brief, report, patch and native attempt receipts outside source checkouts.

Local Orca lane worktrees use `~/.orca/workspaces/monorepo/monorepo/<name>`. The coordinator integrates accepted remote patches in an isolated local branch and one native GitHub stack, using `ccx vcs stack new` and [submit-pr](https://github.com/Forge-AI/monorepo/blob/dev/.agents/skills/submit-pr/SKILL.md). The coordinator owns PR registration, pushes and queue decisions; the remote source worker does not inherit a Mac lane's shipping obligations.

## Models

- The coordinator runs Fable on the Mac; Claude implementation workers run Opus; subdesks use Opus or Sonnet according to the routing table.
- Codex review, diagnosis, sweeps and shell-heavy lanes run `gpt-6.1-sol` at `high` on the standard tier. Only an incident lane runs `xhigh` and adds `WORKER_SERVICE_TIER=fast` with `WORKER_INCIDENT=1`.
- Beyond the coordinator, Fable runs only where the owner named it for the work, local, using the Mac's existing interactive authentication. Never use Fable as a general fallback or set `fallbackModel`.
- Every lane starts at effort `high`, and an Opus standing desk at `medium`. A Fable or astra model, or effort `xhigh` or `max`, starts only on the owner's words for that work: the brief's `Owner pick: owner:m<row>` line, which `lr launch lane --owner-pick owner:m<row>` writes, and `--owner-pick` on `lr launch claude` for a raw start. `lr launch` checks it on every path, posted, Mac, desk-runner and Sprite, and refuses the launch otherwise with the exact fix. Incident lanes keep `xhigh` on their own tier rule.
- Every Claude session `lr launch` starts, root and its subagents included, runs with `CLAUDE_CODE_AUTO_COMPACT_WINDOW=333000` and compacts at 300k tokens; a codex lane gets `-c model_auto_compact_token_limit=300000`, written to the Sprite's `config.toml` before its worker starts. A raw `claude` start in a drive session is rewritten to `lr launch claude`, which sets the same. The communicator's own window is exempt. A running window keeps its effort and limit until it is relaunched.

| Alias | Model ID | Placement |
| --- | --- | --- |
| `opus` | `claude-opus-5-5` | The Claude implementation default |
| `sonnet` | `claude-sonnet-5-5` | Explicitly selected Claude worker |
| `fable` | `claude-fable-5-1` | The coordinator, on the Mac; otherwise explicit local, most sensitive implementation only |
| `gpt-6.1-sol` | `gpt-6.1-sol` | Codex review, diagnosis, sweeps and shell-heavy lanes on the standard tier; incident lanes add the fast tier |

**Routing detail behind the global table.**

- Spawn convenience is not a routing input. Backgrounding, a "quick look", and `Agent(general-purpose)` being one call while a codex lane takes a skill invocation describe how a lane runs, not what work it is. Reach codex with `Skill(codex)` inline, `codex:codex-wrapper` where a stage must route by agent type, or `codex-ask --dispatch` with an owner for parallel lanes.
- Routine coordination runs on a short Sonnet `low` worker even inside a larger Opus or Fable assignment: routine status, an established bar, and PR mechanics. `ship-pr` opens a finished branch with a prepared title and body.
- Sweep audit and land stages run at the lane's tier; up-tier a gate only where it judges something the bar does not settle. A lenient lane is a prompt defect to sharpen, not a lane to up-route.
- For a page redesign, a Fable pass and a codex pass both run, then one combined design with codex's taste weighted higher, a sidebar over a top bar where they disagree. Implementation stays on the implementation row.
- Sol verify lanes count as same-tier. Reading a saved verdict or checking CI state is coordination and does not reopen the review.
- Fable is typed by hand with `model: fable`.
- Opus `high` covers every Opus lane: decision-dense orchestration, design and architecture review, hard planning, synthesis over review findings, net-new implementation, scoped edits and bug fixes.
- Recon and routine chores default to gpt-5.6-luna `xhigh` via codex; exhaustive enumerations need a cross-model verify, and luna `high` drops subsystems. Sonnet itself runs when the surface is Claude-only, coverage must sweep more than 300K tokens in one pass, or a silent miss is unrecoverable. Pass `model: sonnet` to `Explore` when that row applies.
- gemini-4-argon runs through `gemini -m gemini-4-argon --skip-trust -p`; the lane hands it the facts and rules (voice card, slop-cop rules, length caps) and lands the reply verbatim.
- gpt-6.1-sol is codex only; Workflow and subagent `model` take Claude models only. Orca codex lanes launch with the `sol` or `codex` alias. It degrades as mid-task decisions pile up, so back it with a finder pass. Astra runs as `codex-ask -m astra` on the standard tier.
