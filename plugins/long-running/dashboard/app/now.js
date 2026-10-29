const VIEWS = { story: 'column', work: 'rows4', diagram: 'end-state' };
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
  const page = r.screen === 'story' && view('story') === 'page';
  return `<div class="nw">
    ${band(b)}
    ${page ? storyPage(r.id) : `
    <h1 class="nw-head">${esc(b.headline)}</h1>
    <p class="nw-sub">Written from the drive's records at ${clock(b.at)}. <button class="linkish" data-toggle="nw-why">Why this sentence ›</button></p>
    ${state.open.has('nw-why') ? `<ul class="nw-why">${b.because.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
    <section><h2 class="nw-h">Overall progress</h2>${milestones(b)}</section>
    <section><h2 class="nw-h">${r.screen === 'story' ? 'Live story' : 'Being worked on now'}</h2>${r.screen === 'story' ? storyColumn(r.id) : workList(b.work.filter((w) => !feeding(b).includes(w)))}</section>`}
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
function progressLabel() {
  return `<p class="nw-lab">${esc(state.model.milestones_label)}</p>`;
}
function codeLine() {
  const t = tally(state.model.drive.prs);
  return `Code: ${t.landed} of ${t.total} changes merged.`;
}
function current(b) {
  const list = stops(b);
  const at = list.findIndex((s) => s.state === 'next' || s.state === 'broken');
  return at >= 0 ? at : list.findIndex((s) => s.state !== 'live');
}
function chainOf(path) {
  const [depth] = depthOf(path);
  return [...path.nodes].sort((x, y) => depth.get(y.id) - depth.get(x.id));
}
function feeding(b) {
  const path = b.paths.map(unfinished).find((p) => p.milestone === current(b));
  return path ? b.work.filter((w) => path.nodes.some((n) => n.id === w.unblocks)) : [];
}
const TONE = { live: 'done', next: 'doing', progress: 'doing', paused: 'you', broken: 'bad', not: 'idle', fixing: 'doing', you: 'you', nobody: 'bad', waiting: 'idle' };
function workTone(status) {
  const st = slug(status);
  if (st === 'st-stuck') return 'bad';
  if (['st-ready-to-merge', 'st-passed', 'st-finished'].includes(st)) return 'done';
  if (['st-ready-for-you', 'st-needs-you'].includes(st)) return 'you';
  if (['st-building', 'st-fixing', 'st-investigating', 'st-testing', 'st-rolling-out', 'st-under-review'].includes(st)) return 'doing';
  return 'idle';
}
function firstSentence(text) {
  return (text ?? '').split(/(?<=\.)\s/)[0];
}
function milestones(b) {
  const list = stops(b);
  const cur = current(b);
  const paths = b.paths.map(unfinished).filter((p) => p.nodes.length);
  const plan = curated('plan');
  const cols = list.map((s, i) => {
    const tile = `<a class="mc-tile t-${TONE[s.state] ?? 'idle'}" href="#/plan/m${i}"><span class="dot"></span><span class="nm">${esc(s.name)}</span><span class="st">${i === cur ? 'The drive is here · ' : ''}${esc(s.label)}</span>${s.why ? `<span class="why">${esc(s.why)}</span>` : ''}</a>`;
    const path = paths.find((p) => p.milestone === i);
    return `<div class="mc-col${i === cur ? ' cur' : ''} t-${TONE[s.state] ?? 'idle'}">${tile}${i === cur ? column(b, s, path) : nextStep(s, i, list, path, plan && milestoneOf(plan, i))}</div>`;
  });
  return `<div class="nw-panel mc-panel">${progressLabel()}
    <div class="mc-grid" style="grid-template-columns:${list.map((_, i) => (i === cur ? 'minmax(0, 2.7fr)' : 'minmax(0, 1fr)')).join(' ')}">${cols.join('')}</div>
    <p class="nw-sent">${list.filter((s) => s.state === 'live').length} of ${list.length} milestones are live. ${esc(b.progress.sentence ?? '')}</p><p class="nw-code">${codeLine()}</p>
    </div>`;
}
function nextStep(s, i, list, path, p) {
  const [lead, text] = s.state === 'live' ? ['✓ Live', firstSentence(p?.ships)]
    : path ? [s.label, `${NODE_WORD[chainOf(path)[0].state]}: ${chainOf(path)[0].text}`]
    : s.state === 'not' ? [`After ${list[i - 1]?.name ?? 'the start'}`, firstSentence(p?.waits)]
    : [s.label, firstSentence(p?.waits)];
  return `<p class="mc-next"><b>${esc(lead)}</b>${text ? `<span>${esc(text)}</span>` : ''}</p>`;
}
function column(b, s, path) {
  const work = feeding(b);
  const chain = path ? chainOf(path).map(chainCard) : [`<div class="nw-calm mc-calm"><span class="ok">✓</span>${esc(b.calm || 'Nothing is stuck.')}</div>`];
  return `<div class="mc-stack"><i class="mc-link"></i>${chain.join('<i class="mc-link"></i>')}<i class="mc-link"></i>
    <div class="mc-then"><span class="w">Then</span>${esc(s.name)}</div>
    ${work.length ? `<p class="mc-feed">Work feeding it</p>${work.map(feedCard).join('')}` : ''}</div>`;
}
function card(tag, href, tone, word, title, how, clockLine, open) {
  return `<${tag} class="mc-card t-${tone}"${href ? ` href="${href}"` : ''}><span class="bar">${esc(word)}</span><span class="in"><b>${esc(title)}</b>${how ? `<span class="how">${esc(how)}</span>` : ''}${clockLine ? `<span class="clk">${clockLine}</span>` : ''}${open ? '<span class="open">Live story ›</span>' : ''}</span></${tag}>`;
}
function chainCard(n) {
  const work = n.item && workItem(n.item);
  return card(work ? 'a' : 'div', work && storyHref(n.item), TONE[n.state], NODE_WORD[n.state], n.text, n.how, [waited(n.since), nodeWord(n) && wordAt(nodeWord(n))].filter(Boolean).join(' · '), false);
}
function feedCard(w) {
  return card('a', storyHref(w.item), workTone(w.status), w.status, w.title, w.latest, [waited(w.since), wordAt(workWord(w.item))].filter(Boolean).join(' · '), true);
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
function workList(work) {
  if (view('work') === 'boxes') return `<div class="nw-boxes">${work.map((w) => `<a class="bx" href="${storyHref(w.item)}"><b>${esc(w.title)}</b><span class="chip ${slug(w.status)}">${esc(w.status)}</span>${waited(w.since) ? `<span class="for">${waited(w.since)}</span>` : ''}<span>${esc(w.latest)}</span></a>`).join('')}</div>`;
  const rows = work.slice(0, view('work') === 'rows6' ? 6 : 4);
  const more = work.length - rows.length;
  if (!rows.length) return '<div class="nw-calm">No work is running right now.</div>';
  return `<div class="nw-work mc-rest">${rows.map(workRow).join('')}${more ? `<a class="more" href="#/work">${more} more piece${more === 1 ? '' : 's'} of work ›</a>` : ''}</div>`;
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
  const r = route();
  const item = r.screen === 'story' && r.id && decodeURIComponent(r.id);
  const fresh = item && story.item !== item;
  if (item) follow(item);
  else unfollow();
  if (fresh) requestAnimationFrame(() => $('#nw-story')?.closest('section')?.scrollIntoView({ block: 'start' }));
  mountOverview();
}
document.addEventListener('click', (e) => {
  if (e.target.closest('[data-story-more]')) { story.open = true; paintStory(); }
});
