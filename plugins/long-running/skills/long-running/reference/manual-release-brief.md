ccx: lane={lane} role=release place=local incident=none effort=xhigh

# {lane}: cut and publish {component} now, by hand if the standard start refuses

The orca desk runner launched you because `{release_lane}` posted no CUT and no
refusal within the cut deadline after {trigger}. This brief is the `{lane}.full.md`
attachment on the drive's briefs log `{log}`. Work from a fresh checkout of `{repo}`
under `~/.claude/worktrees/<repo>/{lane}`.

Post on cci as `{lane}` to `root` and `{release_lane}`:
`cci post --drive {drive} --lane {lane} --to root,{release_lane} --kind <release|refuse|fix-live|state> --target {component} ...`

Do, in order:

1. Read `cci tail --drive {drive} --lane {release_lane} --since 30m`. When
   `{release_lane}` has since posted a `release` record with `--build` for
   {component}, watch that build instead of starting another.
2. Start the release: `{release} start --emergency {component}` at commit
   `{commit}` or newer. Post `--kind release --target {component} --build <url>` with
   the release id `releases/<id>` in the text the moment the build exists.
3. When the standard start refuses, post the refusal verbatim as
   `--kind refuse --target {component}`, then run the hand path:
   `{hand}`. Fill `--env` and each claimed `<component>/<env>` from the release
   skill's target table when they read `<env>`. A claim that refuses names the
   owner of an overlapping incident or rollout claim: post it to root as a
   `blocker`, since only that owner can hand it over with `{release} transfer`.
4. Watch to the publish. `{release} live <id> --json` names the live version;
   post `--kind fix-live --target {component} --build <url>` with
   `RELEASED {component} <version> (<build>)` as its text.

Never ask the owner anything; a refusal you cannot get past goes to root as a
`blocker` with the command and its output.

Scale: no N; this lane cuts a release and writes no code.

Register slice: lane={lane} step=release
