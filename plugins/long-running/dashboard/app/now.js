const VIEWS = { story: 'page', work: 'rows3' };
const NODE_WORD = { fixing: 'Being fixed', you: 'Needs you', nobody: 'No one assigned', waiting: 'Waiting', done: 'Done' };
const MARK_WORD = { progress: 'Progress', finding: 'Found', stuck: 'Stuck', done: 'Done' };
const BLOCK_RANK = { nobody: 0, you: 1, fixing: 2, waiting: 3 };
const datelineFmt = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Los_Angeles', weekday: 'long', month: 'long', day: 'numeric' });
const story = { item: null, es: null, lines: [], draft: null, status: null, open: false };

function view(key) {
  return new URLSearchParams(location.search).get(key) ?? state.model.views?.[key] ?? VIEWS[key];
}
function workItem(item) {
  const work = state.model.pages?.work;
  const finished = (work?.finished ?? []).map((w) => ({ ...w, status: 'Finished', latest: w.result }));
  return [...(state.model.brief?.work ?? []), ...(work?.running ?? []), ...finished].find((w) => w.item === item);
}
function minutesOf(t) {
  const [, h, mm, ap] = /(\d+):(\d+) ([AP]M)/.exec(t ?? '') ?? [0, 0, 0, 'AM'];
  return (Number(h) % 12 + (ap === 'PM' ? 12 : 0)) * 60 + Number(mm);
}
function minutesAgo(t) {
  const [h, m] = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Los_Angeles', hour: 'numeric', minute: 'numeric', hourCycle: 'h23' }).format(new Date()).split(':').map(Number);
  return (h * 60 + m - minutesOf(t) + 1440) % 1440;
}
function lasted(since) {
  if (!/^\d{1,2}:\d{2} [AP]M$/.test(since ?? '')) return '';
  const mins = minutesAgo(since);
  return `for ${mins < 60 ? `${mins} min` : `${Math.floor(mins / 60)} h${mins % 60 ? ` ${mins % 60} min` : ''}`}`;
}
function waited(since) {
  return lasted(since) && `${lasted(since)}, since ${since}`;
}
function slug(status) {
  return `st-${esc(status.toLowerCase().replace(/\s+/g, '-'))}`;
}
function storyHref(item) {
  return `#/story/${encodeURIComponent(item)}`;
}

