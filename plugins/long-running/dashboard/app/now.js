const VIEWS = { story: 'column', diagram: 'end-state' };
const WIRE_TONE = { fixing: '#2f7bff', you: '#ffd400', nobody: '#ff4545', waiting: '#8a93a3', done: '#00e38c', work: '#b18cff' };
const NODE_WORD = { fixing: 'Being fixed', you: 'Needs you', nobody: 'No one assigned', waiting: 'Waiting', done: 'Done' };
const MARK_WORD = { progress: 'Progress', finding: 'Found', stuck: 'Stuck', done: 'Done' };
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
  const m = state.model;
  const b = m.brief;
  const r = route();
  if (!b) return `<div class="nw sb-now"><p class="nw-wait">Writing the first summary of the drive. It appears here in about a minute.</p>${diagramSlot()}</div>`;
  if (r.screen === 'story' && view('story') === 'page') return `<div class="nw">${band(b)}${storyPage(r.id)}${diagramSlot()}</div>`;
  const list = stops(b);
  const t = tally(m.drive.prs);
  return `<div class="nw sb-now">
    ${band(b)}
    <header class="sb-hero"><div class="sb-say"><h1 class="nw-head">${esc(b.headline)}</h1>
      <p class="nw-sub">Written from the drive's records at ${clock(b.at)}. <button class="linkish" data-toggle="nw-why">Why this sentence ›</button></p>
      ${state.open.has('nw-why') ? `<ul class="nw-why">${b.because.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
      ${b.progress.sentence ? `<p class="sb-next">${esc(b.progress.sentence)}</p>` : ''}</div>
      <div class="sb-stats"><div class="sb-stat ms"><b>${list.filter((s) => s.state === 'live').length}<small>/${list.length}</small></b><span>milestones live</span></div><div class="sb-stat code"><b>${t.landed}<small>/${t.total}</small></b><span>changes merged</span></div></div></header>
    ${sbMap(b, list)}
    ${diagramSlot()}
  </div>${r.screen === 'story' ? sbDrawer(workItem(decodeURIComponent(r.id ?? ''))?.title ?? 'Live story', `<div class="kick">Live story</div><div id="nw-story">${storyBody(decodeURIComponent(r.id ?? ''))}</div>`, `story-${r.id}`) : ''}`;
}
function sbMap(b, list) {
  const m = state.model;
  const plan = curated('plan');
  const paths = b.paths.map(unfinished);
  const edges = [];
  const attached = new Set();
  const columns = list.map((s, i) => {
    const nodes = paths.filter((p) => p.milestone === i && p.nodes.length).flatMap((p) => {
      const [depth, out] = depthOf(p);
      const key = (id) => `n:${i}:${id}`;
      for (const [from, tos] of out) for (const to of tos) edges.push([key(from), key(to), p.nodes.find((n) => n.id === from).state]);
      for (const n of p.nodes) if (!out.get(n.id).length) edges.push([key(n.id), `ms:${i}`, n.state]);
      return [...p.nodes].sort((x, y) => depth.get(x.id) - depth.get(y.id)).map((n) => ({ n, key: key(n.id) }));
    });
    const feeding = b.work.filter((w) => nodes.some(({ n }) => n.id === w.unblocks));
    for (const w of feeding) {
      attached.add(w.item);
      edges.push([`w:${w.item}`, nodes.find(({ n }) => n.id === w.unblocks).key, 'work']);
    }
    const p = plan && milestoneOf(plan, i);
    const quiet = s.state === 'live'
      ? `<div class="sb-quiet ok"><b>✓ Live</b>${p?.ships ? `<span>${esc(p.ships)}</span>` : ''}</div>`
      : s.state === 'not' ? `<div class="sb-quiet"><b>After ${esc(list[i - 1]?.name ?? 'the start')}</b>${p?.waits ? `<span>${esc(p.waits)}</span>` : ''}</div>`
      : `<div class="sb-quiet"><b>${esc(s.label)}</b>${p?.waits ? `<span>${esc(p.waits)}${forSince(p.since)}</span>` : ''}${p?.unblock ? `<span class="sb-how">${esc(p.unblock)}</span>` : ''}</div>`;
    const body = nodes.length || feeding.length ? `${nodes.map(({ n, key }) => sbNode(n, key)).join('')}${feeding.length ? `<p class="sb-feed">Work feeding it</p>${feeding.map(sbWork).join('')}` : ''}` : quiet;
    return { width: nodes.length || feeding.length ? 2.2 : s.state === 'next' ? 1.5 : 1.15, html: `<section class="sb-col ms-${s.state}${s.state === 'next' ? ' cur' : ''}">
      <a class="sb-ms" href="#/plan/m${i}" data-node="ms:${i}"><span class="sb-i">${i + 1}</span><b>${esc(s.name)}</b><span class="sb-st">${s.state === 'next' ? 'Next · ' : ''}${esc(s.state === 'next' ? 'the drive is here' : s.label)}</span>${s.why ? `<span class="sb-why">${esc(s.why)}</span>` : ''}</a>
      <div class="sb-stack">${body}</div></section>` };
  });
  const loose = b.work.filter((w) => !attached.has(w.item));
  return `<div class="sb-lab">${esc(m.milestones_label)} <span>left to right; lines show what unblocks what</span></div>
    <div class="sb-map" data-edges="${esc(JSON.stringify(edges))}" style="grid-template-columns:${columns.map((c) => `minmax(0, ${c.width}fr)`).join(' ')}"><svg class="sb-wires" aria-hidden="true"></svg>${columns.map((c) => c.html).join('')}</div>
    ${loose.length ? `<section class="sb-lane"><h2 class="nw-h">Also being worked on <span>${loose.length}</span><a href="#/work">All work ›</a></h2><div class="sb-lane-row">${loose.map(sbWork).join('')}</div></section>` : ''}
    ${paths.some((p) => p.nodes.length) ? '' : `<div class="nw-calm"><span class="ok">✓</span>${esc(b.calm || 'Nothing is stuck.')}</div>`}`;
}
function sbNode(n, key) {
  const work = n.item && workItem(n.item);
  const tag = work ? 'a' : 'div';
  const clockLine = [waited(n.since), nodeWord(n) && wordAt(nodeWord(n))].filter(Boolean).join(' · ');
  return `<${tag} class="sb-node sb-${esc(n.state)}" data-node="${esc(key)}"${work ? ` href="${storyHref(n.item)}"` : ''}><span class="sb-tag">${NODE_WORD[n.state]}</span><b>${esc(n.text)}</b>${n.how ? `<span class="sb-how">${esc(n.how)}</span>` : ''}${clockLine ? `<span class="sb-clock">${clockLine}</span>` : ''}</${tag}>`;
}
function sbWork(w) {
  const clockLine = [waited(w.since), wordAt(workWord(w.item))].filter(Boolean).join(' · ');
  return `<a class="sb-work ${slug(w.status)}" href="${storyHref(w.item)}" data-node="w:${esc(w.item)}"><span class="sb-tag">${esc(w.status)}</span><b>${esc(w.title)}</b><span class="sb-how">${esc(w.latest)}</span>${clockLine ? `<span class="sb-clock">${clockLine}</span>` : ''}<span class="sb-open">Live story ›</span></a>`;
}
function sbWires() {
  const map = document.querySelector('.sb-map');
  if (!map) return;
  const svg = map.querySelector('svg.sb-wires');
  const o = map.getBoundingClientRect();
  const at = new Map([...map.querySelectorAll('[data-node]')].map((el) => [el.dataset.node, el.getBoundingClientRect()]));
  svg.setAttribute('viewBox', `0 0 ${o.width} ${o.height}`);
  svg.setAttribute('width', o.width);
  svg.setAttribute('height', o.height);
  const lines = JSON.parse(map.dataset.edges).map(([from, to, tone]) => {
    const a = at.get(from), b = at.get(to);
    if (!a || !b) return '';
    const near = a.top - b.bottom < 40 && Math.abs(a.left - b.left) < 4;
    const d = near
      ? `M${a.left + a.width / 2 - o.left} ${a.top - o.top} L${b.left + b.width / 2 - o.left} ${b.bottom - o.top + 1}`
      : `M${a.left - o.left} ${a.top + Math.min(26, a.height / 2) - o.top} C${a.left - o.left - 30} ${a.top - o.top} ${b.left - o.left - 30} ${b.bottom - o.top} ${b.left - o.left} ${b.bottom - 14 - o.top}`;
    return `<path stroke="${WIRE_TONE[tone]}" data-a="${esc(from)}" data-b="${esc(to)}" d="${d}" marker-end="url(#sb-head-${esc(tone)})"/>`;
  }).join('');
  svg.innerHTML = `<defs>${Object.entries(WIRE_TONE).map(([tone, color]) => `<marker id="sb-head-${tone}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="${color}"/></marker>`).join('')}</defs>${lines}`;
  sbLight();
}
function sbLight() {
  const map = document.querySelector('.sb-map');
  if (!map) return;
  map.classList.toggle('hl', Boolean(state.sbLit));
  for (const path of map.querySelectorAll('svg.sb-wires path[data-a]')) path.classList.toggle('lit', path.dataset.a === state.sbLit || path.dataset.b === state.sbLit);
}
document.addEventListener('mouseover', (e) => {
  const key = e.target.closest?.('.sb-map [data-node]')?.dataset.node ?? null;
  if (key === state.sbLit) return;
  state.sbLit = key;
  sbLight();
});

function band(b) {
  const m = state.model;
  if (b.band.kind === 'incident') return `<div class="nw-band incident"><span class="tag">Incident</span><span class="what">${esc(b.band.text)}</span></div>`;
  if (!m.needs.length) return '';
  const prs = m.needs.filter((item) => item.kind === 'prs').flatMap((item) => item.prs.map((pr) => ({ pr, item })));
  const approvals = prs.length === 1 ? needLine({ ...prs[0].item, prs: [prs[0].pr] }) : prs.length ? approvalLine(prs) : '';
  const asks = m.needs.filter((item) => item.kind !== 'prs');
  const questions = asks.length > 1 ? `<div class="nl"><span class="what">${asks.length} questions wait on your answer</span><span class="acts"><a class="nbtn pri" href="#/review/q">Answer them</a></span></div>` : asks.map(needLine).join('');
  const rows = approvals + questions;
  return `<div class="nw-band needs nw-needs"><div class="head"><span class="tag">Needs you</span></div><div class="rows">${rows}</div></div>`;
}
const FEATURE = { 'New tree': 'New app', 'Old tree': "Today's app", Shared: 'Shared code and CI' };
function approvalLine(prs) {
  const features = new Map();
  for (const { pr, item } of prs) {
    const feature = FEATURE[state.drive.get(pr.number)?.tree] ?? (/new (tree|app)/i.test(item.group) ? FEATURE['New tree'] : /today's app|old (tree|world)/i.test(item.group) ? FEATURE['Old tree'] : item.group);
    features.set(feature, (features.get(feature) ?? 0) + 1);
  }
  const split = [...features].map(([feature, count]) => `${feature} ${count}`).join(' · ');
  return `<div class="nl"><span class="what">${prs.length} changes wait on your approval<span class="sub">${esc(split)}</span></span><span class="acts"><a class="nbtn pri" href="#/review">Review them</a></span></div>`;
}
function needLine(item) {
  const line = (what, why, acts, sub) => `<div class="nl"><span class="what"${why ? ` title="${esc(why)}"` : ''}>${esc(what)}${sub ? `<span class="sub">${esc(sub)}</span>` : ''}</span><span class="acts">${acts}</span></div>`;
  if (item.kind === 'prs' && item.prs.length === 1) {
    const r = item.prs[0];
    const busy = pending.number === r.number && !pending.error;
    return line(prTitle(r.number), item.why, `<button class="nbtn pri" data-approve="${r.number}" data-head="${esc(r.head)}"${busy || !boardRow(r.number) ? ' disabled' : ''}>${busy ? 'Sending' : 'Approve'}</button><a class="nbtn" href="#/review/${r.number}">Details</a>`, item.why);
  }
  if (item.kind === 'card') {
    const c = item.card;
    const rec = c.options.find((o) => o.recommended);
    const busy = sending(c.choice ?? c.id) ? ' disabled' : '';
    const said = rewritten('cards', c.key);
    const question = said?.question ?? c.title;
    if (!rec) return line(question, item.why, '<a class="nbtn" href="#/review">Choose an answer</a>');
    return line(question, item.why, `<button class="nbtn pri" data-choose="${esc(rec.id)}" data-card="${esc(c.id)}" data-choice="${esc(c.choice)}"${busy}>Pick it</button><a class="nbtn" href="#/review">Other answers</a>`, `Recommended: ${said?.answers?.[c.options.indexOf(rec)] || rec.label.replace(/\s*\(Recommended\)\s*$/i, '')}`);
  }
  return line(item.ask.what, item.why, item.ask.link ? `<a class="nbtn" href="${esc(item.ask.link)}" target="_blank" rel="noopener">${esc(item.ask.link_text ?? 'Open')}</a>` : '<a class="nbtn" href="#/review">Details</a>');
}

function stops(b) {
  return state.model.milestones.map((name, i) => ({ name, ...(b.progress.stops[i] ?? { state: 'not', label: 'Not known', why: '' }) }));
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
  const reaches = (from, to, seen = new Set()) => out.get(from).some((x) => x === to || (!seen.has(x) && seen.add(x) && reaches(x, to, seen)));
  for (const [from, tos] of out) out.set(from, tos.filter((to) => !tos.some((via) => via !== to && reaches(via, to))));
  const memo = new Map();
  const depth = (id, seen = new Set()) => {
    if (memo.has(id)) return memo.get(id);
    if (seen.has(id)) return 1;
    seen.add(id);
    const d = 1 + Math.max(0, ...out.get(id).map((to) => depth(to, seen)));
    memo.set(id, d);
    return d;
  };
  return [new Map(path.nodes.map((n) => [n.id, depth(n.id)])), out];
}
function nodeWord(n) {
  return (n.change && latestWord(n.change)) || (n.item && workWord(n.item));
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

const DIAGRAMS = {
  'end-state': () => `<h2 class="nw-h">How much of the end state has landed</h2><p class="nw-cap">Each light is one change the drive tracks, placed where it runs: on your Mac, in the cloud, or in the tools that ship it. ${(() => { const t = tally(state.model.drive.prs); return `${t.landed} of ${t.total} have merged, so ${pct(t)}% of the end state is in.`; })()}</p><div class="ck ov">${destination()}<div id="tip" class="tip" hidden></div></div>`,
  parts: () => `<h2 class="nw-h">How much of each part has landed</h2><div class="nw-panel nw-parts">${state.model.parts.map((p) => {
    const t = tally(state.model.drive.prs.filter((e) => e.part === p.key));
    return `<div class="pt"><b>${esc(p.name)}</b><span class="bar"><i style="width:${pct(t)}%"></i></span><span class="pc">${t.landed} of ${t.total}</span><span class="sub">${esc(p.becomes)}</span></div>`;
  }).join('')}</div>`,
  none: () => '',
};
function diagramSlot() {
  const body = DIAGRAMS[view('diagram')]();
  return body ? `<section class="nw-diagram">${body}</section>` : '';
}

function mountNow() {
  const r = route();
  if (r.screen === 'story' && r.id) follow(decodeURIComponent(r.id));
  else unfollow();
  sbWires();
  mountOverview();
}
document.addEventListener('click', (e) => {
  if (e.target.closest('[data-story-more]')) { story.open = true; paintStory(); }
});
window.addEventListener('resize', () => sbWires());
