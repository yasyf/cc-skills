# Release lanes in a drive

The release tooling a drive deploys with records nothing about drives. Its freezes
name only the person or session that set them, its watch posts no cci record when an
apply starts, and a start claims nothing on cci. The orca desk runner couples the drive
to its releases from the records its lanes post, so a lane posts the two records below
and the runner does the rest. `landing.release_owner` in `runner.json` names the release
owner (SKILL.md D20), and `landing.release_cli` names the release command line.

## Post each release you start

Right after a start prints its build, post the build URL and the release id:

```sh
cci post --drive <drive> --lane <lane> --kind release --target <component> \
  --build <build url> --to root "CUT releases/<id> build <number>"
```

The runner then records `<lane>@<drive>` as the build's owner under `releases` in the
drive registry and runs `cci claim --for <lane>@<drive> --build <build url> --rel started`,
so `cci who --build <build> --rel started` names your session; it needs cc-inbox 0.23.0
or newer. A claim it cannot make logs `RELEASE-CLAIM-FAILED` to the root once. Only the
session that started a release cancels it. To stop a release another session started,
find it with `cci who` and ask it with
`cci post --drive <drive> --lane <lane> --kind ask --to peer:<its session prefix> --build <build> --text "<why it should stop>"`.
Never cancel it yourself, and never cancel a running apply at all.

With `landing.applying` set, the runner reads each build it tracks every 15 seconds until
the build finishes, and the first time a job whose step key matches
`landing.applying.step` reads running, for a component `landing.applying.components`
names, it posts one `release` record, keyed by the job id, to the root of every live
drive: `applying <project>/<env> <commit, 8 chars> (<release id>)`. A peer drive pauses
its own watch on it. Post no `applying` record yourself.

## Hear every page while a release runs

With `landing.pages` set, the orca runner reads each release build a lane's `release`
record names, by `--build` or as a Buildkite URL in its text, until the build finishes.
From the record's time until 30 minutes after the last such build finishes, it reads
the newest top-level messages of each channel `landing.pages.channels` lists every 15
seconds and posts each new one to the root once, as an `incident` record carrying its
permalink as `--url`. The root takes each one as the Incident Turn's alert link: the
incident, its fix lane, and a reply in the page's thread, that turn, with no owner
round-trip. Outside that window the runner reads no channel.

## Own every freeze you set

A freeze records its setter and nothing else, so a person who hits it cannot tell
which lane holds it. A lane's freeze carries its owner in the reason, and the lane
posts a hold record the moment the freeze exists, with the until in RFC 3339:

```sh
<release cli> freeze <component> --until <end of the guarded step> \
  --reason "<why>; owner <lane>@<drive>"
cci post --drive <drive> --lane <lane> --kind hold --target <component> --to root \
  --text "froze <component> until <RFC 3339 time> as freeze <id>: <why>"
```

Set `--until` to the end of the step the freeze guards, and freeze again to extend
it. A rollback writes its own `rollback-<build>` freeze; the lane that ran the
rollback owns it the same way and posts the same hold record. To lift it, run
`<release cli> unfreeze <id>`, then close the record with
`cci post --drive <drive> --lane <lane> --kind lift --resolves <hold seq> --target <component>`.
While the hold record is open, the runner posts the lift asks: one to the lane once its
until passes, and one to the root once the lane has no live session. A person or lane
that needs a freeze lifted posts an `ask` to the `<lane>@<drive>` its reason names.

## Hand back what you cannot move

A release lane asks the drive's root, never the owner:

- Owner presence unclear before an off-hours start: ask the root first.
- An apply that stops making progress: send the root a `blocker` with the build URL
  and the last progress line, and leave the apply running.
- A pipeline refusal the release skill gives a hand path for: post the refusal
  verbatim as `--kind refuse --target <component>` with the build URL and what the
  hand path applies, then run it.
- A step that retries on a lost agent: post one `defect` record per loss, naming the
  step and the build.

## Check release order at READY

A PR whose change reads data or a payload another component writes carries a
`Release-Order: <writer> before <reader>` footer, one per pair, and the landing desk
checks for it when the lane reports READY. A `Release-With:` line only ships the
components together and orders nothing. A PR that needs an order and carries only
`Release-With:` is not ready, and the desk answers the lane's report with a `blocker`
naming the missing `Release-Order:` line.

## Cite the ruling a GO carries

A `go` record that acts on an owner ruling cites the cc-notes answer id with
`--ccn <answer id>`, and the PR it lets through cites the same id in its body. A GO
never goes out while an owner question on its subject is still open; the pack's
`owner_facing` hook blocks one written beside an unanswered `AskUserQuestion` or a
board posted that turn.
