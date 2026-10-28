const STOP_TONE = { live: 'ok', progress: 'acc', next: 'acc', paused: 'warn', broken: 'bad', not: '' };
const PART_TONE = { done: 'ok', doing: 'acc', you: 'warn', stuck: 'bad', waiting: '', later: '' };
const WORK_TONE = { Stuck: 'bad', 'Ready for you': 'warn', 'Ready to merge': 'ok', Landing: 'ok', Finished: 'ok', 'Waiting for release': '' };
const HISTORY_WORD = { decision: 'Decision', release: 'Released', incident: 'Incident', fixed: 'Fixed', landed: 'Landed', approved: 'Approved', blocked: 'Stuck', unblocked: 'Moving again' };
const HISTORY_TONE = { decision: 'you', approved: 'you', release: 'ok', fixed: 'ok', landed: 'ok', incident: 'bad', blocked: 'bad', unblocked: 'acc' };
const HISTORY_FILTERS = [
  ['', 'Key events', () => true],
  ['decisions', 'Your decisions', (e) => e.kind === 'decision' || e.kind === 'approved'],
  ['releases', 'Releases and incidents', (e) => ['release', 'incident', 'fixed'].includes(e.kind)],
  ['landed', 'Landed', (e) => e.kind === 'landed'],
  ['stuck', 'Stuck and moving again', (e) => e.kind === 'blocked' || e.kind === 'unblocked'],
];
const ROW_FIELDS = ['title', 'change', 'plan', 'risk', 'review', 'blocker'];
const SETTLED = /^(nothing\b.*|your (approval|tap)\.?)$/i;
const RUNNING_AT_REST = 3;
const EVENTS_AT_REST = 6;
const KEY_EVENTS = new Set(['decision', 'release', 'incident', 'fixed', 'blocked', 'unblocked']);

function curated(name) {
  return state.model.pages?.[name];
}
function rewritten(kind, key) {
  return (curated('review')?.[kind] ?? []).find((entry) => entry.key === key);
}
function pageHead(title, written, extra = '') {
  return `<header class="pg-h"><h1>${title}</h1>${written ? `<span class="pg-at">Written from the drive's records at ${clock(written.at)}</span>` : ''}${extra}</header>`;
}
function writing(title) {
  return `<div class="pg">${pageHead(title)}<p class="pg-wait">Writing this page from the drive's records. It appears here within a few minutes.</p></div>`;
}
function dotted(label, tone) {
  return `<span class="pg-state"><i class="pg-dot ${tone ?? ''}"></i>${esc(label)}</span>`;
}
function cellText(text, empty, since = '') {
  return text ? `<span>${esc(text)}${forSince(since)}</span>` : `<span class="pg-none">${empty}</span>`;
}
function forSince(label) {
  const iso = sinceClock(label);
  return iso ? `<small class="pg-for">${blockedFor(iso)}</small>` : '';
}
function waitingSince(number) {
  return state.model.records.findLast((r) => ['head', 'opened'].includes(r.kind) && r.refs?.prs?.includes(number))?.at;
}
function tableRow(id, cells) {
  return `<a class="pg-row" id="pl-${esc(id)}" href="#/plan/${encodeURIComponent(id)}"><span class="pg-cells">${cells.join('')}</span></a>`;
}
function tableHead(...labels) {
  return `<div class="pg-thead">${labels.map((l) => `<span>${l}</span>`).join('')}</div>`;
}
function fold(id, label, body, open = false) {
  return `<details class="pg-fold" data-id="${esc(id)}"${state.open.has(id) || open ? ' open' : ''}><summary>${label}</summary>${body}</details>`;
}

