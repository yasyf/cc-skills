const VIEWS = { progress: 'journey', blockers: 'chart', story: 'column', work: 'rows3', diagram: 'end-state' };
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
function lasted(since) {
  if (!/^\d{1,2}:\d{2} [AP]M$/.test(since ?? '')) return '';
  const [h, m] = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Los_Angeles', hour: 'numeric', minute: 'numeric', hourCycle: 'h23' }).format(new Date()).split(':').map(Number);
  const mins = (h * 60 + m - minutesOf(since) + 1440) % 1440;
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
  if (!b) return `<div class="nw"><p class="nw-wait">Writing the first summary of the drive. It appears here in about a minute.</p>${diagramSlot()}</div>`;
  const page = r.screen === 'story' && view('story') === 'page';
  return `<div class="nw">
    ${band(b)}
    ${page ? storyPage(r.id) : `
    <h1 class="nw-head">${esc(b.headline)}</h1>
    <p class="nw-sub">Written from the drive's records at ${clock(b.at)}. <button class="linkish" data-toggle="nw-why">Why this sentence ›</button></p>
    ${state.open.has('nw-why') ? `<ul class="nw-why">${b.because.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
    <section><h2 class="nw-h">Overall progress</h2>${view('progress') === 'compact' ? compact(b) : journey(b)}</section>
    <div class="nw-cols">
      <section><h2 class="nw-h">Biggest blockers, and how each is being unblocked</h2>${blockers(b)}</section>
      <section><h2 class="nw-h">${r.screen === 'story' ? 'Live story' : 'Being worked on now'}</h2>${r.screen === 'story' ? storyColumn(r.id) : workList(b)}</section>
    </div>`}
    ${diagramSlot()}
  </div>`;
}

function band(b) {
  const m = state.model;
  if (b.band.kind === 'incident') return `<div class="nw-band incident"><span class="tag">Incident</span><span class="what">${esc(b.band.text)}</span></div>`;
  if (!m.needs.length) return '';
  const rows = m.needs.slice(0, 2).map(needLine).join('');
  const more = m.needs.length - 2;
  return `<div class="nw-band needs nw-needs"><div class="head"><span class="tag">Needs you</span><span class="count">${m.needs.length} decision${m.needs.length === 1 ? '' : 's'}</span>${more > 0 ? `<a class="more" href="#/review">${more} more on Review ›</a>` : ''}</div><div class="rows">${rows}</div></div>`;
}
function needLine(item) {
  const line = (what, why, acts, sub) => `<div class="nl"><span class="what"${why ? ` title="${esc(why)}"` : ''}>${esc(what)}${sub ? `<span class="sub">${esc(sub)}</span>` : ''}</span><span class="acts">${acts}</span></div>`;
  if (item.kind === 'prs' && item.prs.length === 1) {
    const r = item.prs[0];
    const busy = pending.number === r.number && !pending.error;
    return line(plainTitle(r.number).replace(/^#\d+\s*/, ''), item.why, `<button class="nbtn pri" data-approve="${r.number}" data-head="${esc(r.head)}"${busy || !boardRow(r.number) ? ' disabled' : ''}>${busy ? 'Sending' : 'Approve'}</button><a class="nbtn" href="#/review/${r.number}">Details</a>`, item.why);
  }
  if (item.kind === 'prs') return line(`${item.prs.length} changes wait on your approval`, item.group, '<a class="nbtn pri" href="#/review">Review them</a>');
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
function journey(b) {
  const list = stops(b);
  const lastLive = list.map((s) => s.state).lastIndexOf('live');
  const cur = list.findIndex((s) => s.state === 'next');
  const fill = lastLive > 0 ? (lastLive / list.length) * 100 : 0;
  return `<div class="nw-panel">${progressLabel()}
    <div class="nw-journey" style="--n:${list.length}"><i class="fill" style="width:${fill}%"></i>${list.map((s, i) => `<a class="ms ms-${s.state}${i === cur ? ' cur' : ''}" href="#/plan/m${i}"><span class="dot"></span><span class="nm">${esc(s.name)}</span><span class="st">${esc(s.label)}</span>${s.why ? `<span class="why">${esc(s.why)}</span>` : ''}</a>`).join('')}</div>
    ${b.progress.sentence ? `<p class="nw-sent">${esc(b.progress.sentence)}</p>` : ''}
    </div>`;
}
function progressLabel() {
  const t = tally(state.model.drive.prs);
  return `<p class="nw-lab"><span>${esc(state.model.milestones_label)}</span><span class="nw-code">Code: ${t.landed} of ${t.total} changes merged</span></p>`;
}
function compact(b) {
  const list = stops(b);
  const live = list.filter((s) => s.state === 'live');
  const next = list.find((s) => s.state === 'next');
  const odd = list.filter((s) => ['paused', 'broken'].includes(s.state));
  return `<div class="nw-panel">${progressLabel()}<div class="nw-seg">${list.map((s, i) => `<a class="ms-${s.state}" href="#/plan/m${i}" title="${esc(`${s.name}: ${s.label}`)}"></a>`).join('')}</div>
    <p class="nw-sent"><b>${live.length} of ${list.length} milestones live</b>${live.length ? `: ${esc(live.map((s) => s.name).join(', '))}` : ''}.</p>
    ${next ? `<p class="nw-line">Next: <b>${esc(next.name)}</b>${next.why ? `, ${esc(next.why)}` : ''}.</p>` : ''}
    ${odd.map((s) => `<p class="nw-line ms-${s.state}">${esc(s.name)}: ${esc(s.label)}${s.why ? `, ${esc(s.why)}` : ''}.</p>`).join('')}
    <p class="nw-code"><a href="?progress=journey${location.hash}">All ${list.length} milestones ›</a></p></div>`;
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
function blockers(b) {
  const paths = b.paths.map(unfinished).filter((p) => p.nodes.length);
  if (!paths.length) return `<div class="nw-calm"><span class="ok">✓</span>${esc(b.calm || 'Nothing is stuck.')}</div>`;
  return view('blockers') === 'cards' ? cards(paths) : `<div class="nw-chart">${paths.map(chart).join('')}</div>`;
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
function chart(path, index) {
  const [depth, out] = depthOf(path);
  const layers = [...new Set(depth.values())].sort((a, b) => b - a);
  const name = state.model.milestones[path.milestone] ?? 'the next milestone';
  const key = `p${index}`;
  const edges = [...[...out].flatMap(([from, tos]) => tos.map((to) => [from, to])), ...path.nodes.filter((n) => !out.get(n.id).length).map((n) => [n.id, '@goal'])];
  return `<div class="nw-path" data-path="${key}" data-edges="${esc(JSON.stringify(edges))}" style="grid-template-columns:repeat(${layers.length}, minmax(0, 1fr)) 92px"><svg class="arrows" aria-hidden="true"></svg>
    ${layers.map((d) => `<div class="layer">${path.nodes.filter((n) => depth.get(n.id) === d).map((n) => node(n, key)).join('')}</div>`).join('')}
    <div class="layer goal-col"><div class="nd goal" data-node="${key}:@goal">${esc(name)}</div></div></div>`;
}
function node(n, key) {
  const work = n.item && workItem(n.item);
  const tag = work ? `a href="${storyHref(n.item)}"` : 'div';
  return `<${tag} class="nd ${n.state}" data-node="${key}:${esc(n.id)}"><span class="w">${NODE_WORD[n.state]}${waited(n.since) ? ` <i>${waited(n.since)}</i>` : ''}</span>${esc(n.text)}
    ${n.how ? `<span class="how">${esc(n.how)}</span>` : ''}</${work ? 'a' : 'div'}>`;
}
function cards(paths) {
  const rank = { nobody: 0, you: 1, fixing: 2, waiting: 3 };
  const list = paths.flatMap((p) => p.nodes.filter((n) => n.state !== 'done').map((n) => ({ ...n, holds: state.model.milestones[p.milestone] }))).sort((a, b) => rank[a.state] - rank[b.state]);
  return `<div class="nw-cards">${list.map((n) => {
    const work = n.item && workItem(n.item);
    return `<div class="blk ${n.state}"><div class="stuck"><div class="t">${esc(n.text)}</div><div class="meta">Holds up <b>${esc(n.holds ?? 'the next milestone')}</b>${waited(n.since) ? ` · ${waited(n.since)}` : ''}</div></div>
      <div class="unb"><span class="chip ${n.state}">${NODE_WORD[n.state]}</span>${esc(n.how)}${work ? ` <a href="${storyHref(n.item)}">Live story ›</a>` : ''}</div></div>`;
  }).join('')}</div>`;
}
function drawArrows() {
  for (const box of document.querySelectorAll('.nw-path')) {
    const svg = box.querySelector('svg.arrows');
    const origin = box.getBoundingClientRect();
    svg.setAttribute('viewBox', `0 0 ${origin.width} ${origin.height}`);
    svg.setAttribute('width', origin.width);
    svg.setAttribute('height', origin.height);
    const at = (id) => box.querySelector(`[data-node="${CSS.escape(`${box.dataset.path}:${id}`)}"]`)?.getBoundingClientRect();
    const lines = JSON.parse(box.dataset.edges).map(([from, to]) => {
      const a = at(from), b = at(to);
      if (!a || !b) return '';
      const x2 = b.left - origin.left - 2, y2 = b.top + b.height / 2 - origin.top;
      const blocked = [...box.querySelectorAll('.nd')].some((n) => { const r = n.getBoundingClientRect(); return r.left > a.right - 1 && r.right < b.left + 1 && r.top < a.bottom && r.bottom > a.top; });
      if (blocked) {
        const x1 = a.left + a.width / 2 - origin.left, y1 = a.bottom - origin.top;
        const low = Math.max(y1, b.bottom - origin.top) + 9;
        return `<path d="M${x1} ${y1} L${x1} ${low - 6} Q${x1} ${low} ${x1 + 6} ${low} L${x2 - 14} ${low} Q${x2 - 8} ${low} ${x2 - 8} ${low - 6} L${x2 - 8} ${y2 + 6} Q${x2 - 8} ${y2} ${x2} ${y2}" marker-end="url(#nw-head)"/>`;
      }
      const x1 = a.right - origin.left, y1 = a.top + a.height / 2 - origin.top;
      const mid = (x1 + x2) / 2;
      return `<path d="M${x1} ${y1} C${mid} ${y1} ${mid} ${y2} ${x2} ${y2}" marker-end="url(#nw-head)"/>`;
    }).join('');
    svg.innerHTML = `<defs><marker id="nw-head" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z"/></marker></defs>${lines}`;
  }
}

