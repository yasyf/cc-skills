# Pages

A page is a one-off live dashboard built for one of his orders, such as a migration's progress, a stack's landing lane, or an incident's timeline. It lives in the drive's state dir at `pages/<slug>/`, and the drive's dashboard lists it under Pages and serves it at `/pages/<slug>/`. The dashboard runs no second server or port for it.

## Build one

A page job is an Opus `builder` whose goal names the page and its order. It builds the page in a scratch folder outside any checkout, then runs `drive page add <slug> --order <o> --dir <folder>`, which checks the folder and puts it in place. Running it again with the same slug replaces the page whole. The job's `DONE` line names the page's link. A page is not code the repo ships, so it opens no PR.

The folder holds `index.html`, the page the dashboard opens, and `page.json`, which requires `title` and `goal`. In `page.json`, `orders` and `prs` add orders and PR keys, such as `yasyf/cc-skills#42`, that the page shows besides its own order's; `source` links where the page's idea came from, and `job` names the job that built it. The folder may also hold the `.js`, `.css`, `.json`, `.svg`, and `.png` files the page loads, and the verb refuses any other kind. The page's opaque origin cannot fetch its own files or import them as modules, so it loads its own scripts as classic `<script src>` tags and imports only the kit.

`drive page add` files the page under the order's project and workstream. Pages counts it as live while any order it shows is open, and as done once all of them close.

## Scope rules

- The dashboard serves a page under `Content-Security-Policy: sandbox allow-scripts` and opens it in an `<iframe sandbox="allow-scripts">`. Its origin is opaque, so it cannot call `/api/*`, read the board or post an answer, even when opened on its own.
- The page gets its data only from the dashboard frame around it. After each change, the dashboard posts `{type: "drive:model", slice}` to the page. The slice is the page's own part of the board:
  - `page`, its `page.json`
  - `features`, its orders as Now shows them
  - `prs`, the open PRs of its orders and the PRs it names
  - `questions`, his open decisions on its orders
  - `story`, the newest moments naming its orders
  - `asked`, the state of its last ask
  - `at` and `undo_seconds`
- It acts only by posting `{type: "drive:answer", answer, label}` or `{type: "drive:ask", text}` to its parent. The parent takes an answer only while his click or key inside the page is still a fresh user activation, and only for one of the slice's PRs or questions with that label among its options. It then sends the answer exactly as the queue would, with the same 10-second Undo toast. An ask reaches his Slack direct messages through `/api/say`, under the page's title. A page can never approve anything on its own.
- A page reads nothing outside its slice. When it needs a fact the slice lacks, add the fact to the slice in `pageSlice` in `site/app.js`, never a fetch from the page.

## The kit

`/kit/kit.js` is a JavaScript module, and `/kit/kit.css` styles it:

```html
<link rel="stylesheet" href="/kit/kit.css">
<main id="page"></main>
<script type="module">
  import { live, ring, prLane, decision, timeline, counter } from '/kit/kit.js';
  live((slice) => {
    const prs = slice.features.flatMap((entry) => entry.prs);
    document.querySelector('#page').innerHTML = `${ring(prs.filter((pr) => pr.state === 'merged').length, prs.length, 'PRs merged')}
      ${slice.questions.map(decision).join('')}${prLane(slice.prs)}${timeline(slice.story)}`;
  });
</script>
```

| Component | Draws |
| --- | --- |
| `live(draw)` | calls `draw(slice)` now and on every change |
| `ring(done, total, label)` | a progress ring |
| `counter(element, n)` | counts the element's number up or down to `n` |
| `prLane(prs)` | each PR with its checks, review and landing line, and Approve and Hold buttons |
| `decision(question)` | his decision with a button per option, the recommended one filled |
| `timeline(moments)` | each `{at, text}` moment with its Pacific time |
| `graph(host, source, kind)` | a `mermaid`, `dot` or `arch` diagram in `host`, drawn by the dashboard's own visuals |
| `burndown(host, points, field)` | an area chart of `field` over each point's `at` |
| `answer(card, label)`, `ask(text)` | the bridge calls the buttons make |
| `esc(text)`, `time(at)` | HTML escaping and a Pacific clock time |

A button with `data-kit-answer="<answer>"` and `data-label="<label>"` answers when clicked. The PR and decision buttons are such buttons.

## Strategies

Pick one question the page answers at a glance, such as how far along the migration is or what still blocks this stack, and put it in `goal`. Draw what changes live from the slice, and draw fixed context, such as a plan's steps or a dependency graph, from data the job writes into the folder. Keep his actions on the page to the PRs and decisions the slice already holds, since he answers everything else in the queue.
