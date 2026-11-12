ASK How should a finished run's page find each journal file's page map?
A finished run's page fetches about 180 journal files and guesses where each one's page map starts, so a wrong guess costs a second request.
Recommended: one snapshot file per finished run with its map at a fixed place; meanwhile pages keep guessing.
- One snapshot file per finished run, written when it ends, its map at a fixed place: opens in 3 requests (recommended)
  Does: when a run finishes, its uploader writes one snapshot file with the page map at a fixed place, and the run page reads that map, then its page ranges.
  Data: one snapshot.ltx per finished run in the team's bucket, written once when the run ends and read by range requests.
  Touches: the uploader in `journal/snapshot.go`, the run page's reader in `site/journal.js`, and a backfill over old runs.
- Retry each missed guess with a larger range: keeps the guessed range and costs a request per miss
  Does: the run page reads each file's last 64 KiB, and on a missed guess reads again with a larger range.
  Data: none: the journal files in the team's bucket stay as they are.
  Touches: the run page's reader in `site/journal.js` only.

Findings:
Root cause: each LTX file records its page map's length only in its last 24 bytes, so a reader must guess a range before it can read the map.
First principles: One snapshot file per finished run, written when it ends, its map at a fixed place
Interim fix: none: pages open, only slower on a wrong guess
(1) What a journal file is: a run's journal is a SQLite database, copied to the team's bucket as LTX files.
