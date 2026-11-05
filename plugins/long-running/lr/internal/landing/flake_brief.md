# <stem>: fix each open supervisor flake at its cause

Register slice: lane=<stem> step=flake
Scale: one fix lane; code only

The supervisor posts a defect for each dev test or job that failed and passed on retry, with the build, the commit and the failing test, or the job when no test failed. List every open one with `cci tail --drive <drive> --lane <self> --kind defect --topic dev-red` and take each one cci shows open, oldest first.

For each record: find the cause and fix it in a PR whose HEAD names the record, and claim it on the `head` record of every push whose patch changed with `--attr flake-fix=<the test that flaked>`. A fix whose whole change is test or fixture code then lands with no tap on the owner's standing word; without the attr, a tap rule naming its paths sends it to him. A retry, a skip, a quarantine or a longer timeout is not a fix. An INFRA record names a job that failed on infrastructure, such as a GitHub quota 403, rather than a test: fix what broke or exhausted it.

Close a record only with the merged PR that fixes it: `cci post --drive <drive> --kind fix-live --resolves <seq> --pr <number> --text 'FIX-LIVE #<number> merged as <sha>'`. The supervisor reopens a record closed with no merged PR, and a repeat failure of the same test attaches to the open record.
