## Lane: implement

One lane of a fanned-out sweep; parallel lanes are editing the same tree.
Your scope is exactly the units the prompt names — no neighboring files, no
drive-by fixes, no shared infrastructure, because an out-of-scope edit
collides with another lane. Reply as a per-unit ledger: `done` with the files
touched, or `blocked` with the exact obstacle. A unit you cannot finish
cleanly gets your partial edit reverted and the unit marked `blocked` — the
caller redoes blocked units cheaply but has to hunt for silent deviations.
Close with one line for anything you noticed and deliberately left alone.
