# The handoff subagent

Spawn `<lane>-handoff` as `long-running:lane`, model sonnet, effort xhigh, when a
lane must be swapped because it never answered a `ROTATE` ask, outgrew its line, or
died. It writes the handoff doc in cc-notes and returns its id. Fill the angle brackets and
paste the brief.

## Root discipline

Pass the drive checkout and previous handoff's doc id, or `none`, before spawning. Read
nothing of the old lane yourself, neither its transcript, receipts, cursor files, nor
runtime listings. The subagent's reply is at most two lines:
`handoff <lane>: doc <id>; successor <lane>-<N+1>` and
`unverified: <n> items (in the doc)`. The doc carries everything else.

Then do only the swap. Spawn
`<lane>-N+1` with its reference brief, `ccn doc show <id>` as its handoff, and the cursor the
handoff names. Wait for its first report, then send the old lane a stand-down with
`SendMessage` to its name.

## Spawn brief

```text
ccx: role=handoff
You are <lane>-handoff: you write the handoff that lets <lane>-<N+1> replace <lane>.
Model sonnet, effort xhigh. Write only the cc-notes doc and its edit buffer.

Verified facts, do not re-derive:
  lane <lane>, team <team>, model <model>, running since <UTC>
  transcript <~/.claude/projects/<project>/<root session>/subagents/agent-<lane>-*.jsonl>
  reference brief <path, or "first user message of the transcript">
  inbox file <path>, last relayed line <R<n> or line number>; cursor files <paths>
  program <slug of the drive's `progress:<slug>` label>
  previous handoff <previous handoff doc id, or "none">; drive checkout <drive checkout>
  lane state <ledger id, bus id, cc-notes log id, receipts dir, scripts it runs>
  runtime commands <e.g. orca orchestration task-list --run <run>; worker-show --dispatch <id>>

Do, in this order:
  1. If <lane> replied `flushed <ids>` or wrote its own state, start from those ids.
  2. Read the transcript only through `cc-transcript show <path> --head 1` for the
     spawn brief and `cc-transcript show <path> --signal --tail 40` for its last
     reports; use `cc-transcript grep '<pattern>' <path>` for anything else. Never
     cat or Read the jsonl.
  3. Read the inbox with `inbox-digest.py --all <inbox file>`, then read the cursor
     files, the receipts dir, and its ledger rows. Run each runtime command once.
  4. Run from the drive checkout:
     `ccn -R <drive checkout> doc add "<lane> handoff <UTC>" \
       --label handoff --label lane:<lane family> --label program:<slug> \
       --when "Starting <lane>-<N+1> on the <slug> drive" --checkout`.
     Write these sections into the printed edit-buffer path, the only file you write:
     - Identity and facts: ids, paths, scripts, cursor values the successor sets.
     - Live work: one table row per worker, PR, or dispatch it owns, with its ids
       and state as the runtime reported it.
     - Next actions, in order, with the gate each one waits on.
     - `## Standing owner rules`: read the newest owner-approved register with
       `rulings.py register --program <slug>`. Add a pointer to `ccn doc show <id>`
       and quote its whole body verbatim, in order, with `  >` on each line.
       The lint rejects a quote of only the first line. If the result is null,
       state that no register doc exists. Never build or edit the register.
       Then carry every live rule from `standing.py inbox <inbox file>` as a bullet.
       Read the whole inbox, not only from the last relayed line. Name each standing
       inbox id, never a range. Carry each inbox id from the previous handoff or write
       `- <id> superseded by <id>`. Do not add a separate list of durable answer titles.
     - Pending items the old lane was holding, and where to look for traffic
       after its last turn at <UTC>.
     Put every value you could not verify or reconstruct in one `## Unverified`
     section of the doc.
     Every line that gates on the owner ("owner's word", "owner approval", "owner
     sign-off", "owner GO", "reserved for the owner") cites a live answer id;
     otherwise drop it and list it in the doc's `## Unverified` section.
  5. Run `standing.py lint --file <buffer> --program <slug>
     [--previous-doc <previous handoff doc id>]` and fix the buffer until it exits 0.
  6. Run `ccn doc add --apply <buffer>`; it creates the doc and prints its id.
     If the successor needs an inbox snapshot, add `--attach <inbox file>` on apply;
     cc-notes snapshots that file. Never write a separate snapshot.
  7. If there was a previous handoff, run
     `ccn doc supersede <previous handoff doc id> --by <new id>`.

Your last action is one SendMessage to `main` with exactly these lines and nothing else:
  handoff <lane>: doc <new doc id>; successor <lane>-<N+1>
  unverified: <n> items (in the doc)
Final text after it is empty or that same first line.
Never paste doc content, findings, or a summary into either.

Do NOT: message <lane> or any other lane; stop, signal, or relaunch anything;
  edit inboxes, cursors, ledgers, or receipts.
```
