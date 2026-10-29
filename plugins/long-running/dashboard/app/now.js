const VIEWS = { story: 'column', work: 'rows3', diagram: 'end-state' };
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
const QUIET_MS = 5 * 60000;
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
  if (!b) return `<div class="nw"><p class="nw-wait">Writing the first summary of the drive. It appears here in about a minute.</p>${diagramSlot()}</div>`;
  if (r.screen === 'story' && view('story') === 'page') return `<div class="nw">${band(b)}${storyPage(r.id)}${diagramSlot()}</div>`;
  const list = stops(b);
  const t = tally(m.drive.prs);
  const cur = list.findIndex((s) => s.state === 'next') >= 0 ? list.findIndex((s) => s.state === 'next') : list.findIndex((s) => ['broken', 'paused'].includes(s.state));
  const paths = b.paths.map(unfinished).filter((p) => p.nodes.length);
  const here = paths.filter((p) => p.milestone === cur);
  const feeding = b.work.filter((w) => here.some((p) => p.nodes.some((n) => n.id === w.unblocks)));
  const later = paths.filter((p) => p.milestone !== cur);
  const rest = `${later.length ? `<section><h2 class="nw-h">Biggest blockers on other milestones</h2><div class="fd-later">${later.map(laterPath).join('')}</div></section>` : ''}
    <section><h2 class="nw-h">${feeding.length ? 'Also being worked on' : 'Being worked on now'}</h2>${workList(b.work.filter((w) => !feeding.includes(w)))}</section>`;
  return `<div class="nw fd">
    ${band(b)}
    <header class="sb-hero"><div><h1 class="nw-head">${esc(b.headline)}</h1>
      <p class="nw-sub">Written from the drive's records at ${clock(b.at)}. <button class="linkish" data-toggle="nw-why">Why this sentence ›</button></p>
      ${state.open.has('nw-why') ? `<ul class="nw-why">${b.because.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
      ${b.progress.sentence ? `<p class="sb-next">${esc(b.progress.sentence)}</p>` : ''}</div>
      <div class="sb-stats"><div class="sb-stat ms"><b>${list.filter((s) => s.state === 'live').length}<small>/${list.length}</small></b><span>milestones live</span></div><div class="sb-stat code"><b>${t.landed}<small>/${t.total}</small></b><span>changes merged</span></div></div></header>
    <section><h2 class="nw-h">Overall progress <span>${esc(m.milestones_label)}</span></h2>${milestoneMap(list, cur, here, feeding)}</section>
    ${paths.length ? '' : `<div class="nw-calm"><span class="ok">✓</span>${esc(b.calm || 'Nothing is stuck.')}</div>`}
    ${r.screen === 'story' ? `<div class="nw-cols"><div>${rest}</div><section><h2 class="nw-h">Live story</h2>${storyColumn(r.id)}</section></div>` : rest}
    ${diagramSlot()}
  </div>`;
}
function milestoneMap(list, cur, here, feeding) {
  const plan = curated('plan');
  return `<div class="fd-map" style="grid-template-columns:${list.map((s, i) => `minmax(0, ${i === cur ? 2.6 : 1}fr)`).join(' ')}">${list.map((s, i) => {
    const p = plan && milestoneOf(plan, i);
    const quiet = s.state === 'live'
      ? `<div class="sb-quiet ok"><b>✓ Live</b>${p?.ships ? `<span>${esc(p.ships)}</span>` : ''}</div>`
      : i > cur || cur < 0 ? `<div class="sb-quiet"><b>After ${esc(list[i - 1]?.name ?? 'the start')}</b>${p?.waits ? `<span>${esc(p.waits)}</span>` : ''}</div>`
      : `<div class="sb-quiet"><b>${esc(s.label)}</b>${p?.waits ? `<span>${esc(p.waits)}${forSince(p.since)}</span>` : ''}</div>`;
    const inner = i === cur && (here.length || feeding.length)
      ? `${here.map((path) => `<div class="sb-chain">${chain(path).map(sbNode).join('')}</div>`).join('')}${feeding.length ? `<p class="sb-feed">Work feeding it</p>${feeding.map((w) => sbWork(w, here.flatMap((path) => path.nodes).find((n) => n.id === w.unblocks))).join('')}` : ''}`
      : quiet;
    const tone = i === cur ? 'cur' : s.state === 'live' ? 'live' : s.state === 'not' ? 'not' : 'progress';
    return `<section class="sb-col ms-${tone}">
      <a class="sb-ms" href="#/plan/m${i}"><span class="sb-i">${i + 1}</span><b>${esc(s.name)}</b><span class="sb-st">${i === cur ? `The drive is here${s.state === 'next' ? '' : ` · ${esc(s.label)}`}` : esc(s.label)}</span>${s.why ? `<span class="sb-why">${esc(s.why)}</span>` : ''}</a>
      <div class="sb-stack">${inner}</div></section>`;
  }).join('')}</div>`;
}
function chain(path) {
  const [depth] = depthOf(path);
  return [...path.nodes].sort((x, y) => depth.get(x.id) - depth.get(y.id));
}
function sbNode(n) {
  const work = n.item && workItem(n.item);
  const tag = work ? 'a' : 'div';
  const clockLine = [waited(n.since), nodeWord(n) && wordAt(nodeWord(n))].filter(Boolean).join(' · ');
  return `<${tag} class="sb-node sb-${esc(n.state)}"${work ? ` href="${storyHref(n.item)}"` : ''}><span class="sb-tag">${NODE_WORD[n.state]}</span><b>${esc(n.text)}</b>${n.how ? `<span class="sb-how">${esc(n.how)}</span>` : ''}${clockLine ? `<span class="sb-clock">${clockLine}</span>` : ''}</${tag}>`;
}
function sbWork(w, feeds) {
  const clockLine = [waited(w.since), workWord(w.item) && wordAt(workWord(w.item))].filter(Boolean).join(' · ');
  return `<a class="sb-work ${slug(w.status)}" href="${storyHref(w.item)}"><span class="sb-tag">${esc(w.status)}</span><b>${esc(w.title)}</b><span class="sb-how">${esc(w.latest)}</span>${quietLine(w)}${feeds ? `<span class="sb-feeds">Feeds: ${esc(feeds.text)}</span>` : ''}${clockLine ? `<span class="sb-clock">${clockLine}</span>` : ''}<span class="sb-open">Live story ›</span></a>`;
}

function band(b) {
  const m = state.model;
  if (b.band.kind === 'incident') return `<div class="nw-band incident"><span class="tag">Incident</span><span class="what">${esc(b.band.text)}</span></div>`;
  if (!m.needs.length) return '';
  const prs = m.needs.filter((item) => item.kind === 'prs').flatMap((item) => item.prs.map((pr) => ({ pr, item })));
  const asks = m.needs.filter((item) => item.kind !== 'prs');
  const row = prs.length + asks.length > 1 ? summaryLine(prs, asks) : needLine(prs.length ? { ...prs[0].item, prs: [prs[0].pr] } : asks[0]);
  return `<div class="nw-band needs nw-needs"><span class="tag">Needs you</span>${row}</div>`;
}
const FEATURE = { 'New tree': 'New app', 'Old tree': "Today's app", Shared: 'Shared code and CI' };
function summaryLine(prs, asks) {
  const features = new Map();
  for (const { pr, item } of prs) {
    const feature = FEATURE[state.drive.get(pr.number)?.tree] ?? (/new (tree|app)/i.test(item.group) ? FEATURE['New tree'] : /today's app|old (tree|world)/i.test(item.group) ? FEATURE['Old tree'] : item.group);
    features.set(feature, (features.get(feature) ?? 0) + 1);
  }
  const split = [...features].map(([feature, count]) => `${feature} ${count}`).join(' · ');
  const changes = prs.length ? `${prs.length} change${prs.length === 1 ? '' : 's'} wait${prs.length === 1 ? 's' : ''} on your approval` : '';
  const questions = asks.length ? `${asks.length} question${asks.length === 1 ? '' : 's'}${prs.length ? '' : ` wait${asks.length === 1 ? 's' : ''}`} on your answer` : '';
  const act = prs.length ? '<a class="nbtn pri" href="#/review">Review them</a>' : '<a class="nbtn pri" href="#/review/q">Answer them</a>';
  return `<div class="nl"><span class="what">${[changes, questions].filter(Boolean).join('; ')}${split ? `<span class="sub">${esc(split)}</span>` : ''}</span><span class="acts">${act}</span></div>`;
}
function needLine(item) {
  const line = (what, href, why, act, sub) => `<div class="nl"><a class="what" href="${href}"${why ? ` title="${esc(why)}"` : ''}>${esc(what)}${sub ? `<span class="sub">${esc(sub)}</span>` : ''}</a><span class="acts">${act}</span></div>`;
  if (item.kind === 'prs') {
    const r = item.prs[0];
    const busy = pending.number === r.number && !pending.error;
    return line(prTitle(r.number), `#/review/${r.number}`, item.why, `<button class="nbtn pri" data-approve="${r.number}" data-head="${esc(r.head)}"${busy || !boardRow(r.number) ? ' disabled' : ''}>${busy ? 'Sending' : 'Approve'}</button>`, item.why);
  }
  if (item.kind === 'card') {
    const c = item.card;
    const rec = c.options.find((o) => o.recommended);
    const busy = sending(c.choice ?? c.id) ? ' disabled' : '';
    const said = rewritten('cards', c.key);
    const question = said?.question ?? c.title;
    if (!rec) return line(question, '#/review/q', item.why, '<a class="nbtn pri" href="#/review/q">Choose an answer</a>');
    return line(question, '#/review/q', item.why, `<button class="nbtn pri" data-choose="${esc(rec.id)}" data-card="${esc(c.id)}" data-choice="${esc(c.choice)}"${busy}>Pick it</button>`, `Recommended: ${said?.answers?.[c.options.indexOf(rec)] || rec.label.replace(/\s*\(Recommended\)\s*$/i, '')}`);
  }
  return line(item.ask.what, '#/review', item.why, item.ask.link ? `<a class="nbtn pri" href="${esc(item.ask.link)}" target="_blank" rel="noopener">${esc(item.ask.link_text ?? 'Open')}</a>` : '<a class="nbtn pri" href="#/review">Details</a>');
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
function laterPath(path) {
  const name = state.model.milestones[path.milestone] ?? 'a later milestone';
  return `<div class="fd-path"><a class="sb-lab" href="#/plan/m${path.milestone}">To ${esc(name)} ›</a><div class="sb-chain">${chain(path).map(sbNode).join('')}</div></div>`;
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
function workList(work) {
  const rows = view('work') === 'boxes' ? work : work.slice(0, view('work') === 'rows5' ? 5 : 3);
  const more = work.length - rows.length;
  if (!rows.length) return '<div class="nw-calm">No other work is running right now.</div>';
  return `<div class="fd-work">${rows.map((w) => sbWork(w)).join('')}</div>${more ? `<a class="fd-more" href="#/work">${more} more piece${more === 1 ? '' : 's'} of work ›</a>` : ''}`;
}

function quietLine(w) {
  const word = workWord(w.item);
  if (word && Date.now() - new Date(word.at) < QUIET_MS) return '';
  const n = (w.changes ?? []).find((c) => prState(c) !== 'landed');
  const waits = n && prTruth(n).blocks;
  return waits ? `<span class="quiet">${word ? `Quiet since ${clock(word.at)}` : 'Quiet'}; waits on ${esc(waits.replace(/^./, (c) => c.toLowerCase()).replace(/\.$/, ''))}.</span>` : '';
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
  mountOverview();
}
document.addEventListener('click', (e) => {
  if (e.target.closest('[data-story-more]')) { story.open = true; paintStory(); }
});
