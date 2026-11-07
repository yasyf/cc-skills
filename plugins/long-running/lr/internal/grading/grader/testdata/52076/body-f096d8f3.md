## Context

A pod that restarts finds chat copies on its disk. Another pod may have taken and advanced any of them in the bucket while this one was down. Each chat ships to one flat bucket path (`sessions/<id>`), and the browser and Macs read that path.

## Summary

Boot does no per-chat bucket check now. A restarted pod uploads nothing for a chat it found on disk until a journal take claims that chat. The take already compares the local copy with the bucket and catches it up. The found guard (#48602) and the runner's `holdFound` call are deleted.

## Motivation

The owner picked "One path per chat, no folders" on card-rmw5jo8es (2:28 PM Nov 6). Root's ruling (cci 558033) retired the reader PR, see #52130, and cut this PR to its deletions plus that shape. The found guard listed the bucket once per found chat at boot, so a restart cost grew with the chats on disk.

## Risk profile

- **Threat model:** no new route. One admin route is removed: `/v1/admin/found`, which only the runner called.
- **Availability:** a chat still on disk after a pod restart uploads at its next take, not at boot. A take that cannot reach the bucket fails, as it does today.
- **Data:** if a found copy has turns its bucket head does not prove, and another pod has advanced the chat, the take replaces the copy with the bucket head and those turns are lost. Dev kept such a copy gated forever ("Unproven"), and it never shipped either way.

## Design

- The hold is the only pod-only logic, and `Mode::Pod` gates it because only pods share a chat's bucket path with other writers; a Mac is the sole writer of its chats.
- Rejected: per-database epoch folders (#52130 and the previous head of this PR). They changed the bucket layout that browsers and Macs read, which the owner's pick rules out.

<details>
<summary>Full design</summary>

At boot, `Supervisor::hold_found` flags each registered chat with a local db file as untaken. It runs in the server, both in-process and behind `Remote`, and in the replicator sidecar. While the flag is set, `attach_lane` and `Remote::attach_owned` attach no lane. So no wake, sync, or last-stop `attach_tails` can ship the copy's tail. `OneProcess::launched` skips held chats, so boot spends no failed wake on them.

The journal take (`writer::caught_up`) calls `Served::stale_copy` on any parked or held copy. That reads the published head and asks `restore::continues` whether the copy shipped or landed at that head, using the head file's checksum. A copy that does not continue the head is released and replaced through the existing `replace_parked`. Once the take's restore succeeds, `Served::taken` clears the server's flag, and the server's next `/attach` clears the sidecar's.

</details>

## Changes

- Deleted: `supervisor/found.rs` and its tests, the `Replication::found`/`settle_found` methods, the settle gates in `sync_route`, `hold_awake`, and `attach_lane`, `restore::replace_found`, `found_settles` in blob reclaim and admission, `/v1/admin/found` and `FoundDb`, and the Go `holdFound` with `MachineCredential.Hold`.
- Added: the `DbRecord` untaken flag, `restore::continues`, `admin::stale_copy`/`replace_stale`/`taken`. `shipped_file` and `file_checksum` move to `chain.rs`.
- Tests: `a_restarted_pod_ships_a_chat_it_found_on_disk_only_once_it_takes_it` fails on dev, whose guard ships a continuing copy at boot. `a_restarted_pod_whose_chat_another_pod_advanced_takes_the_bucket_over_its_found_copy` covers the stale case. `a_pod_restarted_beside_a_hundred_chats_on_disk_calls_its_bucket_as_beside_none` compares the restart's bucket calls at N=1 and N=100, or N=1,000 under `FORGE_CI_AT_SCALE=1`.

<details>
<summary>Full change list</summary>

- Removed-caller trace: only `/v1/admin/found` read `found()`, only `cloudrunner.holdFound` hit that route, and only `loop.go` ran `holdFound`. The open paths in `data.rs` and `data/blob.rs` awaited `settle_found`; blob reclaim and admission awaited `found_settles`; `Supervisor::guard` alone ran `replace_found`. This PR deletes every one of those callers with its callee.
- Rollout: the runner and the journal sidecar ship in one image, so the runner never calls a server that lacks `/v1/admin/found`.
- Accepted risk: a found copy's unproven turns are replaced at take, as described under Data.

</details>
