# Pages

A page is a one-off live dashboard built for an order and its scoped work, such as a migration's progress, a stack's landing lane, or an incident's timeline. It lives in the drive's state dir at `pages/<slug>/`. The drive's dashboard lists it under Pages and serves it at `/pages/<slug>/`, with no second server or port.

## Applet pages

Most pages are applets the dashboard composes itself. `drive page make [<slug>] --order <o> --goal "<the goal or decision>"`, the drive MCP server's `page` tool, keeps `pages/<slug>/page.json` alone, `kind: applet`, and prints the page's link, `<dashboard>/#/pages/<slug>`. `--project`, `--workstream` and `--word` widen its scope as `scope` below does, and `--whole-drive` takes the whole drive. `drive order set --priority` makes one for its order, and the communicator makes one whenever a page helps him decide or see where a goal stands.

The dashboard reads the page's live slice and has a Claude session compose it from the component library in `applet.py`, five to nine blocks in the order that tells the goal's story:

| Block | Draws |
|---|---|
| `story` | beats he reads in order: why the goal exists, where it stands, what is at stake |
| `decide` | each decision live as its own applet, with its paths and their consequences, answered as the queue answers |
| `approve` | PRs he approves together or one by one, each with its plain status and what Approve does |
| `stack` | a stack bottom first, each change with what it does and where it stands |
| `staging` | where each PR was tested: passed, running, queued, failed or no test |
| `diff` | each PR's change file by file, each file folding open, with the reviewer bot's comments and his own under the lines they name |
| `path` | the steps to the goal as a track, each step's orders done of all |
| `timeline` | the newest moments of its scope, live |
| `compare` | options side by side, each with its consequence, one recommended |
| `before_after` | what is true before the goal is met and after |
| `diagram` | a Mermaid diagram labelled in plain words |
| `metrics` | live counts: PRs ready to land, PRs waiting, decisions, orders done |
| `callout` | one line he must not miss |

Every PR card in the queue walks the same blocks as a five-step story. It opens on the problem and who feels it, then the change as a `diff` with the why of each hunk, and what it does at run time as `before_after` and a `diagram`. Then come the evidence from staging, tests and its risks, and last his call, with what Approve and Hold each do side by side above the Approve bar. The story is written once for each head the PR reaches.

A layout is kept under `applets/<slug>/<digest>.json` by the digest of the page's goal and the PRs, decisions and orders its slice holds, so state changes redraw live with no new compose, and new work in scope composes it again. Until a compose lands the page draws its last layout, else the dashboard's own: the counts, the decisions, the PRs to approve, the path and the timeline.

## Build one by hand

When Jev reads his order as naming a goal or asking where a project or body of work stands, `drive order add` adds a `page` part to it and a `codex` job on that part, briefed by the drive with this section as its plan. An answer order becomes a build whose other parts stay answers. The job starts at once, past the research the order's other parts wait on:

1. Run `drive page make --order <order> --goal "<his goal in plain words>"`, open its link on the installed dashboard once it composes, and click through it. When its blocks show everything his words ask for, report its link in `DONE` and stop here. Otherwise write strict user stories and acceptance criteria for what the blocks leave out, and build that page by hand in a scratch folder outside any checkout.
2. Build the page using `/kit/kit.js`, `/kit/kit.css`, and the shared kit renderers.
3. Run `drive page add <slug> --order <order> --dir <folder>`, which runs the screenshot gate and registers the page only if the gate passes.
4. Look at the gate's screenshots in `drive/page-checks`, then click through every user story on the installed live dashboard. Input values, focus and open state should survive stream updates, and `prefers-reduced-motion` should hold. Three facts about the browser:
   - agent-browser's `frame` command leaves `eval`, `get count`, `wait --fn` and `set media` on the dashboard. `drive page frame <session> <slug>` opens and prints a session on the page itself, `<session>-frame`: run commands on the page there, again after any reload of the dashboard, and take screenshots on `<session>`.
   - To see the page go stale, end the dashboard's stream with `agent-browser --session <session> eval "window.stop()"`.
   - The dashboard refuses every answer, tap, Undo and typed word from an automated browser, one whose `navigator.webdriver` is true as `agent-browser`'s is. An answer button there ends on its `Not sent` toast and no card of his changes.
5. Report the slug and `/pages/<slug>/` link in `DONE` so the coordinator can return them to the original thread.

Running `drive page add` again with the same slug replaces the page whole. A failed gate leaves the installed page as it was.