function answerLink(card) {
  return card && state.model.board.cards.some((c) => c.id === card) ? `<a class="pg-go" href="#/review/q/${encodeURIComponent(card)}">Answer it on Review ›</a>` : '';
}
function milestoneOf(p, i) {
  return p.milestones[i] ?? { state: 'not', label: 'Not known', ships: '', waits: '', since: '', card: '', note: '' };
}
function planScreen(focus) {
  const p = curated('plan');
  if (!p) return writing('Plan');
  const stop = /^m(\d+)$/.exec(focus ?? '');
  if (stop && state.model.milestones[stop[1]]) return milestonePage(p, Number(stop[1]));
  const focused = p.parts.find((part) => part.id === focus);
  if (focused) return partPage(focused);
  const milestones = state.model.milestones.map((name, i) => {
    const s = milestoneOf(p, i);
    const waits = s.state === 'not' ? cellText('', 'After the milestone above') : cellText(s.waits, s.state === 'live' ? 'Live' : 'Nothing open', s.since);
    return tableRow(`m${i}`, [`<b>${esc(name)}</b>`, dotted(s.label, STOP_TONE[s.state]), waits]);
  });
  const partRow = (part) => tableRow(part.id, [`<b>${esc(part.name)}</b>`, dotted(part.label, PART_TONE[part.state]), cellText(part.blocks, 'Nothing', part.since), cellText(part.doing, part.state === 'later' ? 'Not started' : '')]);
  const open = p.parts.filter((part) => part.state !== 'done' && part.state !== 'later');
  const later = p.parts.filter((part) => part.state === 'later');
  const done = p.parts.filter((part) => part.state === 'done');
  return `<div class="pg">${pageHead('Plan', p)}<p class="pg-lead">${esc(p.summary)}</p>
    <section class="pg-sec"><h2>${esc(state.model.milestones_label)}</h2><div class="pg-table pl-ms">${tableHead('Milestone', 'State', 'What it waits on')}${milestones.join('')}</div></section>
    <section class="pg-sec"><h2>Parts of the plan still open</h2><div class="pg-table pl-parts">${tableHead('Part', 'State', 'What blocks it', 'Who acts next')}${open.map(partRow).join('') || '<p class="pg-none">Every part of the plan is done.</p>'}</div>
    ${later.length ? fold('pl-later', `Not started <span class="pg-n">${later.length}</span>`, `<div class="pg-table pl-parts">${later.map(partRow).join('')}</div>`) : ''}
    ${done.length ? fold('pl-done', `Done <span class="pg-n">${done.length}</span>`, `<div class="pg-table pl-parts">${done.map(partRow).join('')}</div>`) : ''}</section></div>`;
}
function detailPage(back, kicker, title, stateLine, facts, extra = '') {
  return `<div class="pg pg-page"><a class="pg-back" href="${back[0]}">‹ ${back[1]}</a><div class="kick muted">${kicker}</div><h1 class="pg-title">${esc(title)}</h1>${stateLine}
    <dl class="pg-facts">${facts.filter(([, value]) => value).map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`).join('')}</dl>${extra}</div>`;
}
function milestonePage(p, i) {
  const s = milestoneOf(p, i);
  const names = state.model.milestones;
  const step = (j, label) => (names[j] ? `<a class="btn" href="#/plan/m${j}">${label}</a>` : '');
  return detailPage(['#/plan', 'Plan'], `${esc(state.model.milestones_label)} · ${i + 1} of ${names.length}`, names[i], `<p class="pg-stateline">${dotted(s.label, STOP_TONE[s.state])}${answerLink(s.card)}</p>`, [
    ['What it waits on', s.waits ? `${esc(s.waits)}${forSince(s.since)}` : esc(s.state === 'not' ? 'The milestone above' : s.state === 'live' ? 'Nothing: it is live' : 'Nothing open')],
    ['What it ships', esc(s.ships)],
    ['What went wrong so far', esc(s.note)],
  ], `<p class="acts">${step(i - 1, '‹ Previous milestone')}${step(i + 1, 'Next milestone ›')}</p>`);
}
function partPage(part) {
  const changes = part.changes.map((c) => `<li><a href="#/review/${c.number}">${esc(c.name)}</a><span>${esc(c.state)}</span></li>`).join('');
  return detailPage(['#/plan', 'Plan'], 'Part of the plan', part.name, `<p class="pg-stateline">${dotted(part.label, PART_TONE[part.state])}${answerLink(part.card)}</p>`, [
    ['What it delivers', esc(part.delivers)],
    ['What blocks it', part.blocks ? `${esc(part.blocks)}${forSince(part.since)}` : ''],
    ['Who acts next', esc(part.doing)],
    ['What went wrong so far', esc(part.went_wrong)],
    ['Open changes', changes ? `<ul class="pg-changes">${changes}</ul>` : ''],
    ['Landed', part.landed.length ? `<ul class="pg-changes">${part.landed.map((name) => `<li>${esc(name)}</li>`).join('')}</ul>` : ''],
  ]);
}
function workerScreen(lane) {
  const m = state.model;
  const prs = Object.entries(m.prs).filter(([, pr]) => pr.lane === lane && pr.state === 'open').map(([n]) => Number(n));
  const said = m.records.findLast((r) => r.lane === lane);
  const work = curated('work');
  const piece = [...(work?.running ?? []), ...(work?.finished ?? [])].find((w) => w.workers.includes(lane));
  const waits = m.drive.prs.filter((e) => e.owner === lane && e.state !== 'landed' && e.blocker);
  return detailPage(['#/work', 'Work'], 'Worker', workerName(lane), piece ? `<p class="pg-stateline">On <a href="#/work/${encodeURIComponent(piece.item)}">${esc(piece.title)} ›</a></p>` : '', [
    ['Its pull requests', prs.length ? `<ul class="pg-changes">${prs.map((n) => `<li><a href="#/review/${n}">${esc(plainTitle(n))}</a><span>${esc(STATE_WORD[prState(n)] ?? '')}</span></li>`).join('')}</ul>` : 'None open'],
    ['Its last word', said ? `${esc(SAID[said.kind] ?? KIND_WORD[said.kind] ?? said.kind)} at ${clock(said.at)} <a href="/record/${said.seq}" target="_blank" rel="noopener">Read it ↗</a>` : 'Nothing today'],
    ['What it waits on', waits.length ? `<ul class="pg-changes">${waits.map((e) => `<li>${inline(e.blocker)}</li>`).join('')}</ul>` : 'Nothing'],
  ]);
}
function workScreen(item) {
  const p = curated('work');
  if (!p) return writing('Work');
  const row = (w, line, side) => w.item === item
    ? `<div class="wk-story"><a class="wk-close" href="#/work">‹ All work</a><div id="nw-story">${storyBody(item)}</div></div>`
    : `<a class="wk-row" href="#/work/${encodeURIComponent(w.item)}"><span class="wk-body"><b>${esc(w.title)}</b><span class="wk-line">${line}</span></span><span class="wk-side">${side}</span></a>`;
  const running = p.running.map((w) => row(w, `${dotted(w.status, WORK_TONE[w.status] ?? 'acc')} ${esc(w.latest)}`, sinceClock(w.blocked_since) ? `Waiting ${blockedFor(sinceClock(w.blocked_since))}` : sinceClock(w.started) ? `Started ${esc(w.started)}` : ''));
  const rest = running.slice(RUNNING_AT_REST);
  const finished = p.finished.map((w) => row(w, `${w.outcome === 'stopped' ? `${dotted('Stopped', 'warn')} ` : ''}${esc(w.result)}`, `${w.outcome === 'stopped' ? 'Stopped' : 'Done'} ${esc(w.at)}`));
  const helpers = p.helpers.map((h) => `<li><b>${esc(h.name)}</b><span>${esc(h.latest)}</span></li>`).join('');
  return `<div class="pg">${pageHead('Work', p)}
    <section class="pg-sec"><h2>Active work <span class="pg-n">${p.running.length}</span></h2><div class="pg-list">${running.slice(0, RUNNING_AT_REST).join('') || '<p class="pg-none">No work is running right now.</p>'}</div>
    ${rest.length ? fold('wk-more', `More active work <span class="pg-n">${rest.length}</span>`, `<div class="pg-list">${rest.join('')}</div>`, p.running.slice(RUNNING_AT_REST).some((w) => w.item === item)) : ''}</section>
    ${finished.length ? `<section class="pg-sec">${fold('wk-finished', `Finished today <span class="pg-n">${finished.length}</span>`, `<div class="pg-list">${finished.join('')}</div>`, p.finished.some((w) => w.item === item))}</section>` : ''}
    ${helpers ? `<section class="pg-sec">${fold('wk-helpers', `Keeping the drive running <span class="pg-n">${p.helpers.length}</span>`, `<ul class="wk-helpers">${helpers}</ul>`)}</section>` : ''}</div>`;
}

function rowWords(row) {
  const rewrite = rewritten('rows', row.key);
  return Object.fromEntries(ROW_FIELDS.map((k) => [k, (rewrite ? rewrite[k] : row[k]) ?? '']));
}
function verdictPill(row) {
  const when = row.verdict?.at ? ` at ${clock(row.verdict.at)}` : '';
  if (verdictState(row) === 'held') return dotted(`You held it${when}`, 'warn');
  return dotted(row.ci_fix_go || boardRow(row.number)?.ci_fix_go ? 'CI fix, landing without your tap' : `You approved${when}`, 'ok');
}
function rowFacts(row) {
  const w = rowWords(row);
  if (row.md && !row.change) return `<div class="md">${markdown(row.md)}</div>`;
  return `<dl class="mr-f"><dt>For users</dt><dd>${esc(w.change)}</dd><dt>Plan</dt><dd>${esc(w.plan)}</dd><dt>Risk</dt><dd>${esc(w.risk)}</dd><dt>Review</dt><dd>${esc(w.review)}</dd></dl>`;
}
function waitLine(row, w) {
  const since = waitingSince(row.number);
  if (!SETTLED.test(w.blocker.trim())) return `<p class="mr-waits">${dotted('Waits on', 'warn')} ${esc(w.blocker)}${since ? `<small class="pg-for">${blockedFor(since)}</small>` : ''}</p>`;
  return verdictState(row) === 'you' && since ? `<p class="mr-waits">Waiting on you ${blockedFor(since)}</p>` : '';
}
function mergeRow(row) {
  const w = rowWords(row);
  const busy = pending.number === row.number && !pending.error;
  const waits = waitLine(row, w);
  const carried = row.carried?.length ? '<p class="mr-waits">Your earlier approval carries to this version; the review line names the fix.</p>' : '';
  const acts = verdictState(row) === 'you'
    ? `<button class="btn pri" data-approve="${row.number}" data-head="${esc(row.head)}"${busy ? ' disabled' : ''}>${busy ? 'Sending' : 'Approve'}</button><button class="btn" data-hold-row="${row.number}"${busy ? ' disabled' : ''}>Hold</button>`
    : verdictPill(row);
  return `<article class="mr"><header><a class="mr-t" href="#/review/${row.number}">${esc(w.title)}</a><span class="mr-acts">${acts}<a class="btn ghost" href="#/review/${row.number}">Details</a></span></header>${waits}${carried}${rowFacts(row)}${pending.number === row.number && pending.error ? `<p class="mr-err">Not recorded: ${esc(pending.error)}</p>` : ''}</article>`;
}
function reviewScreen(list) {
  const m = state.model;
  if (list === 'q') list = '';
  const decided = m.board.rows.filter((r) => verdictState(r) !== 'you');
  const chips = `<nav class="pg-filters"><a href="#/review" class="${list ? '' : 'on'}">Waiting on you <b>${needsTotal()}</b></a><a href="#/review/decided" class="${list === 'decided' ? 'on' : ''}">You approved or held <b>${decided.length}</b></a></nav>`;
  if (list === 'decided') return `<div class="pg">${pageHead('Review', curated('review'))}${chips}<div class="mr-list">${decided.map(mergeRow).join('') || '<p class="pg-none">Nothing you approved is still waiting to land.</p>'}</div></div>`;
  const merges = m.needs.filter((item) => item.kind === 'prs').map((g) => {
    const words = rewritten('groups', g.key);
    const relation = words ? words.relation : g.relation;
    const head = g.prs.length > 1 ? `<h3>${esc(words?.title ?? g.group)}</h3>${relation ? `<p class="pg-rel">${esc(relation)}</p>` : ''}` : '';
    return `<section class="pg-sec mr-group">${head}<div class="mr-list">${g.prs.map(mergeRow).join('')}</div></section>`;
  }).join('');
  const cards = m.needs.filter((item) => item.kind === 'card');
  return `<div class="pg">${pageHead('Review', curated('review'))}${chips}
    ${needsTotal() ? '' : '<p class="pg-lead">Nothing waits on you. When a change or a question needs you, it shows here and in the band on Now.</p>'}
    ${needsTotal() && !merges ? '<p class="pg-rel">No change waits on your approval right now.</p>' : ''}
    ${merges}
    ${cards.length ? `<section class="pg-sec"><h2>Questions</h2><div class="mr-list">${cards.map(boardCard).join('')}</div></section>` : ''}</div>`;
}

function historyEntry(e) {
  const id = `hs-${e.at}-${e.title}`;
  const line = `<time>${esc(e.at)}</time>${dotted(HISTORY_WORD[e.kind], HISTORY_TONE[e.kind])}<b>${esc(e.title)}</b>`;
  return e.detail.trim()
    ? `<li><details data-id="${esc(id)}"${state.open.has(id) ? ' open' : ''}><summary>${line}</summary><p>${esc(e.detail)}</p></details></li>`
    : `<li><div class="hs-line">${line}</div></li>`;
}
function historyScreen(filter) {
  const p = curated('history');
  if (!p) return writing('History');
  const f = HISTORY_FILTERS.find(([id]) => id === (filter ?? '')) ?? HISTORY_FILTERS[0];
  const shown = p.entries.filter(f[2]);
  const key = (f[0] ? shown : shown.filter((e) => KEY_EVENTS.has(e.kind))).slice(0, EVENTS_AT_REST);
  const rest = shown.filter((e) => !key.includes(e));
  const since = p.since ? ` since ${esc(p.since)}` : ' today';
  return `<div class="pg">${pageHead('History', p, p.since ? `<span class="pg-at">Covers today since ${esc(p.since)}</span>` : '')}<div class="pg-lead hs-sum">${p.summary.map((x) => `<p>${esc(x)}</p>`).join('')}</div>
    <nav class="pg-filters">${HISTORY_FILTERS.map(([id, label]) => `<a href="#/history${id ? `/${id}` : ''}" class="${id === f[0] ? 'on' : ''}">${label}</a>`).join('')}</nav>
    <ol class="hs">${key.map(historyEntry).join('') || `<li class="pg-none">Nothing of this kind${since}.</li>`}</ol>
    ${rest.length ? fold(`hs-rest-${f[0]}`, `${f[0] ? 'Earlier' : 'Other events'} <span class="pg-n">${rest.length}</span>`, `<ol class="hs">${rest.map(historyEntry).join('')}</ol>`) : ''}</div>`;
}

document.addEventListener('click', (e) => {
  const hold = e.target.closest('[data-hold-row]');
  if (!hold) return;
  const row = boardRow(hold.dataset.holdRow);
  if (row) decide(row, 'rejected', '', false);
});
