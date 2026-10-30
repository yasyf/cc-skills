const STOP_TONE = { live: 'ok', progress: 'acc', next: 'acc', paused: 'warn', broken: 'bad', not: '' };
const PART_TONE = { done: 'ok', doing: 'acc', you: 'warn', stuck: 'bad', waiting: '', later: '' };
const WORK_TONE = { Stuck: 'bad', 'Ready for you': 'you', 'Ready to merge': 'ok', Landing: 'ok', Finished: 'ok', 'Waiting for release': '' };
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
  return `<header class="pg-h"><div><h1>${title}</h1>${written ? `<span class="pg-at">Written from the drive's records at ${clock(written.at)}</span>` : ''}</div>${extra}</header>`;
}
function ring(done, total, label) {
  const p = total ? Math.round((done / total) * 100) : 0;
  return `<div class="pg-ringwrap"><div class="pg-ring" style="--p:${p}"><b>${p}<small>%</small></b></div><span>${done} of ${total} ${label}</span></div>`;
}
function writing(title) {
  return `<div class="pg">${pageHead(title)}<p class="pg-wait">Writing this page from the drive's records. It appears here within a few minutes.</p></div>`;
}
function light(stage) {
  return stage === 'you' ? 'you' : STATE_TONE[stage] ?? '';
}
function dotted(label, tone) {
  return `<span class="pg-state ${tone ?? ''}">${esc(label)}</span>`;
}
function cellText(text, empty, since = '') {
  return text ? `<span>${esc(text)}${forSince(since)}</span>` : `<span class="pg-none">${empty}</span>`;
}
function forSince(label) {
  const iso = sinceClock(label);
  return iso ? `<small class="pg-for">${blockedFor(iso)}</small>` : '';
}
const named = (n) => `“${prTitle(n)}”`;
const LEDGER_WORDS = [
  [/^waits on #(\d+) below it$/, (n) => `Waits for ${named(n)} below it to land`],
  [/^land #(\d+) first$/, (n) => `${named(n)} lands first`],
  [/^held: a hold record covers it$/, () => 'Held by the coordinator'],
  [/^held: (?:root cci hold: )?(.+?)(?: \(hold records[^)]*\))?$/, (why) => `Held by the coordinator: ${why}`],
  [/^in the merge queue$/, () => 'In the merge queue'],
  [/^green with a go, not queued$/, () => 'Approved and passing, not yet in the merge queue'],
  [/^green, and no go names this head$/, () => 'Passing; this version is not approved yet'],
  [/^needs a restack: the PR below it landed$/, () => 'Needs rebuilding on the change below it, which landed'],
  [/^red on .+$/, () => 'Its tests fail'],
  [/^fix the red build of #\d+ at \w+$/, () => 'its worker fixes the failing tests'],
  [/^lift the hold on #\d+ or do what it waits on$/, () => 'the coordinator lifts the hold, or its worker does what the hold waits on'],
  [/^restack #\d+ at \w+ onto its new parent/, () => 'its worker rebuilds it on the landed change'],
  [/^root posts `go #\d+ at \w+`/, () => 'the coordinator approves this version or puts it on your board'],
  [/^the landing desk enqueues #\d+ at \w+$/, () => 'the merge process queues it'],
  [/^the landing desk reads why the merge queue still holds #\d+ at \w+$/, () => 'the merge process finds out why the queue still holds it'],
];
function ledgerWords(text) {
  const hit = LEDGER_WORDS.find(([pattern]) => pattern.test(text));
  if (hit) return hit[1](...text.match(hit[0]).slice(1));
  return text.replace(/`[^`]*`/g, '').replace(/\b[0-9a-f]{7,40}\b/g, '').replace(/#(\d{4,6})\b/g, (_, n) => named(n)).replace(/\s+/g, ' ').trim();
}
function stackRow(n) {
  return state.model.stack.find((row) => row.pr === String(n));
}
function prTruth(n) {
  if (prState(n) === 'landed') return { blocks: '', next: '', since: null };
  const board = boardRow(n);
  if (board && verdictState(board) === 'you') return { blocks: rowWords(board).blocker, next: '', since: waitingSince(Number(n)) ?? stackRow(n)?.since };
  if (board && verdictState(board) === 'failing') return { blocks: board.blocker, next: '', since: null };
  const approved = Math.max(new Date(approvedAt(Number(n)) ?? 0), new Date(board?.verdict?.at ?? 0));
  const evidence = state.model.evidence[n];
  const work = curated('work');
  const said = work?.pull_requests?.find((pr) => pr.number === Number(n));
  if (evidence && new Date(evidence.at) > approved) return { blocks: evidence.state, next: evidence.next, since: evidence.since };
  if (said && new Date(work.at) > approved) return { blocks: said.stage, next: said.next, since: sinceClock(said.since) };
  const node = blockerNode(n);
  if (node) return { blocks: node.text, next: node.how, since: sinceClock(node.since) };
  const row = stackRow(n);
  if (row) return { blocks: ledgerWords(row.state), next: /^land #\d+ first$/.test(row.next) ? '' : ledgerWords(row.next), since: row.since };
  const e = state.drive.get(Number(n));
  return { blocks: e?.blocker ?? '', next: e?.next ?? '', since: null };
}
function latestWord(n) {
  return state.model.latest_words[n] ?? null;
}
function workWord(item) {
  const w = workItem(item);
  for (const lane of new Set([...(w?.changes ?? []).map((n) => state.model.prs[n]?.lane).filter(Boolean), ...(w?.workers ?? [])])) {
    const prs = Object.entries(state.model.prs).filter(([, pr]) => pr.state === 'open' && sameWorker(pr.lane, lane)).map(([n]) => n);
    const word = prs.length ? prs.map(latestWord).filter(Boolean).sort((a, b) => new Date(a.at) - new Date(b.at)).at(-1) : state.model.records.findLast((r) => sameWorker(r.lane, lane));
    if (word) return word;
  }
  return null;
}
function wordAt(r) {
  return r ? `latest word ${clock(r.at)}` : '';
}
function wordLine(r) {
  const who = r.kind === 'owner' ? 'You' : workerName(r.lane);
  const did = r.kind === 'owner' ? 'answered' : (SAID[r.kind] ?? KIND_WORD[r.kind] ?? r.kind).toLowerCase();
  return `${esc(who)} ${esc(did)} at ${clock(r.at)} <a href="/record/${r.seq}" target="_blank" rel="noopener">Read it ↗</a>`;
}
function truthFacts(n, landed) {
  const t = prTruth(n);
  const word = latestWord(n);
  return `${word ? `<dt>Latest word</dt><dd>${wordLine(word)}</dd>` : ''}<dt>Blocks landing</dt><dd>${t.blocks ? `${esc(t.blocks)}${t.since ? `<small class="pg-for">${blockedFor(t.since)}</small>` : ''}` : landed ? 'Nothing: it landed' : 'Nothing'}</dd>${t.next ? `<dt>What happens next</dt><dd>${esc(t.next.replace(/^./, (c) => c.toUpperCase()))}</dd>` : ''}`;
}
function stackLine(row) {
  const t = prTruth(row.pr);
  const l = { stage: t.blocks, next: t.next, since: t.since };
  const word = latestWord(row.pr);
  return `<div class="wk-row"><span class="wk-body">${dotted('', light(prState(row.pr)))}<a href="#/review/${row.pr}"><b>${esc(prTitle(row.pr))}</b></a><span class="wk-line">${esc(l.stage.replace(/\.$/, ''))}.${l.next ? ` <span class="wk-next">Next: ${esc(l.next.replace(/\.$/, ''))}.</span>` : ''}</span></span><span class="wk-side"><a href="#/worker/${encodeURIComponent(row.owner)}">${esc(workerName(row.owner))}</a>${row.live ? '' : ', not running'}${l.since ? `<br>${blockedFor(l.since)}` : ''}${word ? `<br><a class="wk-word" href="/record/${word.seq}" target="_blank" rel="noopener">Latest word ${clock(word.at)} ↗</a>` : ''}</span></div>`;
}
function blockerNode(n) {
  return (state.model.brief?.paths ?? []).flatMap((path) => path.nodes).find((x) => String(x.change) === String(n));
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

function answerHere(card) {
  const item = openQuestion(card);
  return item ? boardCard(item) : '';
}
function milestoneOf(p, i) {
  return p.milestones[i] ?? { state: 'not', label: 'Not known', ships: '', waits: '', since: '', card: '', note: '', unblock: '' };
}
function planScreen() {
  const p = curated('plan');
  if (!p) return writing('Plan');
  const milestones = state.model.milestones.map((name, i) => {
    const s = milestoneOf(p, i);
    const waits = s.state === 'not' ? cellText('', 'After the milestone above') : cellText(s.waits, s.state === 'live' ? 'Live' : 'Nothing open', s.since);
    return tableRow(`m${i}`, [`<b>${esc(name)}</b>`, dotted(s.label, STOP_TONE[s.state]), waits]);
  });
  const partRow = (part) => tableRow(part.id, [`<b>${esc(part.name)}</b>`, dotted(part.label, PART_TONE[part.state]), cellText(part.blocks, 'Nothing', part.since), cellText(part.doing, part.state === 'later' ? 'Not started' : '')]);
  const open = p.parts.filter((part) => part.state !== 'done' && part.state !== 'later');
  const later = p.parts.filter((part) => part.state === 'later');
  const done = p.parts.filter((part) => part.state === 'done');
  return `<div class="pg">${pageHead('Plan', p, ring(p.parts.filter((x) => x.state === 'done').length, p.parts.length, 'parts done'))}<p class="pg-lead">${esc(p.summary)}</p>
    <section class="pg-sec"><h2>${esc(state.model.milestones_label)}</h2><div class="pg-table pl-ms">${tableHead('Milestone', 'State', 'What it waits on')}${milestones.join('')}</div></section>
    <section class="pg-sec"><h2>Parts of the plan still open</h2><div class="pg-table pl-parts">${tableHead('Part', 'State', 'What blocks it', 'What we are doing to unblock it')}${open.map(partRow).join('') || '<p class="pg-none">Every part of the plan is done.</p>'}</div>
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
  const step = (j, label, key) => (names[j] ? `<a class="btn" href="#/plan/m${j}" data-key="${key}">${label}<kbd>${key.toUpperCase()}</kbd></a>` : '');
  const b = state.model.brief;
  const { nodes, feeding } = b ? milestoneChain(b, i) : { nodes: [], feeding: [] };
  const prs = [...new Set([...nodes.map((n) => n.change), ...feeding.flatMap((w) => w.changes ?? [])].filter(Boolean))];
  const chain = `<div class="nw ms-chain">${nodes.length ? `<section><h2 class="nw-h">What stands in the way, in order</h2><div class="nw-chain">${nodes.map(chainCard).join('')}<div class="cn goal"><span class="w">Then</span>${esc(names[i])}</div></div></section>` : ''}
    ${feeding.length ? `<section><h2 class="nw-h">Work on it</h2><div class="nw-feed">${feeding.map(feedCard).join('')}</div></section>` : ''}
    ${prs.length ? `<section><h2 class="nw-h">Its pull requests <span class="pg-n">${prs.length}</span></h2><ul class="pg-changes ms-prs">${prs.map(changeLine).join('')}</ul></section>` : ''}</div>`;
  return detailPage(['#/plan', 'Plan'], `${esc(state.model.milestones_label)} · ${i + 1} of ${names.length}`, names[i], `<p class="pg-stateline">${dotted(s.label, STOP_TONE[s.state])}</p>${answerHere(s.card)}`, [
    ['What it waits on', s.waits ? `${esc(s.waits)}${forSince(s.since)}` : esc(s.state === 'not' ? 'The milestone above' : s.state === 'live' ? 'Nothing: it is live' : 'Nothing open')],
    ['What we are doing to unblock it', esc(s.unblock ?? '')],
    ['What it ships', esc(s.ships)],
    ['What went wrong so far', esc(s.note)],
  ], `${chain}<p class="acts">${step(i - 1, '‹ Previous milestone', 'k')}${step(i + 1, 'Next milestone ›', 'j')}</p>`);
}
function partPage(part) {
  const changes = part.changes.map((c) => `<li class="${light(prState(c.number))}"><a href="#/review/${c.number}">${esc(c.name)}</a><span>${esc(c.state)}</span></li>`).join('');
  return detailPage(['#/plan', 'Plan'], 'Part of the plan', part.name, `<p class="pg-stateline">${dotted(part.label, PART_TONE[part.state])}</p>${answerHere(part.card)}`, [
    ['What it delivers', esc(part.delivers)],
    ['What blocks it', part.blocks ? `${esc(part.blocks)}${forSince(part.since)}` : ''],
    ['What we are doing to unblock it', esc(part.doing)],
    ['What went wrong so far', esc(part.went_wrong)],
    ['Open changes', changes ? `<ul class="pg-changes">${changes}</ul>` : ''],
    ['Landed', part.landed.length ? `<ul class="pg-changes">${part.landed.map((name) => `<li class="ok"><span class="pg-name">${esc(name)}</span></li>`).join('')}</ul>` : ''],
  ]);
}
function liveSince(table) {
  const [head, rule, ...rows] = table.trim().split('\n');
  const col = head.split('|').findIndex((cell) => /blocked since/i.test(cell));
  const stamp = /\bsince (?:about )?(\d{1,2}:\d{2} [AP]M)(?: ((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2}))?/i;
  const timed = (cell) => {
    const found = stamp.exec(cell);
    if (!found) return cell;
    const iso = found[2] ? new Date(`${found[2]}, ${new Date().getFullYear()} ${found[1]}`).toISOString() : sinceClock(found[1]);
    return `${cell.replace(/,?\s*\b\d+ h(?: \d+ min)?\b|,?\s*\b\d+ min\b|,?\s*\b\d+ hours?\b/g, '').trim()}; ${blockedFor(iso).replace(/, since .*/, '')} now`;
  };
  return [head, rule, ...rows.map((row) => row.split('|').map((cell, i) => (i === col ? ` ${timed(cell.trim())} ` : cell)).join('|'))].join('\n');
}
function statusRows() {
  const split = (line) => line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  const [head, , ...rows] = liveSince(state.model.status_table.split(/\n\s*\n/)[0]).trim().split('\n').map(split);
  return { head, rows };
}
function workerLinks(html) {
  return html.replace(/\[([^\]]+)\]\(worker:([\w-]+)\)/g, (_, said, lane) => {
    const item = [...(state.model.brief?.work ?? []), ...(state.model.pages?.work?.running ?? [])].find((w) => w.workers.some((x) => sameWorker(x, lane)))?.item;
    return `<a class="st-worker" href="#/worker/${encodeURIComponent(lane)}">${said}</a>${item ? ` <a class="st-worker st-story" href="#/work/${encodeURIComponent(item)}">live story ›</a>` : ''}`;
  });
}
function stepLight(cell) {
  return /^done\b/i.test(cell) ? 'ok' : /\b(red|fail|broken|stuck|conflict)/i.test(cell) ? 'bad' : /^not started|^later/i.test(cell) ? '' : 'acc';
}
function statusStep(row) {
  const { head } = statusRows();
  return `<div class="pg pg-page"><div class="kick muted">Plan step</div><h1 class="pg-title">${esc(words(row[0]))}</h1><dl class="pg-facts">${row.slice(1).map((c, i) => `<dt>${esc(head[i + 1] ?? '')}</dt><dd>${workerLinks(inline(c))}</dd>`).join('')}</dl></div>`;
}
function statusScreen() {
  const m = state.model;
  const steps = statusRows();
  const red = Object.entries(m.prs).filter(([, pr]) => pr.state === 'open' && (pr.test_state === 'failure' || pr.mergeable_state === 'dirty')).map(([n, pr]) => ({ n, pr, t: prTruth(n) }));
  const problem = (pr) => [pr.mergeable_state === 'dirty' ? 'Merge conflict with its base' : '', pr.test_state === 'failure' ? (pr.failing_checks ?? []).some((c) => /release-pr-check/.test(c.name)) ? 'Tests and the release check fail' : 'Tests fail' : ''].filter(Boolean).join('; ');
  const prRow = ({ n, pr, t }) => `<div class="pg-row"><span class="pg-cells"><b><a href="#/review/${n}">${esc(prTitle(n))}</a></b><span>${dotted(problem(pr), 'bad')}</span><span>${esc(t.blocks || 'Not known')}</span><span class="st-for">${t.since ? blockedFor(t.since) : '<span class="pg-none">Not known</span>'}</span><span>${esc(t.next || 'No step named yet')}${pr.lane ? ` · <a href="#/worker/${encodeURIComponent(pr.lane)}">${esc(workerName(pr.lane))}</a>` : ''}</span></span></div>`;
  return `<div class="pg">${pageHead('Status')}<p class="pg-lead">Each step of the plan, exactly what blocks it, for how long, and what is being done to unblock it, from the owner table; then every open pull request whose tests fail or that conflicts with its base.</p>
    <section class="pg-sec"><h2>Every step of the plan</h2><div class="md prose st-table"><div class="tablewrap"><table><thead><tr>${steps.head.map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${steps.rows.map((row, i) => `<tr data-step="${i}">${row.map((c, j) => `<td data-h="${esc(steps.head[j] ?? '')}">${j === 1 ? `<span class="st-light ${stepLight(c)}"></span>${inline(c)}` : j ? workerLinks(inline(c)) : `<a class="st-step" href="#/status/s${i}">${esc(words(c))}</a>`}</td>`).join('')}</tr>`).join('')}</tbody></table></div></div></section>
    <section class="pg-sec"><h2>Failing tests and merge conflicts <span class="pg-n">${red.length}</span></h2>${red.length ? `<div class="pg-table st-prs">${tableHead('Change', 'Problem', 'Where it stands', 'For', 'What is being done')}${red.map(prRow).join('')}</div>` : '<p class="pg-none">No open pull request has failing tests or a merge conflict.</p>'}</section></div>`;
}
function sameWorker(a, b) {
  return Boolean(a && b) && a.replace(/-\d+$/, '') === b.replace(/-\d+$/, '');
}
const posts = {};
function lanePosts(lane) {
  const key = `${lane}@${Math.floor(Date.now() / 60000)}`;
  if (!(key in posts)) {
    posts[key] = posts[Object.keys(posts).findLast((k) => k.startsWith(`${lane}@`))] ?? null;
    fetch(`/api/lane/${encodeURIComponent(lane)}`, { cache: 'no-store' }).then((res) => res.json()).then((out) => { posts[key] = out; render(); });
  }
  return posts[key];
}
function workerScreen(lane) {
  const m = state.model;
  const prs = Object.entries(m.prs).filter(([, pr]) => sameWorker(pr.lane, lane) && pr.state === 'open').map(([n]) => Number(n));
  const posted = lanePosts(lane);
  const said = m.records.findLast((r) => sameWorker(r.lane, lane));
  const work = curated('work');
  const piece = [...(work?.running ?? []), ...(work?.finished ?? [])].find((w) => w.workers.some((x) => sameWorker(x, lane)));
  const helper = work?.helpers?.find((h) => h.workers.some((x) => sameWorker(x, lane)));
  return detailPage(['#/work', 'Work'], helper ? 'Keeping the drive running' : 'Worker', helper?.name ?? workerName(said?.lane ?? lane), piece ? `<p class="pg-stateline">On <a href="#/work/${encodeURIComponent(piece.item)}">${esc(piece.title)} ›</a></p>` : '', [
    ['Where it stands', helper ? esc(helper.latest) : ''],
    ['The log', m.register_log && /^register/.test(lane) ? '<a href="/register" target="_blank" rel="noopener">Open the owner request log ↗</a>' : ''],
    ['Its pull requests and what each waits on', prs.length ? `<ul class="pg-changes">${prs.map((n) => `<li class="${light(prState(n))}"><a href="#/review/${n}">${esc(prTitle(n))}</a><span>${esc(prTruth(n).blocks || STATE_WORD[prState(n)] || '')}</span></li>`).join('')}</ul>` : 'None open'],
    ['What it posted lately', !posted ? 'Reading its records…' : posted.length ? `<ul class="pg-changes">${posted.map((r) => `<li><a href="/record/${r.seq}" target="_blank" rel="noopener">${esc(SAID[r.kind] ?? KIND_WORD[r.kind] ?? r.kind)} ↗</a><span>${clock(r.at)}</span></li>`).join('')}</ul>` : 'Nothing in the last day'],
  ]);
}
function workScreen() {
  const p = curated('work');
  if (!p) return writing('Work');
  const lead = focusLane();
  const row = (w, line, side) => `<a class="wk-row${w.item === lead ? ' lead' : ''}" href="#/work/${encodeURIComponent(w.item)}"><span class="wk-body"><b>${esc(w.title)}</b><span class="wk-line">${line}</span></span><span class="wk-side">${side}</span></a>`;
  const running = [...p.running].sort((x, y) => (y.item === lead) - (x.item === lead)).map((w) => row(w, `${dotted(w.status, WORK_TONE[w.status] ?? 'acc')} <span class="wk-latest">${esc(w.latest)}</span>`, [sinceClock(w.blocked_since) ? `Waiting ${blockedFor(sinceClock(w.blocked_since))}` : sinceClock(w.started) ? `Started ${esc(w.started)}` : '', wordAt(workWord(w.item)).replace(/^l/, 'L')].filter(Boolean).join('<br>')));
  const rest = running.slice(RUNNING_AT_REST);
  const finished = p.finished.map((w) => row(w, `${w.outcome === 'stopped' ? `${dotted('Stopped', 'warn')} ` : ''}${esc(w.result)}`, `${w.outcome === 'stopped' ? 'Stopped' : 'Done'} ${esc(w.at)}`));
  const m = state.model;
  const open = m.stack.filter((row) => (m.prs[row.pr]?.state ?? 'open') === 'open');
  const stuck = open.filter((row) => row.stale);
  const moving = open.filter((row) => !row.stale);
  const helpers = p.helpers.map((h) => `<li>${h.workers.length ? `<a href="#/worker/${encodeURIComponent(h.workers[0])}"><b>${esc(h.name)}</b></a>` : `<b>${esc(h.name)}</b>`}<span>${esc(h.latest)}</span></li>`).join('');
  return `<div class="pg">${pageHead('Work', p)}
    <section class="pg-sec"><h2>Active work <span class="pg-n">${p.running.length}</span></h2><div class="pg-list wk-active">${running.slice(0, RUNNING_AT_REST).join('') || '<p class="pg-none">No work is running right now.</p>'}</div>
    ${rest.length ? fold('wk-more', `More active work <span class="pg-n">${rest.length}</span>`, `<div class="pg-list">${rest.join('')}</div>`) : ''}</section>
    <section class="pg-sec"><h2>Open pull requests <span class="pg-n">${open.length}</span></h2>${stuck.length ? `<p class="wk-caption">${dotted('Stuck', 'bad')} These have sat in one state for two hours or more.</p><div class="pg-list wk-stuck">${stuck.map(stackLine).join('')}</div>` : '<p class="pg-none">None has sat in one state for two hours.</p>'}
    ${moving.length ? fold('wk-prs', `Moving <span class="pg-n">${moving.length}</span>`, `<div class="pg-list">${moving.map(stackLine).join('')}</div>`) : ''}</section>
    ${finished.length ? `<section class="pg-sec">${fold('wk-finished', `Finished today <span class="pg-n">${finished.length}</span>`, `<div class="pg-list">${finished.join('')}</div>`)}</section>` : ''}
    ${helpers ? `<section class="pg-sec">${fold('wk-helpers', `Keeping the drive running <span class="pg-n">${p.helpers.length}</span>`, `<ul class="wk-helpers">${helpers}</ul>`)}</section>` : ''}</div>`;
}

function rowWords(row) {
  const rewrite = rewritten('rows', row.key);
  return { ...Object.fromEntries(ROW_FIELDS.map((k) => [k, (rewrite ? rewrite[k] : row[k]) ?? ''])), title: prTitle(row.number) };
}
function verdictPill(row) {
  const when = row.verdict?.at ? ` at ${clock(row.verdict.at)}` : '';
  if (verdictState(row) === 'held') return dotted(`You held it${when}`, 'warn');
  return dotted(row.ci_fix_go || boardRow(row.number)?.ci_fix_go ? 'Landing under the CI-fix exemption' : `You approved${when}`, 'ok');
}
function rowFacts(row) {
  const w = rowWords(row);
  if (row.md && !row.change) return `<div class="md">${markdown(row.md)}</div>`;
  return `<dl class="mr-f"><dt>For users</dt><dd>${esc(w.change)}</dd><dt>Plan</dt><dd>${esc(w.plan)}</dd><dt>Risk</dt><dd>${esc(w.risk)}</dd><dt>Review</dt><dd>${esc(w.review)}</dd></dl>`;
}
function waitLine(row, w) {
  const since = waitingSince(row.number);
  if (!SETTLED.test(w.blocker.trim())) return `<p class="mr-waits">${dotted('Waits on', 'warn')} ${esc(w.blocker)}${since ? `<small class="pg-for">${blockedFor(since)}</small>` : ''}</p>`;
  return verdictState(row) === 'you' && since ? `<p class="mr-waits">${dotted('Waiting on you', 'you')} ${blockedFor(since)}</p>` : '';
}
function rowActs(row) {
  const busy = pending.number === row.number && !pending.error;
  return verdictState(row) === 'you'
    ? `<button class="btn pri" data-approve="${row.number}" data-head="${esc(row.head)}"${busy ? ' disabled' : ''}>${busy ? 'Sending' : 'Approve'}</button><button class="btn" data-hold-row="${row.number}"${busy ? ' disabled' : ''}>Hold</button>`
    : verdictPill(row);
}
function changeLine(n) {
  const row = boardRow(n);
  return `<li class="${light(prState(n))}"><a href="#/review/${n}">${esc(prTitle(n))}</a><span>${esc(prTruth(n).blocks || STATE_WORD[prState(n)] || '')}</span>${row && verdictState(row) === 'you' && prState(n) !== 'landed' ? `<span class="pg-acts">${rowActs(row)}</span>` : ''}</li>`;
}
function mergeRow(row) {
  const w = rowWords(row);
  const waits = waitLine(row, w);
  const carried = row.carried?.length ? '<p class="mr-waits">Your earlier approval carries to this version; the review line names the fix.</p>' : '';
  return `<article class="mr t-${verdictState(row)}" data-peek="${row.number}"><header><a class="mr-t" href="#/review/${row.number}">${esc(w.title)}</a><span class="mr-acts">${rowActs(row)}<a class="btn ghost" href="#/review/${row.number}">Details</a></span></header>${waits}${carried}${rowFacts(row)}${pending.number === row.number && pending.error ? `<p class="mr-err">Not recorded: ${esc(pending.error)}</p>` : ''}${changeBox(row.number, row.head)}${prPeek(row.number)}</article>`;
}
const peek = { n: null, at: 0 };
for (const kind of ['pointerover', 'focusin']) {
  document.addEventListener(kind, (e) => {
    const card = e.target.closest?.('.mr[data-peek]');
    if (!card) return;
    const n = Number(card.dataset.peek);
    describe(n);
    if (peek.n !== n) Object.assign(peek, { n, at: performance.now() });
  });
}
document.addEventListener('pointerout', (e) => {
  const card = e.target.closest?.('.mr[data-peek]');
  if (card && !card.contains(e.relatedTarget)) peek.n = null;
});
function holdPeek() {
  if (peek.n !== null && performance.now() - peek.at > 350) document.querySelector(`.mr[data-peek="${peek.n}"]`)?.classList.add('held');
}
function reviewScreen(list) {
  const m = state.model;
  if (list === 'q') list = '';
  const decided = m.board.rows.filter((r) => ['approved', 'held'].includes(verdictState(r)));
  const chips = `<nav class="pg-filters"><a href="#/review" class="${list ? '' : 'on'}">Waiting on you <b>${needsTotal()}</b></a><a href="#/review/decided" class="${list === 'decided' ? 'on' : ''}">You approved or held <b>${decided.length}</b></a></nav>`;
  if (list === 'decided') return `<div class="pg">${pageHead('Review', curated('review'))}${chips}<div class="mr-list">${decided.map(mergeRow).join('') || '<p class="pg-none">Nothing you approved is still waiting to land.</p>'}</div></div>`;
  const merges = m.needs.filter((item) => item.kind === 'prs').map((g) => {
    const words = rewritten('groups', g.key);
    const relation = words ? words.relation : g.relation;
    const head = g.prs.length > 1 ? `<h3>${esc(words?.title ?? g.group)}</h3>${relation ? `<p class="pg-rel">${esc(relation)}</p>` : ''}` : '';
    return `<section class="pg-sec mr-group">${head}<div class="mr-list">${g.prs.map(mergeRow).join('')}</div></section>`;
  }).join('');
  const cards = m.needs.filter((item) => item.kind === 'card');
  const notes = m.board.cards.filter((c) => !c.decides);
  return `<div class="pg">${pageHead('Review', curated('review'))}${chips}
    ${needsTotal() ? '' : '<p class="pg-lead">Nothing waits on you. When a change or a question needs you, it shows here and in the band on Now.</p>'}
    ${needsTotal() && !merges ? '<p class="pg-rel">No change waits on your approval right now.</p>' : ''}
    ${merges}
    ${cards.length ? `<section class="pg-sec" id="review-questions"><h2>Questions</h2><div class="mr-list">${cards.map(boardCard).join('')}</div></section>` : ''}
    ${notes.length ? `<section class="pg-sec" id="review-notes"><h2>For your information</h2><div class="mr-list">${notes.map(noteCard).join('')}</div></section>` : ''}</div>`;
}
function noteCard(c) {
  return `<article class="mr bq note" id="card-${esc(c.id)}"><header><span class="mr-t">${esc(words(c.title))}</span><span class="mr-acts"><a class="btn ghost" href="${esc(state.model.board.url)}?block=${encodeURIComponent(c.id)}" target="_blank" rel="noopener">Open on your board</a></span></header>${c.summary ? `<p class="mr-why">${esc(words(c.summary))}</p>` : ''}</article>`;
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
