import re

import pytest
from drive import limits, state
from drive.state import DriveError

RETIRED_COLUMN_SHOW_ME = r'''```sand
table orders {
  retired total: int,
}
```
Retired: publishes now, and new rows store null for `total`.
```sand
table orders {
  total: ?int,
}
```
Brought back as optional: old rows keep their totals, rows written meanwhile read null.
```sand
table orders {
  total: int,
}
```
Brought back as required: refused under either option, with a message naming the other route.'''
RETIRED_COLUMN_OPTIONS = r'''- Comes back only as optional, old values intact and rows written meanwhile reading null; costs authors a null case to handle (recommended)
- Never comes back, so retiring a required column is one-way and the author adds a new column; costs losing access to the old values'''

LANDING_SHOW_ME = r'''Who acts on your Approve tap, a merge and a production cut under each option
![Who acts on your Approve tap, a merge and a production cut under each option]({root}/durable-agents/pictures/hier/f3.png)'''
LANDING_OPTIONS = r'''- The sub-coordinator lands and installs its project's changes; the top starts, approves and rolls back production cuts, one at a time. (recommended)
- The top lands and installs every change; sub-coordinators stop at READY, so every tap and merge event still routes to the top.
- The sub-coordinator lands its changes and starts production cuts too; two projects can race to start one, and the lock refuses the second.'''

DEEP_SUBAGENTS_SHOW_ME = r'''```
dev
 └ #53615  a unichat agent knows the agents it starts can start their own   open, green, approved, no tap
    └ #53781  chats record no topology; agents default to communicator-coordinator   you approved 7:46 PM
       └ #53816, #53838   green, approved, waiting on your tap
```'''
DEEP_SUBAGENTS_OPTIONS = r'''- Land both now, deep-subagents first (recommended)
- Move the topology change onto dev without deep-subagents, and leave that one with you'''

ALERT_FEED_SHOW_ME = r'''One tag the coordinator read tonight: a Datadog recovery post, about 650 characters of Slack JSON.
```json
{"type":"slack.message","watch_id":2816,"kind":"channel","channel_id":"C0822AHFY3G","thread_ts":"1794373183.532399","ts":"1794379244.508779","user":"U0828UHTV42","user_name":"Datadog","from_claude":false,"text":"Recovered: SandSQL database past 4 GiB — wf for team_1 on plat [a bot's first line; the whole message is at the permalink]","permalink":"https://poetic-technology.slack.com/archives/C0822AHFY3G/p1794379244508779?thread_ts=1794373183.532399&cid=C0822AHFY3G"}
```
The same evening through the recommended feed: one subscribe call, then one line when an alert opens or closes and one per release step.
```console
$ cc-slack subscribe --kind alerts --kind releases
ALERT open monitor 315028134 "SandSQL Error Log Count — plat" since 10:34 PM https://poetic-technology.slack.com/archives/C0822AHFY3G/p1794378966727299
ALERT closed monitor 333476851 "SandSQL database past 4 GiB — wf for team_1 on plat" after 1h41m, 1 warning folded https://poetic-technology.slack.com/archives/C0822AHFY3G/p1794373183532399
RELEASE live mysterious-novak (sand-next) 10:25 PM https://poetic-technology.slack.com/archives/C0B75AL4XEW/p1794377811176679
```
Measured in #alerts-api, 24 hours to 10:45 PM Nov 10: posts that each reach the coordinator today, against distinct alerts the feed would open.
```chart
{"$schema": "https://vega.github.io/schema/vega-lite/v5.json", "data": {"values": [{"what": "Slack posts, each a tag today", "count": 384}, {"what": "distinct alerts, one open line each", "count": 41}]}, "mark": "bar", "encoding": {"y": {"field": "what", "type": "nominal", "title": null, "sort": null}, "x": {"field": "count", "type": "quantitative", "title": "in 24 hours"}}}
```'''
ALERT_FEED_OPTIONS = r'''- cc-slack itself: its one reader, already following these channels, sorts each post; any session subscribes by kind or monitor; no tap needed. (recommended)
- Monorepo tooling: incidents are ai-oncall's own and releases come from release records; one more reader running per subscribing session; waits on your tap.
- The drive's own watch: alerts and releases join the coordinator's existing feed; no other session can subscribe; it rereads the channels every minute.'''

