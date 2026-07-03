---
name: agent-browser-with-cookies
description: Run AUTHENTICATED agent-browser automation against one or more sites by reusing your existing local browser login — stream those sites' cookies straight out of the local browser store (one Touch ID tap via the `cookiesync` CLI and its resident daemon) into a fresh agent-browser session, then do the task. Use when a browser task needs you to be logged in (dashboards, gated pages, account settings, an app that calls a separate API host, "do X on <site> as me", "use my session/cookies") and the user is already signed in via their desktop browser. macOS; authorized local use on the user's own machine.
allowed-tools: Bash(cookiesync:*), Bash(agent-browser:*), Read
effort: medium
---

# agent-browser-with-cookies

Authenticated browser automation without re-logging-in or disturbing the user's
running browser. This skill pulls the cookies for one or more sites out of the local
browser store via the `cookiesync` CLI and streams them straight into an isolated
`agent-browser` session, then hands off to the normal `agent-browser` skill for the
task itself.

It's two commands: authorize once (`cookiesync auth`, one Touch ID tap), then pipe
the sites' cookies into agent-browser's `--state -` (stdin). No temp file, no state
path to track, nothing to `rm`.

## Prerequisites

- **`cookiesync` on `PATH` with its daemon running.** The plugin's `Setup` hook
  installs it on `claude --init`; otherwise `uv tool install cookiesync-cli`. Then
  `cookiesync install` starts the resident daemon that caches the Safe Storage key
  for a short TTL after a Touch ID tap.
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

3. **Launch the authenticated session** by streaming the sites' cookies straight into
   agent-browser via stdin. List **every** host the task touches; open only the
   **primary** URL:

   ```bash
   cookiesync cookies "$U1" "$U2" … --format playwright | agent-browser --session abwc --state - open "$U1"
   ```

   `cookiesync cookies` emits **one merged** Playwright `storageState` document on
   stdout: the best-effort union across every registered endpoint — all local browsers
   and all registered hosts — with the newest cookie winning; ties prefer the local
   machine. Endpoints that fail are skipped with warnings on stderr; the call fails
   only when zero endpoints contribute. If nothing is primed yet and the session is
   live, this call itself costs the one tap. `agent-browser --state -` reads the JSON
   from stdin. You `open` only `$U1` — the other domains' cookies are present
   in the context and become active when navigation or requests reach them. With a
   single site it's `cookiesync cookies "$U1" …`. The cookies live only in the
   browser context — nothing touches disk.

4. **Verify auth.** `agent-browser --session abwc snapshot -i` (and/or `get url`) —
   confirm you landed on the app, **not** a login/SSO page. Look for an account/avatar
   affordance. If it looks logged-out, see **Failure handling**.

5. **Do the task** with normal `agent-browser --session abwc …` commands (defer to the
   `agent-browser` skill for command mechanics).

6. **Clean up.** `agent-browser --session abwc close`. There's no state file to remove.

## Failure handling

- **`cookies` says to run `cookiesync auth` first** — the cached key's TTL expired and
  the call couldn't prompt. Run the `cookiesync auth --reason "…"` from step 2, then
  re-run the `cookies` pipe.
- **Few or no cookies returned** — the union already covered every registered browser
  and host, so the user isn't signed in to the site on any of them. Ask the user to
  log in via their browser first, then retry.
- **App loads but a cross-host call is unauthorized** (the page renders but its API
  requests 401) — you probably missed a host in step 3. Add that host as another
  `cookies` argument and re-run the pipe.
- **Loaded but still logged out** (step 4) — almost always **localStorage-based auth**
  (token in `localStorage`, not a cookie); the cookie store can't see it. Fall back to
  agent-browser's live import: quit the browser and
  `agent-browser --profile "<Profile>" open "$U1"`, or start the browser with
  `--remote-debugging-port=9222` then `agent-browser --auto-connect state save ./s.json`.
- **Touch ID denied / cancelled** — re-run `cookiesync auth`. The prompt may have been
  routed to another machine; make sure the user approves it there.

## Notes

- **Multiple domains:** pass every host the task touches to one `cookiesync cookies`
  call — it merges them into a single `storageState`. Reach for this when an app calls a
  separate API host, or you read from a second dashboard. One `auth`, one pipe, one
  session; you still `open` only the primary URL.
- The cookies stream is a Playwright `storageState` document carrying plaintext session
  tokens. It goes process-to-process over a pipe (never a file on disk), and
  `cookiesync` keeps the raw values out of its own logs.
- `cookiesync auth` is the one Touch ID consent checkpoint: the daemon caches the Safe
  Storage key for a short TTL so `cookies` calls inside that window need no further
  prompt. If nothing is primed yet and the session is live, the `cookies` call itself
  costs the one tap.
- The Touch ID prompt is a per-task consent checkpoint, not a hardware binding of the
  key. Your `--reason` shows verbatim, so the user approves an informed, task-specific
  request — keep the reason short and honest.
- **One session, one tap:** every `cookiesync` call in a Claude Code session shares
  one Touch ID grant — the CLI derives the requestor from the session — so `auth`
  (step 2), `cookies` (step 3), and any retries cost one tap total. A second tap
  means a different requestor (new session, `COOKIESYNC_REQUESTOR` set) or an
  expired key TTL.
- **Outside Claude Code:** pin the requestor inline on every call —
  `COOKIESYNC_REQUESTOR="agent-browser · $(id -un)" cookiesync auth --reason "…"` —
  an export in a separate step doesn't persist across steps.
- **Browsers:** omit `--browser` and one tap unions every registered browser and host.
  `--browser chrome|arc` forces a single browser as the escape hatch; `--profile`
  requires `--browser`.
