---
name: agent-browser-with-cookies
description: Run AUTHENTICATED agent-browser automation against a site by reusing your existing local browser login — stream that site's cookies straight out of the local browser store (one Touch ID tap via the `cookiesync` CLI and its resident daemon) into a fresh agent-browser session, then do the task. Use when a browser task needs you to be logged in (dashboards, gated pages, account settings, "do X on <site> as me", "use my session/cookies") and the user is already signed in via their desktop browser. macOS; authorized local use on the user's own machine.
allowed-tools: Bash(cookiesync:*), Bash(agent-browser:*), Read
effort: medium
---

# agent-browser-with-cookies

Authenticated browser automation without re-logging-in or disturbing the user's
running browser. This skill pulls the cookies for one site out of the local browser
store via the `cookiesync` CLI and streams them straight into an isolated
`agent-browser` session, then hands off to the normal `agent-browser` skill for the
task itself.

It's two commands: authorize once (`cookiesync auth`, one Touch ID tap), then pipe
the site's cookies into agent-browser's `--state -` (stdin). No temp file, no state
path to track, nothing to `rm`.

## Prerequisites

- **`cookiesync` on `PATH` with its daemon running.** The plugin's `Setup` hook
  installs it on `claude --init`; otherwise `uv tool install cookiesync-cli`. Then
  `cookiesync install` starts the resident daemon that caches the Safe Storage key
  for a short TTL after a Touch ID tap.
- **macOS.**
- **The user is signed in via their desktop browser** (Chrome or Arc) to the site
  you'll automate.

## Procedure

1. **Parse the request** into the target site (URL `U`, host `R`) and the task. If the
   task doesn't actually require being logged in, just use the `agent-browser` skill.

2. **Pick the browser.** Default to `--browser chrome`; use `--browser arc` if the
   user signed in via Arc (or chrome turns up no cookies for the site). The browser
   choice is passed to **both** `auth` and `cookies`.

3. **Authorize.** This is the step that prompts (Touch ID):

   ```bash
   cookiesync auth --browser chrome --reason "<verb phrase: what you'll do>"
   ```

   **Always pass `--reason`** as a concise, truthful verb phrase for the task you're
   about to do — it surfaces **verbatim** in the Touch ID dialog the user approves, so
   it must accurately describe the action (e.g.
   `--reason "post a release-announcement tweet"`). One tap caches the key for a short
   TTL; subsequent `cookies` calls within that window need no further prompt.

   If you're not physically at this machine, the daemon may **route the Touch ID
   prompt to another of the user's machines** — wait for the approval to come back.

4. **Launch the authenticated session** by streaming the site's cookies straight into
   agent-browser via stdin:

   ```bash
   cookiesync cookies "$U" --browser chrome --format playwright | agent-browser --session abwc --state - open "$U"
   ```

   `cookiesync cookies` emits the site's cookies as a Playwright `storageState`
   document on stdout; `agent-browser --state -` reads it from stdin. The cookies live
   only in the browser context — nothing touches disk.

5. **Verify auth.** `agent-browser --session abwc snapshot -i` (and/or `get url`) —
   confirm you landed on the app, **not** a login/SSO page. Look for an account/avatar
   affordance. If it looks logged-out, see **Failure handling**.

6. **Do the task** with normal `agent-browser --session abwc …` commands (defer to the
   `agent-browser` skill for command mechanics).

7. **Clean up.** `agent-browser --session abwc close`. There's no state file to remove.

## Failure handling

- **`cookies` says to run `cookiesync auth` first** — `cookiesync cookies` fails closed
  if it's called before `auth` (or after the cached key's TTL expired). Run the
  `cookiesync auth --browser … --reason "…"` from step 3, then re-run the `cookies`
  pipe.
- **Few or no cookies returned** — the user isn't actually signed in to the site in
  that browser. Try the other browser (`--browser arc`); if that's empty too, ask the
  user to log in via their browser first, then retry.
- **Loaded but still logged out** (step 5) — almost always **localStorage-based auth**
  (token in `localStorage`, not a cookie); the cookie store can't see it. Fall back to
  agent-browser's live import: quit the browser and
  `agent-browser --profile "<Profile>" open "$U"`, or start the browser with
  `--remote-debugging-port=9222` then `agent-browser --auto-connect state save ./s.json`.
- **Touch ID denied / cancelled** — re-run `cookiesync auth`. The prompt may have been
  routed to another machine; make sure the user approves it there.

## Notes

- The cookies stream is a Playwright `storageState` document carrying plaintext session
  tokens. It goes process-to-process over a pipe (never a file on disk), and
  `cookiesync` keeps the raw values out of its own logs.
- `cookiesync auth` must precede `cookiesync cookies`: `auth` is the one Touch ID
  consent checkpoint, and the daemon caches the Safe Storage key for a short TTL so the
  `cookies` call that follows needs no further prompt.
- The Touch ID prompt is a per-task consent checkpoint, not a hardware binding of the
  key. Your `--reason` shows verbatim, so the user approves an informed, task-specific
  request — keep the reason short and honest.
- **Browsers:** `--browser chrome` (default) and `--browser arc`. Pass the same
  `--browser` to both `auth` and `cookies`.
