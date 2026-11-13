# Pages

A page is a one-off live dashboard built for an order and its scoped work, such as a migration's progress, a stack's landing lane, or an incident's timeline. It lives in the drive's state dir at `pages/<slug>/`, and the drive's dashboard lists it under Pages and serves it at `/pages/<slug>/`. The dashboard runs no second server or port for it.

## Build one

Naming a goal to prioritize or asking for a page adds a page part on its existing build order; there is no separate page job or order kind. A `codex` job builds the page in a scratch folder outside any checkout:

1. Write strict user stories and acceptance criteria before implementation.
2. Build the page using `/kit/kit.js`, `/kit/kit.css`, and the shared kit renderers.
3. Run `drive page add <slug> --order <order> --dir <folder>`, which runs the screenshot gate and registers the page only if the gate passes.
4. Inspect the gate screenshots and click through every user story on the installed live dashboard, verifying that input values, focus, and open state survive stream updates and checking `prefers-reduced-motion`.
5. Report the slug and `/pages/<slug>/` link in `DONE` so the coordinator can return them to the original thread.

Running `drive page add` again with the same slug replaces the page whole; a failed gate leaves the previously installed page intact.

The folder holds `index.html` and `page.json` (which requires `title` and `goal`), plus any `.js`, `.css`, `.json`, `.svg`, and `.png` files the page loads (`drive page add` refuses other file types). The page's opaque origin cannot fetch its own files or import them as modules, so it loads local scripts with classic `<script src>` tags and imports modules only from `/kit/`.

In `page.json`, `orders` and `prs` (such as `yasyf/cc-skills#42`) include specific orders and PRs besides the build order's; `source` links where the page's idea came from, and `job` names the job that built it. `scope` is additive to `orders` and dynamically includes existing and future orders matching `projects`, `workstreams`, or case-insensitive title substring `words`:

```json
{
  "title": "Auth Migration Lane",
  "goal": "What still blocks cutover across auth orders?",
  "orders": ["o104"],
  "prs": ["yasyf/cc-skills#42"],
  "scope": {
    "projects": ["identity"],
    "workstreams": ["token-cutover"],
    "words": ["oauth", "session"]
  },
  "source": "slack://channel/p123456",
  "job": "j889"
}
```

Pages files the page wherever its order is filed now, so a `drive order set` that refiles the order moves its pages too. Pages counts it as live while any matched order is open, and as done once all matched orders close.

## Gate and verification

`drive page add` runs a gate that loads the sandboxed page with its actual scoped slice in `agent-browser` under namespace `codex` and writes `desktop` (`1280x900`) and `narrow` (`390x844`) screenshots under `drive/page-checks`.

The gate refuses:

- Runtime errors or console errors
- Horizontal overflow at `1280x900` or `390x844`
- Repeated frame title or goal
- Raw record lines or visible `<pre>` blocks
- Visible text outside kit components, semantic headings, actions, or navigation

Do not show raw record lines or wrap arbitrary blocks inside a kit component class to evade the gate. The gate is a structural and screenshot artifact check; the `codex` job must still inspect the screenshots in `drive/page-checks` and interactively verify every user story on the installed live dashboard before finishing.

## Scope rules

- The dashboard serves a page under `Content-Security-Policy: sandbox allow-scripts` and opens it in an `<iframe sandbox="allow-scripts">`. Its origin is opaque, so it cannot call `/api/*`, hold secrets or API keys, read the board, or post an answer directly.
- After each change, the parent dashboard posts `{type: "drive:model", slice}` to the page:
  - `page`, its `page.json`
  - `features`, its matched orders as Now shows them
  - `prs`, the open PRs of its matched orders and the PRs it names
  - `questions`, open decisions on its matched orders, each with `href`, the queue route that focuses it
  - `story`, the newest moments naming its matched orders
  - `asked`, the state of its last ask
  - `at`
- A page reads nothing outside its slice. When it needs a fact the slice lacks, add the fact to `pageSlice` in `site/app.js`, never a fetch from the page.
- The page acts only through parent messages validated by the dashboard host:
  - Single and group answers (`answer(card, label)` and `answers([{card, label}])`) require a fresh user activation inside the page and validate that every `{card, label}` matches a visible scoped PR or question and one of its option labels. The parent sends each answer at once, exactly as the queue sends one, and a group shows one toast whose Undo takes back every answer in it. A page can never approve anything on its own.
  - `ask(text)` posts `{type: "drive:ask", text}`, reaching Slack direct messages through `/api/say` under the page's title.
  - `open(route)` navigates the parent dashboard to a scoped `#/feature` route or a shown question's `href`; given an `https` URL, the parent opens it in a new tab. The kit sends every `https` link a page draws through `open`, on a click or a middle-click, since the sandbox lets the page open no tab itself.
  - `model(messages, onToken)` streams text from the parent's `/api/complete` endpoint using fixed Cerebras `gpt-oss-120b` with owner and origin checks. `messages` accepts `1..20` entries with roles `user` or `assistant` and `1..64000` characters each. No API key is present in the slice or client; if the host lacks a key, the call yields a visible error.

