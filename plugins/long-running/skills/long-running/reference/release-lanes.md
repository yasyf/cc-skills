# Release lanes in a drive

The release tooling a drive deploys with records nothing about drives. Its freezes
name only the person or session that set them, and its watch posts no cci record when
an apply starts. A start claims nothing on cci either. The release owner and every
lane that starts, freezes, or watches a release post those records themselves, in the
shapes below. `landing.release_owner` in `runner.json` names the release owner
(SKILL.md D20).

## Claim each release you start

Right after a start prints its build, claim it so peers can find the session that
owns it:

```sh
cci claim --build <build number> --rel started
```

`cci who --build <build> --rel started` then names that session and the address that
reaches it. Only the session that started a release cancels it. To stop a release
another session started, find it with `cci who` and ask it with
`cci post --drive <drive> --lane <lane> --kind ask --to peer:<its session prefix> --build <build> --text "<why it should stop>"`.
Never cancel it yourself, and never cancel a running apply at all.

## Announce an apply peers watch

Before a component that other drives watch starts applying, such as a database, the
API, or the executors, the lane watching that release tells every live drive root, so
a peer drive pauses its own watch first. Read the roots with
`cci who --role root --json`, then post one record addressed to each:

```sh
cci post --drive <drive> --lane <lane> --kind release --id <apply job id> \
  --stack <project>/<env> --build <build url> --to root --to root@<peer drive> \
  "applying <project>/<env> <image>@<commit, 8 chars> (<release id>)"
```

The `--id` key makes a repeat post of the same apply job a duplicate, so a restarted
watch never announces it twice. Name every image the apply rolls, sorted by name.

## Own every freeze you set

A freeze records its setter and nothing else, so a person who hits it cannot tell
which lane holds it. A lane's freeze carries its owner in the reason, and the lane
posts a hold record the moment the freeze exists:

```sh
tools/ci release freeze <component> --until <end of the guarded step> \
  --reason "<why>; owner <lane>@<drive>"
cci post --drive <drive> --lane <lane> --kind hold --target <component> --to root \
  --text "froze <component> until <time> as freeze <id>: <why>"
```

Set `--until` to the end of the step the freeze guards, and freeze again to extend
it. A rollback writes its own `rollback-<build>` freeze; the lane that ran the
rollback owns it the same way and posts the same hold record. To lift it, run
`tools/ci release unfreeze <id>`, then close the record with
`cci post --drive <drive> --lane <lane> --kind lift --resolves <hold seq> --target <component>`.
A person or lane that needs a freeze lifted posts an `ask` to the `<lane>@<drive>`
its reason names.

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
