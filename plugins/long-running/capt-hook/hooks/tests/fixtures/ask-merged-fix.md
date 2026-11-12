ASK Start a release now to carry the page-map fix?
<https://github.com/acme/app/pull/12|The fix that writes each finished run's page map at a fixed place> merged at 3:40 PM and no release carries it, so pages keep guessing until one does.
I recommend starting now; meanwhile nothing ships.
- Start now: a release starts from main tonight and carries the fix; pages stop guessing once it is live (recommended)
  Does: a release starts from main tonight, carries the merged fix to production, and pages stop guessing once it is live.
  Data: none: the release writes no data; the fix writes each new run's map at a fixed place.
  Touches: none: the code merged already; the release ships `journal/snapshot.go` as merged.
- Wait for the next release: nothing starts tonight; the fix rides the next release, and pages keep guessing until then
  Does: nothing starts tonight, and the next scheduled release carries the merged fix.
  Data: none: nothing is written until that release.
  Touches: none: the code merged already and ships with the next release.

Findings:
Root cause: fixed: each LTX file recorded its page map's length only in its last 24 bytes, so a reader guessed a range before it could read the map; removed by https://github.com/acme/app/pull/12, merged 3:40 PM
Interim fix: none: pages open, only slower on a wrong guess
(1) What a journal file is: a run's journal is a SQLite database, copied to the team's bucket as LTX files.