## The kit

`/kit/renderers.js` is the one owner of the text both the main dashboard and `/kit/kit.js` draw: `createRenderers` returns rich prose, tables, links, option choices and timeline moments, and the module exports the decision-story parsing and PR state facts beside it. Each surface draws its own cards from them.

`/kit/kit.js` is a JavaScript module styled by `/kit/kit.css`. Keep key UI regions in stable containers: use `patch(host, html)` for live content and mount `diagram(host, archDoc, options)` in its own dedicated host rather than replacing the whole page on each update.

```html
<link rel="stylesheet" href="/kit/kit.css">
<main id="page">
  <section id="overview" data-kit-component="overview"></section>
  <section id="arch" style="height: 360px"></section>
  <section id="work"></section>
</main>
<script type="module">
  import { live, patch, ring, prose, decisionGroups, prGroups, diagram, timeline, esc } from '/kit/kit.js';

  const overviewHost = document.querySelector('#overview');
  const archHost = document.querySelector('#arch');
  const workHost = document.querySelector('#work');

  const archDoc = {
    arch: 1,
    name: 'auth-migration',
    title: 'Auth Migration Architecture',
    nodes: [
      { id: 'gateway', name: 'API Gateway', role: 'Edge session router', kind: 'service' },
      { id: 'tokens', name: 'Token Service', role: 'OAuth token issuer', kind: 'service' },
    ],
  };

  live(async (slice) => {
    const prs = slice.features.flatMap((entry) => entry.prs);
    const merged = prs.filter((pr) => pr.state === 'merged').length;

    patch(overviewHost, `
      <header data-key="summary" data-kit-component="overview">
        <h2>Cutover readiness</h2>
        ${ring(merged, prs.length, 'PRs merged')}
      </header>
    `);

    await diagram(archHost, archDoc, {
      onNode: (nodeId) => console.info('selected', nodeId),
    });

    patch(workHost, `
      <section data-key="decisions">${decisionGroups(slice.questions, (q) => q.blocks?.[0]?.title || "Decisions")}</section>
      <section data-key="prs">${prGroups(slice.prs, (pr) => pr.workstream || "Changes")}</section>
      <section data-key="story">${timeline(slice.story)}</section>
    `);
  });
</script>
```

| Export | Behavior |
| --- | --- |
| `live(draw)` | Calls sync or `async` `draw(slice)` now and on every slice update; sets `document.documentElement.dataset.kitState` (`data-kit-state`) to `ready` or `failed` |
| `patch(host, html)` | Keyed DOM update in `host` that preserves `[data-key]` nodes, open `<details>`, input focus/values, and checkbox choices |
| `prose(text)` | Renders rich block and inline prose via `/kit/renderers.js` |
| `ring(done, total, label)` | Renders a progress ring |
| `counter(element, n)` | Animates `element`'s numeric value to `n` |
| `prLane(prs)` | Renders each PR with checks, review and landing status, and Approve / Hold buttons |
| `prGroups(prs, by)` | Renders grouped PR approval sections, each PR named by its repository and number such as `litestream#20`, with batch approval wired to `answers([{card, label}])` |
| `decision(question)` | Renders a decision card with context, a button per option (recommended option filled), and Answer it in your queue opening its `href` |
| `decisionGroups(questions, by)` | Renders grouped decision sections with group recommendations and per-option review |
| `timeline(moments)` | Renders each `{at, text}` moment with its Pacific time |
| `diagram(host, archDoc, options)` | Mounts `arch-map` once in `host` and performs keyed updates preserving camera and focus; node IDs must be stable, unique, lowercase, and `<= 40` chars; `options` accepts optional `onState` and `onNode`; scrolling zooms and zooming out steps up the architecture |
| `graph(host, source, kind)` | Renders a `mermaid`, `dot`, or `arch` diagram in `host` |
| `burndown(host, points, field)` | Renders an area chart of `field` over each point's `at` |
| `answer(card, label)` | Sends a single scoped PR or decision answer |
| `answers(items)` | Sends `[{card, label}]` as one grouped answer with a single Undo toast |
| `ask(text)` | Sends a question to Slack direct messages under the page's title |
| `open(route)` | Navigates the parent dashboard to a scoped `#/feature` route or a question's `href`, or opens an `https` URL in a new tab |
| `model(messages, onToken)` | Returns a `Promise<string>` streaming a completion from parent `/api/complete` (`gpt-oss-120b`) |
| `esc(text)`, `time(at)` | HTML escaping and Pacific clock time formatting |

A button with `data-kit-answer="<card>"` and `data-label="<label>"` sends an answer when clicked. The single and grouped PR and decision components wire their action buttons automatically.
