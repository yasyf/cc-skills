ASK How should a finished run's page find each journal file's page map?
A finished run's page fetches about 180 journal files and guesses where each one's page map starts, so a wrong guess costs a second request.
Recommended: retry each missed guess with a larger range; meanwhile pages keep guessing.
- Retry each missed guess with a larger range: no format change, a request per miss (recommended)
  Touches:
  ```mermaid
  flowchart TD
    page[run page reader] --> range[a larger range request per miss]
  ```
  Leaves out: the LTX format and the bucket layout
  Starts: the larger retry in the run page reader
- One snapshot file per finished run, written when it ends, its map at a fixed place: opens in 3 requests
  Touches:
  ```mermaid
  flowchart TD
    writer[run end] --> snapshot[snapshot file with its map at a fixed place]
    page[run page reader] --> snapshot
  ```
  Leaves out: the live journal files, which keep their map at the end
  Starts: the snapshot writer, then the reader change

Findings:
Root cause: each LTX file records its page map's length only in its last 24 bytes, so a reader must guess a range before it can read the map.
First principles: Retry each missed guess with a larger range
Interim fix: none: pages open, only slower on a wrong guess
(1) What a journal file is: a run's journal is a SQLite database, copied to the team's bucket as LTX files.
