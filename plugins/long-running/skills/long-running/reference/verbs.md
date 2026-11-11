# The drive verbs

Every verb `drive` takes and every flag of each, one row per verb, as the installed CLI parses
them; a test holds this table and the parser equal. Read a verb's row with
`drive manual verbs "<verb>"`, such as `drive manual verbs "job set"`, never with
`drive … --help`. The rows leave out `--drive <name>`, which defaults to this session's drive,
and `--json`, which prints JSON. `[ ]` marks what may be left out, and `...` what repeats.

| Verb | Takes |
|---|---|
| `drive start <name>` | `--role communicator/coordinator`, required; `--channel <his DM id>`; `--watch <channel>`..., another channel to hold |
| `drive order add "<his words>"` | `--from slack:<channel>/<ts>`, default terminal; `--thread <ts>`; `--due <time>` his ack promised a line by; `--parts "<part>"...`, each deliverable his words name; `--incident`, which needs a `fix` part and records a codex job on each part, its agent started at once; `--joins <o>`, an incident on the failing service of open incident `<o>`, with its record and no job; `--milestone <name>`; `--project <name>` and `--workstream <name>` the Now screen files it under |
| `drive order set <o>` | `--state open/working/waiting/done/dropped`; `--due <time>`; `--note "<text>"`; `--proof <link>`; `--thread <ts>`; `--lined`; `--withdrawn <link>`, his message calling off a promised time; `--parts "<part>"...`, replacing the list; `--incident`, refused until a job owns its `fix` part, or `--no-incident`; `--milestone <name>`, `--project <name>` and `--workstream <name>`, each empty to take it out; `--record <seq>`, the handler's report a close from the communicator needs |
| `drive order unfiled` | each open order naming no project or workstream yet, then the pairs in use |
| `drive order posted <o> [<link>]` | the drive's post in Slack, or `--undo` to take back the mark of `--part "<part>"`, the part a post delivers |
| `drive job add <kind>` | `<kind>` builder, reader, design or codex; `--order <o>` and `--goal "<one line>"`, required; `--agent <id>`; `--repo <owner/name>`; `--branch <b>`; `--worktree <path>`; `--remote`, a builder on a Sprite; `--part "<part>"`..., of its order, default its only part. Prints the job id |
| `drive job set <j>` | `--state running/waiting/built/done/failed/stopped`; `--said "<its line>"`; `--agent <id>`; `--branch <b>`; `--pr <url>`...; `--repro <file>`; `--part "<part>"`..., of its own order or of the one `--carries <o>` names, another order it delivers for; `--card <link>`, a question card it asked on, kept beside its others, each carrying its parts until he answers it; `--waits <card or job>`...; `--no-waits`, dropping every wait first; `--told <link>`, the drive's line in an incident's thread linking the card, which a job on an open incident order needs before it takes a card |
| `drive job ask <j> [<file>]` | keeps the question beside the job's other open ones; the ASK block's file, `-` for stdin, default a remote lane's latest report; `--unsettled "<why>"`: each answer of his the refusal listed, by id, and why it does not settle this |
| `drive job card <file>` | the card a kept ASK block draws, as JSON, and gives the question kept in that file its waits id; `--waits <drive>:<j>-<seq>`, required |
| `drive job report <j> [-]` | `-` keeps the return piped on stdin, its first line setting a running job's state and said line; with nothing, prints the kept one |
| `drive job launch <j> <brief>` | starts a remote job's lane on a Sprite; `-` reads the brief from stdin |
| `drive job resume <j> "<text>"` | answers a remote lane or sends its next event; `-` reads the text from stdin |
| `drive job replies <j>` | each result and question a remote lane sent, oldest first |
| `drive job end <j>` | ends a done remote lane and frees its Sprite |
| `drive pr add <url>` | `--job <j>`, required; `--tap`, waits on his tap |
| `drive pr set <url>` | `--tap`, which needs `--serves <o>` and `--design asked:<answer>` or `none`; `--no-tap`, withdrawing his card, which needs `--why "<reason>"`; one of `--exempt "pure port"/test-only/ci-only/"flake fix"/"incident fix"`, `--no-exempt` or `--port-of <N>`; `--fundamental "<what changed>"`, clearing his tap and any exemption; `--tapped Approve/Hold`, a tap he gave elsewhere, and `--tap-source <where>`; `--waits <card or job>`...; `--no-waits` |
| `drive pr tapped <url>` | whether his tap lets it land: exit 0 if so, 1 if not |
| `drive place` | where the next monorepo builder starts |
| `drive bound <run> <command>...` | runs the command after `--` with a lane's Run bound to this terminal |
| `drive answer <id>` | `--words "<his words>"` and `--link <permalink>` on the first call, which lists every open card and PR and exits 2; then `--stands <key>...`, `--superseded <key>=stop` or `<key>=<j>`..., `--reopened <key>=<j>`... and `--others-stand` until each is marked |
| `drive heard` | `--away`, he said he is going to sleep or away |
| `drive status [<id>...]` | orders, jobs or PRs; default every open order |
| `drive manual <page> [<row>]` | `<page>` start, records, communicator, coordinator, jobs, scope, design, messages, concepts, landing, incidents, migration or verbs; `<row>` prints the table rows whose first cell names it |
| `drive context` | `--role communicator/coordinator`, default this session's |
| `drive view` | what waits on him and where each feature stands |
| `drive dashboard` | `--port <n>`; `--serve`, in the foreground |
| `drive watch` | the events Monitor follows |
| `drive card` | answers one cc-slack card request, JSON on stdin and stdout |
| `drive mcp` | serves `say` to a Claude Code session over stdio, as this plugin's `drive` MCP server |
| `drive tool <name>` | `<name>` say; runs that MCP tool on the JSON arguments read from stdin and prints its one line |
| `drive install <plugin>@<marketplace>` | `--sha <merge sha>`; `--repo <owner/name>`; `--timeout <seconds>`, default 900 |
| `drive setup` | `--bin-dir <dir>` for the `drive` shim |
