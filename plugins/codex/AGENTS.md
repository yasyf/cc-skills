# Session Instructions

## Browser Automation

Use the `agent-browser` CLI for anything that needs a browser — verifying DOM or
frontend behavior, fetching rendered pages, form flows, screenshots.

NEVER launch a browser binary directly: no `/Applications/Google Chrome.app/...`,
no `chromium`/`google-chrome` (headless or not), no `--remote-debugging-port`, no
`open -a`. Direct launches open windows on the user's desktop or crash, and leave
orphaned processes. If `agent-browser` is not installed (`command -v
agent-browser` fails), say so in your reply and answer from documentation or
source instead — a direct browser launch is never the fallback.

Rules:

- Prefix every call with `AGENT_BROWSER_NAMESPACE=codex` so your browsing never
  touches the user's own agent-browser sessions.
- Before first use in a session, run `agent-browser skills get core --full` and
  follow it.

Core loop:

1. `AGENT_BROWSER_NAMESPACE=codex agent-browser open <url>` — navigate (the
   daemon auto-starts on first use)
2. `AGENT_BROWSER_NAMESPACE=codex agent-browser snapshot -i` — interactive
   elements with refs (`@e1`, `@e2`)
3. `AGENT_BROWSER_NAMESPACE=codex agent-browser click @e1` /
   `... fill @e2 "text"` — interact via refs
4. Re-snapshot after page changes.