ALERT_FEED_REDRAWN_SHOW_ME = r'''![Where alert, incident and release posts go under each option, and which session sees them]({root}/durable-agents/asks/j739-163e22cf-flow.png)
Measured in #alerts-api, 24 hours to 10:45 PM Nov 10: posts that each reach the coordinator today, against distinct alerts the feed would open.
```chart
{"$schema": "https://vega.github.io/schema/vega-lite/v5.json", "data": {"values": [{"what": "Slack posts, each a tag today", "count": 384}, {"what": "distinct alerts, one open line each", "count": 41}]}, "mark": "bar", "encoding": {"y": {"field": "what", "type": "nominal", "title": null, "sort": null}, "x": {"field": "count", "type": "quantitative", "title": "in 24 hours"}}}
```'''
ALERT_FEED_REDRAWN_OPTIONS = r'''- cc-slack itself: its one reader, already following these channels, sorts each post; any session subscribes by kind or monitor; no tap needed. (recommended)
- Monorepo tooling: incidents are ai-oncall's own and releases come from release records; one more reader running per subscribing session; waits on your tap.
- The drive's own watch: alerts and releases join the coordinator's existing feed; no other session can subscribe; it rereads the channels every minute.'''

BLOB_NAMES_SHOW_ME = r'''```text
team acme, the service's record

names
  sha256        len       namer                             since
  9f86..0a08    81233     db acme/proj_42/production        2026-11-10 21:40
  9f86..0a08    81233     db acme/chat_7f3c                 2026-11-10 21:52
  1e59..6093    2097152   build pb_9921                     2026-11-10 22:03

files
  sha256        len       state   unnamed since
  9f86..0a08    81233     live    -
  1e59..6093    2097152   live    -

chat_7f3c drops its last row holding 9f86..  -> its server tells the service -> one name left
proj_42 deletes the row too                  -> no names left -> 9f86.. "unnamed since 23:10"
```'''
BLOB_NAMES_OPTIONS = r'''- One SandSQL database per team, written only by the service; a namer is a whole database or a build (recommended)
- One table in the main Postgres database for all teams: easy to query, but it holds a row per file and namer for every team
- A small marker in the bucket per file and namer: no database, but every delete must list the bucket first'''

RESTORE_PARKED_SHOW_ME = r'''What a failed restore copy does now; the dashed exits exist only if a reset, a removal or 7 days also clear it.
```mermaid
flowchart LR
  F[Copy fails] --> P[Parked: no database, publishes refused, copied files kept]
  P -->|same restore again| R[Resumes, copying only missing files] --> D[Restored]
  R -->|fails again| P
  P -->|restore with other rows| X[Parked one discarded, its files deleted] --> N[New restore starts]
  P -.->|Env reset or removed| Y[Discarded, files deleted]
  P -.->|7 days unresumed| Z[Discarded, files deleted]
```
| | Only another restore clears it | Also cleared when the Env is reset or removed | Also cleared after 7 days unresumed |
|---|---|---|---|
| A parked restore nobody touches | keeps its copied files and refuses publishes until someone restores into the Env again | same until someone resets or removes the Env, which deletes the copied files | deletes its copied files on day 7, then the Env takes publishes as a new empty database |
| What it adds | nothing beyond the branch | the Env reset and removal ask the server to discard a parked restore; today both skip an Env holding no database | a server pass every day over parked restores, a new background timer |
| A server restart | keeps the parked restore | keeps it | keeps it, counting days from the failure |'''
RESTORE_PARKED_OPTIONS = r'''- Only another restore into the Env clears it and deletes its files; an untouched one keeps them and refuses publishes (recommended)
- Also clear it when the Env is reset or removed; adds one server call to each
- Also clear it after 7 days unresumed, by a new daily server pass; frees storage unattended but adds a timer'''

