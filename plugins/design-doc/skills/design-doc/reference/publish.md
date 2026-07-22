# Publishing the doc

The doc is a static folder; anything that serves files works. The default path is Cloudflare via `wrangler` (the CLI arm of [Cloudflare Drop](https://www.cloudflare.com/drop/); workflow per `cloudflare.com/drop/llms.txt`).

## Stage

Deploy only the files meant to ship, with the doc as the index:

```bash
$TOOL snapshot . --note "<what changed>"
mkdir -p dist
cp design-doc.html dist/index.html
cp registers.json qa-log.json NOTES.md design-doc.pdf dist/
cp -R history dist/
```

Rerun `design.py pdf` first when the registers changed since the last build, so the PDF button serves the current doc. The folder must contain an `index.html`; the renderer fetches its JSON with relative paths, so the flat copy is the whole build.

The snapshot stamps this publish as a revision (`meta.rev`, `history/rev-<N>.json`). On a first deploy that revision is just the baseline; from the second onward, readers get a revision picker and returning readers an "updated since your last visit" banner, each showing exactly which register entries were added, changed, or removed since the revision they pick.

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

Updates ship the same way the site first deployed. After editing the registers, from the project directory:

```bash
$TOOL snapshot . --note "<what changed>"
$TOOL pdf .                      # so the PDF button serves the current doc
cp design-doc.html dist/index.html
cp registers.json qa-log.json NOTES.md design-doc.pdf dist/
cp -R history dist/
npm exec --yes wrangler@latest -- deploy dist --name <slug> --compatibility-date <today>
```

The snapshot is what lets a returning reviewer diff this update against the one they last read. When the registers haven't changed it records nothing and exits 0, so the sequence is safe for SVG- or NOTES-only updates; pass `--force` to stamp a revision for those anyway (a registers diff can't see them, but the revision note still tells readers what moved). `history/` rides along on every deploy because a deploy replaces the asset set wholesale.

When announcing an update, share the live URL with `?since=<rev>` appended: a reader whose browser has never seen the doc gets that revision as their baseline — the update banner offers the diff from there — while a reader with a remembered visit keeps their own.

The same `--name` on an authenticated wrangler updates the same `workers.dev` URL, and a deploy replaces the asset set wholesale — a file removed from `dist/` disappears from the site too. A `--temporary` deploy is a one-off preview: each redeploy mints a new one, so the user claims it (or authenticates wrangler) when the URL needs to survive updates. A project scaffolded before the changes-since feature keeps its older `design-doc.html`. To pick the feature up, copy the plugin's current template over it, then re-splice the project's hand-drawn SVG back between the `<!--SYSD-->` markers — the diagram is the one hand-authored part of the file, and a plain copy would replace it with the placeholder.

Record the deploy name and live URL in NOTES.md's changelog along with what changed; that entry is what a later session redeploys from.

## Verify

One lightweight check, on first deploy and on every redeploy: load the live URL and confirm the title renders. A 404 right after deploying usually means the route hasn't propagated; wait briefly and retry before changing anything. Exhaustive per-asset probing after a confirmed load is noise.

Local serving stays the fallback for private docs: `python3 -m http.server 8641` in the project folder.
