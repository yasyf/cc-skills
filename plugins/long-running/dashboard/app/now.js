const VIEWS = { progress: 'journey', story: 'column', work: 'rows3', diagram: 'end-state' };
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
  const r = pageRoute();
  if (!b) return `<div class="nw"><p class="nw-wait">Writing the first summary of the drive. It appears here in about a minute.</p>${diagramSlot()}</div>`;
  const page = r.screen === 'story' && view('story') === 'page';
  return `<div class="nw">
    ${band(b)}
    ${page ? storyPage(r.id) : `
    <h1 class="nw-head">${esc(b.headline)}</h1>
    <p class="nw-sub">Written from the drive's records at ${clock(b.at)}. <button class="linkish" data-toggle="nw-why">Why this sentence ›</button></p>
    ${state.open.has('nw-why') ? `<ul class="nw-why">${b.because.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
    <section><h2 class="nw-h">Overall progress</h2>${view('progress') === 'compact' ? compact(b) : journey(b)}</section>`}
    ${diagramSlot()}
  </div>`;
}

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
function current(list) {
  return list.findIndex((s) => !['live', 'not'].includes(s.state));
}
function journey(b) {
  const list = stops(b);
  const lastLive = list.map((s) => s.state).lastIndexOf('live');
  const cur = current(list);
  const fill = lastLive > 0 ? (lastLive / list.length) * 100 : 0;
  return `<div class="nw-panel">${progressLabel()}
    <div class="nw-journey" style="--n:${list.length}"><i class="fill" style="width:${fill}%"></i>${list.map((s, i) => `<a class="ms ms-${s.state}${i === cur ? ' cur' : ''}" href="#/plan/m${i}"><span class="dot"></span><span class="nm">${esc(s.name)}</span><span class="st">${esc(s.label)}</span>${s.why ? `<span class="why">${esc(s.why)}</span>` : ''}</a>`).join('')}</div>
    ${milestoneBody(b, list, cur, `<p class="nw-sent">${list.filter((s) => s.state === 'live').length} of ${list.length} milestones are live. ${esc(b.progress.sentence ?? '')}</p><p class="nw-code">${codeLine()}</p>`)}
    </div>`;
}
function milestoneBody(b, list, cur, lead) {
  const r = pageRoute();
  const paths = b.paths.map(unfinished).filter((p) => p.nodes.length);
  const here = paths.filter((p) => p.milestone === cur);
  const nodes = here.flatMap((p) => {
    const [depth] = depthOf(p);
    return [...p.nodes].sort((x, y) => depth.get(y.id) - depth.get(x.id));
  });
  const feeding = b.work.filter((w) => nodes.some((n) => n.id === w.unblocks || n.item === w.item));
  const rest = b.work.filter((w) => !feeding.includes(w));
  const name = list[cur]?.name;
  const column = cur < 0 ? `<div class="nw-calm"><span class="ok">✓</span>${esc(b.calm || 'Nothing is stuck.')}</div>`
    : `<div class="nw-col ms-${list[cur].state}"><p class="plab">To ${esc(name)}</p>
      ${nodes.length ? `<div class="nw-chain">${nodes.map(chainCard).join('')}<div class="cn goal"><span class="w">Then</span>${esc(name)}</div></div>` : `<div class="nw-calm"><span class="ok">✓</span>${esc(b.calm || 'Nothing is stuck.')}</div>`}
      ${feeding.length ? `<p class="plab feed">Work feeding it</p><div class="nw-feed">${feeding.map(feedCard).join('')}</div>` : ''}</div>`;
  const later = paths.filter((p) => p.milestone !== cur);
  return `<svg class="nw-drop" aria-hidden="true"></svg><div class="nw-expand">${column}
    <div class="nw-side">${lead}
      <h3 class="nw-h">${r.screen === 'story' ? 'Live story' : 'Also being worked on'}</h3>${r.screen === 'story' ? storyColumn(r.id) : workList(rest)}
      ${later.length ? `<div class="nw-laters">${later.map(laterPath).join('')}</div>` : ''}</div></div>`;
}
function chainCard(n) {
  const work = n.item && workItem(n.item);
  const tag = work ? 'a' : 'div';
  const clockLine = [waited(n.since), nodeWord(n) && wordAt(nodeWord(n))].filter(Boolean).join(' · ');
  return `<${tag} class="cn ${esc(n.state)}"${work ? ` href="${storyHref(n.item)}"` : ''}><span class="bar">${NODE_WORD[n.state]}</span><b>${esc(n.text)}</b>${n.how ? `<span class="how">${esc(n.how)}</span>` : ''}${clockLine ? `<span class="clk">${clockLine}</span>` : ''}</${tag}>`;
}
function feedCard(w) {
  const clockLine = [waited(w.since), workWord(w.item) && wordAt(workWord(w.item))].filter(Boolean).join(' · ');
  return `<a class="cn work ${slug(w.status)}" href="${storyHref(w.item)}"><span class="bar">${esc(w.status)}</span><b>${esc(w.title)}</b><span class="how">${esc(w.latest)}</span>${clockLine ? `<span class="clk">${clockLine}</span>` : ''}<span class="open">Live story ›</span></a>`;
}
function dropLine() {
  const svg = document.querySelector('.nw-drop');
  const ms = document.querySelector('.nw-journey .cur');
  const dot = ms?.querySelector('.dot');
  const col = document.querySelector('.nw-col .plab');
  if (!svg || !dot || !col) return svg?.replaceChildren();
  const o = svg.getBoundingClientRect();
  const a = dot.getBoundingClientRect();
  const c = col.getBoundingClientRect();
  const x1 = a.left + a.width / 2 - o.left, y1 = ms.getBoundingClientRect().bottom - o.top + 4;
  const x2 = c.left - o.left, y2 = c.top - o.top + c.height / 2;
  const turn = Math.max(y1 + 10, (svg.nextElementSibling.getBoundingClientRect().top - o.top) - 10);
  const bend = Math.min(10, Math.abs(x1 - x2) / 2);
  const dir = x2 < x1 ? -1 : 1;
  svg.innerHTML = `<path d="M${x1} ${y1} V${turn - bend} Q${x1} ${turn} ${x1 + dir * bend} ${turn} H${x2 - 14 - dir * bend} Q${x2 - 14} ${turn} ${x2 - 14} ${turn + bend} V${y2 - bend} Q${x2 - 14} ${y2} ${x2 - 14 + bend} ${y2} H${x2 - 3}"/>`;
}
function progressLabel() {
  return `<p class="nw-lab">${esc(state.model.milestones_label)}</p>`;
}
function codeLine() {
  const t = tally(state.model.drive.prs);
  return `Code: ${t.landed} of ${t.total} changes merged.`;
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
    <p class="nw-code"><a href="?progress=journey${location.hash}">All ${list.length} milestones ›</a></p>
    ${milestoneBody(b, list, current(list), '')}</div>`;
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
  return `<div class="nw-later"><a class="plab" href="#/plan/m${path.milestone}">To ${esc(name)} ›</a><ul>${path.nodes.map((n) => `<li class="${esc(n.state)}"><p><b>${esc(n.text)}</b> <span title="${esc(NODE_WORD[n.state])}">${esc(n.how)}${lasted(n.since) ? ` · ${lasted(n.since)}` : ''}${nodeWord(n) ? ` · ${wordAt(nodeWord(n))}` : ''}</span></p></li>`).join('')}<li class="goal"><p><b>Then ${esc(name)}</b></p></li></ul></div>`;
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
function workList(list) {
  if (view('work') === 'boxes') return `<div class="nw-boxes">${list.map((w) => `<a class="bx" href="${storyHref(w.item)}"><b>${esc(w.title)}</b><span class="chip ${slug(w.status)}">${esc(w.status)}</span>${waited(w.since) ? `<span class="for">${waited(w.since)}</span>` : ''}<span>${esc(w.latest)}</span></a>`).join('')}</div>`;
  const rows = list.slice(0, view('work') === 'rows5' ? 5 : 3);
  const more = list.length - rows.length;
  if (!rows.length) return '<div class="nw-calm">No other work is running right now.</div>';
  return `<div class="nw-work">${rows.map(workRow).join('')}${more ? `<a class="more" href="#/work">${more} more piece${more === 1 ? '' : 's'} of work ›</a>` : ''}</div>`;
}
function workRow(w) {
  return `<a class="wr ${slug(w.status)}" href="${storyHref(w.item)}"><span class="pulse"></span>
    <span class="body"><b>${esc(w.title)}</b><span class="line"><span class="chip ${slug(w.status)}">${esc(w.status)}</span>${esc(w.latest)}</span>${quietLine(w)}</span>
    <span class="side">${lasted(w.since) ? `<span>${lasted(w.since)}</span><span>since ${esc(w.since)}</span>` : ''}${workWord(w.item) ? `<span>${wordAt(workWord(w.item))}</span>` : ''}<span class="open">Live story ›</span></span></a>`;
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
  const r = pageRoute();
  if (r.screen === 'story' && r.id) follow(decodeURIComponent(r.id));
  else unfollow();
  dropLine();
  mountOverview();
}
document.addEventListener('click', (e) => {
  if (e.target.closest('[data-story-more]')) { story.open = true; paintStory(); }
});
window.addEventListener('resize', () => dropLine());