SCRATCH_CALLS_SHOW_ME = r'''The options side by side
```compare
{
 "options": [
  {
   "name": "1. Named calls in scratch::",
   "recommended": true,
   "language": "sand",
   "code": "scratch::create_table(\"leads\",\n  columns=_{ url: \"text\" })\nscratch::insert(\"leads\",\n  values=_{ url: @u }) -> @id\nscratch::rows(\"leads\",\n  where=_{ url: @u }) -> @found\nscratch::drop_table(\"leads\")",
   "points": [
    "The table is named by a string on every call."
   ]
  },
  {
   "name": "2. Create returns a table handle",
   "recommended": false,
   "language": "sand",
   "code": "scratch::create_table(\"leads\",\n  columns=_{ url: \"text\" }) -> @t\ntable::insert(@t,\n  values=_{ url: @u }) -> @id\ntable::rows(@t,\n  where=_{ url: @u }) -> @found\ntable::drop(@t)",
   "points": [
    "@t is a new kind of value that functions pass around."
   ]
  }
 ]
}
```'''
SCRATCH_CALLS_OPTIONS = r'''- Named scratch calls taking the table's name: sits beside the file calls; rows are checked when run, not by the checker (recommended)
- One create call returning a table handle: no repeated names, passes between functions; a new kind of value in the language'''

AUDIT_LOG_SHOW_ME = r'''The organization trail gains one selector
```diff
 selectors: [
   { fields: [ eventCategory = Data, readOnly = true, resources.type = AWS::CloudWatch::Metric ] },
   { name: "Management events selector", fields: [ eventCategory = Management ] },
+  { name: "Object deletes by our roles", fields: [
+      eventCategory = Data,
+      resources.type = AWS::S3::Object,
+      eventName = DeleteObject | DeleteObjects,
+      userIdentity.arn starts with arn:aws:sts::<each of our accounts>:assumed-role/ ] },
 ]
```

|  | Records | Cost |
|---|---|---|
| 1. Every bucket (recommended) | every delete under our roles, incl. litestream and legacy journal | ~$4.70/month per busy SandSQL db; $470 at 100 |
| 2. Customer-account buckets only | misses team buckets we host for them | less |
| 3. No audit log | litestream and legacy journal deletes unrecorded | $0 |'''
AUDIT_LOG_OPTIONS = r'''- Every bucket: records each delete under our roles; about $4.70 a month per busy SandSQL database, so $470 at 100 (recommended)
- Only buckets in customers' own accounts: cheaper, but misses deletes in the team buckets we host for them
- No audit log: costs nothing; litestream and legacy journal deletes stay unrecorded until each gets the log line'''

INCIDENT_CARD_SHOW_ME = r'''Both paths from the minute <https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794373177288019|the jobs-at-once question> held back <https://github.com/Forge-AI/monorepo/tree/o277-job-runner|the job-runner change>, with tonight's times

```mermaid
flowchart LR
  A[8:59 PM question posted, change built] --> B{incident order?}
  B -- lands on recommendation --> C[PR opens 9:00 PM, lands on CI green]
  C --> D[question stays open; your pick becomes a follow-up PR]
  B -- waits for your tap --> E[question pinned at top of incident thread 8:59 PM]
  E --> F[repeated every 5 min: WAITING-ON-HIM]
  F --> G[your tap 10:44 PM, PR opens 10:47 PM]
```'''
INCIDENT_CARD_OPTIONS = r'''- The repair lands on the recommendation, the question stays open, another pick is a follow-up: no incident waits; costs a second PR. (recommended)
- The repair waits; the question is pinned atop the incident's messages, repeated every 5 minutes: you choose first; the outage runs meanwhile.
- One Go tap on the recommendation replaces the question there: one tap, no reading; a design settled under outage pressure.'''

