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
    return { state: s.state, row: tableRow(`m${i}`, [`<b>${esc(name)}</b>`, dotted(s.label, STOP_TONE[s.state]), waits]) };
  });
  const foldedStops = (id, stateName, label, tone, say) => {
    const stops = milestones.filter((s) => s.state === stateName);
    return stops.length ? rowFold(id, [`<b>${esc(say(stops.length))}</b>`, dotted(label, tone), '<span></span>'], stops.map((s) => s.row)) : '';
  };
  const clipped = (text, since) => (text ? `<span>${esc(firstClause(text))}${sinceClock(since) ? `<small class="pg-for">${spanFor(sinceClock(since))}</small>` : ''}</span>` : '<span></span>');
  const partRow = (part) => tableRow(part.id, [`<b>${esc(part.name)}</b>`, dotted(part.label, PART_TONE[part.state]), clipped(part.blocks, part.since), clipped(part.doing)]);
  const open = p.parts.filter((part) => part.state !== 'done' && part.state !== 'later');
  const later = p.parts.filter((part) => part.state === 'later');
  const done = p.parts.filter((part) => part.state === 'done');
  return `<div class="pg">${pageHead('Plan', p, ring(p.parts.filter((x) => x.state === 'done').length, p.parts.length, 'parts done'))}<p class="pg-lead">${esc(p.summary)}</p>
    <section class="pg-sec"><h2>${esc(state.model.milestones_label)}</h2><div class="pg-table pl-ms">${tableHead('Milestone', 'State', 'What it waits on')}${foldedStops('pl-live', 'live', 'Live', 'ok', (n) => `${n} milestones live`)}${milestones.filter((s) => s.state !== 'live' && s.state !== 'not').map((s) => s.row).join('')}${foldedStops('pl-later', 'not', 'Later', '', (n) => `${n} milestones later`)}</div></section>
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
function statusStep(row) {
  const { head } = statusRows();
  return `<div class="pg pg-page"><div class="kick muted">Plan step</div><h1 class="pg-title">${esc(words(row[0]))}</h1><dl class="pg-facts">${row.slice(1).map((c, i) => `<dt>${esc(head[i + 1] ?? '')}</dt><dd>${workerLinks(inline(c))}</dd>`).join('')}</dl></div>`;
}
const STATUS_STATES = [['', 'Every state'], ['you', 'Needs you'], ['bad', 'Stuck'], ['acc', 'Moving'], ['ok', 'Done']];
const OWNER_HEAD = ['Step', 'What it delivers', 'State', 'What blocks it', 'What we are doing to unblock it', 'What went wrong so far'];
function rowTone(row) {
  if (/^(done|live|landed)\b/i.test(row.state)) return 'ok';
  if (/\b(your|you)\b/i.test(row.blocks) || /^waits on your?\b/i.test(row.state)) return 'you';
  if (/\b(nobody|no one|stuck|fails?|failing|broken)\b/i.test(`${row.step} ${row.state} ${row.blocks}`)) return 'bad';
  return 'acc';
}
function statusTables() {
  const plan = curated('plan');
  const tone = { done: 'ok', doing: 'acc', you: 'you', stuck: 'bad', waiting: 'acc', later: '' };
  const parts = (plan?.parts ?? []).map((part) => {
    const said = state.model.step_records?.[part.id] ?? {};
    return { step: part.name, delivers: part.delivers, state: part.label, blocks: part.blocks, doing: part.doing, wrong: said.wrong, prs: said.prs, lanes: said.lanes, tone: tone[part.state] };
  });
  return [{ id: 'plan', title: 'The plan', as_of: plan ? clock(plan.at) : '', short: plan?.summary, rows: parts }, ...(state.model.status_tables ?? []).map((table) => ({ ...table, rows: table.rows.map((row) => ({ ...row, tone: rowTone(row) })) }))];
}
function ownerRow(tableId, row, i) {
  const id = `st-${tableId}-${i}`;
  const said = (text) => (/^(nothing|none needed|not needed)\b/i.test(text ?? '') ? '' : firstClause(text));
  const cell = (text) => `<span class="st-clip">${esc(said(text))}</span>`;
  const facts = OWNER_HEAD.slice(1).map((label, j) => [label, [row.delivers, row.state, row.blocks, row.doing, row.wrong][j]]).filter(([, text]) => text);
  const records = [...(row.prs ?? []).map((n) => `<a href="#/review/${n}">${esc(plainTitle(n))}</a>`), ...(row.lanes ?? []).map((lane) => {
    const word = state.model.records.findLast((r) => sameWorker(r.lane, lane));
    return word ? wordLine(word) : esc(workerName(lane));
  })];
  return `<details class="pg-rows" data-id="${esc(id)}"${state.open.has(id) ? ' open' : ''}><summary class="pg-row"><span class="pg-cells"><b>${esc(row.step)}</b>${cell(row.delivers)}<span class="st-clip">${dotted(said(row.state).replace(/^(.{44}\S*)\s.+$/, '$1…'), row.tone)}</span>${cell(row.blocks)}${cell(row.doing)}${cell(row.wrong)}</span></summary><dl class="pg-facts st-full">${facts.map(([label, text]) => `<dt>${esc(label)}</dt><dd>${inline(text)}</dd>`).join('')}${records.length ? `<dt>Records</dt><dd>${records.join('<br>')}</dd>` : ''}</dl></details>`;
}
function ownerTable(table, tone) {
  const rows = table.rows.map((row, i) => ({ row, i })).filter(({ row }) => !tone || row.tone === tone);
  if (!rows.length) return '';
  const done = tone ? [] : rows.filter(({ row }) => row.tone === 'ok');
  const open = rows.filter((x) => !done.includes(x));
  const draw = ({ row, i }) => ownerRow(table.id, row, i);
  const empty = OWNER_HEAD.slice(2).map(() => '<span></span>').join('');
  return `<section class="pg-sec" id="status-${esc(table.id)}"><h2><a href="#/status/${esc(table.id)}">${esc(table.title)}</a> <span class="pg-n">${table.rows.length}</span></h2>${table.short ? `<p class="pg-rel">${esc(table.short)}${table.as_of ? ` <span class="pg-at">As of ${esc(table.as_of)}.</span>` : ''}</p>` : ''}<div class="pg-table st-six">${tableHead(...OWNER_HEAD)}${open.map(draw).join('')}${done.length ? rowFold(`st-done-${table.id}`, [`<b>${done.length} done</b>`, `<span></span>${empty}`], done.map(draw)) : ''}</div></section>`;
}
function statusScreen(feature = 'all', tone = '') {
  const m = state.model;
  const tables = statusTables();
  const shown = tables.filter((table) => feature === 'all' || table.id === feature);
  const count = (table) => (tone ? table.rows.filter((row) => row.tone === tone).length : table.rows.length);
  const features = `<nav class="pg-filters">${[{ id: 'all', title: 'Everything', rows: tables.flatMap((table) => table.rows) }, ...tables].map((table) => `<a href="#/status/${esc(table.id)}${tone ? `/${tone}` : ''}" class="${table.id === feature ? 'on' : ''}">${esc(table.title)} <b>${count(table)}</b></a>`).join('')}</nav>`;
  const states = `<nav class="pg-filters st-states">${STATUS_STATES.map(([id, label]) => `<a href="#/status/${esc(feature)}${id ? `/${id}` : ''}" class="${id === tone ? 'on' : ''}">${label}</a>`).join('')}</nav>`;
  const red = Object.entries(m.prs).filter(([, pr]) => pr.state === 'open' && (pr.test_state === 'failure' || pr.mergeable_state === 'dirty')).map(([n, pr]) => ({ n, pr, t: prTruth(n) }));
  const problem = (pr) => [pr.mergeable_state === 'dirty' ? 'Merge conflict with its base' : '', pr.test_state === 'failure' ? (pr.failing_checks ?? []).some((c) => /release-pr-check/.test(c.name)) ? 'Tests and the release check fail' : 'Tests fail' : ''].filter(Boolean).join('; ');
  const prRow = ({ n, pr, t }) => `<a class="pg-row" href="#/review/${n}"><span class="pg-cells"><b>${esc(plainTitle(n))}</b><span>${dotted(problem(pr), 'bad')}</span><span>${esc(firstClause(t.blocks))}</span><span class="st-for">${t.since ? spanFor(t.since) : ''}</span><span>${esc(firstClause(t.next))}</span></span></a>`;
  const unowned = red.filter(({ t }) => /no live worker/i.test(t.next));
  const held = red.filter((x) => !unowned.includes(x) && /\b(hold|holds|held)\b/i.test(`${x.t.blocks} ${x.t.next}`));
  const fixing = red.filter((x) => !unowned.includes(x) && !held.includes(x));
  const redFold = (id, list, say, tint, word) => (list.length ? rowFold(id, [`<b>${esc(say(list.length))}</b>`, `<span>${dotted(word, tint)}</span>`, '<span></span>', '<span></span>', '<span></span>'], list.map(prRow)) : '');
  const reds = feature === 'all' && !tone && red.length ? `<section class="pg-sec"><h2>Failing tests and merge conflicts <span class="pg-n">${red.length}</span></h2><div class="pg-table st-prs">${tableHead('Change', 'Problem', 'Where it stands', 'For', 'What is being done')}${unowned.map(prRow).join('')}${redFold('st-fixing', fixing, (n) => `${n} being fixed by their workers`, 'acc', 'Being fixed')}${redFold('st-held', held, (n) => `${n} held until what they depend on is live`, '', 'Held')}</div></section>` : '';
  return `<div class="pg">${pageHead('Status')}${features}${states}${shown.map((table) => ownerTable(table, tone)).join('')}${reds}</div>`;
}
function sameWorker(a, b) {
  return Boolean(a && b) && a.replace(/-\d+$/, '') === b.replace(/-\d+$/, '');
}
const posts = {};
function lanePosts(lane) {
  const key = `${lane}@${Math.floor(Date.now() / 60000)}`;
  if (!(key in posts)) {
    posts[key] = posts[Object.keys(posts).findLast((k) => k.startsWith(`${lane}@`))] ?? null;
    fetch(`/api/lane/${encodeURIComponent(lane)}`, { cache: 'no-store' }).then((res) => res.json()).then((out) => { posts[key] = out; refresh(); });
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
const PR_GROUPS = [
  [/^held/, 'held by the coordinator', 'Held', ''],
  [/^waits on #/, 'wait for the change below them', 'Waiting', ''],
  [/^red on /, 'have failing tests', 'Tests fail', 'bad'],
  [/^conflicts/, 'conflict with their base', 'Conflict', 'bad'],
  [/^green, and no go/, 'pass but this version is not approved', 'Not approved', 'warn'],
  [/^green with a go/, 'approved and passing, not in the queue', 'Not queued', 'warn'],
  [/owner's tap/, 'wait on your approval', 'Needs you', 'you'],
];
function prGroup(text) {
  const hit = PR_GROUPS.find(([pattern]) => pattern.test(text));
  return hit ? hit.slice(1) : [ledgerWords(text), 'Moving', 'acc'];
}
const ACK = { received: ['Received', 'you'], routed: ['Routed', 'acc'], done: ['Done', 'ok'] };
const OVERDUE_GROUP = {
  pick: (n) => `${n} of your picks not carried out`,
  ask: (n) => `${plural(n, 'ask')} with no answer`,
  retro: (n) => `${plural(n, 'retro action')} not done`,
  finding: (n) => `${plural(n, 'finding')} not closed`,
  repeat: (n) => plural(n, 'repeating alarm'),
};
function askTitle(text) {
  return firstClause(text.replace(/https?:\/\/\S+/g, '').trim() || 'A message with only a link').replace(/^(.{96}\S*)\s.+$/, '$1…');
}
function receivedLine(ask) {
  return new Date(ask.due) <= Date.now() ? `Past due: the answer was due by ${clock(ask.due)}` : `Received, with ${ask.holder}, answer by ${clock(ask.due)}`;
}
function receivedTone(ask) {
  return new Date(ask.due) <= Date.now() ? 'bad' : 'you';
}
function overdueRows() {
  const clipped = (item) => esc(item.text.replace(/^(.{120}\S*)\s.+$/s, '$1…'));
  const named = (item) => `<li><time>${esc(item.due)}</time><span>${item.seq ? `<a href="/record/${item.seq}" target="_blank" rel="noopener">${clipped(item)}</a>` : clipped(item)}</span></li>`;
  const groups = new Map(groupBy(state.model.coordinator_overdue ?? [], (item) => item.group));
  return Object.keys(OVERDUE_GROUP).filter((group) => groups.has(group)).map((group) => {
    const list = groups.get(group);
    return { id: `overdue-${group}`, at: list[0].due, title: OVERDUE_GROUP[group](list.length), word: 'Overdue', tone: 'bad', side: '', more: `<ol class="ev-named">${list.map(named).join('')}</ol>` };
  });
}
function workScreen() {
  const p = curated('work');
  if (!p) return writing('Work');
  const lead = focusLane();
  const row = (w, line, side) => `<a class="wk-row${w.item === lead ? ' lead' : ''}" href="#/work/${encodeURIComponent(w.item)}"><span class="wk-body"><b>${esc(w.title)}</b><span class="wk-line">${line}</span></span><span class="wk-side">${side}</span></a>`;
  const open = state.model.stack.filter((x) => (state.model.prs[x.pr]?.state ?? 'open') === 'open');
  const running = p.running.filter((w) => w.item === lead).map((w) => row(w, `${dotted(w.status, WORK_TONE[w.status] ?? 'acc')} <span class="wk-latest">${esc(w.latest)}</span>`, [sinceClock(w.blocked_since) ? `Waiting ${blockedFor(sinceClock(w.blocked_since))}` : sinceClock(w.started) ? `Started ${esc(w.started)}` : '', wordAt(workWord(w.item)).replace(/^l/, 'L')].filter(Boolean).join('<br>')));
  const byStatus = groupBy(p.running.filter((w) => w.item !== lead), (w) => w.status).map(([status, list]) => {
    const latest = list.map((w) => ({ w, word: workWord(w.item) })).sort((x, y) => new Date(y.word?.at ?? 0) - new Date(x.word?.at ?? 0))[0];
    return { id: `wk-${status}`, iso: latest.word?.at ?? 0, at: latest.word ? clock(latest.word.at) : '', word: status, tone: WORK_TONE[status] ?? 'acc', title: list.length === 1 ? list[0].title : `${list.length} ${status.toLowerCase()}`, why: list.length === 1 ? '' : `latest: ${latest.w.title}`, side: list.length === 1 ? '' : countPill(list.length), more: `<div class="pg-list">${list.map((w) => row(w, `${dotted(w.status, WORK_TONE[w.status] ?? 'acc')} <span class="wk-latest">${esc(w.latest)}</span>`, wordAt(workWord(w.item)).replace(/^l/, 'L'))).join('')}</div>` };
  });
  const prRows = groupBy(open, (x) => prGroup(x.state)[0]).map(([label, list]) => {
    const [, word, tone] = prGroup(list[0].state);
    const dated = [...list].sort((x, y) => new Date(y.since) - new Date(x.since));
    return { id: `wk-pr-${label}`, iso: dated[0].since, at: clock(dated[0].since), word, tone, title: list.length === 1 ? plainTitle(list[0].pr) : `${list.length} ${label}`, side: list.length === 1 ? '' : `oldest ${spanFor(dated.at(-1).since).replace(/^for /, '')} ${countPill(list.length)}`, more: `<div class="pg-list">${list.map(stackLine).join('')}</div>` };
  });
  const finished = p.finished.map((w) => row(w, `${w.outcome === 'stopped' ? `${dotted('Stopped', 'warn')} ` : ''}${esc(w.result)}`, `${w.outcome === 'stopped' ? 'Stopped' : 'Done'} ${esc(w.at)}`));
  const m = state.model;
  const helpers = p.helpers.map((h) => `<li>${h.workers.length ? `<a href="#/worker/${encodeURIComponent(h.workers[0])}"><b>${esc(h.name)}</b></a>` : `<b>${esc(h.name)}</b>`}<span>${esc(h.latest)}</span></li>`).join('');
  return `<div class="pg">${pageHead('Work', p)}
    ${p.running.length ? `<section class="pg-sec"><h2>Active work <span class="pg-n">${p.running.length}</span></h2>${running.length ? `<div class="pg-list wk-active">${running.join('')}</div>` : ''}${eventList(newestFirst(byStatus))}</section>` : ''}
    ${open.length ? `<section class="pg-sec"><h2>Open pull requests <span class="pg-n">${open.length}</span></h2>${eventList(newestFirst(prRows))}</section>` : ''}
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
  if (verdictState(row) !== 'you') return '';
  if (awaitingReply(row.number)) {
    const ask = changeAsks(row.number).at(-1);
    return `<p class="mr-waits">${ask.due ? dotted(receivedLine(ask), receivedTone(ask)) : dotted('Waiting on the reply to your question', 'warn')} It returns to your queue when the answer lands.</p>`;
  }
  const checked = row.checked_at && row.head.slice(0, 7) === row.checked_at.slice(0, 7) ? ' Reviewed and checked; it lands on your tap.' : '';
  return since || checked ? `<p class="mr-waits">${dotted('Waiting on you', 'you')}${checked} ${since ? blockedFor(since) : ''}</p>` : '';
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
  const decided = m.board.rows.filter((r) => ['approved', 'held'].includes(verdictState(r)));
  const chips = `<nav class="pg-filters"><a href="#/review" class="${list ? '' : 'on'}">Waiting on you <b>${prsWaiting()}</b></a><a href="#/review/decided" class="${list === 'decided' ? 'on' : ''}">You approved or held <b>${decided.length}</b></a></nav>`;
  if (list === 'decided') return `<div class="pg">${pageHead('Review', curated('review'))}${chips}<div class="mr-list">${decided.map(mergeRow).join('') || '<p class="pg-none">Nothing you approved is still waiting to land.</p>'}</div></div>`;
  const merges = m.needs.filter((item) => item.kind === 'prs').map((g) => {
    const words = rewritten('groups', g.key);
    const relation = words ? words.relation : g.relation;
    const head = g.prs.length > 1 ? `<h3>${esc(words?.title ?? g.group)}</h3>${relation ? `<p class="pg-rel">${esc(relation)}</p>` : ''}` : '';
    return `<section class="pg-sec mr-group">${head}<div class="mr-list">${g.prs.map(mergeRow).join('')}</div></section>`;
  }).join('');
  return `<div class="pg">${pageHead('Review', curated('review'))}${chips}
    ${merges ? '' : '<p class="pg-lead">No change waits on your approval. When one does, it shows here and in the band on Now.</p>'}
    ${merges}</div>`;
}
function noteCard(c) {
  return `<article class="mr bq note" id="card-${esc(c.id)}"><header><span class="mr-t">${esc(words(c.title))}</span></header>${c.summary ? `<p class="mr-why">${esc(words(c.summary))}</p>` : ''}${c.parts.map(cardPart).join('')}${cardAsk(c)}</article>`;
}

