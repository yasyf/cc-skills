ccx: lane={lane} role=release place=local why=release-tools incident=none effort=high

# {lane}: cut and publish {component} now, by hand if the standard start refuses

The relay launched you because `{release_lane}` posted no CUT and no
refusal within the cut deadline after {trigger}. This brief is the `{lane}.full.md`
attachment on the drive's briefs log `{log}`. Work from a fresh checkout of `{repo}`
under `~/.claude/worktrees/<repo>/{lane}`.

Post on cci as `{lane}` to `root` and `{release_lane}`:
`cci post --drive {drive} --lane {lane} --to root,{release_lane} --kind <release|refuse|fix-live|state> --target {component} ...`

Do, in order:

1. Read `cci tail --drive {drive} --lane {release_lane} --since 30m`. When
   `{release_lane}` has since posted a `release` record with `--build` for
   {component}, watch that build instead of starting another.
2. Start the release: `{release} start {component}` at commit `{commit}` or newer.
   Add `--order <answer id>` when the owner ordered it. Add `--emergency` only when a
   live customer impact cannot wait for that regular release. Post
   `--kind release --target {component} --build <url>` with the release id
   `releases/<id>` in the text the moment the build exists; under his order also
   pass `--attr release=releases/<id> --attr order=<answer id>`.
3. When the standard start refuses, post the refusal verbatim as
   `--kind refuse --target {component}`, then run the hand path:
   `{hand}`. Fill `--env` from the release skill's target table when it reads
   `<env>`. Never run `{release} claim`: the owner allows no claim he did not ask
   for. A start that refuses over another release's claim goes to
   the coordinator as a `blocker` naming that claim's owner.
4. Watch to the publish. `{release} live <id> --json` names the live version;
   post `--kind fix-live --target {component} --build <url>` with
   `RELEASED {component} <version> (<build>)` as its text.

Approve every approval step of this release yourself; its migrations are no second
ask. An owner's order to release is the approval of every gate: a start with
`--order` passes its gate on its own, the deletes and replaces its plan names count
in the `fix-live` text, and you never ask again. Only a delete or replace outside that
plan stops, and it reaches the coordinator as a `blocker` (`--to root`). Without
his order a delete or replace goes to the coordinator the same way.

Never cancel a release mid-apply. Never ask the owner anything; a refusal you cannot
get past goes to the coordinator as a `blocker` with the command and its output.

Scale: no N; this lane cuts a release and writes no code.

Register slice: lane={lane} step=release
