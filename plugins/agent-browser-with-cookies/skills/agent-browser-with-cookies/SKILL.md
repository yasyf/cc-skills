---
name: agent-browser-with-cookies
description: Run AUTHENTICATED agent-browser automation against one or more sites by reusing your existing local browser login — stream those sites' cookies straight out of the local browser store (one Touch ID tap via the `cookiesync` CLI and its resident daemon) into a fresh agent-browser session, then do the task. Use when a browser task needs you to be logged in (dashboards, gated pages, account settings, an app that calls a separate API host, "do X on <site> as me", "use my session/cookies") and the user is already signed in via their desktop browser. macOS; authorized local use on the user's own machine.
allowed-tools: Bash(cookiesync:*), Bash(bash:*), Bash(open:*), Bash(pkill:*), Bash(brew install:*), Read
effort: medium
---

# agent-browser-with-cookies

Authenticated browser automation without re-logging-in or disturbing the user's
running browser. This skill pulls the cookies for one or more sites out of the local
browser store via the `cookiesync` CLI and streams them straight into an isolated
`agent-browser` session, then hands off to the normal `agent-browser` skill for the
task itself.

It's authorize once (`cookiesync auth`, one Touch ID tap), then one `abwc-seed` call
streams the sites' cookies **and web storage** (localStorage/sessionStorage) into the
session. The payload never lands on disk. Every browser command — launch, seed,
verify, task, cleanup, recovery — goes through **`ab`**, the plugin's universal entry
point; never call `agent-browser` raw.

## Prerequisites

- **`cookiesync` on `PATH`.** The plugin's `Setup` hook installs it on
  `claude --init`; otherwise `brew install --cask yasyf/tap/cookiesync`, then a
  one-time `cookiesync install` starts the resident daemon that caches the Safe
  Storage key for a short TTL after a Touch ID tap. No preflight check needed —
  `auth` and `cookies` failures name the fix in their error text.
- **macOS.**
- **The user is signed in via their desktop browser** to the site(s) you'll
  automate. First run auto-registers each installed browser's primary profile —
  no manual `browser add` needed.

## Procedure

