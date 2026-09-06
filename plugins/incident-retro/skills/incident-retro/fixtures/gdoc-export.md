# \[2026-03-14\] Checkout API returned 502s and the order queue backed up (\#7)

Date: Date Authors: [Jordan Rivera](mailto:jordan@acme.example) Status: In Progress  
[Linear Ticket](https://linear.app/acme/issue/ENG-1/checkout-502s-sev-2)

**Status:** Resolved; follow-ups in progress  
**Incident ID / Severity:** INC-7 / SEV-2  
**Note:** Confirm the timezone of the Slack transcript before publishing.

Attendees:  
[Jordan Rivera](mailto:jordan@acme.example)[Sam Okafor](mailto:sam@acme.example)[Jordan Rivera](mailto:jordan@acme.example)

### Summary (Multiple incidents)

1. \[Checkout\] The checkout API returned 502s for 41 minutes after the `api` deploy.  
2. \[Queue\] The order queue backed up while the API was down and drained slowly.

> `upstream connect error or disconnect/reset before headers`

### 

### Impact

Checkout was unavailable for every customer between 10:12 and 10:53.  
The order queue held 1 240 orders at peak; all were delivered by 11:30.

* Payments retried and no charges were lost.  
* Two enterprise tenants opened support tickets.

### Root Causes

#### Confirmed root cause

\[1\] The new connection pool capped at 4 connections per pod, so every pod saturated within minutes of the deploy.

```ts
const pool = new Pool({ max: 4 })
```

\[2\] The queue consumer paused on the first 502 and never resumed until it was restarted.

#### Contributing factors

- **No canary:** the deploy went to every pod at once.  
- **Silent saturation:** pool exhaustion logged at debug level only.

### Trigger

The `api` deploy of [\#12](https://github.com/acme/monorepo/pull/12) at 10:08.

### Resolution

[Rolled the pool size back](https://app.graphite.com/github/pr/acme/monorepo/13?panel=timeline) to 32 and restarted the queue consumer. Build: [https://buildkite.com/acme/release/builds/301](https://buildkite.com/acme/release/builds/301)

### Detection

Monitor [https://app.datadoghq.com/monitors/2](https://app.datadoghq.com/monitors/2) fired at 10:15. Sam Okafor reported the errors in [\#incidents](https://acme.slack.com/archives/C01ACME0001/p1773480900000000).

## Action Items (Linear tickets)

| item | source | state |
| :---- | :---- | :---- |
| Alert on pool saturation | sam | [Jordan Rivera](mailto:jordan@acme.example)to create |
| Canary deploys for `api` | jordan | done [ENG-1](https://linear.app/acme/issue/ENG-1/checkout-502s-sev-2) |

- [x] ~~Restart the queue consumer automatically after a 502 burst (Sam Okafor)~~  
- [ ] \[p1\] Raise the pool size ceiling in config \[Jordan\] [ENG-2](https://linear.app/acme/issue/ENG-2/raise-pool-ceiling)  
- [ ] **Add a runbook for queue drain**  
      - Owner: @Sam  
      - Deadline: 2026-04-01  
- [ ] Review the on-call rotation ✅  
- [ ] [https://linear.app/acme/issue/ENG-3/pool-metrics-dashboard](https://linear.app/acme/issue/ENG-3/pool-metrics-dashboard)

General nice to have: a staging environment.

## Lessons Learned

### What went well

Rollback was quick once the pool was suspected.  
The queue absorbed the outage without dropping orders.

### What went wrong

* The pool limit shipped without a load test.  
* The monitor fired three minutes after customers noticed.

### Where we got lucky

1. The deploy landed in a low-traffic hour.

## Timeline

All times Pacific, 2026-03-14 (PDT, UTC-7).

| time | event |
| :---- | :---- |
| 10:08 | build 301, target `api`, starts on `releases/2026-03-14/1` with [\#12](https://github.com/acme/monorepo/pull/12) |
| 10:12 | first 502s on `/checkout` |
| 10:14 | Sam Okafor reports checkout failures in [\#incidents](https://acme.slack.com/archives/C01ACME0001/p1773480900000000) |
| 10:15 | **monitor 2 alerts** on 5xx rate ([https://app.datadoghq.com/monitors/2](https://app.datadoghq.com/monitors/2)) |
| 10:20 | Jordan suspects the new pool limit ([notebook](https://app.datadoghq.com/notebook/1)) |
| 10:41 | rollback to `releases/2026-03-13/4` kicked off |
| 10:53 | 502s stop; checkout recovers |
| 11:30 | queue drained, all-clear called |
| 12:48 | Sentry issue [https://acme.sentry.io/issues/100/](https://acme.sentry.io/issues/100/) linked to the retro |
| 1:10 | deploying the pool fix [\#13](https://github.com/acme/monorepo/pull/13) |

Outage window: from 10:12 to 10:53.

| Time | Event |
| ----- | ----- |
| 2026-03-15 |  |
| 09:00 | Retro scheduled |

- **17:30Z** — Queue consumer restarted and resumed draining.  
- 11:05 — Sam posts the drain graph ![][image1]  
- During mitigation — the on-call rotation was reviewed.

## Supporting information

- Investigation notebook: [https://app.datadoghq.com/notebook/1](https://app.datadoghq.com/notebook/1)  
- Monitor: [https://app.datadoghq.com/monitors/2](https://app.datadoghq.com/monitors/2)  
- Cause: [\#12](https://github.com/acme/monorepo/pull/12)  
- Dashboard: [https://app.datadoghq.com/dashboard/abc-123](https://app.datadoghq.com/dashboard/abc-123)  
- Status page: [https://status.acme.example/incidents/7](https://status.acme.example/incidents/7)  
- Release builds: 301 `api`, 302 `worker`

### Pool configuration

The pool config before the fix.

![][image2]

```
max_connections: 4
```

## Retro meeting notes

Attendees agreed to run the canary experiment next sprint.

[image1]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGM4IScHAAK2AQU0pnWqAAAAAElFTkSuQmCC>

[image2]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGOQqzgBAAIWAV+8KCb8AAAAAElFTkSuQmCC>
