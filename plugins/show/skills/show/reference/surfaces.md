# Surface mechanics

One section per surface. The dispatch table in SKILL.md decides which one you're
in; this file is how to execute it well.

## Static Artifact page

1. Load `artifact-design` (Skill tool) before writing any HTML — it calibrates
   treatment (utilitarian vs editorial) and carries the token-system process.
2. Write the page to your scratchpad, then publish with the Artifact tool.
3. Constraints that bite: the CSP blocks every external host, so inline all
   CSS/JS and embed assets as data URIs; style both light and dark themes; wide
   content scrolls inside its own `overflow-x: auto` container, never the body.
4. Keep the `<title>` and favicon stable across redeploys — users find the tab
   by them. Redeploying the same file path updates the same URL.
5. Charts and dashboards: load `dataviz` as well, and give the numbers the same
   design care as the type.

A page is right when the human reads and reacts in chat. The moment they must
act per item, move up to a board.

## cc-present live board

Owned end to end by the `cc-present:present` skill — trigger it and follow its
loop: compose typed blocks, serve them with `start --doc`, share the printed
URL, watch the event stream, answer feedback with `reply` and patch blocks live
with `update-block`, then drain the round with `outcomes` and `close`. What
matters at dispatch time:

- The document is typed JSON blocks: section, card, approval, choice, input,
  markdown, code, diff, image, table, progress. Never hand-written HTML.
- Human verdicts and agent content live in separate lanes — redrafting a card
  never clobbers a decision already made on it.
- The loop is live: a rejected draft becomes an in-place redraft in the open
  tab. Do not park waiting for clicks; keep working and react to events.

## AskUserQuestion

- One call, up to four questions, each with 2–4 concrete options; batch related
  decisions instead of serializing them across turns.
- Recommend by putting the favored option first and suffixing "(Recommended)".
- Use option previews when the choices are visual (mockups, code variants).
- Right for one decision the work is blocked on. Wrong for per-item review of a
  list — that's a board — and wrong for anything the user already decided.

## Chat prose

Still the best surface for a direct answer, a short causal explanation, or a
verdict with its reasoning. If you're formatting more than a screen of structure
into markdown — tables of options, numbered candidate lists, findings with
per-item asks — you've left prose territory; go back to the dispatch table.
