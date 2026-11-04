# The relay

## Contents

- [Run the desk processes](#run-the-desk-processes)
- [Configure and start the relay](#configure-and-start-the-relay)
- [Reach the Orca server on the service host](#reach-the-orca-server-on-the-service-host)
- [Subscribe the coordinator and issue commands](#subscribe-the-coordinator-and-issue-commands)
- [Relay, launch, and withdraw lines](#relay-launch-and-withdraw-lines)
- [Track actions and acknowledgements](#track-actions-and-acknowledgements)
- [Follow Orca pass rules O1 to O16](#follow-orca-pass-rules-o1-to-o16)
- [Handle alerts, incident clocks, and case lanes](#handle-alerts-incident-clocks-and-case-lanes)
- [Hold an urgent release](#hold-an-urgent-release)
- [Act on relay escalations](#act-on-relay-escalations)

## Run the desk processes

`lr relay run` owns Orca worker traffic. No model-operated orca-desk sits between the coordinator and Orca, and the coordinator never runs the check/ack loop, relaunch sweeps or helper scripts inline.

- One relay runs on the Mac as its own launchd job, `com.long-running.<drive>.relay`, whenever `orca.desk_inbox` is set. The communicator starts nothing.
- launchd restarts it. A restart reads from the saved cursor and is idempotent, and `relay.lock` keeps it to one process per drive. Restarting never relaunches its workers.
- It leaves for a new binary between passes, once the installed plugin pins a different `lr` release.
- A pass that raises posts one `blocker` to the coordinator naming the error and runs its next pass.
- The supervisor's landing pass is the drive's merge queue. Its checks and rules D3, D14, D16 and D20 are in [reference/landing.md](landing.md); the landing-desk lane's duties are in [reference/landing-desk-brief.md](landing-desk-brief.md).

### Liveness

- A timer posts an `orca alive` cci `state` record to `drive-watchdog` at start and once a minute, whatever the pass is doing. It reads `orca alive pass <n>` with the pid, `pass_seconds` and `pass_step` in its attrs, so a long pass cannot read as a dead relay. Readers use the newest cci record; the relay writes no heartbeat or view file. `drive.py digest` reads it too.
- A frozen process stops the timer: the record goes stale and readers refuse after five minutes.
- A pass stuck on a hung call keeps the timer running. Past 20 minutes in one pass the relay posts one `blocker` to the coordinator naming the pass and the step, and `lr launch` and `lr relay` refuse with the same words. launchd leaves a live process alone, so the coordinator ends a stuck relay.
- A `cci post` that fails stays queued for the next pass instead of stopping the relay.
- The `desk_liveness` hook answers `LAUNCH-FAILED` and names the logs to read when a `cci post --to orca-desk` finds no orca alive record from the last five minutes. The supervisor restarts the relay within a minute, and it reads from its saved cursor. A cci that does not answer leaves the hook silent.
- A Bash append to `orca-desk.md` in a drive session draws `UNREAD` and the `cci post` form, since nothing reads the file.

## Configure and start the relay

`runner.json` configures the relay. It reads the file at start, except for the fields marked re-read every pass.

| Config key | Meaning and default |
|---|---|
| `store` | Action store path; optional, defaults to `~/.claude/long-running/incidents`. Holds one JSON container per lane, plus `desk-landing` and `desk-runner`, with an owner and `owner_generation`. |
| `drive` | Required cci drive name for runner traffic and escalations to the coordinator. |
| `alert.facts` | File path whose contents go into each generated brief. The file lists drive checkout, apply authority, target facts, and the drive's deploy inbox for `MECHANISM`, `FIX-LIVE`, and `NOT-OURS` lines. |
| `incident.case_lanes` | Cap on case lanes launched per incident; default `2`. |
| `tooling_lane` | Lane told once an hour per cause when a grader pass dies on a tooling error, not a verdict. Set it to the meta lane, `meta`. |
| `orca.run` | Orca Run id. Match it to the launch environment. |
| `orca.launches` | Which launches this runner takes when two runners share the drive: `mac` takes Mac-pinned launches (the coordinator, Fable, incident agents, and `place=local` briefs) and every alert, incident, and hold line; `sprite` takes only launches that start on a Sprite. Empty takes everything, and relay and withdraw lines reach both. Any other value refuses at start. |
| `orca.load_limit` | One-minute load average above which a launch that only prefers the Mac starts on a Sprite, under O14. Defaults to the Mac's core count. |
| `orca.desk_inbox` | Required path to the drive's `inbox/orca-desk.md`. Anchors runner logs and state; the relay never reads this file. |
| `orca.briefs.repo` | Drive checkout path for cc-notes brief attachments. |
| `orca.briefs.log` | Briefs log id for generated fix briefs. Checked at start and before every attachment; if unset or unresolvable, the relay switches to or creates `briefs: <drive>` and logs `BRIEFS-LOG` with the new id. |
| `orca.gc` | The plugin's `bin/orca-gc`, prefixed with the `WORKER_CC_REMOTE` and `CC_REMOTE_CONFIG` it destroys Sprites with, which every `RECLAIM` line carries; the coordinator runs it from the drive checkout. |
| `orca.launch_env` | Environment variables passed to `lr launch start`. `ORCA_LAUNCH_PREPARE` is one shell command run in each new worktree before the worker starts; set it to the repo's code generators, never a JavaScript dependency install, and, in a repo with `tools/hooks/warm-lane.sh`, to that script. `ORCA_LAUNCH_SPARSE=1` makes each new lane a sparse `git worktree add` checkout instead of a full `orca worktree create` one, as [orca-workers.md](orca-workers.md) describes. A failure or a run past `ORCA_LAUNCH_PREPARE_SECONDS`, which defaults to `120`, does not stop the launch, and the worker's brief ends with the command and its log path. |
| `orca.sprite.launcher` | Path to the plugin's `bin/worker-launch.sh` for Sprite launches. |
| `orca.sprite.env` | Environment passed to Sprite launches alongside `WORKER_RUN`. Set `WORKER_CHECKOUT` to the checkout whose `tools/cc-remote` starts Sprites, and `WORKER_CCI_URL` and `WORKER_CCI_SHA256` to the cci `linux_amd64` release archive each Sprite worker installs before its brief. Set `WORKER_RELAY_SPONSOR` to the Sprite holding the drive's enrolled cci service key, so each worker joins the relay and its records reach the Mac. Set `CC_REMOTE_CONFIG`, because without it `cc-remote` reads `~/.config/cc-remote/config.yaml`. Set `WORKER_CC_REMOTE` to a worktree on branch `dev` or `cc-remote-<version>` that moves only with a `cc-remote` pin PR, never a session checkout. |
| `orca.sprite.tools` | The tools each new Sprite installs before its brief, at the versions the Mac runs: a list of `{"kind": "binary", "package" or "plugin", "name", "url", "sha256", "version"}`, each a release asset and its SHA-256 from that release's checksums. The desk passes it to the launcher as `WORKER_TOOLS`; [orca-sprite-workers.md](orca-sprite-workers.md) describes the install. A pin may name a `follows` plugin; `lr sprites pin`, which `lr install` also runs, moves each pin to the version the Mac runs once its checksum matches. Run `lr sprites roll` to move the Sprites already running. |
| `deadlines.start_minutes` | Minutes before an accepted action without start proof triggers `DEADLINE`; default `10`. |
| `deadlines.launch_minutes` | Minutes a launch or incident line stays valid from its record time; default `15`. |
| `deadlines.hold_minutes` | Minutes before an urgent release hold triggers `DECIDE hold:<slug>`; default `15`. |
| `deadlines.incident_mechanism_minutes` | Minutes from incident fix launch to mechanism milestone; default `10`. |
| `deadlines.incident_pr_minutes` | Minutes from incident fix launch to PR opened milestone; default `15`. |
| `deadlines.incident_ready_minutes` | Minutes from incident fix launch to ready milestone; default `20`. |
| `deadlines.pr_silent_minutes` | Minutes a class record waits for a coordinator `go`, `decide`, or `decision` before `CLASS-UNOWNED`; default `10`. |
| `deadlines.enqueue_minutes` | Enqueue deadline in minutes; default `15`. |
| `deadlines.release_start_minutes` | Step in minutes between D20 release blockers; default `10`. |
| `finder` | Optional object that turns on finder dispatch. Keys include `model` for the `codex-ask -m` model with default `sol`, `concurrent` for max running passes with default `2`, `checkout` for the repo checkout with default `landing.checkout`, `to` for extra cci addresses, `repo` with default `landing.repo`, and `timeout_minutes` with default `60`. Re-read every pass. |
| `landing.*` | Merge queue and release configuration covering `repo`, `ledger`, `checkout`, `fidelity_lane`, `merge_mode`, `high_risk_paths`, `adapter`, `checks`, `release_owner`, `release_cli`, `interval_seconds`, `status_card`, and `policy`. Set `policy.revision` to the revision of the accepted prefix merge rule. Full landing configuration is in [reference/landing.md](landing.md). |
| `landing.applying` | Optional object enabling the relay's apply announcements. `build` is a command relative to `landing.checkout` printing Buildkite REST build JSON with `{build}`, `{pipeline}`, and `{number}`. `step` is a regex with `component` and `env` named groups matching a job's step key whole, and `components` holds fnmatch globs. Partial config logs `APPLYING-CONFIG-INVALID` and exits. Details are in [reference/release-lanes.md](release-lanes.md). |
| `landing.pages` | Optional object enabling the relay's page watch during releases. `channels` lists Slack channel ids whose top-level messages are pages, and `build` sets the release build reader or falls back to `landing.applying.build`. Details are in [reference/release-lanes.md](release-lanes.md). |

The relay reads each lane's dispatch and terminal from `$XDG_STATE_HOME/long-running/orca-launch/<orca.run>` (`$XDG_STATE_HOME` defaults to `~/.local/state`), the default receipt directory `lr launch start` writes, and launches with `ORCA_LAUNCH_STATE` set to it. A lane launched by hand with `lr launch start` is relayed to like one the relay launched, with no receipts to copy. Never set `ORCA_LAUNCH_STATE` on a hand launch.

```json
{
  "store": "/absolute/drive/actions",
  "drive": "<cci drive>",
  "alert": {"facts": "/absolute/drive/alert-facts.md"},
  "incident": {"case_lanes": 2},
  "orca": {
    "run": "<run id>",
    "desk_inbox": "/absolute/drive/inbox/orca-desk.md",
    "briefs": {"repo": "/absolute/drive/checkout", "log": "<briefs log id>"},
    "gc": "env WORKER_CC_REMOTE=/absolute/drive/checkout/tools/cc-remote/bin/cc-remote CC_REMOTE_CONFIG=/absolute/drive/checkout/tools/cc-remote/config.yaml /absolute/plugin/bin/orca-gc",
    "launch_env": {
      "ORCA_LAUNCH_RUN": "<run id>",
      "ORCA_LAUNCH_REPO": "<Orca repo id>",
      "ORCA_LAUNCH_PARENT": "/absolute/coordinator/worktree",
      "ORCA_LAUNCH_PREFIX": "<drive prefix>",
      "ORCA_LAUNCH_BASE": "origin/dev",
      "ORCA_LAUNCH_CLAUDE_ARGS": "",
      "ORCA_LAUNCH_BOOT_SECONDS": "180",
      "ORCA_LAUNCH_PREPARE": "<the repo's code generators, one shell command>"
    },
    "sprite": {
      "launcher": "/absolute/plugin/bin/worker-launch.sh",
      "env": {
        "WORKER_CHECKOUT": "/absolute/drive/checkout",
        "WORKER_CCI_URL": "https://github.com/yasyf/cc-inbox/releases/download/v<version>/cci_<version>_linux_amd64.tar.gz",
        "WORKER_CCI_SHA256": "<that archive's SHA-256 from the release checksums.txt>",
        "WORKER_CC_REMOTE": "/absolute/drive/checkout/tools/cc-remote/bin/cc-remote",
        "CC_REMOTE_CONFIG": "/absolute/drive/checkout/tools/cc-remote/config.yaml",
        "WORKER_REF": "dev",
        "WORKER_RELAY_SPONSOR": "<Sprite holding the enrolled cci service key>"
      },
      "tools": [
        {"kind": "plugin", "name": "long-running@skills", "url": "https://github.com/yasyf/cc-skills/releases/download/long-running--v<version>/long-running_<version>.tar.gz", "sha256": "<from that release's checksums.txt>", "version": "<version>"},
        {"kind": "binary", "name": "ccx", "url": "https://github.com/yasyf/cc-context/releases/download/v<version>/ccx_<version>_linux_amd64.tar.gz", "sha256": "<from that release's checksums.txt>", "version": "<version>"}
      ]
    }
  },
  "deadlines": {
    "start_minutes": 10,
    "launch_minutes": 15,
    "hold_minutes": 15,
    "incident_mechanism_minutes": 10,
    "incident_pr_minutes": 15,
    "incident_ready_minutes": 20,
    "enqueue_minutes": 15,
    "release_start_minutes": 10
  },
  "finder": {"model": "sol", "concurrent": 2},
  "landing": {
    "repo": "Forge-AI/monorepo",
    "ledger": "<ledger id>",
    "checkout": "/absolute/monorepo/worktree",
    "fidelity_lane": ["fidelity-pipeline", "fidelity-desk", "fidelity-sweep"],
    "merge_mode": "high-risk",
    "high_risk_paths": ["*migrations/*", "*.sql", "*backfill*", "*secret*", "*credential*", "infra/rows/*"],
    "adapter": {"kind": "trunk-stack-enqueue", "script": ".agents/skills/submit-pr/scripts/stack-enqueue"},
    "release_owner": "peer:<release session>",
    "release_cli": "tools/ci release",
    "applying": {"build": "tools/ci bk api /pipelines/{pipeline}/builds/{number}", "step": "apply-(?P<component>.+?)--(?P<env>.+)", "components": ["sandsql", "api", "executor", "rsw-*"]},
    "pages": {"channels": ["C0PAGES0001", "C0PAGES0002"], "build": "tools/ci bk api /pipelines/{pipeline}/builds/{number}"},
    "interval_seconds": 180,
    "status_card": true,
    "policy": {
      "rule": "prefix",
      "revision": "<revision of the accepted prefix rule>",
      "source": "<repo and PR that accepted the prefix rule>"
    }
  }
}
```

### Install and upgrade

- `lr supervisor install --config <drive>/lr.json` writes both launchd jobs. The supervisor writes the relay's job again at each start, so a change to its plist reaches a live drive with its next release. Its keeper only reads `relay.lock` and posts a blocker to root when the relay stays down past launchd's restart window.
- `lr desks start` first runs `lr relay rebind --config <drive>/runner.json`, which takes the Run for its terminal, then launches the standing desks.
- The relay stays on the Mac because it drives the Mac's Orca app and cuts Mac worktrees; it is the one desk that is not remote. The `desk_ownership` hook refuses `lr relay run` from a session.
- A fix reaches a running relay only as a merged PR and a release that moves `bin/lr.binrun`; nothing edits an installed copy. The supervisor upgrades itself on the new pin and restarts the relay on it.

### Keep the Run bound to the coordinator terminal

- Only the terminal bound to the Run may call `worker-start`. Every pass, the relay reads the Run's coordinator from Orca and calls Orca and starts launchers as that terminal.
- If the coordinator disappears while the relay runs, it posts one `UNBOUND` line and fails every launch at once with `LAUNCH-FAILED`, which no incident clock defers. The coordinator launches by hand with `lr launch start`.
- When the coordinator moves to a new terminal, run `lr relay rebind --config <drive>/runner.json` there; the running relay follows on its next pass without a restart.

## Reach the Orca server on the service host

The cloud service host runs Orca's own server as systemd unit `orca-serve`. It takes no inbound connection, so this Mac reaches it over an AWS Systems Manager (SSM) port forward:

```sh
lr run-host install --host <Name tag> --profile desk-host-reach --region us-west-2 --port 6768 --environment desk-host
```

The command:

1. Reads the unit's `orca_server_ready` event over `aws ssm send-command`, and refuses an Electron fallback, a server bound to another port, and a server that offers no pairing.
2. Installs the launchd job `com.long-running.<drive>.run-host`. It runs `lr run-host forward`, which resolves the one running instance with that Name tag and becomes `aws ssm start-session --document-name AWS-StartPortForwardingSession`. launchd restarts it whenever the session ends, so a replaced box is found again.
3. Pairs this Mac's Orca with the server as `--environment`, unless that environment already exists. The pairing code goes from memory into argv and is never printed.

Two things must already be on this Mac:

- The AWS Session Manager plugin.
- A `desk-host-reach` profile in `~/.aws/config`, with `source_profile = devbox-machine`, the `role_arn` of this machine's CI-account desk-host reach role, and the machine's operator as `role_session_name`. That role may start the port forward and read the ready event only on instances named `*-desk-host`.

Set `orca.launches` to `mac` on this Mac's relay, and to `sprite` on the relay that serves the host's Run.

## Subscribe the coordinator and issue commands

### Subscribe to the `cci` channel at drive start

Under R9 in [SKILL.md](../SKILL.md), launch the coordinator with `--channels plugin:cc-inbox@cc-inbox`, which `allowedChannelPlugins` lists in managed settings, and run once:

```bash
cci subscribe --drive <drive> --reader root --to-kind incident --follow oncall=incident
```

Bus delivery rules, cursor catch-up after compaction, and desk watch commands are in [reference/bus-contracts.md](bus-contracts.md) and R9 in [SKILL.md](../SKILL.md).

### Mint commands with `lr launch`

The coordinator never types an `R<n>` or a clock by hand. `lr launch` posts the line as `cci post --lane <lane> --kind go --to orca-desk --text 'orca-desk: <line>'` and prints the record. A launch's key is `R` and the number every cci node names its record by, such as `R700@8d2775d4`, so two posters on any session or machine never share a key; the relay ignores any `R<n>` a launch line opens with.

```text
lr launch incident <name> <fix-brief> [<evidence-brief> [<comms-brief>]] [--alert TEXT]
lr launch lane <lane> <model> <effort> <brief> [--now]
lr relay <lane>[,<lane>...] <text>
lr launch hold <slug> <owner> <what>
lr launch unhold <slug>
lr launch withdraw <R<n> | unfixed:<msg>:<n> | lane>
```

- `lr launch lane <stem>` numbers the lane itself: `<stem>-<n>`, one past the highest number any lane of that stem ever registered on the drive, so a name is never reused. Its brief may sit in `<stem>/brief.md`. A bare name already registered with no numbered generation, such as `owner-relay`, launches as itself, and an explicit `<stem>-<n>` launches as typed. The new lane holds the stem from its spawn, and the supervisor stands the lane it takes over down for it on the bus.
- `lane` and `incident` first run every launch check and exit naming every fault together, so a missing `Register slice:` and `Scale:` line post nothing. They then wait for `LAUNCHED`, `LAUNCH-FAILED`, `LAUNCH-DUPLICATE`, `LAUNCH-EXPIRED`, or `UNVERIFIABLE` and print the relay's line with the dispatch and terminal.
- A wait that ends with no verdict while the relay's `orca alive` record is under five minutes old prints that the launch is posted and exits 0, since a Sprite launch takes minutes and its verdict arrives as a record. Only a stale `orca alive` record exits 2, naming the `cci tail` that checks it. The other verbs return at once.
- Every launch verdict (`LAUNCHED`, `LAUNCH-FAILED`, `LAUNCH-DUPLICATE`, `LAUNCH-EXPIRED`, `UNVERIFIABLE`, `SPRITE-UNVERIFIABLE`) goes on cci to the lane that posted the go, and to the coordinator only when it posted the go. A go the supervisor or the relay itself posted keeps the launched lane's notice route.
- Submitting the same key again in the same lane returns the existing action. Never `SendMessage` a desk.
- `lr launch` warns and still posts when a live lane already claims an Iris session id or Slack thread its brief names, read with `cci who --ref`, or when the brief says no Slack grant covers its work without quoting a `ccn answer search` by subject. Once it reads `LAUNCHED`, it claims those session ids and threads for the new lane with `cci claim --for <lane>@<drive>`.

### Line length and inbox files

- Post a relay or coordinator reply on cci whole: any length, spanning lines, as one record, without shortening or splitting it.
- Keep every other inbox line under 400 characters; put evidence in a file or cc-notes and leave a pointer in the line.
- Never reconstruct lane state with `grep ... | tail` across files or `orca orchestration inbox | grep -A`. `inbox/` files are never command authority.
- Do not mirror runner actions into `TaskCreate` or `TaskUpdate`.

## Relay, launch, and withdraw lines

### Read orders from cci

The relay reads orders only from cci records addressed to `orca-desk`. It starts at the drive's newest sequence so history never replays.

- A directive opens with `orca-desk:`; the runner skips the clock label or `R<n> (<clock>)` an older record opens with.
- A record without `R<n>` takes the key `inbox@cci#<seq>`.
- Any record addressed to `orca-desk` whose text is a directive runs as that directive even when it carries `--re`.
- A record that is not a valid directive and is not a coordinator `--re` reply logs `DIRECTIVE-FAILED`, quoting how it opens.

### Relay a message to lanes

```text
orca-desk: relay to <lane>[, <lane>…][ and <lane>]: <text>
```

- The first line is the directive and the relay text runs to the end of the record, keeping all paragraphs and lists. One record is one relay.
- A lane named by its stem, with no `-<n>`, is the lane cci routes that stem to, its newest live lane.
- For each named lane with a live dispatch, the relay runs `relay --key R<n>`. When the dispatch's newest question to the Run has no answer on its terminal, the relay replies to that question and releases `orca orchestration ask`; else it sends a plain relay that wakes the terminal.
- Each lane logs `RELAYED <key> <lane>` or `RELAY-FAILED <key> <lane>: <reason>` to the coordinator on cci.
- A key holding different text, or malformed `orca-desk: relay` text, fails visibly and relays nothing.
- A relay to a lane with a dead or missing dispatch waits for a launch in flight, else goes to the lane on cci in the same pass, unless a send already delivered it: a line about a PR as a `blocker`, any other relay as a `note` logged `RELAYED <key> <lane>: … as #<seq>`. The lane's next dispatch reads it from its cursor, so no such relay fails or waits for a `DEADLINE`.
- When Orca is unreachable, times out, errors, or returns non-JSON, the relay stops the pass without marking any dispatch dead or failing any relay. It logs `ORCA-UNREACHABLE` once for the streak, backs off up to two minutes between passes, and logs `ORCA-BACK` on the first pass Orca answers.

**A second order to a busy lane of a pool goes to a free lane of that pool.**

- A pool is a role named at launch with `role=pool-<name>`, for lanes that can stand in for each other. A lane with any other role has a brief of its own and always keeps its orders.
- A lane is busy once it has answered `started` on a relay and not yet `done`; a lane with a delivered relay it has yet to start is not busy.
- A plain relay to a busy lane moves to a live lane of the same pool with no started relay, no open item and no open ask or `blocked` record. A `ROUTED <key> <lane> -> <free lane>: <lane> is busy with <its key>` record names both.
- With no free lane, the order waits behind nobody: the relay posts root one ask, `spawn <role> lane for <key>`, with the order as the brief body, and tells the sender `POOL-NONE` with that ask's number.
- A reply to an open question and a relay naming a PR the busy lane owns also stay.

### Route coordinator `--re` records as question replies

- Any non-directive coordinator cci record carrying `--re`, or `--resolves` on the relay's `DECIDE`, is read as a reply.
- A `--re` or `--resolves` on the relay's `DECIDE` on a worker question, or a `--re` to a lane's own record such as its cci ask, is relayed as the reply to that lane's open Orca question.
- When another reply or inbox relay line already claimed that question, it goes to the lane as a plain relay, because Orca refuses a second answer to one question.
- When Orca refuses a reply with `answer_conflict` because something answered it outside the relay, the relay sends the reply once to the dispatch as a message on the relay's thread.
- A lane with no open question gets nothing on Orca because it reads the answer on cci.

### Launch a lane

Post a launch directive as one record with nothing after the brief path:

```sh
cci post --drive <drive> --lane root --kind go --to orca-desk --text 'orca-desk: launch <lane> [NOW] <model> <effort> [role=<role>] brief=<absolute path>'
```

- `NOW` sets `--owner-directed`, and `role=<role>` sets `--role` for the incident clock.
- An incident fix lane launched from `<state>/<dir>/brief.md` must take `<dir>` as its lane name: `orca-desk: launch <dir> NOW incident xhigh role=fix brief=<state>/<dir>/brief.md`. The relay refuses any other name with `LAUNCH-FAILED` and the command that launches it under `<dir>`.
- `lr launch incident` posts two lines, with `role=fix` and `role=evidence`. The evidence lane attaches `<slug>-evidence.md` to the same briefs log, where the fix brief tells the fix lane to read it.
- Only `incident` runs on the fast tier; plain `sol` runs on the standard tier.

**Freshness and failure checks.** A launch or incident line expires 15 minutes after its record time, or after the longest of the relay's last ten passes when that is longer, logging `LAUNCH-EXPIRED <key> <lane>` with both, and launching nothing. The relay reads desk lines at each pass's start, between its slow steps, and from its `desk-lines` thread whenever ten seconds pass with no read, so neither the sweep nor a slow step holds a launch line. The relay logs `LAUNCH-FAILED <key> <lane>: <reason>` and launches nothing when any of these hold:

- the lane has a live dispatch or a launch in flight,
- the key already holds an action,
- `launch` refuses the model or effort,
- the brief is not a file,
- the line says `orca-desk: launch` outside the exact form, which logs as `LAUNCH-FAILED <key> inbox`, or
- a non-incident-fix brief lacks a `Register slice: lane=<lane> step=<n>` line.

Register slice and scale:

- If a judge reads a non-incident brief's `Scale:` line as missing the N its paths run at, the relay launches anyway and posts `SCALE-MISSED <key> <lane>` to the coordinator.
- Before launching, the relay refreshes the slice with `rulings.py slice --refresh <brief>`. The refreshed slice opens with step one: `ccn answer search <topic>` for each `topics=` entry on the slice line and `ccn relevant <dir>` for each `paths=` glob, each run once, with every answer they found beneath them.
- The refresh writes `Register slice: lane=<lane> step=incident` into an incident brief that lacks one, and into every alert, case, fallback, and manual release brief it attaches.
- An unreadable slice logs `SLICE-UNREAD` and the launch proceeds. A verified launch logs `LAUNCHED <key> <lane>: dispatch <ctx> terminal <handle>`.

### Withdraw a launch or lane

`lr launch withdraw` posts one of:

```text
orca-desk: withdraw R<m>
orca-desk: withdraw <lane>
```

The target may also be `unfixed:<msg>:<n>`, or the coordinator can run `cci post --kind withdraw --re <seq>` on a launch record.

- The relay fails an accepted launch that has not started, stops a launched dispatch with `worker-stop`, marks the lane withdrawn, and logs `WITHDRAWN`.
- Once a lane is withdrawn, its `UNFIXED` lines launch no case lane and a repeat Orca dispatch offer logs `WITHDRAWN`, not `DECIDE`.
- A launch still starting logs `WITHDRAW-PENDING` and is stopped the moment it logs `LAUNCHED`.
- A settled launch with no dispatch, a target holding nothing, or a `worker-stop` Orca refuses logs `WITHDRAW-FAILED`.
- A `worker-stop` Orca accepts that does not show its terminal gone or idle within 30 seconds, such as `stop_unknown`, logs `WITHDRAW-UNCONFIRMED` with the dispatch and terminal; the coordinator runs `lr gc --done` on it.

## Track actions and acknowledgements

### Action states

Actions move `accepted`, `started`, `completed`, `verified`; `failed` and `unverifiable` record failure or missing proof. Question-reply actions complete on send without worker acks.

For guidance relays, sending proves delivery only. The message carries the action's thread id and tells the worker to send status before and after acting, with the addressing and capability arguments from the worker's Orca preamble on both sends:

```text
orca orchestration send --type status --thread-id <thread> --dispatch-id <its dispatch> --subject "started <key>"
orca orchestration send --type status --thread-id <thread> --dispatch-id <its dispatch> --subject "done <key>: <result>"
```

A `done` reply records completion; verification requires the action's external receipt.

### Settle unverifiable sends and resend after 30 seconds

- For an `unverifiable` send, including one whose CLI response is unparseable, the relay checks Orca `request-show`, `--retry-request`, or the recipient's mailbox.
- A send to a dispatch that is no longer the lane's live one stays `unverifiable`, emits `UNVERIFIABLE`, and is never resent.
- When neither a receipt nor mailbox proof appears 30 seconds after sending to a live dispatch, the relay resends on the same thread under the same key every 30 seconds until proof appears, logging nothing for a delivered resend.
- A status the lane posts on the Run naming the relay's `R<n>` or cci sequence also settles the send. It acknowledges `started` when its subject starts with `started`, and `done` otherwise.
- After two resends lost the same way, the relay emits `UNACKED` once with the count and the relay text at the record's path, and stops resending. A resend Orca rejects emits `SEND-FAILED`.

### Relay long cci texts from their attached file

- When `cci post` clips a record over 400 characters and writes the full text to a path, the relay reads that file and relays the full text with line breaks.
- If the file's first line does not match the record's text, it is an attachment and the relay relays the record's own text.
- If the relay cannot read the named file, such as one on another machine, it refuses the relay with `RELAY-FAILED` naming the path; post it again on the relay's machine.

## Follow Orca pass rules O1 to O16

Each pass settles detached launches, delivers accepted relays and launches, reconciles lost sends, reads new Run messages, checks start and launch deadlines and the incident clock, and writes changed views and escalation lines. A quiet pass writes nothing. Every five minutes, the relay sweeps stale mail, prompts, and dispatch liveness.

**O1. Launch through the relay.** The coordinator runs `lr launch lane <lane> <model> <effort> <brief> [--now]`, and the relay runs `launch` and starts `lr launch start` detached. No model desk runs lifecycle helpers.

**O2. Settle a launch from its line or receipt.** A launcher holds a lock on its log until it exits, so a runner restarted mid-launch waits for it instead of reading a half-written log.
- A `ready` or `unsupervised` line counts as launched; `unsupervised` also escalates separately as `UNSUPERVISED`.
- A `<lane> failed` line logs `LAUNCH-FAILED`.
- A new dispatch receipt with neither line counts as launched.
- A launcher that ended with none of these is marked `UNVERIFIABLE` with what the log said quoted, and never triggers an automatic relaunch.

**O3. Keep one Run reader.** Only the relay consumes the Run.
- Each pass takes the Run's oldest unacknowledged Delivery with `orca orchestration check --run <run>` and reads `orca orchestration inbox --terminal run:<run> --limit N --json` past its saved cursor oldest first. It expands full pages until the cursor is covered, skips heartbeats, and saves each sequence in `desk-runner` `facts` under `inbox:<run>`. Once the read is relayed, it acknowledges that Delivery with `--ack <delivery id> --peek`.
- Its first read saves the newest sequence without replaying history, and nothing launches or relays until that read succeeds.
- Relayed message ids are saved under `relayed:<run>`. Any Delivery message with no saved id is relayed before acknowledgement, whatever its sequence, or logged `UNRELAYED` with sender, type, and sequence when its type has no relay.
- No other loop may run `orca orchestration check --run <run>` with `--wait` or `--ack`.

**O4. Only the coordinator answers a worker's question.** The relay escalates every worker `question` or `escalation` to the coordinator as `DECIDE` with the question and options, and never answers one itself.
- Only this drive's coordinator answers: with `relay --reply-to <msg id>`, with `cci post --kind answer --to <lane> --re <DECIDE seq> --text "<answer>"`, with a record that closes the `DECIDE` with `--resolves <DECIDE seq>`, or with `--re` on the lane's own record.
- A peer drive's reply relays nothing. A `--resolves` on any other record, such as a `lift` closing a lane's `hold`, relays nothing.

**O5. Relay to the current dispatch.** Relays check the current receipt and `worker-show`. Completed or failed dispatches receive no new relay. Replies address the original question id through `--reply-to`.

**O6. Never end a session.** The relay never stops, signals, releases, or closes Claude, Codex, Orca, terminals, pseudo-terminal daemons, or their supervisors, except on an explicit `withdraw`. Finished sessions stay open.
- The 5-minute sweep names each newly settled dispatch once in a `RECLAIM` line with the config's `orca.gc` command, which the coordinator runs under R195 in [reference/orca-workers.md](orca-workers.md).
- `lr gc` closes only the idle terminal named in a settled launch's `<lane>.terminal.json` receipt, and keeps and names every other.
- The coordinator ends a lane it decides is done with `<orca.gc> --run <run> --done <ctx>`, without messaging the lane to send `worker_done` first.

**O7. Keep acceptance, start, result, and verification distinct.** The action record tracks `accepted`, `started`, `completed`, `verified`, `failed`, and `unverifiable` as in [Track actions and acknowledgements](#track-actions-and-acknowledgements). Never resend a send that has a receipt, and never resend by hand before `UNACKED`.

**O8. A stale heartbeat never authorizes a relaunch.** Every 5 minutes the relay sweeps receipt dispatches. A non-live active dispatch emits `LIVENESS`; resume its session in place. A requested relaunch offers the lane container to the new dispatch, whose first ack takes ownership at the next generation. Until that ack, the old owner retains authority; after transfer, another dispatch's ack gets a stand-down reply and cannot move the original action.

**O9. Never re-brief.** Update the attachment with `ccn log append <briefs log> --entry "<what changed>" --attach <lane>.full.md --replace`, then relay its pointer from `ccn attachment path <briefs log> <lane>.full.md`. Never start another lane to carry a follow-up.

**O10. Never answer stale questions to a replacement.** Keep the original question id as the reply address. The relay has no stale-sender fence; never report this rule as an enforced runner guarantee.

**O11. Every ask or report in Run home reaches the bus.** A `question` or `escalation`, which `orca orchestration ask` writes as `question`, escalates as `DECIDE`; a `status` that is no milestone and names no relay escalates as `REPORT`. Both go to the desk that launched the lane, or its notice lane when none did or cci refuses the desk, once per message id. Once per Run, the relay replays Run home's mail behind its cursor that never reached the bus and posts the count as `REPLAYED` to the meta desk.

**O12. Pause ten seconds between Run reads.** The relay sleeps 10 seconds between passes. The landing process sleeps for `landing.interval_seconds` (default 180). Model desks retain the 60-second inbox-wait rule.

**O13. A prompt is a bug, and the prompt watch finds it.** `lr watch prompts` is the one reader of Orca's `agentWait`, as [reference/watchers.md](watchers.md) describes.
- The relay's 5-minute sweep reads the watch's last poll and emits `PROMPT-WATCH-DOWN` once an hour while that poll is over two minutes old.
- Stale unread mail gets one terminal wake per message, typed only when the lane's screen shows the plain empty prompt, Claude's or Codex's. Completed or failed dispatch mail emits `STALE-MAIL`.
- No sweep relaunches a worker or types a guessed answer to a prompt.

**O14. Mac load queues and refuses no launch.** Before each Mac launch, the relay reads how busy the CPU was over the last minute from the kernel's tick counters, never the load average, which counts every sleeping shell.
- At 90% busy or more, an Orca worktree checkout can time out, so every 10 minutes of it posts one `LOAD-HIGH` blocker to the drive watchdog, which moves the load to in-region compute under R15 in [SKILL.md](../SKILL.md).
- A `role=desk` or `role=watch` launch starts on a Sprite at once, and its `LAUNCHED` line names the box.
- A launch that may run only on the Mac starts there anyway: the coordinator, a `place=local` brief naming its need with `why=` (the author overriding load), a Fable or incident launch, and an incident clock fallback. An incident launch also posts one `LOAD-INCIDENT` report.
- A `place=local` brief whose only reason is `role=read` or `why=predecessor-worked-here` only prefers the Mac. When the Mac's one-minute load average from `sysctl vm.loadavg` is above `orca.load_limit`, it starts on a Sprite, and on a Sprite fault it starts on the Mac as before.
- The first runner to read the launch records that reading in the drive's `launch-load/<key>`, so the Mac's runner and the host's runner route the launch on one reading; an unreadable load launches as today.
- Its `LAUNCHED` line names the reading, as in `on Sprite pool-1: Mac load 41 over 32`, and a read lane's pointer tells it that its Sprite's cci store starts at its launch, so an absence it finds needs a check on the Mac.
- `NOW` does not change where a lane starts; it only launches beside a live lane holding the same work. The relay never kills a worker to lower load. A Sprite launch never reads Mac load.

**O15. Preserve the codex and incident launch routes.**
- `sol`, `codex`, and `gpt-*` use Orca's codex agent on the standard tier.
- Incident launches name `--model incident --effort xhigh`, which selects `gpt-6.1-sol`, a `--no-parent` worktree, and an explicit terminal command with `--dangerously-bypass-approvals-and-sandbox` and `-c service_tier=fast`.
- The Opus fallback names `--model incident-opus`, the only alias whose Claude command line turns fast mode on.
- Keep Orca's runtime defaults unchanged. Inline lanes use `Skill(codex)` or `codex:codex-wrapper`; one-off questions use `codex-ask`.

**O16. Default ordinary lanes to a Sprite.**
- Placement: a brief's `ccx:` line sets `place=remote` for a Sprite or `place=local why=<reason>` for the Mac. `place=local` with no `why=` goes to a Sprite. With no `place=`, an ordinary lane goes to a Sprite even without `cpu=high`, running `orca.sprite.launcher` (`lr launch worker`).
- Local always: the coordinator, lane `root`, Fable, incident, and an incident clock fallback launch locally whatever the line says, and `lr launch` walks no Sprite payload for them.
- Standing desks: a `role=desk` or `role=watch` brief takes a Sprite like any lane unless O14 keeps it on the Mac, and `lr desks start` refuses one that says `place=local`.
- Carried files: each file under the home and each ccn doc or task the brief names, rendered from `orca.briefs.repo`, reaches the Sprite under `/home/sprite/drive/carried`, and the brief the worker reads names those copies. A brief naming a path that does not exist, a credential path, or a path over 64 MiB fails the launch with `LAUNCH-FAILED` naming it, and `lr launch` refuses such a launch before it posts. [reference/orca-lane-brief.md](orca-lane-brief.md) states when to choose each placement.
- Fails with `LAUNCH-FAILED` and starts nothing locally: missing `orca.sprite`, a lane name a Sprite refuses, or a `WORKER_CC_REMOTE` or `CC_REMOTE_CONFIG` checkout not on `dev` or `cc-remote-<version>`. So do receipts in the brief's `.worker` directory that lack `worker-start.json` and have a nonzero exit in `prepare.status`, or lack `prepare.status` after the launcher exited, which prove `worker-start` did not run.
- Unverifiable: any other Sprite launch without a ready line is marked `unverifiable`, logs `SPRITE-UNVERIFIABLE` with the receipt directory and any Task and Dispatch, and starts nothing locally; inspect them before relaunching. A Sprite launch that prints nothing is `UNVERIFIABLE` and is never relaunched.
- Hook drift: once a Sprite launch settles, the relay reads `captainHookDrift` from the `.worker` directory's `allocation.json` and raises one `HOOK-DRIFT` blocker per hook the Sprite's Captain Hook cannot load. It goes to the drive's meta desk, the live lane the lane registry records with role `meta`, or to root when the drive has none. Each blocker is keyed by hook and import error, so a later launch with the same drift raises nothing new, and a refused post waits for the next pass without touching the settled launch.
- Model and effort are identical on both routes, the relay sets no Codex service tier on either, and remote API keys travel only to the worker process.

**Grading is not this desk's.** With `grading` listed in `lr.json`, `lr supervisor run` dispatches every finder pass and fidelity grade; see [reference/landing.md](landing.md).

## Handle alerts, incident clocks, and case lanes

Full coordinator incident execution, scope rules, and comms under R16 are in [reference/active-alert-brief.md](active-alert-brief.md). The sections below cover the relay's intake, clocks, and case-lane mechanics.

### Process `alert` and `incident` lines

`monitor-watch.py --alert-drive` posts `alert` records to `orca-desk`, and the alerts desk writes an `incident` line when it judges a lane necessary. A line naming a phrase of a ruling labelled `drop-class` is logged at debug and dropped before any of this. Those phrases live on the ruling itself, as its `match:` labels in cc-notes, so the owner's ruling is recorded once; the desk and supervisor read them when they start. A launch brief naming one still launches, with a `BRIEF-NAMES-RULING` warning to whoever launched it and the ruling in its slice.

```text
orca-desk: alert <slug> <link> :: <what fired>
orca-desk: incident <slug> <link> :: <what fired>
```

- The incident line's record time is the report time its brief's clock runs from, and `<slug>` may contain `_` characters.
- A malformed line logs `ALERT-FAILED` or `INCIDENT-FAILED` and launches nothing.
- `alert`: the relay logs `ALERT` and launches nothing; a repeat relays to a live fix lane.
- `incident`: the relay checks cci at intake and right before launch for another owner. Another owner shows as a coordinator `done` resolving a record naming `<slug>`, a coordinator `stopped` addressed to `<slug>-fix`, or an `answer` from another drive's coordinator addressed to `root@<this drive>` naming `<slug>`. If owned elsewhere, it fails any pending launch and posts one `state` record: `INCIDENT-OWNED <key> <lane>: incident <slug> owned by <who>; no launch`.
- Otherwise it fills [reference/alert-fix-brief.md](alert-fix-brief.md) with `alert.facts`, attaches it to `orca.briefs.log` as `<slug>-fix.full.md` with `ccn log append --attach --replace` instead of writing under `~/.claude/scratch`, and launches `<slug>-fix` on `incident xhigh` at that attachment path. A brief that fails to attach logs `INCIDENT` with the `ccn` error and launches nothing.
- A repeat `incident` line relays to the live lane and keeps its brief.
- For a Datadog alert, the relay posts `INCIDENT` to the coordinator with topic `dd-<id>`; a `fix-live` record with `--topic dd-<id>` closes it.

### Clock every incident fix lane the relay launches

A lane is clocked when launched on the `incident` alias or when its brief's `ccx:` line says `role=incident` or names an `incident=`. A launch naming another role, such as `role=evidence` from `lr launch incident`, `incident=none`, or a lane name containing `evidence`, `proof`, `closeout`, `desk`, or `audit`, is never clocked. On each pass, the relay reads the lane's cci records, posted as `<name>` or `<name>-fix`, and its `mechanism:`, `ready:`, and `fix-live:` Run statuses:

| Minutes since launch | Missing milestone | Runner action |
|---|---|---|
| `deadlines.incident_mechanism_minutes` (default 10) | `mechanism` record or status, or any record whose text says `mechanism` | Launches `<lane>-opus` fallback and logs `INCIDENT-CLOCK <lane> mechanism`. |
| `deadlines.incident_pr_minutes` (default 15) | `opened` record | Launches `<lane>-opus` unless already launched, and logs `INCIDENT-CLOCK <lane> pr`. |
| `deadlines.incident_ready_minutes` (default 20) | `ready` record or status, or a `report` or `incident` starting `READY` | Launches `<lane>-opus` unless already launched, and logs `INCIDENT-CLOCK <lane> ready` with missing milestones and phase minutes. |

```text
INCIDENT-CLOCK <lane> ready: not ready 20 min after its launch at <time>, limit 20 min; missing PR, ready; launch to mechanism 7 min, mechanism to now 13 min | the Opus fallback <lane>-opus is already launched
```

**The `<lane>-opus` fallback and clock lifecycle.**

- A later milestone satisfies earlier thresholds, a record posted before launch does not count, and each threshold logs once per launch.
- The clock stops at `ready`, a `fix-live`, `not-ours`, `stopped`, or `stand-down` record, a `fix-live:` status, a coordinator `hold` addressed to or naming the lane, or when the dispatch settles. A coordinator `decide` steers the lane and leaves its clock running.
- The fallback is `<lane>-opus` on `incident-opus xhigh`, Opus 5.5 in Claude fast mode, launched on the Mac as `--owner-directed`. Its brief is the lane's brief under a header stating why the clock started it, that it works in parallel and the first PR wins, and that it posts on cci as `<lane>-opus` to `<lane>` and the coordinator. The relay attaches it to the briefs log as `<lane>-opus.full.md` and writes `<state>/briefs/<lane>-opus.full.md`.
- A `-opus` lane is clocked but gets no fallback of its own. A fix lane gets at most one Opus fallback.
- If the Orca launch of `<lane>-opus` fails, the relay retries once under `<key>:retry`. After a second failure while the clock runs, it logs `INCIDENT-CLOCK <lane> in-process: the Orca launch of the Opus fallback <lane>-opus failed twice: <reasons> | coordinator: start <lane>-opus in-process now, ...` with the brief path and launches nothing more.
- `show` prints each clocked lane's minutes and milestones.

**Count milestones across the incident.**

- Siblings are the clocked fix lanes sharing the lane's incident name or `incident=` slug, such as `inc-disk-cred` beside `inc-disk-fix`, a `-case<n>` sibling, or the `-opus` fallback. The incident name is the lane's name less `-opus`, `-case<n>`, and its last `-<part>`; a two-part name such as `api-fix` is its own.
- One mechanism, PR, or ready anywhere among the siblings counts as the lane's mechanism, so no mechanism fallback launches, while the PR threshold still clocks the lane that has no PR.
- One `fix-live` anywhere among the siblings, a cci record or a `fix-live:` status, stops every one of their clocks.
- A clock whose lane the coordinator withdrew, or sent `stand-down` or `stopped`, ends as `withdrawn by <who>` and posts no `INCIDENT-CLOCK` line.

**Defer outside escalations while a clock runs.** While any incident clock runs, `DEADLINE`, `NOT-DELIVERED`, `UNACKED`, `LIVENESS`, `STALE-MAIL`, and `RECLAIM` escalations about anything outside the incident go to the relay's view only. When the last clock ends, they reach the coordinator as one `DEFERRED <n> escalations held during <incident>` record naming each.

### Launch case lanes from `UNFIXED` lines

- Fix lanes close their `ready:` and `fix-live:` Run statuses with one `UNFIXED <lane>: <case>` line per uncovered case, or `UNFIXED <lane>: none`. Only a line that names the reporting lane counts, and `UNFIXED <lane>: none` launches nothing.
- Only `ready:` and `fix-live:` statuses launch case lanes; `worker_done`, `NOT-OURS`, `STOPPED`, or a report opening with `NOT-OURS`, `stood down`, or `Stopped` launches nothing.
- Each unique case across a lane, its `-opus` fallback, and its case lanes launches `<first>-case<n>` once on `incident xhigh` in the same pass, where `<first>` strips trailing `-opus` and `-case<n>`, and `<n>` is the lowest unused container index.
- The relay fills [reference/unfixed-case-brief.md](unfixed-case-brief.md) with the case verbatim, the reporting lane, the path of that lane's brief, and the PRs its cci `opened` records name. It attaches `<first>-case<n>.full.md`, clocks the new lane, and logs:

```text
INCIDENT <lane>: its fix does not cover: <case> | case lane <first>-case<n> launching on incident xhigh, brief <path>; coordinator: ratify, and keep the reporter told that this case is still open
```

- A case whose launch is refused, or whose brief does not attach, logs `not launched` with the reason and launches on the next report that names it.
- If a case is past `incident.case_lanes` (default two), if a case lane reports it since case lanes are one generation, or if the report or newest `mechanism` says `UNKNOWN`, the relay attaches the brief without launching and posts:

```text
DECIDE unfixed:<msg> <lane>: <reason>: <case> | to launch its lane, post `orca-desk: launch <first>-case<n> NOW incident xhigh brief=<path>`; <case> | …
```

- An incident ends at a `not-ours` or `stand-down` from the fix or `-opus` lane, a `stopped` posted by the reporting lane before its report, or a coordinator `stand-down` or `stopped` to the fix, `-opus`, or case lane. A report after the end or from a withdrawn lane launches nothing and logs:

```text
UNFIXED <lane>: <case>; <case> | no case lane launched: the incident ended with #<seq> <KIND> <lane>
```

- A pending case launch whose incident ends fails and logs `WITHDRAWN unfixed:<msg>:<n> <first>-case<n>: the incident ended with …`.

## Hold an urgent release

A lane holding an urgent release or a blocked catch-up of a coupled control-plane set posts a hold with `lr launch hold`, and lifts it with `lr launch unhold` when the release moves, as R9a in [SKILL.md](../SKILL.md) describes. It names itself or a dedicated decision-owner lane as owner. A held or blocked coupled release gets its own decision-owner lane.

```text
orca-desk: hold <slug> owner=<lane> :: <what is held, and on what>
orca-desk: unhold <slug>
```

- Repeating an active hold keeps its first clock; a hold after `unhold` starts a new clock. A malformed line logs `HOLD-FAILED`.
- After `deadlines.hold_minutes` (default 15), the relay writes once per hold:

```text
DECIDE hold:<slug> <owner>: held <n> min: <what> | the coordinator decides it now with <owner>, ahead of any open owner question on another subject
```

The hold verb records a decision clock only; it does not hold or lift a PR or authorize a release.

## Act on relay escalations

### Where escalations arrive

- Each escalation posts to the config's `drive` with `--lane desk-runner --to root` and the line's second token, its key, as `--topic`.
- An escalation about a meta lane, one the lane registry records with role `meta` or launched by a meta lane, goes to that lane's launcher instead, and one about the meta desk itself goes to `owner-relay`.
- Each line formats as `<KIND> <key> <lane>: <text>`, and its time is the record's own. Kinds map to cci kinds: `DECIDE` to `decide`; `INCIDENT` to `incident`; `UNBOUND`, `UNOWNED`, `UNACKED`, `INCIDENT-CLOCK`, and `LOAD-HIGH` to `blocker`; any `*-FAILED` to `defect`; all other labels to `report`.
- Text over 400 characters is clipped, saved to `<receipts>/cci/<hash>.txt`, and attached with `--path`.
- Landing restack routes sent through cci use `--kind blocker --to <lane> --topic <pr>` with the same text limit and attachment rule. A landing's release record and its blockers use `--kind release` and `--kind blocker` with `--to <landing.release_owner>,root --topic <pr> --pr <pr>`.
- Store keys deduplicate escalations; liveness keys include the hour and `ORCA-INBOX` keys include a 10-minute bucket so an unchanged fault can recur.

### What the coordinator does for each kind

| Kind | Coordinator action |
|---|---|
| `DECIDE` | For `hold:<slug>`, decide with the named lane ahead of unrelated owner questions under R9a in [SKILL.md](../SKILL.md). For a worker question, answer with `cci post --kind answer --to <lane> --re <DECIDE seq>` (a record that `--resolves <DECIDE seq>` relays too) or `relay --reply-to <msg id>`. For `unfixed:<msg>`, post the launch line of each case worth a lane. |
| `INCIDENT` | Follow R16 and [reference/active-alert-brief.md](active-alert-brief.md) to ratify the fix launch, adopt the evidence and comms lanes it started, and start the incident-doc lane, or ratify a launched case lane and update the reporter. |
| `INCIDENT-OWNED` | None. Another owner holds the incident; verify the named record agrees. |
| `INCIDENT-CLOCK` | Follow [reference/active-alert-brief.md](active-alert-brief.md). Post the line as a `report` to `owner-relay` ahead of other work, ratify or launch `<lane>-opus` or start it in-process as an `Agent` of type `long-running:lane-ship` on `opus` in fast mode for `in-process`, and clear what holds the missing milestone. |
| `MERGE-ASK`, `MERGE-CARD-FAILED`, `MERGE-MODE-UNSET`, `MQ-CONFIG-INVALID`, `FILES-READ-FAILED`, `STALE-POLICY`, `ENQUEUE-STRANDED`, `ENQUEUE-UNSETTLED`, `ENQUEUE-FAILED`, `ROUTE`, `UNOWNED`, `GITHUB-BACKOFF`, `QUOTA-READ-FAILED`, `RELEASED`, `RELEASE-HELD-AFTER-LANDING`, `CUT-SILENT`, `PARKED`, `DEFERRED`, `SQUASH-FAILED`, `RELEASE-FAILED` | Landing and release escalations; act per [reference/landing.md](landing.md) and [reference/release-lanes.md](release-lanes.md). |
| `RULING-ASK` | Queue ruling confirmations for the next board round with `rulings.py round --drive <id>`, presenting 5 to 10 cards per round with foundational rows first, and confirm or reply per [reference/owner-rulings.md](owner-rulings.md). |
| `SLICE-UNREAD` | Read the `rulings.py slice --refresh` failure, fix the register or the brief's `Register slice:` line, run the refresh by hand, and relay the fresh slice to the lane. |
| `LOAD-HIGH` | Move local passes, backfills, dry runs, or bulk rewrites to in-region compute in that turn. Never wait the load out. |
| `ALERT-FAILED`, `HOLD-FAILED`, `DIRECTIVE-FAILED` | Correct the line's grammar, slug, link, or form and post it again. |
| `RELAYED`, `ORCA-BACK` | None; informational confirmation. It stays on the bus under the runner's lane and is not addressed to the coordinator. |
| `LAUNCHED` | None; it tells the launcher that a launch it posted settled. |
| `RELAY-FAILED` | Fix the relay's key or form and send under a new key. If a file path was unreadable, post the relay again on the relay's machine. |
| `LAUNCH-EXPIRED` | If the lane is still wanted, post a fresh launch line under a new key. |
| `LAUNCH-FAILED` | Read by the lane that posted the go. Read the failure before issuing a new action; relay to a live dispatch or post a corrected launch line under a new key. A launch that never started a worker leaves the brief free to launch again. |
| `DEADLINE` | Resolve the action's missing delivery, start, launch, or enqueue proof. A lane that posted a `head` or `review` record for the relayed PR since the relay, or sent a heartbeat after it, counts as started and draws no `DEADLINE`; a lane that sent a heartbeat within 30 minutes before the relay draws no `DEADLINE` for 30 minutes. |
| `NOT-DELIVERED` | Read by the lane that posted the relay. Orca accepted the send, but past its deadline the message is still unread in the worker's mailbox, heartbeats or not. It fires only when the lane has posted nothing since the relay and is still live: a lane that posted, a retired or stood-down lane, and a missing terminal draw none, since a lane can read mail without marking it. The desk has tried one wake on the worker's own server; if the lane stays silent, reach it another way or relaunch it. |
| `UNACKED` | Read the lane's Run messages first; if it never acted, deliver the relay text at the record's path by hand. |
| `UNVERIFIABLE` | Reconcile the missing external receipt; never repeat the mutation blindly. |
| `SPRITE-UNVERIFIABLE` | Inspect the named `.worker` receipt directory and any Task or Dispatch before relaunching; a remote worker may be running. |
| `HOOK-DRIFT` | Pin a `captainHook` build that loads the named hook in the monorepo's `tools/cc-remote/inventory.yaml`; until it ships, that hook's guard is off on every Sprite. |
| `BASE-DRIFT` | A new Sprite's packages differ from the base its payload captured, so they installed online once and the launch took longer; rebuild the payload on that base. |
| `OUTCOME` | Consume the worker's `worker_done` result. |
| `FIX-LIVE`, `MECHANISM`, `READY` | Read the lane's status milestone by message id; the line includes the subject and up to 300 body characters. |
| `PROMPT-WATCH-DOWN` | The supervisor runs the watch, so read the supervisor's log, then run the line's `lr watch prompts` command. |
| `LIVENESS` | Inspect the non-live dispatch and preserve its session. |
| `STALE-MAIL` | Resolve unread work on a completed or failed dispatch. |
| `RECLAIM` | Run the `orca.gc` command from the line under R195 in [reference/orca-workers.md](orca-workers.md). |
| `UNSUPERVISED` | Arrange the launched lane's cci reporting without launching it again. |
| `SEND-FAILED` | Resolve a send that failed three explicit attempts or a resend Orca rejected. |
| `DECISION_GATE`, `HANDOFF` | Act on the worker's gate or handoff. |
| `ORCA-INBOX` | Resolve the mailbox error without restarting Orca. |
| `ORCA-UNREACHABLE` | None while brief; if no `ORCA-BACK` arrives after 10 minutes, check `orca status` and report to `owner-relay`. Never restart Orca. |
| `FINDER-FAILED` | Inspect the failed pass's `job.log`, fix the error, or ask `finder-desk` for `finder: whole`. |
| `GRADE-STOPPED` | No pass is left to run at this head, for the reason it names. Its grade item is closed and a verdict item is open for the lane. Get the grade it needs: a desk's read for a reader handoff, `finder: whole` for a failed finder. |

The relay never edits worker code, mutates production, writes Slack, invents scope, or derives authority from a branch prefix or display name. The coordinator owns decisions and holds; lanes own their worktrees.
