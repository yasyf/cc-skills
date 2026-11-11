# Design first

His ruling, Nov 9 9:38 PM, after a run-page order went straight to a builder that picked a journal
file layout, a per-statement store and an API shape across three PRs he first saw as tap cards:
"You can change the skill and apply this: when you have a tricky design like this to make, you
should 1) spawn a light fable lane just for the initial design and 2) ask the user early and often,
it's incorrect that you went ahead and built a jank solution without consulting me on the design.
Fix that now, fix the skill, and apply it to any other pending things you've sent me recently".
And Nov 10 1:26 AM, on whether PRs that wait on a design pick of his get Approve buttons: "2, it can
proactively build the recommended but can't put up a PR until I answer design Qs, and make that state a
first class thing in our ledger". And Nov 10 2:53 AM, after finished work sat behind his open cards and
was then to land on their recommended options: "Did I answer those question cards they were blocked on or did you just skip them? You should surface them to me if I ask and they are blocking, not skip them".
And Nov 10 4:38 PM, under the design card on how a chat declares its local tables, which had no
Show me: "1 or 2, but bring me some examples of how iris woujld do this, an imperaitve API
along witrht he declerative, etc. dontg be lazy flash this out wayu more and od that for
everyhtrfing in the future, bring me the card while /after flehsing out more of the dfesign and
then the optins. shoujld not be so high lelkver. this would also satidyt the rtequriement you care
curently violating by not having a show-me bc then shw show-0me cvould be code snipeptrs".

Before any builder starts on a part, the coordinator decides whether the part carries a tricky
design: a choice with two reasonable shapes that cost a lot to switch between once built. It
counts when the part settles a data layout or file format, such as where a journal keeps its
index; a schema or the shape of a migration; a lock, queue, scheduler or other coordination; a
public or Sand-facing API shape; or a new background process. When unsure, it counts.

1. **One design lane, first.** The part's first job is the drive's one Fable job: read only, the
   design alone, no code and no PR, fleshed out with worked examples before any question goes to
   him.
   Add it with `drive job add design --order <o> --part "<part>" --goal "<one line>"`, then start
   it with the Agent tool: `subagent_type` `long-running:designer`, `model` `fable`, `effort`
   `medium`, `run_in_background: true`, a short `description`, never a `name`, and the brief as
   `prompt`, whose `Drive:` line records the agent. Its brief is the job brief in [Jobs](jobs.md) with no `Serves`, `Done when` or
   `Handoff` line, and a `Plan:` that names what exists: the files, PRs and findings it starts
   from. Its return, by its agent definition, opens `DESIGN <part>` and carries the design in
   plain words with a worked example of each piece, such as the calls of an API, imperative beside
   declarative, the rows a schema holds or the bytes a file format lays down. It names what his
   words already settle, and each real fork as an `ASK` block it kept with `drive job ask`: the
   design in a line, a Show me of each option in code or the visual that fits it, then two to four one-line options,
   recommendation first. Every option lands releasable alone, by [Land a change](landing.md#every-pr):
   a shape that needs a fill, backfill, hand-run command or target order after its release is split
   until each part is safe alone, or left out. `drive job ask` refuses an option that lands first and
   fixes up after, such as "land it now, then backfill".
2. **He is asked before any PR.** Each fork goes to him as its own question card by the `DESIGN`
   row of the job results, the design and its Show me above the options. It is never a wall of
   findings nor a high-level line he must ask to have fleshed out, and what his words settle is
   never asked. Meanwhile a builder may build the recommended option of each fork on a pushed branch.
3. **Built is a state, not a PR.** That builder returns `BUILT <branch>, waits on <cards>` and
   opens no PR. That line sets the job built on its branch, waiting on each card's waits id or Slack link it
   names, and the ledger, `drive
   status`, `drive view`, the dashboard and his status tab show the job as built, with the branch
   and each card. `drive pr add` refuses any new PR for the job while one of its cards is open.
   The watch drops each card as he answers it and says `FREE <j>` once, when the last is answered
   and no question the job itself asked him is still open.
   A handoff builder then opens the PR, rebuilding first wherever his pick differs from the
   recommended option it built.
4. **Nothing lands around an open card.** A PR already open that carries a design card he has
   not answered gets `drive pr set <url> --waits <card>`, one for each card, which withdraws any
   Approve card drawn for it. `drive pr set --tap` and `drive card` refuse while one holds, and the
   watch says `FREE <pr>` once, when the last is answered; nothing is closed for this. No change is
   landed, enqueued or decided on a recommended option while his card on it is open, and his order
   to land a change does not answer a design question still open on it: that card is redrawn in
   front of him at once, in the thread of his order, and the change lands after he answers. His
   question cards in his queue show how many green approved PRs and built branches wait on each and
   which features they block, and come most first; each feature names the cards that block it.
5. **The builder carries the design.** Its brief's `Plan:` is the design, and `Decisions that
   bind` holds his answer to each fork, verbatim. A builder that meets a new fork mid-build stops
   and returns `ASK` with it rather than choosing.
6. **No card for a design he never saw.** A `READY` whose PR makes a design decision he never
   answered goes back as a design question, and `drive pr set --tap` refuses without `--design
   asked:<his answer id or card permalink>` or `--design none`.
