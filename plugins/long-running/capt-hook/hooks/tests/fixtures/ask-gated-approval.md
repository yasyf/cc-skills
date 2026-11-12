ASK Approve this exact search benchmark command on a staging box?
The benchmark scoring search setups on about 400 labeled queries is built and has never run. Your rule holds every benchmark run for your approval of its exact command, on staging only, so it waits for your word.
Show me:
What the staging box runs once you approve, and nothing else.
```sh
export SEARCH_REGION=us-west-2
./searchbench -largest 3 -items 75 -judged 50 -concurrency 4 -out searchbench-out
```
- Approve the staging run: a lane runs the command above once on a staging box and opens a results PR; about $50 (recommended)
  Does: on your approval a lane starts a staging box, runs the command above once, and opens a results PR with each setup's score.
  Data: the scores in `searchbench-out/` on the box, committed to the results PR; the box is torn down after.
  Touches: `searchbench/` and a results file in the PR; about $50 of staging compute, once.
- Skip the run: a PR adds the benchmark code alone; nothing measured, no winner named
  Does: a PR adds the benchmark code alone, and nothing runs.
  Data: none: nothing is measured or stored.
  Touches: `searchbench/` only.

Findings:
Root cause: none: a measurement, not a defect; your rule holds every benchmark run for your approval of its exact command, on staging only
Interim fix: none: search is unchanged, so nobody is hurt