function nowScreen() {
  const b = state.model.brief;
  const r = route();
  if (!b) return '<article class="ed"><p class="ed-wait">Writing the first summary of the drive. It appears here in about a minute.</p></article>';
  if (r.screen === 'story' && view('story') === 'page') return `<article class="ed">${alarm(b)}${storyPage(r.id)}</article>`;
  const storyHere = r.screen === 'story';
  return `<article class="ed">
    <header class="ed-mast"><p class="ed-date">${datelineFmt.format(new Date())} · ${clock(new Date().toISOString())} Pacific</p>
    <h1 class="ed-head">${esc(b.headline)}</h1>
    <p class="ed-by">Written from the drive's records at ${clock(b.at)}. <button class="linkish" data-toggle="nw-why">Why this sentence</button></p>
    ${state.open.has('nw-why') ? `<ul class="ed-why">${b.because.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}</header>
    ${alarm(b)}
    ${briefSection(1, 'Where we are', whereWeAre(b))}
    ${briefSection(2, 'What is in the way', inTheWay(b))}
    ${briefSection(3, 'What needs you', briefNeeds())}
    ${briefSection(4, storyHere ? 'What is moving: the live story' : 'What is moving', storyHere ? storyColumn(r.id) : moving(b))}
  </article>`;
}
function briefSection(n, title, body) {
  return `<section class="ed-sec"><h2><span>${n}</span>${esc(title)}</h2>${body}</section>`;
}
function alarm(b) {
  return b.band.kind === 'incident' ? `<div class="ed-alarm"><b>Incident</b>${esc(b.band.text)}</div>` : '';
}

function stops(b) {
  return state.model.milestones.map((name, i) => ({ name, ...(b.progress.stops[i] ?? { state: 'not', label: 'Not known', why: '' }) }));
}
function whereWeAre(b) {
  const list = stops(b);
  const cur = list.findIndex((s) => s.state === 'next');
  return `<p class="ed-lede"><b>${list.filter((s) => s.state === 'live').length} of ${list.length}</b> milestones are live. ${esc(b.progress.sentence ?? '')}</p>
    <p class="ed-note">${esc(state.model.milestones_label)}</p>
    <ol class="ed-ms">${list.map((s, i) => `<li class="ms-${s.state}${i === cur ? ' cur' : ''}"><a href="#/plan/m${i}"><span class="n">${i + 1}</span><span class="nm">${esc(s.name)}</span><span class="st">${esc(s.label)}</span>${s.why ? `<span class="why">${esc(s.why)}</span>` : ''}</a></li>`).join('')}</ol>
    ${partLedger()}`;
}
function partLedger() {
  const m = state.model;
  const t = tally(m.drive.prs);
  const rows = m.parts.map((p) => [p, tally(m.drive.prs.filter((e) => e.part === p.key))]).filter(([, pt]) => pt.total);
  return `<table class="ed-ledger"><caption>The code, part by part: ${t.landed} of ${t.total} changes merged</caption><tbody>${rows.map(([p, pt]) => `<tr><td>${esc(p.name)}</td><td class="says">${esc(p.becomes)}<span class="rule"><i style="width:${pct(pt)}%"></i></span></td><td class="fig">${pt.landed} of ${pt.total}</td></tr>`).join('')}</tbody></table>`;
}

function unfinished(path) {
  let edges = path.edges;
  for (const d of path.nodes.filter((n) => n.state === 'done')) {
    const into = edges.filter((e) => e.to === d.id).map((e) => e.from);
    const outof = edges.filter((e) => e.from === d.id).map((e) => e.to);
    edges = [...edges.filter((e) => e.from !== d.id && e.to !== d.id), ...into.flatMap((from) => outof.map((to) => ({ from, to })))];
  }
  return { ...path, nodes: path.nodes.filter((n) => n.state !== 'done'), edges };
}
function depthOf(path) {
  const out = new Map(path.nodes.map((n) => [n.id, []]));
  for (const e of path.edges) if (out.has(e.from) && out.has(e.to) && !out.get(e.from).includes(e.to)) out.get(e.from).push(e.to);
  const memo = new Map();
  const depth = (id, seen = new Set()) => {
    if (memo.has(id)) return memo.get(id);
    if (seen.has(id)) return 1;
    seen.add(id);
    const d = 1 + Math.max(0, ...out.get(id).map((to) => depth(to, seen)));
    memo.set(id, d);
    return d;
  };
  return new Map(path.nodes.map((n) => [n.id, depth(n.id)]));
}
function inTheWay(b) {
  const paths = b.paths.map(unfinished).filter((p) => p.nodes.length);
  if (!paths.length) return `<p class="ed-calm">${esc(b.calm || 'Nothing is stuck.')}</p>`;
  return paths.map((path) => {
    const name = state.model.milestones[path.milestone] ?? 'the next milestone';
    const depth = depthOf(path);
    const nodes = [...path.nodes].sort((x, y) => depth.get(y.id) - depth.get(x.id) || BLOCK_RANK[x.state] - BLOCK_RANK[y.state]);
    return `<div class="ed-path"><h3>Before <a href="#/plan/m${path.milestone}">${esc(name)}</a></h3><ol class="ed-chain">${nodes.map(blockerItem).join('')}<li class="goal"><p>Then ${esc(name)}</p></li></ol></div>`;
  }).join('');
}
function nodeWord(n) {
  return (n.change && latestWord(n.change)) || (n.item && workWord(n.item));
}
function blockerItem(n) {
  const said = nodeWord(n);
  const when = [waited(n.since), said && wordAt(said)].filter(Boolean).join(' · ');
  const title = n.item && workItem(n.item) ? `<a class="t" href="${storyHref(n.item)}">${esc(n.text)}</a>` : `<p class="t">${esc(n.text)}</p>`;
  return `<li class="${esc(n.state)}">${title}<p class="how"><span class="sc">${NODE_WORD[n.state]}</span>${esc(n.how ?? '')}</p>${when ? `<span class="ed-clock">${when}</span>` : ''}</li>`;
}

function briefNeeds() {
  const m = state.model;
  if (!m.needs.length) return '<p class="ed-calm">Nothing waits on you. When a change or a question needs you, it shows here first.</p>';
  const prs = m.needs.filter((item) => item.kind === 'prs').flatMap((item) => item.prs.map((pr) => ({ ...item, prs: [pr] })));
  const asks = m.needs.filter((item) => item.kind !== 'prs');
  const all = [...prs, ...asks];
  const shown = all.slice(0, 4);
  const count = [prs.length ? `${plural(prs.length, 'change')} ${prs.length === 1 ? 'waits' : 'wait'} on your approval` : '', asks.length ? `${plural(asks.length, 'question')} ${asks.length === 1 ? 'waits' : 'wait'} on your answer` : ''].filter(Boolean).join(', and ');
  return `<p class="ed-lede">${esc(count)}.</p><ul class="ed-asks nw-needs">${shown.map(askLine).join('')}</ul>${all.length > shown.length ? `<a class="ed-more" href="#/review">${all.length - shown.length} more on Review, one at a time ›</a>` : ''}`;
}
function askLine(item) {
  const line = (what, sub, acts, href) => `<li><span class="q">${href ? `<a href="${href}">${esc(what)}</a>` : esc(what)}${sub ? `<span class="sub">${esc(sub)}</span>` : ''}</span><span class="acts">${acts}</span></li>`;
  if (item.kind === 'prs') {
    const r = item.prs[0];
    const busy = pending.number === r.number && !pending.error;
    return line(prTitle(r.number), item.why, `<button class="btn pri" data-approve="${r.number}" data-head="${esc(r.head)}"${busy || !boardRow(r.number) ? ' disabled' : ''}>${busy ? 'Sending' : 'Approve'}</button>`, `#/review/${r.number}`);
  }
  if (item.kind === 'card') {
    const c = item.card;
    const said = rewritten('cards', c.key);
    const rec = c.options.find((o) => o.recommended);
    const href = `#/review/q/${encodeURIComponent(c.id)}`;
    if (!rec) return line(said?.question ?? c.title, said?.means ?? item.why, `<a class="btn" href="${href}">Answer</a>`, href);
    const busy = sending(c.choice ?? c.id) ? ' disabled' : '';
    return line(said?.question ?? c.title, `Recommended: ${said?.answers?.[c.options.indexOf(rec)] || rec.label.replace(/\s*\(Recommended\)\s*$/i, '')}`, `<button class="btn pri" data-choose="${esc(rec.id)}" data-card="${esc(c.id)}" data-choice="${esc(c.choice)}"${busy}>Pick it</button>${c.choice ? sentNote(c.choice) : ''}`, href);
  }
  return line(item.ask.what, item.why, item.ask.link ? `<a class="btn" href="${esc(item.ask.link)}" target="_blank" rel="noopener">${esc(item.ask.link_text ?? 'Open')}</a>` : '', '#/review');
}

