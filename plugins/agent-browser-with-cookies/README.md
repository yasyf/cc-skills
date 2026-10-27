# agent-browser-with-cookies

**Authenticated `agent-browser` sessions off your local browser login, one Touch ID tap.** Stream cookies, `localStorage`, and `sessionStorage` from your desktop browser into an isolated session without logging in again or disturbing your running browser.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install agent-browser-with-cookies@skills
```

- macOS only.
- `cookiesync` must be on `PATH`: the plugin's Setup hook installs it on `claude --init`; otherwise, run `brew install --cask yasyf/tap/cookiesync`, then a one-time `cookiesync install` to start the resident daemon.
- `agent-browser` must be on `PATH`, since `ab` drives it.
- You're signed in to the target site in your desktop browser, and the first run auto-registers each installed browser's primary profile.
- Optionally, a Browserbase API key from `BROWSERBASE_API_KEY` or 1Password through `op read`; without one, `ab` uses the local stealth browser, and a locked 1Password stalls each `ab` call about 10 s unless you pass `--local`.

## Use it

### Read a gated dashboard using your existing session

Automating an authenticated page usually forces you to hand over credentials or let an agent hijack your active browser window. With `agent-browser-with-cookies` installed, ask:

```text
Open dashboard.example.com as me and list the unread notifications
```

The skill runs `cookiesync auth --reason "<what you'll do>"`, showing that reason verbatim in a Touch ID prompt that primes every registered browser, such as Chrome and Arc; with no live local session, the prompt routes to a live peer Mac for approval. Every `cookiesync` call in one Claude Code session shares that Touch ID grant, so auth, seeding, and retries cost one tap total. Next, `abwc-seed https://dashboard.example.com` streams your cookies, `localStorage`, and `sessionStorage` process-to-process so the payload never lands on disk, while `ab` selects Browserbase cloud when its API key resolves or the local stealth browser. After verifying the session landed on the app, not a sign-in page, the skill runs every browser command through `"$ab"` and finishes with `"$ab" close` to close the local session or release the Browserbase `keepAlive` session.

### Drive a web app that talks to a separate API host

Single-page apps often split the frontend and API across domains, leaving session state stranded if you only seed the main URL. With `agent-browser-with-cookies` installed, ask:

```text
Check the billing page on app.example.com as me (it calls api.example.com)
```

The skill parses the primary URL and the extra host, authorizes once with Touch ID, and runs `abwc-seed https://app.example.com api.example.com` to stream cookies and web storage for both hosts in one pass. If `abwc-seed` exits `3` because the primary URL landed on a sign-in or SSO page, the skill opens the site in your desktop browser, waits for you to sign in and confirm, and then re-seeds. `IndexedDB` can't be restored in either mode, so a site whose sign-in lives only there (such as Firebase) needs a live-browser fallback. Separately, passing `--bridge` on every `ab` call routes through a local `cookiesync` Chrome DevTools Protocol bridge, a throwaway Chrome already seeded with your cookies, and skips `abwc-seed`. It defaults to `chrome:Default`; `--bridge=<host:browser:profile>` picks another browser or profile.

## What's inside

| Piece | Kind | What it does |
| --- | --- | --- |
| `agent-browser-with-cookies:agent-browser-with-cookies` | skill | Authorizes once with Touch ID, seeds a browser session with your sign-in for every host the task touches, does the task, and closes the session |
| Setup hook | hook | On `claude --init`, installs `cookiesync` and the `clark-browser` stealth Chromium, and points `~/.agent-browser/config.json` at it |
| `ab` | CLI (bin/) | The one entry point for every browser command; picks Browserbase, the local stealth browser (`--local`), or the cookiesync bridge (`--bridge`); `--session <name>` for a separate per-agent session; `ab mode` prints the backend |
| `abwc-seed` | CLI (bin/) | Streams cookies and `localStorage`/`sessionStorage` for the listed hosts into the session and ends on the primary URL |
| `abwc-press` | CLI (bin/) | Key-press workaround `ab` routes `press` through, avoiding an agent-browser 0.38 phantom-keydown flood in macOS Chrome |

Part of [cc-skills](../../README.md).
