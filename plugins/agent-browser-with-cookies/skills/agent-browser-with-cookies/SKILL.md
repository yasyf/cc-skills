---
name: agent-browser-with-cookies
description: Run AUTHENTICATED agent-browser automation against a site by reusing your existing local browser login — extract that site's cookies from the local cookie store (self-decrypt Chrome behind one Touch ID tap, with a cross-browser @mherod/get-cookie fallback), seed them into a fresh agent-browser session, then do the task. Use when a browser task needs you to be logged in (dashboards, gated pages, account settings, "do X on <site> as me", "use my session/cookies") and the user is already signed in via their desktop browser. macOS; authorized local use on the user's own machine.
allowed-tools: Bash(uv:*), Bash(agent-browser:*), Bash(rm:*), Read
effort: medium
---

# agent-browser-with-cookies

Authenticated browser automation without re-logging-in or disturbing the user's
running browser. This skill pulls the cookies for one site out of the local cookie
store and seeds them into an isolated `agent-browser` session, then hands off to the
normal `agent-browser` skill for the task itself.

How it gets the cookies (handled by the bundled `cookies.py`):
1. **Self-decrypt Chrome** (primary) — reads `Chrome Safe Storage` via Apple's signed
   `/usr/bin/security` and decrypts the cookie DB. A **Touch ID** tap gates each run;
   the key read is silent after a one-time "Always Allow".
2. **`@mherod/get-cookie`** (fallback) — if Chrome has no cookies for the site, sweeps
   **all** browsers (Brave, Arc, Edge, Safari, Firefox, …). Lazily installed and cached.

## Procedure

Set once:

```bash
SCRIPT="uv run ${CLAUDE_PLUGIN_ROOT}/skills/agent-browser-with-cookies/scripts/cookies.py"
```

1. **Parse the request** into the target site (URL `U`, host `R`) and the task. If the
   task doesn't actually require being logged in, just use the `agent-browser` skill.

2. **Warn about the prompt(s), once.** On the **first ever run** the user will see a
   macOS "wants to use Chrome Safe Storage" dialog — they must click **Always Allow**
   (you can't click it) — plus a **Touch ID** prompt. **Every run after** is a single
   Touch ID tap. Macs without Touch ID / SSH sessions get the password dialog instead.

3. **Pick the profile (optional).** If the user has several Chrome profiles, run
   `$SCRIPT list-profiles --url "$U" --json` (no decryption, no prompt) and pick by
   account email. Pass it through as `--profile "Profile 3"` in the next step. Skip this
   if there's an obvious single match.

4. **Extract.** This is the step that prompts (Touch ID):

   ```bash
   STATE=$($SCRIPT extract --url "$U" --reason "<verb phrase: what you'll do>" | tail -1)
   ```

   **Always pass `--reason`** as a concise, truthful verb phrase for the task you're about
   to do — it completes *"access your `<host>` session to …"* in the Touch ID prompt the
   user approves, so it must accurately describe the action (e.g.
   `--reason "post a release-announcement tweet"`).

   stderr shows a summary (`engine=…`, cookie count, which browser the fallback used).
   On a nonzero exit, see **Failure handling**. `AMBIGUOUS` → re-run with `--profile`.

5. **Launch the authenticated session**, then delete the state file immediately (the
   cookies now live in the browser context, not on disk):

   ```bash
   agent-browser --session abwc --state "$STATE" open "$U"
   rm -f "$STATE"
   ```

6. **Verify auth.** `agent-browser --session abwc snapshot -i` (and/or `get url`) —
   confirm you landed on the app, **not** a login/SSO page. Look for an account/avatar
   affordance. If it looks logged-out, see **Failure handling**.

7. **Do the task** with normal `agent-browser --session abwc …` commands (defer to the
   `agent-browser` skill for command mechanics).

8. **Clean up.** `agent-browser --session abwc close`; make sure `rm -f "$STATE"` ran.
   Never write the state file into the repo or commit it.

## Failure handling

- **`not logged into <R> in any local browser`** — the user isn't signed in to the site
  anywhere locally. Ask them to log in via their browser first, then retry.
- **Loaded but still logged out** (step 6) — almost always **localStorage-based auth**
  (token in `localStorage`, not a cookie); the cookie store can't see it. Fall back to
  agent-browser's live import: quit Chrome and
  `agent-browser --profile "<Profile>" open "$U"`, or start Chrome with
  `--remote-debugging-port=9222` then `agent-browser --auto-connect state save ./s.json`.
- **Keychain denied / Touch ID cancelled** — re-run; on the first run click **Always
  Allow** on the Chrome Safe Storage dialog. If it persists, the get-cookie fallback
  still runs (`$SCRIPT extract --url "$U" --reason "<verb phrase>" --engine get-cookie`).
- **`AMBIGUOUS: multiple Chrome profiles match`** — re-run step 4 with `--profile`.

## Notes

- The state file holds plaintext session tokens. `cookies.py` writes it `0600` to a temp
  path and prints only the path (never values); delete it right after `open`.
- macOS + Chrome `v10` cookies for the self-decrypt path. App-bound (`v20`) cookies and
  non-Chrome browsers go through the get-cookie fallback automatically.
- The Touch ID prompt is a per-task consent checkpoint, not a hardware binding of the key.
  It shows the target domain plus your `--reason` (*"access your `<host>` session to …"*),
  so the user approves an informed, task-specific request — keep the reason short and honest.
