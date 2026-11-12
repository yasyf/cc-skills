ASK Approve this exact search benchmark command on a staging box?
The benchmark scoring search setups on about 400 labeled queries is built and has never run. Your rule holds every benchmark run for your approval of its exact command, on staging only, so it waits for your word.
Show me:
What the staging box runs once you approve, and nothing else.
```sh
export SEARCH_REGION=us-west-2
./searchbench -largest 3 -items 75 -judged 50 -concurrency 4 -out searchbench-out
```
- Approve the staging run: a lane runs the command above once on a staging box and opens a results PR; about $50 (recommended)
  On your approval a lane starts a staging box, runs the command above once, and commits each setup's score from `searchbench-out/` to a results PR beside `searchbench/`; the box is torn down after, about $50 of staging compute.
- Skip the run: a PR adds the benchmark code alone; nothing measured, no winner named
  A PR adds the benchmark code in `searchbench/` alone; nothing runs, and nothing is measured or stored.

Findings:
Root cause: none: a measurement, not a defect; your rule holds every benchmark run for your approval of its exact command, on staging only
Interim fix: none: search is unchanged, so nobody is hurt