1. **Parse the request** into the **primary** URL (`U1`, the page you'll open) plus any
   **additional** hosts the task touches (`U2`, `U3`, … — e.g. a separate API host the
   app calls, or a second dashboard you read from), and the task. Usually there's just
   one URL. If the task doesn't actually require being logged in, just use the
   `agent-browser` skill.

2. **Authorize.** This is the step that prompts (Touch ID):

   ```bash
   cookiesync auth --reason "<verb phrase: what you'll do>"
   ```

   One tap primes **every** registered browser — the Touch ID sheet names them all
   (e.g. "Chrome + Arc"). **Always pass `--reason`** as a concise, truthful verb
   phrase for the task you're about to do — it surfaces **verbatim** in the dialog
   the user approves, so it must accurately describe the action (e.g.
   `--reason "post a release-announcement tweet"`). The tap caches the key for a
   short TTL; subsequent `cookies` calls within that window need no further prompt.

   With no live local session, the daemon **routes the prompt to a live peer Mac** —
   wait for the approval to come back. That Mac's console may show one named consent
   sheet the first time; approving grants about an hour of silent access.

3. **Launch the authenticated session.** `ab` picks the backend itself: Browserbase
   (one keepAlive cloud session per agent session, reconnected across calls) when its
   key resolves, the local stealth Clark browser otherwise. `--local` anywhere forces
   local. `--session <name>` (both commands; `AB_SESSION` env works too, the flag
   wins) names a separate per-agent session — carry it on **every** later `"$ab"`
   call, `close` included (**Parallel per-host fan-out**, Notes). Seed with the
   shipped helper, listing **every** host the task touches, primary URL first; then
   open only the primary URL:

   ```bash
   ab="${CLAUDE_PLUGIN_ROOT}/bin/ab"
   "${CLAUDE_PLUGIN_ROOT}/bin/abwc-seed" "$U1" "$U2" …
   "$ab" open "$U1"
   ```

   `abwc-seed` streams cookies **and** localStorage/sessionStorage process-to-process:
   locally a merged Playwright `storageState` over a short-lived FIFO plus a per-origin
   sessionStorage pass; on Browserbase (which ignores `--state`) per-host cookie
   headers plus per-origin `storage set` calls, ending in a reload. `cookiesync` emits
   the union across every registered browser/host, newest cookie wins. The helper
   relays cookiesync's per-peer skip warnings on stderr and exits non-zero when the
   payload was bad (usually `cookies` wanting `auth` — re-run step 2, then re-run it)
   or, on Browserbase, when any seeding step failed (a summary line counts them).
   Single site: just `"$U1"`. Other domains' cookies activate when navigation reaches
   them.

   **IndexedDB can't be restored** in either mode — `cookiesync` can't capture it and
   agent-browser has no IndexedDB primitive. A site whose login lives only there needs
   the live-import fallback under **Failure handling**.

4. **Verify auth.** `"$ab" snapshot -i` (and/or `"$ab" get url`) — confirm you landed
   on the app, **not** a login/SSO page. Look for an account/avatar affordance. If it
   looks logged-out, see **Failure handling** — most often **Log in and retry**.

5. **Do the task**, running every command as `"$ab" …` (defer to the `agent-browser`
   skill for command mechanics; substitute `"$ab"` for `agent-browser` throughout).

6. **Clean up.** `"$ab" close` — same `--session <name>` if you launched with one —
   closes the local session, or releases the Browserbase keepAlive session (which
   otherwise lingers until it times out). Always end a run with it.

## Failure handling

- **`cookies` says to run `cookiesync auth` first** (`abwc-seed` relays it and exits
  non-zero) — the cached key's TTL expired and the call couldn't prompt. Run the
  `cookiesync auth --reason "…"` from step 2, then re-run `abwc-seed`.
- **Few or no cookies returned** — the union already covered every registered browser
  and host, so the user isn't signed in to the site on any of them. Do **Log in and
  retry** (below). If it's still thin after a fresh login, they signed in via a
  browser or profile `cookiesync` doesn't track: `cookiesync browser ls` to check,
  `browser add` to register it, or redo the login in a registered browser's primary
  profile.
- **App loads but a cross-host call is unauthorized** (the page renders but its API
  requests 401) — you probably missed a host in step 3. Re-run `abwc-seed` with that
  host added.
- **Daemon wedged — commands hang or fail `Resource temporarily unavailable (os error
  35)`** — the agent-browser daemon is stuck (classically a reader left blocked on a
  FIFO by a killed writer). Kill the daemon and the stealth browser, then re-run the
  launch step (step 3):

  ```bash
  pkill -f agent-browser; pkill -if clark
  ```

- **Browserbase renders logged-out** — cookies and web storage were seeded, but a
  seeded cookie can still be an **expired** desktop session: do **Log in and retry**
  once. If that doesn't fix it, the site rejects Browserbase's cloud IP, or its login
  lives in **IndexedDB** (which Browserbase can't restore). `"$ab" close` to release
  the cloud session, then re-run step 3 forcing local — `abwc-seed --local`, then
  `"$ab" --local open "$U1"` and `--local` on every later call.
- **Loaded but still logged out** (step 4) — first suspect the desktop session itself
  (logged out, or expired since those cookies were written): do **Log in and retry**
  once. If a fresh login doesn't fix it, the login isn't in cookies *or*
  localStorage/sessionStorage, so it's **IndexedDB-based auth** (e.g. Firebase), which
  `cookiesync` can't capture. Fall back to the live import — a live browser reads
  IndexedDB natively: quit the browser and `"$ab" --local --profile "<Profile>" open
  "$U1"`, or start it with `--remote-debugging-port=9222` then
  `"$ab" --local --auto-connect state save ./s.json`.
- **Touch ID denied / cancelled** — re-run `cookiesync auth`. The prompt may have been
  routed to another machine; make sure the user approves it there.

### Log in and retry

The guided fix when the root cause is a missing or stale desktop session — the branches
above that suspect one route here. Run it **once**, then return to the sending branch's
deeper diagnosis.

1. **Open the site for the user** in their own desktop browser — the primary URL, not a
   guessed login path (the site redirects to its own login/SSO entry):

   ```bash
   open "$U1"
   ```

   The default browser is fine — `cookiesync` unions every registered browser — and so
   is any browser the user normally uses, in its **primary profile** (what first-run
   registration tracks). If a separately-gated additional host is the problem, `open`
   that host too.

2. **Tell them, then block.** Ask the user to sign in until the site shows them logged
   in, and wait on explicit confirmation — `AskUserQuestion` with options like **Done** /
   **Can't right now**. Don't poll or assume; on "can't", skip the retry and report. The
   exchange also buys the browser time to flush the fresh cookies to disk — don't race
   it.

3. **Re-seed and reload.**
   - **Local:** `"$ab" close` (with `--local` if you forced it), then re-run step 3
     whole. Closing first matters: `state load` overlays a live session; it doesn't
     clear the logged-out visit's leftover cookies/sessionStorage.
   - **Browserbase:** re-run `abwc-seed` as-is against the live session — `cookies set`
     overwrites same-name cookies and the seeding ends in a reload. Don't `"$ab"
     close`; that releases the cloud session.

   If the retry's seeding asks for `auth` again, the key TTL lapsed while the user
   logged in — re-run the `cookiesync auth --reason "…"` from step 2, then continue.

4. **Verify again** (step 4). If it's still logged out after a fresh login, don't loop;
   take the sending branch's next diagnosis (unregistered browser/profile, Browserbase
   IP, IndexedDB).

## Notes

- **Multiple domains:** pass every host the task touches to one `abwc-seed` call — it
  merges them into a single seeding pass. Reach for this when an app calls a separate
  API host, or you read from a second dashboard. One `auth`, one seed, one session;
  you still `open` only the primary URL.
- **Parallel per-host fan-out:** when N independent hosts are each their own task
  (reading a balance from each of N dashboards), a coordinator primes once with one
  `cookiesync auth --reason` naming every host, then each parallel agent runs step 3
  with its own session name — `abwc-seed --session <slug> <host>`, the same
  `--session <slug>` on every later `"$ab"` call, ending with `"$ab" close --session
  <slug>` (an unclosed Browserbase session lingers for its full timeout). The shared
  grant (**One session, one tap**, below) keeps the whole fan-out at one tap; the
  override suffixes the requestor identity, so slugs need only be unique among this
  session's agents. The boundary with merging: merge co-dependent origins of one flow
  (an app plus its API host, an SSO chain); fan out hosts that stand alone, even when
  one request names them together.
- The stream carries plaintext session tokens — cookies **and** localStorage/
  sessionStorage (a Playwright `storageState` over a FIFO locally; a cookie header
  plus per-origin `storage set` calls in Browserbase mode). It goes process-to-process
  (never a file on disk), and `cookiesync` keeps the raw values out of its own logs.
- `cookiesync auth` is the one Touch ID consent checkpoint: the daemon caches the Safe
  Storage key for a short TTL so `cookies` calls inside that window need no further
  prompt. If nothing is primed yet and the session is live, the seeding call itself
  costs the one tap.
- The Touch ID prompt is a per-task consent checkpoint, not a hardware binding of the
  key. Your `--reason` shows verbatim, so the user approves an informed, task-specific
  request — keep the reason short and honest.
- `--reason` is capped at 160 characters and silently truncated in the dialog. A host
  list that won't fit: name the count and kind instead ("balances + status from 9
  airline and bank sites").
- **One session, one tap:** every `cookiesync` call in a Claude Code session shares
  one Touch ID grant — the CLI derives the requestor from the session — so `auth`
  (step 2), `abwc-seed` (step 3), and any retries cost one tap total. A second tap
  means a different requestor (new session, `COOKIESYNC_REQUESTOR` set) or an
  expired key TTL.
- **Outside Claude Code:** pin the requestor inline on every call —
  `COOKIESYNC_REQUESTOR="agent-browser · $(id -un)" cookiesync auth --reason "…"` —
  an export in a separate step doesn't persist across steps.
- **Browsers:** `abwc-seed` unions every registered browser and host. To force a
  single browser, drop to a direct `cookiesync cookies --browser chrome|arc …` call;
  `--profile` requires `--browser`.