EVENTS_SHOW_ME = r'''Where a PR's "checks passed" line goes under each option, with tonight's counts from the audit

```mermaid
flowchart LR
  E[GitHub, CI, Datadog, job returns: 973 events in 44 h] --> W{drive watch}
  W -- daemon --> J[the owning job: PR events 452, alerts collapsed per monitor]
  J -- merged, red with no owner, tap, orphan --> C[coordinator: decisions only]
  W -- as today --> C2[coordinator: 1,334 wakes, 474 no-ops, 43 compactions]
  W -- per project --> P[sub-coordinator: its project's share of 1,334]
```'''
EVENTS_OPTIONS = r'''- A daemon hands each event to its owning job and sets its state; a coordinator wakes for decisions only; costs a standalone process. (recommended)
- Every event wakes a coordinator, batched per 5 minutes: nothing new to run; it still reads and files every line itself.
- The hierarchy alone splits events per project: fewer per session; each sub-coordinator still does tonight's bookkeeping for its share.'''

SCRATCH_CREATOR_SHOW_ME = r'''Recommended: the same path the journal takes today, no network call
```dot
digraph { graph [fontname=Helvetica fontsize=11 rankdir=LR nodesep=0.3 ranksep=0.45 bgcolor=transparent]; node [fontname=Helvetica fontsize=11 shape=box style="rounded,filled" fillcolor="#f6f8fa" color="#8c959f"]; edge [fontname=Helvetica fontsize=10 color="#57606a"];
 subgraph cluster_pod { label="the run's pod or Mac" fontname=Helvetica fontsize=11 style="rounded,dashed" color="#8c959f";
   runner [label="run's writer"]; server [label="SandSQL server\n(beside it)"];
   journal [label="sessions-<run>\njournal" fillcolor="#ddf4ff"]; scratch [label="scratch-<run>\nlocal tables" fillcolor="#dafbe1" color="#1a7f37"]; }
 runner -> server [label="1. create journal"]; runner -> server [label="2. create scratch db\nacting as run:<run id>" color="#1a7f37" fontcolor="#1a7f37"];
 server -> journal; server -> scratch; runner -> journal [label="3. index row:\nscratch db exists" style=dashed];
  }
```

| Who creates it | Network call per run | Who can make a database |
|---|---|---|
| Run's own writer, as the run (recommended) | none | only the run, for itself |
| API, when it assigns the run | one call + one row | only the API |
| Server, on first write to an unknown name | none | anyone holding a token |

| Started by | Acts as |
|---|---|
| a run | run:<run id> |
| a cloud agent | env:<env id> |
| a person's chat | user:<user id> |'''
SCRATCH_CREATOR_OPTIONS = r'''- The run's own writer, through the server beside it, as the run: same path as its journal; no control-plane call (recommended)
- The API creates it when it assigns the run: a central record of each one; a network call and a row per run
- The server creates it on the first write to an unknown name: no create call; anyone holding a token can make databases'''

HEAVY_JOBS_SHOW_ME = r'''One job at a time today:
```rust
// sandsql-server/src/jobs.rs:12
const JOB_THREADS: usize = 1;
```
A fixed few:
```rust
const JOB_THREADS: usize = 3;
```'''
HEAVY_JOBS_OPTIONS = r'''- 2 threads, 375 GB free floor: a 21 GB copy no longer blocks small moves; least added load on memory already at its limit (recommended)
- 3 threads, same reserve: backlog clears about a third sooner; three whole-file copies share one disk and the page cache
- 4 threads, same reserve: fastest backlog; a quarter of the CPUs and the most memory pressure, on a machine that froze tonight'''

SCRATCH_CREATOR_DRAWN = r'''![Show me]({root}/durable-agents/pictures/redraw/j571-660789/who.png)'''


def ask(show_me: str, options: str) -> str:
    shown = show_me.replace("{root}", str(state.root()))
    return f"ASK Which way should this go?\nThe pick below decides it; the recommended option is marked.\nShow me:\n{shown}\n{options}\nFindings:\nDirect fix: none: a design pick\nKept on disk.\n"


