# Publishing the doc

The doc is a static folder; anything that serves files works. The default path is Cloudflare via `wrangler` (the CLI arm of [Cloudflare Drop](https://www.cloudflare.com/drop/); workflow per `cloudflare.com/drop/llms.txt`).

## Stage

Deploy only the files meant to ship, with the doc as the index:

```bash
mkdir -p dist
cp design-doc.html dist/index.html
cp registers.json qa-log.json NOTES.md design-doc.pdf dist/
```

Rerun `design.py pdf` first when the registers changed since the last build, so the PDF button serves the current doc. The folder must contain an `index.html`; the renderer fetches its JSON with relative paths, so the flat copy is the whole build.

## Deploy

Check auth first: `npm exec --yes wrangler@latest -- whoami`.

**Authenticated** (OAuth, `CLOUDFLARE_API_TOKEN`, or a global API key):

```bash
npm exec --yes wrangler@latest -- deploy dist --name <slug> --compatibility-date <today>
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
design.py pdf .                  # so the PDF button serves the current doc
cp design-doc.html dist/index.html
cp registers.json qa-log.json NOTES.md design-doc.pdf dist/
npm exec --yes wrangler@latest -- deploy dist --name <slug> --compatibility-date <today>
```

The same `--name` on an authenticated wrangler updates the same `workers.dev` URL, and a deploy replaces the asset set wholesale — a file removed from `dist/` disappears from the site too. A `--temporary` deploy is a one-off preview: each redeploy mints a new one, so the user claims it (or authenticates wrangler) when the URL needs to survive updates.

Record the deploy name and live URL in NOTES.md's changelog along with what changed; that entry is what a later session redeploys from.

## Verify

One lightweight check, on first deploy and on every redeploy: load the live URL and confirm the title renders. A 404 right after deploying usually means the route hasn't propagated; wait briefly and retry before changing anything. Exhaustive per-asset probing after a confirmed load is noise.

Local serving stays the fallback for private docs: `python3 -m http.server 8641` in the project folder.