function workList(b) {
  if (view('work') === 'boxes') return `<div class="nw-boxes">${b.work.map((w) => `<a class="bx" href="${storyHref(w.item)}"><b>${esc(w.title)}</b><span class="chip ${slug(w.status)}">${esc(w.status)}</span>${waited(w.since) ? `<span class="for">${waited(w.since)}</span>` : ''}<span>${esc(w.latest)}</span></a>`).join('')}</div>`;
  const rows = b.work.slice(0, view('work') === 'rows5' ? 5 : 3);
  const more = b.work.length - rows.length;
  if (!rows.length) return '<div class="nw-calm">No work is running right now.</div>';
  return `<div class="nw-work">${rows.map(workRow).join('')}${more ? `<a class="more" href="#/work">${more} more piece${more === 1 ? '' : 's'} of work ›</a>` : ''}</div>`;
}
function workRow(w) {
  return `<a class="wr ${slug(w.status)}" href="${storyHref(w.item)}"><span class="pulse"></span>
    <span class="body"><b>${esc(w.title)}</b><span class="line"><span class="chip ${slug(w.status)}">${esc(w.status)}</span>${esc(w.latest)}</span></span>
    <span class="side">${lasted(w.since) ? `<span>${lasted(w.since)}</span><span>since ${esc(w.since)}</span>` : ''}<span class="open">Live story ›</span></span></a>`;
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
  const lines = [...story.lines].sort((x, y) => minutesOf(y.at) - minutesOf(x.at));
  const latest = lines[0];
  const shown = story.open ? lines : lines.slice(0, 3);
  const hidden = lines.length - shown.length;
  return `<div class="nw-storyhead"><h3>${esc(w.title)}</h3><p class="meta"><span class="chip ${slug(w.status)}">${esc(w.status)}</span>${esc(waited(w.since))}</p><p class="now"><b>Now</b>${esc(latest ? latest.line : w.latest)}</p>
    ${latest ? `<p class="nw-sub"><i></i>Latest update ${esc(latest.at)} · new lines appear as it works</p>` : ''}</div>
    ${story.status ? `<p class="catching">${/^(Catching|Opening)/.test(story.status) ? '<i></i>' : ''}${esc(story.status)}</p>` : ''}
    ${story.draft ? `<div class="beat draft"><span class="t"></span><span class="ln">${esc(story.draft)}<i class="cursor"></i></span></div>` : ''}
    ${shown.map((l) => `<div class="beat ${esc(l.mark)}"><span class="t">${esc(l.at)}</span><span class="mk">${MARK_WORD[l.mark]}</span><span class="ln">${esc(l.line)}</span></div>`).join('')}
    ${hidden ? `<button class="linkish" data-story-more>${hidden} earlier update${hidden === 1 ? '' : 's'} today</button>` : ''}`;
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
  drawArrows();
  mountOverview();
}
document.addEventListener('click', (e) => {
  if (e.target.closest('[data-story-more]')) { story.open = true; paintStory(); }
});
window.addEventListener('resize', () => drawArrows());