@pytest.mark.parametrize(
    ("show_me", "options", "refusals"),
    [
        pytest.param(RETIRED_COLUMN_SHOW_ME, RETIRED_COLUMN_OPTIONS, ["the Show me draws 3 code fences as 3 pictures, one per option"], id="10:09 three sand pictures"),
        pytest.param(
            LANDING_SHOW_ME,
            LANDING_OPTIONS,
            ['the Show me picture "Who acts on your Approve tap, a merge and a production cut under each option" is a file drawn by hand'],
            id="10:30 a hand-drawn landing picture",
        ),
        pytest.param(
            DEEP_SUBAGENTS_SHOW_ME,
            DEEP_SUBAGENTS_OPTIONS,
            ["names no language, so his card would get a mock or words drawn as a picture", "the Show me lists 4 PRs by number, #53615, #53781, #53816, #53838: a stack card"],
            id="10:33 a PR tree in words",
        ),
        pytest.param(ALERT_FEED_SHOW_ME, ALERT_FEED_OPTIONS, ["is JSON, raw data or tool output", "is a terminal session, raw data or tool output"], id="10:52 json and a terminal"),
        pytest.param(
            ALERT_FEED_REDRAWN_SHOW_ME,
            ALERT_FEED_REDRAWN_OPTIONS,
            ['the Show me picture "Where alert, incident and release posts go under each option, and which session sees them" is a file drawn by hand'],
            id="10:55 one hand-drawn picture for every option",
        ),
        pytest.param(SCRATCH_CREATOR_DRAWN, SCRATCH_CREATOR_OPTIONS, ['the Show me picture "Show me" is a file drawn by hand'], id="11:01 a diagram and two tables in one picture"),
        pytest.param(HEAVY_JOBS_SHOW_ME, HEAVY_JOBS_OPTIONS, ["shows a constant, which a sentence says as well as a picture"], id="11:03 a const"),
        pytest.param(BLOB_NAMES_SHOW_ME, BLOB_NAMES_OPTIONS, ["names no language, so his card would get a mock or words drawn as a picture"], id="11:54 a text mock of one option"),
    ],
)
def test_a_show_me_he_called_useless_is_refused_naming_its_rule_and_what_to_carry_instead(show_me: str, options: str, refusals: list[str]) -> None:
    with pytest.raises(DriveError) as refused:
        limits.question(ask(show_me, options))
    for refusal in refusals:
        assert refusal in str(refused.value)
    assert re.search(r"his Nov 10 1[01]:\d\d PM words", str(refused.value))


@pytest.mark.parametrize(
    ("show_me", "options"),
    [
        pytest.param(RESTORE_PARKED_SHOW_ME, RESTORE_PARKED_OPTIONS, id="a mermaid flow and a table"),
        pytest.param(INCIDENT_CARD_SHOW_ME, INCIDENT_CARD_OPTIONS, id="a mermaid flow with tonight's times"),
        pytest.param(EVENTS_SHOW_ME, EVENTS_OPTIONS, id="a mermaid flow of every option"),
        pytest.param(SCRATCH_CALLS_SHOW_ME, SCRATCH_CALLS_OPTIONS, id="a compare labeled per option"),
        pytest.param(AUDIT_LOG_SHOW_ME, AUDIT_LOG_OPTIONS, id="a real multi-line diff"),
        pytest.param(SCRATCH_CREATOR_SHOW_ME, SCRATCH_CREATOR_OPTIONS, id="the diagram first, then its tables native"),
    ],
)
def test_a_show_me_he_let_stand_passes(show_me: str, options: str) -> None:
    assert limits.question(ask(show_me, options)).show_me


def test_a_screenshot_kept_outside_the_drives_folders_still_draws() -> None:
    shot = "The New Chat screen while connected:\n![The New Chat screen](/Users/me/shots/new-chat.png)"
    assert limits.question(ask(shot, LANDING_OPTIONS)).show_me[1] == "![The New Chat screen](/Users/me/shots/new-chat.png)"
