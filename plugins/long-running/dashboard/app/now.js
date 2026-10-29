const VIEWS = { progress: 'journey', story: 'column', diagram: 'end-state' };
const NODE_WORD = { fixing: 'Being fixed', you: 'Needs you', nobody: 'No one assigned', waiting: 'Waiting', done: 'Done' };
const MARK_WORD = { progress: 'Progress', finding: 'Found', stuck: 'Stuck', done: 'Done' };
const story = { item: null, es: null, lines: [], draft: null, status: null, open: false, shown: null };

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
  if (!b) return `<div class="nw"><p class="nw-wait">Writing the first summary of the drive. It appears here in about a minute.</p>${diagramSlot()}</div>`;
  const page = r.screen === 'story' && view('story') === 'page';
  return `<div class="nw">
    ${band(b)}
    ${page ? storyPage(r.id) : `
    <h1 class="nw-head">${esc(b.headline)}</h1>
    <p class="nw-sub">Written from the drive's records at ${clock(b.at)}. <button class="linkish" data-toggle="nw-why">Why this sentence ›</button></p>
    ${state.open.has('nw-why') ? `<ul class="nw-why">${b.because.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
    <section><h2 class="nw-h">Overall progress</h2>${view('progress') === 'compact' ? compact(b) : journey(b)}</section>
    ${r.screen === 'story' ? `<section id="nw-story-at"><h2 class="nw-h">Live story</h2>${storyColumn(r.id)}</section>` : ''}`}
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
function current(b, list) {
  const next = list.findIndex((s) => s.state === 'next');
  return next >= 0 ? next : b.paths.map(unfinished).find((p) => p.nodes.length)?.milestone ?? list.findIndex((s) => s.state !== 'live');
}
function journey(b) {
  const list = spine(b);
  const cur = current(b, list);
  const lastLive = list.map((s) => s.state).lastIndexOf('live');
  const open = (i) => (i === cur) !== state.open.has(`ms${i}`);
  const widths = list.map((s, i) => `minmax(0, ${open(i) ? 3.6 : s.state === 'live' ? 0.75 : s.left.length || s.work.length ? 1.5 : 1}fr)`).join(' ');
  return `<div class="nw-panel">${progressLabel()}
    <div class="nw-journey nw-spine" style="--n:${list.length};grid-template-columns:${widths}">${list.map((s, i) => {
      const cls = ['sp', `ms-${s.state}`, i === cur && 'cur', open(i) && 'open', i > 0 && i <= lastLive && 'l-on', i < lastLive && 'r-on'].filter(Boolean).join(' ');
      return `<div class="${cls}"><a class="ms ms-${s.state}${i === cur ? ' cur' : ''}" href="#/plan/m${i}"><span class="here">${i === cur ? 'Drive is here' : ''}</span><span class="dot"></span><span class="nm">${esc(s.name)}</span><span class="st">${esc(s.label)}</span>${s.why ? `<span class="why">${esc(s.why)}</span>` : ''}${open(i) ? '<i class="stem"></i>' : ''}</a>
        <div class="bd">${open(i) ? openStop(s, b, i === cur) : shutStop(s, list[i - 1])}</div></div>`;
    }).join('')}</div>
    ${tray(b)}
    <p class="nw-sent">${list.filter((s) => s.state === 'live').length} of ${list.length} milestones are live. ${esc(b.progress.sentence ?? '')}</p><p class="nw-code">${codeLine()}</p>
    </div>`;
}
function placed(b) {
  return b.work.map((w) => ({ ...w, at: b.paths.find((p) => p.nodes.some((n) => n.id === w.unblocks || n.item === w.item))?.milestone ?? -1 }));
}
function spine(b) {
  const work = placed(b);
  return stops(b).map((s, i) => {
    const paths = b.paths.filter((p) => p.milestone === i);
    const left = paths.map(unfinished).flatMap((p) => {
      const [depth] = depthOf(p);
      return p.nodes.map((n) => ({ ...n, depth: depth.get(n.id) })).sort((x, y) => x.depth - y.depth);
    });
    return { ...s, i, done: paths.flatMap((p) => p.nodes).filter((n) => n.state === 'done'), left, work: work.filter((w) => w.at === i) };
  });
}
function openStop(s, b, cur) {
  const left = s.left.length ? `<p class="hl">Holding it up, nearest first</p><div class="chain">${s.left.map(hangNode).join('')}</div>` : '';
  const feed = s.work.length ? `<p class="hl">Work feeding it</p><div class="feed">${s.work.map(hangWork).join('')}</div>` : '';
  const done = s.done.length ? `<p class="hl">Done so far</p><ul class="did">${s.done.map((n) => `<li>${esc(n.text)}</li>`).join('')}</ul>` : '';
  const calm = s.state === 'live' ? 'Live. Nothing is left to do here.' : cur ? b.calm || 'Nothing is stuck.' : 'Nothing is recorded as holding it up yet.';
  return `<div class="nw-col ms-${s.state}">${left || feed ? left + feed : `<div class="nw-calm"><span class="ok">✓</span>${esc(calm)}</div>`}${done}<button class="linkish tg" data-toggle="ms${s.i}">Fold it to one line</button></div>`;
}
function shutStop(s, before) {
  const tg = s.left.length || s.work.length || s.done.length ? `<button class="linkish tg" data-toggle="ms${s.i}">Open ›</button>` : '';
  if (s.state === 'live') return `<div class="shut live"><span class="dn">✓ Done${s.done.length ? `, ${s.done.length} step${s.done.length === 1 ? '' : 's'}` : ''}</span>${tg}</div>`;
  const rank = { nobody: 0, you: 1, fixing: 2, waiting: 3 };
  const next = [...s.left].sort((x, y) => y.depth - x.depth || rank[x.state] - rank[y.state])[0];
  const lead = next
    ? `<span class="k ${esc(next.state)}">Next step</span><span class="nx">${esc(next.text)}</span>${s.left.length > 1 ? `<span class="mo">and ${s.left.length - 1} more holding it</span>` : ''}`
    : before && s.state === 'not' ? `<span class="k">Starts after</span><span class="nx">${esc(before.name)}</span>` : '<span class="mo">No blockers recorded</span>';
  return `<div class="shut">${lead}${s.work.map((w) => `<a class="wk ${slug(w.status)}${onStory(w.item) ? ' on' : ''}" href="${storyHref(w.item)}" title="${esc(`${w.status}: ${w.latest}`)}"><span class="pulse"></span><span>${esc(w.title)}</span></a>`).join('')}${tg}</div>`;
}
function tray(b) {
  const loose = placed(b).filter((w) => w.at < 0);
  if (!loose.length) return '';
  return `<div class="nw-tray"><span class="k">Feeding no milestone</span>${loose.map((w) => `<a class="tw ${slug(w.status)}${onStory(w.item) ? ' on' : ''}" href="${storyHref(w.item)}" title="${esc(w.latest)}"><span class="pulse"></span>${esc(w.title)}<span class="chip ${slug(w.status)}">${esc(w.status)}</span></a>`).join('')}<a class="more" href="#/work">All work ›</a></div>`;
}
function onStory(item) {
  const r = route();
  return r.screen === 'story' && decodeURIComponent(r.id ?? '') === item;
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
    ${(() => { const at = current(b, list); return at < 0 ? '' : `<div class="nw-compact-col">${openStop(spine(b)[at], b, true)}</div>`; })()}
    ${tray(b)}
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
function clockLine(since, word) {
  return [waited(since), wordAt(word)].filter(Boolean).join(', ');
}
function hangNode(n) {
  const tag = n.item && workItem(n.item) ? 'a' : 'div';
  const when = clockLine(n.since, nodeWord(n));
  return `<${tag} class="hc ${esc(n.state)}"${tag === 'a' ? ` href="${storyHref(n.item)}"` : ''}><span class="bar">${NODE_WORD[n.state]}</span><span class="in"><b>${esc(n.text)}</b>${n.how ? `<span class="how">${esc(n.how)}</span>` : ''}${when ? `<span class="when">${when}</span>` : ''}</span></${tag}>`;
}
function hangWork(w) {
  const when = clockLine(w.since, workWord(w.item));
  return `<a class="hc ${slug(w.status)}${onStory(w.item) ? ' on' : ''}" href="${storyHref(w.item)}"><span class="bar">${esc(w.status)}</span><span class="in"><b>${esc(w.title)}</b><span class="how">${esc(w.latest)}</span>${when ? `<span class="when">${when}</span>` : ''}<span class="open">Live story ›</span></span></a>`;
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
  if (r.screen === 'story' && r.id) {
    follow(decodeURIComponent(r.id));
    if (story.shown !== r.id) {
      story.shown = r.id;
      requestAnimationFrame(() => document.getElementById('nw-story-at')?.scrollIntoView({ block: 'start' }));
    }
  } else {
    unfollow();
    story.shown = null;
  }
  mountOverview();
}
document.addEventListener('click', (e) => {
  if (e.target.closest('[data-story-more]')) { story.open = true; paintStory(); }
});
