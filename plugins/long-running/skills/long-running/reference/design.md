# Design first

His ruling, Nov 9 9:38 PM, after a run-page order went straight to a builder that picked a journal
file layout, a per-statement store and an API shape across three PRs he first saw as tap cards:
"You can change the skill and apply this: when you have a tricky design like this to make, you
should 1) spawn a light fable lane just for the initial design and 2) ask the user early and often,
it's incorrect that you went ahead and built a jank solution without consulting me on the design.
Fix that now, fix the skill, and apply it to any other pending things you've sent me recently".
And Nov 10 1:26 AM, on whether PRs that wait on a design pick of his get Approve buttons: "2, it can
proactively build the recommended but can't put up a PR until I answer design Qs, and make that state a
first class thing in our ledger".

Before any builder starts on a part, the coordinator decides whether the part carries a tricky
design: a choice with two reasonable shapes that cost a lot to switch between once built. It
counts when the part settles a data layout or file format, such as where a journal keeps its
index; a schema or the shape of a migration; a lock, queue, scheduler or other coordination; a
public or Sand-facing API shape; or a new background process. When unsure, it counts.

1. **One light design lane, first.** The part's first job is the drive's one Fable job: read only,
   the initial design alone, no code and no PR, back in about 20 minutes with a few hundred words.
   Start it with the Agent tool: `subagent_type` `long-running:designer`, `model` `fable`, `effort`
   `medium`, `run_in_background: true`, a short `description`, never a `name`, and the brief as
   `prompt`; record it with `drive job add design --order <o> --part "<part>" --goal "<one line>"
   --agent <id>`. Its brief is the job brief in [Jobs](jobs.md) with no `Serves`, `Done when` or
   `Handoff` line, and a `Plan:` that names what exists: the files, PRs and findings it starts
   from. Its return, by its agent definition, opens `DESIGN <part>` and carries the design in
   plain words, what his words already settle, and each real fork as a short question with two
   to four one-line options, recommendation first.
2. **He is asked before any PR.** Each fork goes to him as its own short question card by the
   `DESIGN` row of the job results, never a wall of findings, and what his words settle is never
   asked. Meanwhile a builder may build the recommended option of each fork on a pushed branch.
3. **Built is a state, not a PR.** That builder returns `BUILT <branch>, waits on <cards>` and
   opens no PR. The coordinator records it with `drive job set <j> --state built --branch <branch>
   --waits <card>`, one `--waits` for each card's waits id or Slack link, and the ledger, `drive
   status`, `drive view`, the dashboard and his status tab show the job as built, with the branch
   and each card. `drive pr add` refuses any new PR for the job while one of its cards is open.
   The watch drops each card as he answers it and says `FREE <j>` once, when the last is answered.
   A handoff builder then opens the PR, rebuilding first wherever his pick differs from the
   recommended option it built.
4. **No Approve while a card it carries is open.** A PR already open that carries a design card
   he has not answered gets `drive pr set <url> --waits <card>`, one for each card, which withdraws
   any Approve card drawn for it. `drive pr set --tap` and `drive card` refuse while one holds, and
   the watch says `FREE <pr>` once, when the last is answered; nothing is closed for this. His
   question cards in his queue show how many green approved PRs and built branches wait on each,
   and come most first.
5. **The builder carries the design.** Its brief's `Plan:` is the design, and `Decisions that
   bind` holds his answer to each fork, verbatim. A builder that meets a new fork mid-build stops
   and returns `ASK` with it rather than choosing.
6. **No card for a design he never saw.** A `READY` whose PR makes a design decision he never
   answered goes back as a design question, and `drive pr set --tap` refuses without `--design
   asked:<his answer id or card permalink>` or `--design none`.