function firstClause(text) {
  const t = String(text ?? '').trim();
  return /^(.{14,}?)(?:[.;:](?:\s|$)|,\s)/.exec(t)?.[1] ?? t;
}
function plainTitle(n) {
  return prTitle(n).replace(/^[\w./-]+:\s*(?:\p{Extended_Pictographic}\S*\s*)?/u, '').replace(/^./, (c) => c.toUpperCase());
}
function countPill(n) {
  return `<span class="pg-n">${n}</span>`;
}
function eventRow(e) {
  const line = `<time>${esc(e.at)}</time>${dotted(e.word, e.tone)}<b>${esc(e.title)}${e.why ? `<span class="ev-why">${esc(e.why)}</span>` : ''}</b><span class="ev-side">${e.side ?? ''}</span>`;
  return e.more
    ? `<li><details data-id="${esc(e.id)}"${state.open.has(e.id) ? ' open' : ''}><summary>${line}</summary><div class="ev-more">${e.more}</div></details></li>`
    : `<li><div class="hs-line">${line}</div></li>`;
}
function eventList(rows) {
  return `<ol class="hs ev-rows">${rows.map(eventRow).join('')}</ol>`;
}
function newestFirst(rows) {
  return rows.sort((a, b) => new Date(b.iso) - new Date(a.iso));
}
function groupBy(list, key) {
  const groups = new Map();
  for (const item of list) groups.set(key(item), [...(groups.get(key(item)) ?? []), item]);
  return [...groups];
}
function rowFold(id, cells, rows) {
  return `<details class="pg-rows" data-id="${esc(id)}"${state.open.has(id) ? ' open' : ''}><summary class="pg-row"><span class="pg-cells">${cells.join('')}</span></summary>${rows.join('')}</details>`;
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

const TAP_WORD = { land: 'Land it', pick: 'Your pick', slack: 'Slack post' };
const OTHERS_INLINE = 2;
function tapWords(text) {
  return plain(text).replace(/\s*\(?#\d{4,5}\)?,?/g, '');
}
function tapCard(t) {
  return t.subject ? t : state.model.board.cards.find((c) => c.id === t.card);
}
function tapOpen(t) {
  if (t.kind === 'land') return verdictState(boardRow(t.number) ?? {}) === 'you';
  return Boolean(tapCard(t)) && !ov.sent.get(t.choice)?.recorded;
}
function openTaps() {
  return (state.model?.taps ?? []).filter(tapOpen);
}
function tapButtons(t) {
  if (t.kind === 'land') {
    const failed = pending.number === t.number && pending.error ? `<em class="nsent bad">Not recorded: ${esc(pending.error)}. Try again.</em>` : '';
    return `${rowActs(boardRow(t.number))}<a class="btn ghost" href="#/review/${t.number}">Details</a>${failed}`;
  }
  const c = tapCard(t);
  const question = `#/question/${encodeURIComponent(c.id)}`;
  if (!c.choice) return `<a class="btn pri" href="${question}">Answer it</a>`;
  const busy = sending(c.choice) ? ' disabled' : '';
  const subject = t.subject ? ` data-subject="${esc(t.subject)}"` : '';
  const button = (o) => `<button class="btn${o.recommended ? ' pri' : ''}" data-choose="${esc(o.id)}" data-choice="${esc(c.choice)}"${subject}${busy}>${esc(tapWords(o.label.replace(/\s*\(Recommended\)\s*$/i, '')))}</button>`;
  const lead = c.options.filter((o) => o.recommended);
  const others = c.options.filter((o) => !o.recommended);
  const openId = `tap-more-${c.id}`;
  const rest = lead.length && others.length > OTHERS_INLINE
    ? `<details class="bq-more" data-id="${esc(openId)}"${state.open.has(openId) ? ' open' : ''}><summary>Other answers</summary><div class="fm-acts">${others.map(button).join('')}</div></details>`
    : others.map(button).join('');
  return `${lead.map(button).join('')}${rest}${t.subject ? '' : `<a class="btn ghost" href="${question}">Details</a>`}${sentNote(c.choice)}`;
}
function tapRow(t, at) {
  const textId = `tap-text-${t.id}`;
  return `<article class="fm${t.urgent ? ' bad' : t.frees ? ' you' : ''}" data-tap="${esc(t.id)}"><span class="fm-icon">${at + 1}</span><div class="fm-main">
    <header><b>${TAP_WORD[t.kind]}</b>${t.frees ? `<span class="fm-frees">Frees ${t.frees}</span>` : ''}</header>
    <p class="fm-lead">${esc(tapWords(t.title))}</p>
    <p class="fm-why">${esc(tapWords(t.line))}</p>
    <div class="fm-acts">${tapButtons(t)}</div>
    ${t.text ? `<details class="fm-thread" data-id="${esc(textId)}"${state.open.has(textId) ? ' open' : ''}><summary>Read the post</summary><div class="fm-open fm-post">${esc(t.text)}</div></details>` : ''}</div></article>`;
}
function feedDivider(word) {
  return `<h2 class="fd-div"><span>${word}</span></h2>`;
}
function needsScreen() {
  const taps = openTaps();
  const incidents = taps.filter((t) => t.urgent);
  const freeing = taps.filter((t) => !t.urgent && t.frees);
  const rest = taps.filter((t) => !t.urgent && !t.frees);
  const lead = taps.length ? `${plural(taps.length, 'tap')} wait on you, the most downstream work first.` : 'Nothing waits on your tap.';
  const notes = state.model.board.cards.filter((c) => !c.decides);
  return `<div class="pg fd"><header class="fd-h"><h1>${lead}</h1></header>
    ${incidents.length ? `${feedDivider('Live incidents')}${incidents.map(tapRow).join('')}` : ''}
    ${freeing.length ? `${feedDivider('These free other work')}${freeing.map((t, at) => tapRow(t, at + incidents.length)).join('')}` : ''}
    ${rest.length ? `${feedDivider('Nothing recorded waits on these')}${rest.map((t, at) => tapRow(t, at + incidents.length + freeing.length)).join('')}` : ''}
    ${notes.length ? `${feedDivider('For your information')}<div class="mr-list">${notes.map(noteCard).join('')}</div>` : ''}</div>`;
}
document.addEventListener('click', (e) => {
  const row = e.target.closest('.fm[data-tap], .nl');
  if (!row || e.target.closest('a, button, summary, details, form') || String(getSelection())) return;
  row.querySelector('a[href*="question/"], a[href*="review/"]')?.click();
});

const REPLY_WORD = { info: ['Note', ''], progress: ['Progress', 'acc'], needs_you: ['Needs you', 'you'], incident: ['Incident', 'bad'], done: ['Done', 'ok'] };
const SURFACE_WORD = { board: 'Board', talk: 'Talk', slack: 'Slack', record: 'Record' };
const CHAT_AT_REST = 60;
const SIMPLE_PICK = 50;
const BARE_ASK_MINUTES = 60;
const collate = new Intl.Collator('en', { numeric: true, sensitivity: 'base' }).compare;
const chatDraft = (on) => `draft:chat:${on}:`;
const rp = { sort: new Map(), sent: [], failed: new Map(), threads: new Set(Object.keys(localStorage).flatMap((key) => /^draft:chat:(.+):$/.exec(key)?.[1] ?? [])), limit: CHAT_AT_REST, bottom: true, anchor: null, focus: null, box: null, modal: null };
const NOISE = [
  [/<!--[\s\S]*?-->/g, ''],
  [/\s*\([^()]*\b(?:records?|owner table|card-[a-z0-9-]+)\b[^()]*\)/gi, ''],
  [/\brecords?\s+#*\d[\dx]*\b/gi, ''],
  [/\bowner table\s+[0-9a-f]{6,}\b/gi, ''],
  [/\bcard-[a-z0-9-]+\b/g, 'the card'],
  [/#+\d{6}\b|#\d{3}xxx\b/g, ''],
  [/\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b/g, ''],
  [/\b[\w.-]+(?:\/[\w.-]+)?#\d+\b/g, 'a tooling change'],
];
function plain(text) {
  const workers = (state.model?.lanes ?? []).map((row) => row.lane).filter((lane) => lane && /-/.test(lane) && /\d/.test(lane));
  const scrubbed = NOISE.reduce((out, [pattern, word]) => out.replace(pattern, word), text);
  return workers.reduce((out, lane) => out.replaceAll(lane, 'a worker'), scrubbed).replace(/ +([,.;:])/g, '$1').replace(/ {2,}/g, ' ');
}
function changeWords(n) {
  return state.model.prs[n] || boardRow(n) ? plainTitle(n).replace(/[[\]|]/g, '') : 'this change';
}
function namedChanges(md) {
  return md.replace(/#(\d{5})\b/g, (_, n) => `[${changeWords(n)}](https://github.com/${state.model.repo}/pull/${n})`);
}
function replyCard(r) {
  return state.model.board.cards.find((c) => c.id === `card-${r.id}`);
}
function openReplies() {
  return state.model?.replies ? state.model.replies.filter((entry) => bareAskOpen(entry, false)).length : 0;
}
function sortableTable(key, lines) {
  const cells = (line) => line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  const [head, ...rows] = lines.filter((line) => !/^\s*\|[\s:|-]+\|\s*$/.test(line)).map(cells);
  const sort = rp.sort.get(key);
  const shown = sort ? [...rows].sort((a, b) => sort.dir * collate(a[sort.col] ?? '', b[sort.col] ?? '')) : rows;
  const order = (i) => (sort?.col === i ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none');
  return `<div class="tablewrap"><table class="rp-table"><thead><tr>${head.map((c, i) => `<th aria-sort="${order(i)}"><button class="rp-sort" data-sort="${esc(key)}" data-col="${i}">${inline(c)}</button></th>`).join('')}</tr></thead><tbody>${shown.map((r) => `<tr>${r.map((c, i) => `<td data-h="${esc(head[i] ?? '')}">${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function replyMarkdown(id, md) {
  const out = [];
  let prose = [];
  let table = null;
  let fenced = false;
  const flush = () => {
    if (prose.length) out.push(markdown(prose.join('\n')));
    prose = [];
  };
  const close = () => {
    if (table) out.push(sortableTable(`${id}:${out.length}`, table));
    table = null;
  };
  for (const line of md.split('\n')) {
    if (/^\s*```/.test(line)) fenced = !fenced;
    if (!fenced && /^\s*\|/.test(line)) {
      flush();
      (table ??= []).push(line);
      continue;
    }
    close();
    prose.push(line);
  }
  close();
  flush();
  return out.join('');
}
function replyPR(url) {
  const [, repo, n] = /github\.com\/([\w.-]+\/[\w.-]+)\/pull\/(\d+)$/.exec(url);
  return `<a class="prc" href="${esc(url)}" target="_blank" rel="noopener">${repo === state.model.repo ? esc(changeWords(n)) : 'A tooling change'} ↗</a>`;
}
function liveChatBox(on) {
  return $('#main').querySelector(`[name="chat"][data-draft="${CSS.escape(on)}"]`);
}
function chatBox(on, about, placeholder) {
  const failed = rp.failed.get(on);
  return `${failed ? `<p class="ct-failed">Not sent: ${esc(failed)}. Try again.</p>` : ''}<form class="ct-box" data-chat-send="${esc(on)}" data-about="${esc(about)}"><textarea name="chat" data-draft="${esc(on)}" rows="1" placeholder="${placeholder}"></textarea><button class="ct-send" aria-label="Send"><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 13V3M3.5 7.5 8 3l4.5 4.5"/></svg></button></form>`;
}
function chatEntries() {
  const m = state.model;
  const rowOf = (line) => m.owner_asks.messages.find((row) => row.text.includes(line.text.trim()));
  const typedHere = new Set(m.chat_lines.map((line) => rowOf(line)?.m));
  const told = m.replies.map((entry) => entry.reply.md).join('\n');
  const firstAnswer = new Map(m.replies.filter((entry) => entry.reply.order).map((entry) => [entry.reply.order, entry.at]));
  const above = (row) => (firstAnswer.has(`m${row?.m}`) ? new Date(new Date(firstAnswer.get(`m${row.m}`)) - 1).toISOString() : undefined);
  const asks = m.owner_asks.messages.filter((row) => !typedHere.has(row.m)).flatMap((row) => [
    { key: `line:m${row.m}`, on: '', at: row.iso, sort: above(row), you: true, text: row.text, row, about: `his line m${row.m} "${row.text.slice(0, 80).replaceAll('\n', ' ')}"` },
    ...(row.answer && !told.includes(row.answer.slice(0, 40)) ? [{ key: `answer:m${row.m}`, on: '', at: row.iso, answer: row.answer, about: `your answer to his line m${row.m}` }] : []),
  ]);
  const taps = m.owner_asks.taps.filter((tap) => !tap.pr && !m.board.answered[tap.card]).map((tap) => ({ key: `tap:m${tap.m}:${tap.card}`, on: '', at: tap.iso, tap }));
  const typed = m.chat_lines.map((line) => ({ key: `seq:${line.seq}`, on: line.on, at: line.at, sort: above(rowOf(line)), you: true, text: line.text, row: rowOf(line), about: `his line #${line.seq} "${line.text.slice(0, 80).replaceAll('\n', ' ')}"` }));
  const landed = new Set(m.chat_lines.map((line) => line.seq));
  const waiting = rp.sent.filter((sent) => !landed.has(sent.seq)).map((sent) => ({ key: `sent:${sent.at}`, on: sent.on, at: sent.at, you: true, text: sent.text, sending: true }));
  const replies = m.replies.map((entry) => ({ key: `reply:${entry.reply.id}`, on: entry.reply.on ?? '', at: entry.at, entry, about: `reply ${entry.reply.id} "${entry.reply.title}"` }));
  return [...replies, ...asks, ...taps, ...typed, ...waiting].sort((a, b) => new Date(a.sort ?? a.at) - new Date(b.sort ?? b.at));
}
function bareAskOpen(entry, answeredInThread) {
  return entry.reply.status === 'needs_you' && !entry.reply.picks?.length && !answeredInThread && minutesSince(entry.at) < BARE_ASK_MINUTES;
}
function chatAction(entry, answeredInThread) {
  const r = entry.reply;
  const key = `reply:${r.id}`;
  const about = `reply ${r.id} "${r.title}"`;
  const picked = state.model.board.answered[`card-${r.id}`];
  if (picked) return `<p class="ct-picked">You picked <b>${esc(picked.picked)}</b></p>`;
  const card = replyCard(r);
  if (card) {
    if (!card.choice || card.options.length > 2 || card.options.some((o) => o.label.length > SIMPLE_PICK)) return `<div class="ct-acts"><button class="btn pri" data-chat-modal="${esc(card.id)}">Open the pick</button></div>`;
    const busy = sending(card.choice) ? ' disabled' : '';
    return `<div class="ct-acts">${card.options.map((o) => `<button class="btn${o.recommended ? ' pri' : ''}" data-choose="${esc(o.id)}" data-choice="${esc(card.choice)}"${busy}>${esc(o.label.replace(/\s*\(Recommended\)\s*$/i, ''))}</button>`).join('')}</div>${sentNote(card.choice)}`;
  }
  if (r.picks?.length) return '<p class="ct-closed">This pick is no longer open.</p>';
  if (bareAskOpen(entry, answeredInThread)) return `<div class="ct-acts"><button class="btn pri" data-chat-done="${esc(key)}" data-about="${esc(about)}">Done</button></div>`;
  return '';
}
function chatFlag(entry, answeredInThread) {
  const r = entry.reply;
  if (r.status === 'incident') return '<span class="ct-flag bad">Incident</span>';
  const open = replyCard(r) ? !state.model.board.answered[`card-${r.id}`] : bareAskOpen(entry, answeredInThread);
  return open ? '<span class="ct-flag you">Needs you</span>' : '';
}
function chatTheirs(item, kids) {
  const entry = item.entry;
  const r = entry.reply;
  const answeredInThread = kids.some((kid) => kid.you);
  const failed = entry.surfaces.filter((s) => s.error).map((s) => `<li class="bad">${SURFACE_WORD[s.surface]} failed: ${esc(s.error)}</li>`).join('');
  return `<div class="ct-from">Communicator <time>${clock(item.at)}</time>${chatFlag(entry, answeredInThread)}</div>
    <h3 class="ct-title">${esc(plain(r.title).replace(/#\d{4,5}\b,? ?/g, ''))}</h3>
    <div class="rp-md prose">${replyMarkdown(r.id, plain(namedChanges(r.md)))}</div>
    ${chatAction(entry, answeredInThread)}
    ${r.links?.length ? `<ul class="rp-links">${r.links.map((l) => `<li><a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(plain(l.label).replace(/^#(\d{5})$/, (_, n) => changeWords(n)))} ↗</a></li>`).join('')}</ul>` : ''}
    ${r.prs?.length ? `<p class="rp-prs">${r.prs.map(replyPR).join(' ')}</p>` : ''}
    ${failed ? `<ul class="rp-went">${failed}</ul>` : ''}`;
}
function lineState(row) {
  return row?.due ? receivedLine(row) : ACK[row?.ack ?? 'received'][0];
}
function chatYours(item) {
  const words = item.text.replace(/\[Image #\d+\]/g, ' ').trim();
  const title = item.row?.title ?? askTitle(words);
  const shot = /\[Image #\d+\]/.test(item.text) ? '<span class="ct-shot">Screenshot</span>' : '';
  const id = `said-${item.key}`;
  const whole = title === words ? '' : `<details class="ct-whole" data-id="${esc(id)}"${state.open.has(id) ? ' open' : ''}><summary>Whole message</summary>${esc(item.text)}</details>`;
  return `<div class="ct-bubble">${esc(title)}${shot ? ` ${shot}` : ''}${whole}</div><div class="ct-from"><time>${item.sending ? 'Sending…' : `${clock(item.at)} · ${lineState(item.row)}`}</time></div>`;
}
function chatBody(item, kids) {
  if (item.entry) return chatTheirs(item, kids);
  if (item.answer) return `<div class="ct-from">Communicator <time>${clock(item.at)}</time></div><div class="rp-md prose">${markdown(plain(namedChanges(item.answer)))}</div>`;
  return chatYours(item);
}
function chatWaiting(after) {
  return new Date(after.at) > new Date(state.model.replies[0]?.at ?? 0) ? '<p class="ct-wait">Waiting for the communicator<i></i><i></i><i></i></p>' : '';
}
function chatThread(item, kids) {
  const open = rp.threads.has(item.key);
  const replies = kids.length === 1 ? '1 reply' : `${kids.length} replies`;
  const toggle = `<button class="ct-link" data-chat-thread="${esc(item.key)}">${open ? 'Hide thread' : kids.length ? replies : 'Reply'}</button>`;
  if (!open) return `<div class="ct-tools${kids.length ? ' has' : ''}">${toggle}</div>`;
  const last = kids.at(-1);
  return `<div class="ct-tools has">${toggle}</div><div class="ct-thread">${kids.map((kid) => `<article class="ct-turn ${kid.you ? 'ct-you' : 'ct-them'}">${chatBody(kid, [])}</article>`).join('')}${last?.you ? chatWaiting(last) : ''}${chatBox(item.key, item.about, 'Reply in thread')}</div>`;
}
function chatTurn(item, kids) {
  if (item.tap) return `<p class="ct-tap">You answered ${esc(askTitle(plain(item.tap.question).replace(/^Owner on board,?\s*/, '')))}: <b>${esc(item.tap.said)}</b> <time>${clock(item.at)}</time></p>`;
  const id = item.entry ? ` id="rp-${esc(item.entry.reply.id)}"` : '';
  return `<article class="ct-turn ${item.you ? 'ct-you' : 'ct-them'}"${id}>${chatBody(item, kids)}${item.sending ? '' : chatThread(item, kids)}</article>`;
}
function repliesScreen() {
  if (state.shown !== location.hash) Object.assign(rp, { bottom: true, touched: false });
  const entries = chatEntries();
  const keys = new Set(entries.map((item) => item.key));
  const threaded = entries.filter((item) => keys.has(item.on));
  const flow = entries.filter((item) => !keys.has(item.on));
  const kidsOf = (item) => threaded.filter((kid) => kid.on === item.key);
  const shown = flow.slice(-rp.limit);
  const hidden = flow.length - shown.length;
  const last = flow.at(-1);
  const overdue = overdueRows();
  return `<div class="ct">
    ${overdue.length ? `<div class="ct-owed">${eventList(overdue)}</div>` : ''}
    ${hidden ? `<button class="ct-link ct-earlier" data-chat-earlier>Show earlier (${hidden})</button>` : ''}
    ${flow.length ? '' : '<div class="ct-empty"><b>Ask the communicator</b><span>It answers here, and you can reply to any message in a thread.</span></div>'}
    <div class="ct-flow">${shown.map((item) => chatTurn(item, kidsOf(item))).join('')}${last?.you ? chatWaiting(last) : ''}</div>
    <div class="ct-foot">${chatBox('', '', 'Message the communicator')}</div>
  </div>`;
}
async function chatSend(on, about, text) {
  if (!text.trim()) return;
  const sent = { on, text: text.trim(), at: new Date().toISOString() };
  rp.sent.push(sent);
  localStorage.removeItem(chatDraft(on));
  if (liveChatBox(on)) liveChatBox(on).value = '';
  rp.failed.delete(on);
  if (!on) rp.bottom = true;
  render();
  const out = await fetch('/api/reply', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, on, about }) })
    .then((res) => res.json())
    .catch((failure) => ({ error: `could not reach the dashboard server (${failure.message})` }));
  if (out.error) {
    rp.sent.splice(rp.sent.indexOf(sent), 1);
    localStorage.setItem(chatDraft(on), text);
    if (liveChatBox(on)) liveChatBox(on).value = text;
    rp.failed.set(on, out.error);
  } else {
    sent.seq = out.seq;
  }
  render();
}
function liveChatBoxes() {
  return new Map([...$('#main').querySelectorAll('.ct-box')].filter((form) => form.chat.value || form.contains(document.activeElement)).map((form) => [form.dataset.chatSend, form]));
}
function keepChatBoxes(live) {
  for (const form of $('#main').querySelectorAll('.ct-box')) {
    const held = live.get(form.dataset.chatSend);
    if (held) form.replaceWith(held);
  }
}
function chatScroll() {
  const page = document.documentElement;
  const spot = rp.focus && document.getElementById(`rp-${rp.focus}`);
  if (spot) {
    rp.focus = null;
    rp.bottom = false;
    spot.scrollIntoView({ block: 'center' });
  } else if (rp.anchor !== null) {
    window.scrollBy(0, page.scrollHeight - rp.anchor);
    rp.anchor = null;
  } else if (rp.bottom) {
    window.scrollTo(0, page.scrollHeight);
  }
  if (rp.box !== null) {
    liveChatBox(rp.box)?.focus();
    rp.box = null;
  }
}
function syncChatModal() {
  const box = $('#chatmodal');
  const card = rp.modal && state.model.board.cards.find((c) => c.id === rp.modal);
  if (!card || state.model.board.answered[card.id]) {
    rp.modal = null;
    delete box.dataset.card;
    if (box.open) box.close();
    return;
  }
  if (!box.open) box.showModal();
  if (box.dataset.card === card.id && (card.embed || box.contains(document.activeElement))) return;
  box.dataset.card = card.id;
  const body = card.embed
    ? `<iframe title="${esc(card.title)}" src="${esc(framedUrl(card.embed))}"></iframe>`
    : boardCard({ card });
  box.innerHTML = `<header><b>${inline(plain(card.title))}</b><button class="btn ghost" data-chat-close>Close</button></header>${body}`;
  restoreBoxes();
}
document.addEventListener('close', (e) => {
  if (e.target.id === 'chatmodal') rp.modal = null;
}, true);
for (const type of ['wheel', 'touchstart', 'keydown', 'pointerdown']) {
  window.addEventListener(type, () => { rp.touched = true; }, { passive: true, capture: true });
}
window.addEventListener('scroll', () => {
  const page = document.documentElement;
  const atBottom = window.innerHeight + window.scrollY >= page.scrollHeight - 160;
  if (atBottom || rp.touched) rp.bottom = atBottom;
}, { passive: true });
document.addEventListener('keydown', (e) => {
  if (e.target.name === 'chat' && e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    e.target.form.requestSubmit();
  }
});
document.addEventListener('click', (e) => {
  const thread = e.target.closest('[data-chat-thread]');
  if (thread) {
    const key = thread.dataset.chatThread;
    if (!rp.threads.delete(key)) {
      rp.threads.add(key);
      rp.box = key;
    }
    rp.bottom = false;
    return render();
  }
  const done = e.target.closest('[data-chat-done]');
  if (done) {
    rp.threads.add(done.dataset.chatDone);
    rp.bottom = false;
    return chatSend(done.dataset.chatDone, done.dataset.about, 'Done');
  }
  const opener = e.target.closest('[data-chat-modal]');
  if (opener) {
    rp.modal = opener.dataset.chatModal;
    return render();
  }
  if (e.target.closest('[data-chat-close]') || e.target.id === 'chatmodal') {
    rp.modal = null;
    return render();
  }
  if (e.target.closest('[data-chat-earlier]')) {
    rp.limit += CHAT_AT_REST;
    rp.bottom = false;
    rp.anchor = document.documentElement.scrollHeight;
    return render();
  }
});
document.addEventListener('click', (e) => {
  const sorter = e.target.closest('[data-sort]');
  if (!sorter) return;
  const col = Number(sorter.dataset.col);
  const was = rp.sort.get(sorter.dataset.sort);
  rp.sort.set(sorter.dataset.sort, { col, dir: was?.col === col ? -was.dir : 1 });
  render();
});
document.addEventListener('submit', (e) => {
  const form = e.target.closest('[data-chat-send]');
  if (!form) return;
  e.preventDefault();
  chatSend(form.dataset.chatSend, form.dataset.about, form.chat.value);
});
const NOTICED_KEPT = 500;
function reportBrowser() {
  (navigator.serviceWorker?.getRegistration() ?? Promise.resolve()).then((worker) => fetch('/api/browser', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: location.origin, secure: window.isSecureContext, permission: window.Notification?.permission ?? 'absent', service_worker: Boolean(worker), hidden: document.hidden, fired: JSON.parse(localStorage.getItem('alerts-fired')) }),
  }));
}
function openNotice(a) {
  window.focus();
  if (a.card) {
    location.hash = `#/needs~question/${encodeURIComponent(a.card)}`;
    return;
  }
  rp.focus = a.id;
  rp.limit = Infinity;
  location.hash = '#/replies';
  render();
}
function fireNotice(a) {
  const title = a.incident && !/^incident/i.test(a.title) ? `Incident: ${plain(a.title)}` : plain(a.title);
  const notice = new Notification(title, { body: a.body, tag: a.id, requireInteraction: a.incident });
  const fired = (outcome) => {
    localStorage.setItem('alerts-fired', JSON.stringify({ title, at: new Date().toISOString(), outcome }));
    reportBrowser();
  };
  notice.onshow = () => fired('shown');
  notice.onerror = () => fired('refused');
  notice.onclick = () => openNotice(a);
}
function noticeAlerts(alerts) {
  const seen = JSON.parse(localStorage.getItem('alerts-noticed'));
  localStorage.setItem('alerts-noticed', JSON.stringify([...new Set([...(seen ?? []), ...alerts.map((a) => a.id)])].slice(-NOTICED_KEPT)));
  if (!seen || Notification.permission !== 'granted') return;
  alerts.filter((a) => !seen.includes(a.id)).reverse().forEach(fireNotice);
}
function followAlerts() {
  const es = new EventSource('/api/alerts');
  es.onopen = reportBrowser;
  es.addEventListener('build', (e) => { if (e.data !== $('meta[name="build"]').content) reloadWhenIdle(); });
  es.addEventListener('alerts', (e) => noticeAlerts(JSON.parse(e.data)));
  es.onerror = () => { es.close(); setTimeout(followAlerts, 3000); };
}
function alertsBar() {
  const el = $('#alerts');
  const home = $('meta[name="secure-url"]').content;
  const permission = window.Notification?.permission;
  el.innerHTML = home && new URL(home).origin !== location.origin
    ? `<span>Browser notifications cannot work on this address.</span><a class="btn pri" href="${esc(home)}${esc(location.hash)}">Open the address where they work</a>`
    : !window.isSecureContext ? ''
    : permission === 'default' ? '<span>Get a browser notification on each new reply, card and update.</span><button class="btn pri" id="alerts-on">Turn on notifications</button>'
    : permission === 'denied' ? '<span>This browser blocks the dashboard\'s notifications. Click the icon left of the address and set Notifications to Allow.</span>'
    : '';
  el.hidden = !el.innerHTML;
}
function startAlerts() {
  alertsBar();
  reportBrowser();
  if (!window.isSecureContext || !('Notification' in window)) return;
  $('#alerts').addEventListener('click', (e) => {
    if (e.target.id !== 'alerts-on') return;
    Notification.requestPermission().then((answer) => {
      alertsBar();
      if (answer === 'granted') fireNotice({ id: 'alerts-on', title: 'Notifications are on', body: 'One lands here on each new reply, card and update.', incident: false });
    });
  });
  navigator.permissions.query({ name: 'notifications' }).then((status) => {
    status.onchange = () => {
      alertsBar();
      reportBrowser();
    };
  });
  navigator.locks.request('drive-alerts', () => {
    followAlerts();
    return new Promise(() => {});
  });
}
