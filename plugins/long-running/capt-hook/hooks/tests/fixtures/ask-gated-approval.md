ASK Approve this exact search benchmark command on a staging box?
The benchmark scoring search setups on about 400 labeled queries is built and has never run. Your rule holds every benchmark run for your approval of its exact command, on staging only, so it waits for your word.
Show me:
What the staging box runs once you approve, and nothing else.
```sh
export SEARCH_REGION=us-west-2
./searchbench -largest 3 -items 75 -judged 50 -concurrency 4 -out searchbench-out
```
- Approve the staging run: a lane runs the command above once on a staging box and opens a results PR; about $50 (recommended)
  Touches:
  ```mermaid
  flowchart TD
    lane[a lane on a staging box] --> bench[searchbench, once]
    bench --> pr[a results PR]
  ```
  Leaves out: production and every other benchmark run
  Starts: the one staging run of the command above
- Skip the run: a PR adds the benchmark code alone; nothing measured, no winner named
  Touches:
  ```mermaid
  flowchart TD
    pr[a PR] --> code[the benchmark code]
  ```
  Leaves out: any run, so no setup is measured
  Starts: the PR with the benchmark code alone

Findings:
Root cause: none: a measurement, not a defect; your rule holds every benchmark run for your approval of its exact command, on staging only
Interim fix: none: search is unchanged, so nobody is hurt