function moving(b) {
  const rows = b.work.slice(0, view('work') === 'rows5' ? 5 : 3);
  if (!rows.length) return '<p class="ed-calm">No work is running right now.</p>';
  const more = b.work.length - rows.length;
  return `<ul class="ed-work">${rows.map(movingRow).join('')}</ul>${more ? `<a class="ed-more" href="#/work">${more} more piece${more === 1 ? '' : 's'} of work ›</a>` : ''}`;
}
function movingRow(w) {
  const said = workWord(w.item);
  const when = [waited(w.since), said && wordAt(said)].filter(Boolean).join(' · ');
  return `<li><h3><a href="${storyHref(w.item)}">${esc(w.title)}</a></h3><p><span class="sc st ${slug(w.status)}">${esc(w.status)}</span>${esc(w.latest)}</p><span class="ed-clock">${when ? `${when} · ` : ''}<a href="${storyHref(w.item)}">Live story ›</a></span></li>`;
}

function follow(item) {
  if (story.item === item && story.es) return;
  story.es?.close();
  Object.assign(story, { item, lines: [], draft: null, status: 'Opening the story', open: false });
  story.es = new EventSource(`/api/story/${encodeURIComponent(item)}`);
  story.es.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.type === 'saved') Object.assign(story, { lines: ev.lines, status: ev.status, draft: ev.draft });
    if (ev.type === 'line') { story.lines.push(ev); story.draft = null; }
    if (ev.type === 'draft') story.draft = ev.text;
    if (ev.type === 'status') story.status = ev.text;
    if (ev.type === 'idle') { story.status = null; story.draft = null; }
    paintStory();
  };
}
function unfollow() {
  story.es?.close();
  story.es = null;
  story.item = null;
}
function paintStory() {
  const host = $('#nw-story');
  if (host) host.innerHTML = storyBody(story.item);
}
function storyColumn(item) {
  return `<div class="nw-story-col"><a class="back" href="#/">‹ All work</a><div id="nw-story">${storyBody(item)}</div></div>`;
}
function storyPage(item) {
  const b = state.model.brief;
  const top = b.paths.map(unfinished).flatMap((p) => p.nodes).sort((x, y) => ({ nobody: 0, you: 1, fixing: 2, waiting: 3 })[x.state] - ({ nobody: 0, you: 1, fixing: 2, waiting: 3 })[y.state])[0];
  return `<div class="nw-storypage"><a class="back" href="#/">‹ Back to now</a><p class="nw-sub">${esc(b.headline)}</p>
    ${top ? `<p class="nw-sub">Biggest blocker: ${esc(top.text)}. ${esc(NODE_WORD[top.state])}: ${esc(top.how)}</p>` : ''}<div id="nw-story">${storyBody(item)}</div></div>`;
}
function storyBody(item) {
  const w = workItem(item);
  if (!w) return '<p class="nw-calm">This work finished or is no longer on the main page.</p>';
  const lines = [...story.lines].sort((x, y) => minutesAgo(x.at) - minutesAgo(y.at));
  const latest = lines[0];
  const shown = story.open ? lines : lines.slice(0, 3);
  const hidden = lines.length - shown.length;
  return `<div class="nw-storyhead"><h3>${esc(w.title)}</h3>${w.goal ? `<p class="goal">${esc(w.goal)}</p>` : ''}<p class="meta"><span class="chip ${slug(w.status)}">${esc(w.status)}</span> <span>${esc(waited(w.since))}</span></p><p class="now"><b>Now</b>${esc(latest ? latest.line : w.latest)}</p>
    ${latest ? `<p class="nw-sub"><i></i>Latest update ${esc(latest.at)} · new lines appear as it works</p>` : ''}</div>
    ${story.status ? `<p class="catching">${/^(Catching|Opening)/.test(story.status) ? '<i></i>' : ''}${esc(story.status)}</p>` : ''}
    ${story.draft ? `<div class="beat draft"><span class="t"></span><span class="ln">${esc(story.draft)}<i class="cursor"></i></span></div>` : ''}
    ${shown.map((l) => `<div class="beat ${esc(l.mark)}"><span class="t">${esc(l.at)}</span><span class="mk">${MARK_WORD[l.mark]}</span><span class="ln">${esc(l.line)}</span></div>`).join('')}
    ${hidden ? `<button class="linkish" data-story-more>${hidden} earlier update${hidden === 1 ? '' : 's'} today</button>` : ''}
    <a class="nw-raw" href="/api/transcript/${encodeURIComponent(item)}" target="_blank" rel="noopener">Open the transcript this story reads ›</a>`;
}

function mountNow() {
  const r = route();
  if (r.screen === 'story' && r.id) follow(decodeURIComponent(r.id));
  else unfollow();
}
document.addEventListener('click', (e) => {
  if (e.target.closest('[data-story-more]')) { story.open = true; paintStory(); }
});
