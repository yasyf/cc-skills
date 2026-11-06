---
name: bulk-read
description: Read a large corpus on Sonnet and triage it on Opus into fix lanes as one verb, lr bulk read, so the caller's context stays small and every claim carries its record seq or transcript line. Use when a question takes more than a few reads of transcripts, cci records or logs, such as where a session struggled, what a drive's records show, or what a log says went wrong, and the answer should end in fix lanes rather than a summary in the caller's window. Not for a lookup whose target you already know, which is one cci show or cc-transcript show.
---

# Bulk read on Sonnet, triage on Opus

`lr bulk read` takes a corpus and a question. A Sonnet lane reads the corpus into a findings file, an Opus `high` lane triages that file into fix lanes, and you keep only a path and two lane names.

```sh
lr bulk read <lane> --drive <drive> --question "<question>" --transcript <file> --records "--lane root --since '2:13 AM'" --log <file> --launcher <lane> --contract <file>
```

Name the lane `<topic>-1`; the triage becomes `<topic>-triage-1`. Each corpus flag repeats, and at least one is required:

| Flag | Takes | Read through |
| --- | --- | --- |
| `--transcript` | one file `cc-transcript list --project <substring>` printed | `cc-transcript`, never a recursive search of `~/.claude` |
| `--records` | the arguments of one `cci tail` over the drive's records | `cci tail`, paged forward by seq |
| `--log` | one log file | line ranges |

`--launcher` is the lane that launches the fix lanes, by default the lane calling the verb. `--contract` names a file every fix brief must point at, the launcher's contract for its helpers.

## What comes back

Three lines: `findings: <path>`, `reader: <lane>` and `triage: <lane>`. Later you get one `done` record from the triage. It names the fix lanes and the file of their launch commands, carries each lane's deadline as a `due:<lane>` attr, and links the findings and the one-pager. Launch each lane from that list. No other report comes back, so your window stays small however large the corpus was.

## What each lane does

1. **The reader** only reads, and writes only the findings file. Each claim is one `- ` line ending in `[#N]` for a record seq or `[<file name>:<n>]` for a transcript event or a log line. `lr bulk triage` refuses a claim with no cite, and a file cite outside the corpus, then launches the triage. The reader's lane has no tool restriction; the rule lives in its brief.
2. **The triage** works from the findings alone, checking a cite only when a decision turns on it. It writes one brief per fix lane, the list with a deliverable and a due time for each, and a one-pager published with `lr explain`. `lr bulk file` refuses a lane with no deliverable or no future due time, and any brief the launch runner would refuse, then posts the `done` record.

The triage never launches a lane. The launcher launches every one, so one desk keeps the count. A lane cannot spawn a subagent, which is why the reader and the triage are Orca lanes started through `lr launch` and not `Agent` calls.

## Cost

One Sonnet lane and one Opus lane. Both run on the Mac (`place=local`), because the findings and the fix briefs live in the drive's state directory. Use it when the corpus is too big for your window, not for one read.