The folder holds `index.html` and `page.json`, which requires `title` and `goal`, plus any `.js`, `.css`, `.json`, `.svg` and `.png` files the page loads; `drive page add` refuses other file types. The page's opaque origin cannot fetch its own files or import them as modules, so it loads local scripts with classic `<script src>` tags and imports modules only from `/kit/`.

In `page.json`:

- `orders` and `prs` include specific orders and PRs besides the build order's.
- `scope` adds every existing and future order matching `projects`, `workstreams`, or a case-insensitive title substring in `words`. `"scope": "drive"` takes the whole drive instead: every order Now shows with every open PR and decision, for a page on where all the work stands.
- `source` links where the page's idea came from, and `job` names the job that built it.

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

A page names a drive order, job or PR in those fields and nowhere else: not in its title, its goal, a `data.js` of edges or a link. He cannot open an id the page shows, and an id its code keeps freezes what it draws, so the page draws each from its slice. `drive page add` refuses a page naming one, by file and line.

Pages files the page wherever its order is filed now, so a `drive order set` that refiles the order moves its pages too. A page is live while any matched order is open, and done once all matched orders close.

## Gate and verification

`drive page add` loads the sandboxed page with its actual scoped slice in `agent-browser` under namespace `codex` and writes `desktop` (`1280x900`) and `narrow` (`390x844`) screenshots under `drive/page-checks`.

The gate refuses:

- Runtime errors or console errors
- Horizontal overflow at `1280x900` or `390x844`
- Repeated frame title or goal
- Raw record lines or visible `<pre>` blocks
- Visible text outside kit components, semantic headings, actions, or navigation

The gate checks structure and screenshots. It does not check that the stories work, which is step 4 of Build one, and wrapping a raw block in a kit component class passes it while leaving the page unreadable.

## Scope rules

- The dashboard serves a page under `Content-Security-Policy: sandbox allow-scripts` and opens it in an `<iframe sandbox="allow-scripts">`. Its origin is opaque, so it cannot call `/api/*`, hold secrets or API keys, read the board, or post an answer directly.
- After each change, the parent dashboard posts `{type: "drive:model", slice}` to the page:
  - `page`, its `page.json`
  - `features`, its matched orders as Now shows them
  - `edges`, each `{from, to, kind}` between two matched orders, read from the drive's jobs whenever it changes: `carries` when a job of `from` also builds parts of `to`, `waits` while a job of `from` waits on a part, job, PR or PR's release of `to`, so a page draws what holds what without keeping a list of its own
  - `prs`, the open PRs of its matched orders and the PRs it names, each with `staging`, the line its PR cards say of its staging test, such as `Tested on staging: <staging env> at f3a6f23, passed` with a link to its proof
  - `questions`, open decisions on its matched orders, each with `href`, the queue route that focuses it
  - `story`, the newest moments naming its matched orders
  - `asked`, the state of its last ask
  - `at`
- A page reads only its slice, since the sandbox blocks a fetch. A fact the slice lacks is added to `pageSlice` in `site/app.js`.
- The page acts through parent messages the dashboard host validates:
  - `answer(card, label)` and `answers([{card, label}])` need a fresh user activation inside the page, so a page cannot answer on its own. Every `{card, label}` must match a visible scoped PR or question and one of its option labels. The parent sends each answer at once, as the queue sends one, and a group shows one toast whose Undo takes back every answer in it.
  - `ask(text)` posts `{type: "drive:ask", text}`, reaching Slack direct messages through `/api/say` under the page's title.
  - `open(route)` navigates the parent dashboard to a scoped `#/feature` route or a shown question's `href`; given an `https` URL, the parent opens it in a new tab. The kit sends every `https` link a page draws through `open`, on a click or a middle-click, since the sandbox lets the page open no tab itself.
  - `model(messages, onToken)` streams text from the parent's `/api/complete` endpoint using fixed Cerebras `gpt-oss-120b` with owner and origin checks. `messages` accepts `1..20` entries with roles `user` or `assistant` and `1..64000` characters each. No API key is present in the slice or client; if the host lacks a key, the call yields a visible error.

## The kit

`/kit/renderers.js` is the one owner of the text both the main dashboard and `/kit/kit.js` draw: `createRenderers` returns rich prose, tables, links, option choices and timeline moments, and the module exports option parsing, plain card text and PR state facts beside it. Each surface draws its own cards from them.

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
| `prLane(prs)` | Renders each PR with checks, review, staging and landing status, its staging line whole under Landing details, and Approve / Hold buttons |
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
