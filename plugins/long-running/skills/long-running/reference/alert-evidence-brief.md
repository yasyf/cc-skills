# <lane> (incident evidence lane)

Alert: <monitor or Slack link, times PT, drive, what fails where>.

The fix lane has this lane's name with `-fix` in place of `-evidence`. This lane feeds it and gates nothing: no code change, no PR, no release, no mute. Post every record on the drive the Alert line names, `--kind evidence --to <fix lane>,root`, with `--lane` set to this lane's name.

1. Within 10 minutes, read the paging monitor itself (`pup --no-agent --read-only monitors get <id>`), its own query, and the alerting service's production logs from the alert time to now. One created or modified in the last hour is the first suspect, and so is the release that applied it. Post what fails, with status codes or exit reasons, error text, team, cluster and the component that answered.
2. Post the deploy timeline on the alerting target for the two hours before the alert: each release, apply and merge, its time in PT, and what it changed there.
3. For a credential or token failure, post 4xx counts per endpoint and cluster for every path that verifies the same credential or token class, over the incident window and the thirty minutes after the fix lane reports fix-live. A non-zero count on a sibling path keeps the incident open.
4. Keep posting what the fix lane asks for until it posts FIX-LIVE or STOPPED, then post `DONE` and stop.

Rules: no Slack posts, no credential read from any store, no customer bucket read under a person's role, no test or build on this Mac.
