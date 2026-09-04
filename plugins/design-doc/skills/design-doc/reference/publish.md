# Publishing the doc

The doc is a static folder; anything that serves files works. The default path is Cloudflare via `wrangler` (the CLI arm of [Cloudflare Drop](https://www.cloudflare.com/drop/); workflow per `cloudflare.com/drop/llms.txt`).

## Stage

Deploy only the files meant to ship, with the doc as the index:

```bash
$TOOL check --strict .
$TOOL render-check .
$TOOL snapshot . --note "<headline>" --item "<one change, for the reader>"
mkdir -p dist
cp design-doc.html dist/index.html
cp registers.json qa-log.json NOTES.md summary.html dist/
cp -R history dist/
```

A doc with a `components/` directory compiles it before the copy and ships the result:

```bash
$TOOL build .
cp components.js dist/
```

`build` runs the plugin's Vite over `components/*.tsx` and writes `components.js` beside the doc; it needs `node` on the machine that publishes, and CI runs the same step when the directory exists. A doc with no `components/` skips the step.

`check --strict` fails on a missing summary, a deck over budget, an entry without its twin or its handle, a question-form title, or a component whose props fail its schema. `render-check` proves every diagram and component draws. A doc that fails either isn't ready to ship.

The stage copies no `sysd.svg`, no PDF, and no vendored code. The diagram source is in the registers, and the PDF button prints the page on the fly through the template's print stylesheet. Mermaid, its ELK layout, `svg-pan-zoom`, and the lucide icons load from jsdelivr at the versions pinned in the template. A doc that kept a hand-drawn diagram (`diagram.kind: "svg"`) adds `sysd.svg` to the copy. `design.py pdf` writes `design-doc.pdf` through the same stylesheet for the reader who wants a file to forward; it is never staged. The folder must contain an `index.html`; the renderer fetches its JSON and the summary with relative paths, so the flat copy is the whole build.

The snapshot stamps this publish as a revision (`meta.rev`, `history/rev-<N>.json`). On a first deploy that revision is just the baseline; from the second onward, a returning reader opens straight into the changes since their last visit — no banner to click through — with unchanged content hidden behind a "show unchanged" toggle, plus a picker for diffing against any earlier revision. The diff lists exactly which register entries were added, changed, or removed, and flags the Summary section when `summary.html` moved.

## Deploy

Check auth first: `npm exec --yes wrangler@latest -- whoami`.

**Authenticated** (OAuth, `CLOUDFLARE_API_TOKEN`, or a global API key):

```bash
npm exec --yes wrangler@latest -- deploy dist --name <slug> --compatibility-date <today>
```

Wrangler auto-loads `.env` from its working directory and prefers a `CLOUDFLARE_API_TOKEN` found there over its OAuth login. When the repo keeps such a token for another purpose, an Access-only token for instance, the deploy fails with `Authentication error [code: 10000]`. Run wrangler from inside `dist/` with the variable stripped:

```bash
( cd dist && env -u CLOUDFLARE_API_TOKEN npm exec --yes wrangler@latest -- deploy . --name <slug> --compatibility-date <today> )
```

The output ends with the live `<name>.<account>.workers.dev` URL. Redeploying with the same `--name` updates the same URL.

**Unauthenticated** — add `--temporary`:

```bash
npm exec --yes wrangler@latest -- deploy dist --name <slug> --temporary --compatibility-date <today>
```

This returns two URLs: the live `workers.dev` URL and a **claim URL**. Hand both to the user immediately: the claim URL grants ownership of the temporary deployment, expires after 60 minutes, and is sensitive (whoever opens it owns the site).

## Redeploy

Updates ship the same way the site first deployed. After editing the registers, from the project directory, with the `build` line only for a doc that has `components/` and the `ai.json` line only for a site with access control in front of it, without its `github` half when there is no token:

```bash
$TOOL check --strict .
$TOOL render-check .
$TOOL snapshot . --note "<headline>" --item "<one change, for the reader>"
cp design-doc.html dist/index.html
cp registers.json qa-log.json NOTES.md summary.html dist/
cp -R history dist/
$TOOL build . && cp components.js dist/
printf '{"endpoint":"%s","model":"%s","key":"%s","github":{"token":"%s"}}' "$AI_ENDPOINT" "$AI_MODEL" "$AI_KEY" "$GITHUB_TOKEN" > dist/ai.json
npm exec --yes wrangler@latest -- deploy dist --name <slug> --compatibility-date <today>
```

The snapshot is what lets a returning reviewer diff this update against the one they last read. It hashes the registers, the diagram source with them, and `summary.html`: when none of them changed it records nothing and exits 0, so the sequence is safe for NOTES-only updates; pass `--force` to stamp a revision for those anyway (no hash sees them, but the revision note still tells readers what moved). An edit to the summary alone is a revision of its own, named in the entry's `changed`, so a returning reader sees that section flagged. `history/` rides along on every deploy because a deploy replaces the asset set wholesale.

When announcing an update, share the live URL with `?since=<rev>` appended: every reader who follows it opens straight into the diff from that revision, whatever their browser remembers. The link's baseline wins for that load only — it is never stored, so the reader's own visit tracking survives; the visit still counts as one, and a later bare visit diffs from the rev they just saw. Clearing the diff, or the "show unchanged" toggle, brings back the full doc.

The same `--name` on an authenticated wrangler updates the same `workers.dev` URL, and a deploy replaces the asset set wholesale — a file removed from `dist/` disappears from the site too. A `--temporary` deploy is a one-off preview: each redeploy mints a new one, so the user claims it (or authenticates wrangler) when the URL needs to survive updates. A project scaffolded before 0.12 keeps its diagram in `sysd.svg`, and the current template reads it from the `diagram` register key instead. Before copying the plugin's template over the old one, move the diagram into `diagram.source` as Mermaid, with nodes named after the `arch` cards, or set `diagram.kind: "svg"` and keep the file. Its `summary.html` is a single page, not a deck, and its entries carry no twins; `check --strict` names each gap, `design.py plainify` drafts the twins, and the deck is rewritten by hand. A project from before 0.11 carries the diagram inline in `design-doc.html` between `<!--SYSD-->` markers; extract that block to `sysd.svg` first, then take the 0.12 step.

Record the deploy name and live URL in NOTES.md's changelog along with what changed; that entry is what a later session redeploys from.

## Site config: the assistant and GitHub links

One file switches on the two features that need a secret. The assistant answers questions from the whole document, summarises, quizzes, explains an entry for a role, and moves the page on the reader's behalf, with every model call made from the browser. The GitHub state on every link chip keeps the Still open progress bars current and gives each chip a hover card. The page fetches `../ai.json` first, for a collection that shares one config, then `ai.json` beside `index.html`, and takes each half from the first file that carries it. The file holds a live config, in whole or in either half, or the kill switch:

```json
{"endpoint": "https://api.cerebras.ai/v1", "model": "gpt-oss-120b", "key": "<api key>", "reasoning": "high", "github": {"token": "<fine-grained PAT>"}}
```

```json
{"disabled": true}
```

`endpoint`, `model`, and `key` travel as a set. `reasoning` (`low`, `medium`, `high`, `none`) sets one effort for every call; absent, chat, the quiz, and read-as run at `high`, the other one-shots at `medium`, and the follow-up suggestions at `low`. `gpt-oss-120b` on Cerebras is the model the assistant is built around; `gemma-4-31b` is the pick only when the assistant must read an image, and its reasoning is on or off, so `check` warns on a graded value under it. `github.token` is the second half and stands alone.

No file, the kill switch, or no AI half, and the page hides every AI affordance: the Ask bar at the bottom right, the `?` and Cmd-J shortcuts, the AI rows and inline answers in Cmd-K, the ✦ on every register row. No `github` half, and the link chips render plain, without a state dot or a hover card; the "N of M closed" bar on each Still open group and the Closed label on a row follow `s` alone. `check` validates the shape when the file is present: `endpoint` is an absolute `https://` URL, http only on localhost, because an https page cannot call an http model host; a file with neither half is an error.

The token is a fine-grained personal access token, read-only, with Pull requests: Read, Issues: Read, Commit statuses: Read, and Contents: Read (Metadata comes with them), scoped to the repositories the docs cite (`meta.repo` and every other one a link names), with a one-year expiry. A token on an organisation's repositories may need the organisation's approval before it works.

Contents: Read is what the evidence hover reads through. A `path:line` token in an assumption's evidence opens a window of the file around the cited lines, fetched from the Contents API and cached for ten minutes in `sessionStorage`, 403s included. Without the scope the token stays a plain link to GitHub, and after the scope is added the page needs a fresh tab.

The page spends three calls on each pull request (the pull request, its reviews, the combined commit status of its head, which is where Buildkite reports) and one on an issue, caches each answer for ten minutes in `sessionStorage`, and stops for the session after a 401 with one notice under the Still open heading. A rate limit pauses it until the reset, with the chips plain until then, and a 404, which is what a private repository the token cannot see returns, renders as unknown, as does a 403 on the status endpoint.

Both values are readable by anyone who can load the page, so `ai.json` ships only on a site with access control in front of it (SSO, a private Pages site, an Access policy). The exposure model is the same for the key and the token, and deleting the secret is the kill switch for either. The client's own limits are the only other guard. The cap of 30 requests a minute lives in `localStorage`, so a reload and a second tab share one allowance rather than minting their own. A back-off honours `retry-after` on a 429, whether it arrives as seconds or as a date.

The follow-ups the assistant offers under an answer come from a prompt that carries the section list and every handle in the document and tells the model to pick from those. Each arrives with the handles it draws on, and the page drops one whose handles resolve to nothing. A follow-up the document cannot answer is a prompt bug.

The panel's settings disclosure names the model and the host, so a reader knows where a question goes, and shows what each call cost. The prefix line counts the document, notes, and log tokens and names any part dropped to fit the budget; the call line gives the last call's prompt, cached, completion, and reasoning tokens, and a tally for the tab. The whole document leaves the browser on every call. The registers as Markdown, NOTES.md, `qa-log.json`, and the glossary go into one system prompt built once per page load, which the model host caches between turns. There is nothing for a reader to switch on, and nothing they can see that the model cannot.

The file is written at deploy, never committed: `.gitignore` lists `ai.json`, a CI check refuses a tracked one, and the deploy step writes it from secrets (GitHub Actions secrets into the Pages artifact, environment variables into `dist/` before `wrangler deploy`), each half only when its secret exists. Rotate either value by redeploying; revoke the assistant by deploying `{"disabled": true}`, and the link states by deleting the token secret. For local work, set `localStorage["design-doc-ai"]` to the AI half and `localStorage["design-doc-github"]` to `{"token": "…"}` in the browser console; the page reads both before it fetches anything, and no file exists to leak.

## Writing the revision note

The note and its `--item` bullets are the changelog a returning reader sees first — in the revision picker, and at the top of the auto-opened diff. Write them for someone who read the doc days ago and remembers the shape but not the details: plain language, what changed, and what it means for them. The diff panel already lists exactly which entries were added, changed, or removed, so the note never carries register IDs, round numbers, or supersession bookkeeping — that's the panel's job; the note tells the story.

The note is a headline (`check` warns past ~90 characters). Each `--item` is one change, stated as its consequence.

Bad — author-frame bookkeeping a reader can't parse:

> Round 8 — monorepo/FDE-repo topology: shared escape-hatch IaC component set in the monorepo (DQ20), release pipeline attached to the FDE repo with no submodule and no pin (DQ21), AMI split into shared base + app layer (DQ22, supersedes DQ17).

Good — the same update, told to the reader:

```bash
$TOOL snapshot . --note "Settled which repo owns the infrastructure code" \
  --item "The shared escape-hatch infrastructure lives in the monorepo now, not in each customer repo" \
  --item "The release pipeline attaches to the FDE repo directly — no submodule, no version pin" \
  --item "Machine images build in two layers, a shared base plus an app layer, instead of one golden image"
```

Revision prose is doc prose: the voice gate in [writing.md](writing.md) applies. Check `wlm profile list`; with a profile, write against the style card, put the drafted note and bullets in a scratch file, and run `wlm -p <profile> adversary critique` over it before stamping. Run `slop-cop check <draft> --lang=markdown --llm-effort=off` on the draft either way.

## Verify

One lightweight check, on first deploy and on every redeploy: load the live URL and confirm the title renders. A 404 right after deploying usually means the route hasn't propagated; wait briefly and retry before changing anything. Exhaustive per-asset probing after a confirmed load is noise.

Local serving stays the fallback for private docs: `python3 -m http.server 8641` in the project folder.
