const PACIFIC = 'America/Los_Angeles';
const STALL_MIN = 45;
const LATE_MS = 3 * 60 * 1000;
const clockFmt = new Intl.DateTimeFormat('en-US', { timeZone: PACIFIC, hour: 'numeric', minute: '2-digit' });
const dayFmt = new Intl.DateTimeFormat('en-US', { timeZone: PACIFIC, weekday: 'short' });
const KEY_EVENT = (e) => ['owner', 'go', 'landed', 'release', 'incident', 'fix-live', 'hold', 'lift'].includes(e.kind) || (e.kind === 'report' && /READY/.test(e.raw));
const TIMELINE_FILTERS = [
  ['key', 'Key events', KEY_EVENT],
  ['hour', 'Last hour', (e) => KEY_EVENT(e) && Date.now() - new Date(e.at) < 3600 * 1000],
  ['owner', 'You said', (e) => e.kind === 'owner'],
  ['go', 'Approvals and landings', (e) => e.kind === 'go' || e.kind === 'landed'],
  ['incident', 'Releases and incidents', (e) => ['release', 'incident', 'fix-live', 'mechanism'].includes(e.kind)],
  ['all', 'Everything', () => true],
];
const KIND_WORD = { owner: 'You', go: 'Approved to land', landed: 'Landed', release: 'Release', incident: 'Incident', mechanism: 'Cause found', 'fix-live': 'Fix live', opened: 'Opened', report: 'Report', hold: 'Held', lift: 'Hold lifted', blocker: 'Blocked', defect: 'Tool problem', correction: 'Correction', decide: 'Decision', ask: 'Question', answer: 'Answer' };

const state = { drive: new Map(), model: null, version: -1, open: new Set(), seen: new Set(), chat: [], asking: false, overlay: null };
const SUGGESTED = ['What is blocking the next release?', 'What did I approve tonight that has not landed yet?', 'Which workers are stuck, and on what?'];
const $ = (sel) => document.querySelector(sel);

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}
function clock(iso) {
  const d = new Date(iso);
  const today = dayFmt.format(new Date()) === dayFmt.format(d);
  return (today ? '' : dayFmt.format(d) + ' ') + clockFmt.format(d);
}
function minutesSince(iso) {
  return Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
}
function agoWords(iso) {
  const m = minutesSince(iso);
  if (m < 1) return 'just now';
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h >= 12) return `at ${clock(iso)}`;
  return m % 60 ? `${h} h ${m % 60} min ago` : `${h} h ago`;
}
function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}
function plainTitle(n) {
  const row = boardRow(n);
  if (row) return row.title;
  const title = state.model.prs[n]?.title ?? `Pull request ${n}`;
  return title.replace(/^[\w./-]+:\s*/, '').replace(/^[^\w#]+/, '').replace(/^./, (c) => c.toUpperCase());
}
function boardRow(n) {
  return state.model.board.rows.find((r) => r.number === Number(n));
}
function verdictState(row) {
  return row.state;
}
function driveEntry(n) {
  return state.drive.get(Number(n));
}
function prState(n) {
  return driveEntry(n)?.state ?? (state.model.steps.some((s) => s.untracked.includes(Number(n))) ? 'untracked' : 'closed');
}
const STATE_WORD = { landed: 'Landed', closed: 'Closed', you: 'Waiting on you', failing: 'Tests failing', held: 'Held', queued: 'Approved, waiting to land', doing: 'Being worked on', waiting: 'Waiting its turn', untracked: 'Not tracked by the merge ledger yet' };
const STATE_TONE = { landed: 'ok', closed: '', you: 'bad', failing: 'bad', held: 'warn', queued: 'ok', doing: 'acc', waiting: '', untracked: 'warn' };
function prHref(n) {
  return state.model.prs[n] ? `#/review/${n}` : `https://github.com/${state.model.repo}/pull/${n}`;
}
function prLink(n) {
  const external = !state.model.prs[n];
  const s = prState(n);
  return `<a class="prc ${s}" href="${prHref(n)}"${external ? ' target="_blank" rel="noopener"' : ''} title="${esc(plainTitle(n))} · ${STATE_WORD[s]}">${s === 'landed' ? '✓' : s === 'closed' ? '' : `<i class="lt ${s}"></i>`}#${n}</a>`;
}
function inline(md) {
  return esc(md)
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/#(\d{5})\b/g, (_, n) => prLink(n));
}
function markdown(md) {
  const out = [];
  let list = null;
  let table = null;
  let code = null;
  const cells = (line) => line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  const flushTable = () => {
    if (!table) return;
    const [head, ...rows] = table;
    out.push(`<div class="tablewrap"><table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td data-h="${esc(head[i] ?? '')}">${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
    table = null;
  };
  for (const line of md.split('\n')) {
    if (code !== null) {
      if (/^\s*```/.test(line)) { out.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`); code = null; } else code.push(line);
      continue;
    }
    if (/^\s*```/.test(line)) { flushTable(); code = []; continue; }
    const disclose = /^\s*(<details>)?(?:<summary>(.*?)<\/summary>)?(<\/details>)?\s*$/.exec(line);
    if (disclose && (disclose[1] || disclose[2] !== undefined || disclose[3])) {
      flushTable();
      if (list) { out.push(`</${list}>`); list = null; }
      out.push(`${disclose[1] ? '<details>' : ''}${disclose[2] !== undefined ? `<summary>${inline(disclose[2])}</summary>` : ''}${disclose[3] ? '</details>' : ''}`);
      continue;
    }
    if (/^\s*\|/.test(line)) {
      if (list) { out.push(`</${list}>`); list = null; }
      if (/^\s*\|[\s:|-]+\|\s*$/.test(line)) continue;
      (table ??= []).push(cells(line));
      continue;
    }
    flushTable();
    const item = /^\s*(?:[-*]|\d+\.)\s+(.*)$/.exec(line);
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (!item && list) { out.push(`</${list}>`); list = null; }
    if (item) {
      const kind = /^\s*\d+\./.test(line) ? 'ol' : 'ul';
      if (list !== kind) { if (list) out.push(`</${list}>`); out.push(`<${kind}>`); list = kind; }
      out.push(`<li>${inline(item[1])}</li>`);
    } else if (heading) {
      const id = heading[2].toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
      out.push(`<h${heading[1].length + 1} id="md-${id}">${inline(heading[2])}</h${heading[1].length + 1}>`);
    } else if (line.trim()) {
      out.push(`<p>${inline(line)}</p>`);
    }
  }
  flushTable();
  if (code !== null) out.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);
  if (list) out.push(`</${list}>`);
  return out.join('');
}
function firstSentences(text, max = 220) {
  const t = String(text ?? '').replace(/\s+/g, ' ').trim();
  return t.length <= max ? t : t.slice(0, t.lastIndexOf(' ', max)) + '…';
}

function timeline() {
  const m = state.model;
  const events = [];
  for (const r of m.records) {
    if (r.kind === 'landed') continue;
    let kind = r.kind, who = r.lane, text = r.text;
    const owner = /^OWNER [^:]*?(?:,\s*verbatim)?(?:, a repeat)?:\s*['"](.+?)['"](?:\.|$|\s)/s.exec(r.text);
    if (r.kind === 'owner' || owner) { kind = 'owner'; who = 'You'; text = (owner ? owner[1] : r.text).replace(/^Owner on board \w+, (.+?) \([\w-]+\): /s, 'On “$1”, you '); }
    else if (r.lane === 'root') who = 'Coordinator';
    const go = /^go #(\d+) at (\w+)/.exec(r.text);
    const ready = /READY #?(\d{5})/.exec(r.text);
    if (r.kind === 'go' && go) { who = 'Merge process'; text = `#${go[1]} ${plainTitle(go[1])}, approved at ${go[2].slice(0, 7)}`; }
    else if (r.kind === 'report' && ready) { text = `#${ready[1]} ${plainTitle(ready[1])} is ready to land`; }
    events.push({ id: `r${r.seq}`, at: r.at, kind, who, text, raw: r.text });
  }
  const dayAgo = Date.now() - 24 * 3600 * 1000;
  for (const [n, pr] of Object.entries(m.prs)) {
    if (pr.state === 'landed' && pr.landed_at && new Date(pr.landed_at) > dayAgo) {
      events.push({ id: `l${n}`, at: pr.landed_at, kind: 'landed', who: 'Merge queue', text: `#${n} ${plainTitle(n)}`, raw: '' });
    }
  }
  return events.sort((a, b) => new Date(b.at) - new Date(a.at));
}
function timelineRows(events, gaps) {
  const out = [];
  events.forEach((e, i) => {
    const prev = events[i - 1];
    if (gaps && prev) {
      const quiet = Math.round((new Date(prev.at) - new Date(e.at)) / 60000);
      if (quiet >= 30) out.push(`<li class="gap" style="display:block">${quiet} min with no updates</li>`);
    }
    const isNew = state.seen.size && !state.seen.has(e.id);
    const open = state.open.has(e.id) ? ' open' : '';
    out.push(`<li class="k-${e.kind}${isNew ? ' new' : ''}"><span class="time">${clock(e.at)}</span><span class="dot"></span>
      <details data-id="${e.id}"${open}><summary><span class="who">${esc(e.who)}${e.kind !== 'owner' ? ' · ' + (KIND_WORD[e.kind] ?? e.kind) : ''}</span>
      <div class="say">${inline(e.text)}</div></summary>${e.raw && e.raw !== e.text ? `<div class="dim" style="white-space:pre-wrap;margin-top:4px">${inline(e.raw)}</div>` : ''}</details></li>`);
  });
  return out.join('');
}

function dots(numbers) {
  return `<span class="dots">${numbers.map((n) => `<a class="d ${prState(n)}" href="${prHref(n)}" title="#${n} ${esc(plainTitle(n))} · ${STATE_WORD[prState(n)]}"></a>`).join('')}</span>`;
}
function inDrive(numbers) {
  return numbers.filter((n) => state.drive.has(Number(n)));
}
const RANK = { landed: 0, queued: 1, doing: 2, waiting: 3, you: 4, failing: 5, held: 6, closed: 7 };
function prRank(n) {
  return RANK[prState(n)];
}
function liveBox(l) {
  const n = /^\d{5}$/.test(l.topic ?? '') ? l.topic : /#(4\d{4})\b/.exec(l.text)?.[1];
  const what = n ? `<a href="${prHref(n)}"><span class="d ${prState(n)}"></span> #${n} ${esc(plainTitle(n))}</a>` : esc(firstSentences(l.text, 70));
  return `<div class="live${l.stall ? ' stall' : ''}"><div class="live-h"><i class="${l.stall ? 'still' : 'pulse'}"></i><b>${esc(l.lane)}</b><span class="dim">${agoWords(l.at)}</span></div><div class="live-t">${what}</div></div>`;
}
function workingNow() {
  const since = Date.now() - 20 * 60 * 1000;
  const lanes = state.model.lanes.filter((l) => new Date(l.at) > since && l.lane !== 'root').sort((a, b) => new Date(b.at) - new Date(a.at)).slice(0, 8);
  return lanes.map(liveBox).join('') || '<div class="dim">No worker has written in the last 20 minutes.</div>';
}
function planSteps(full) {
  return state.model.plan.map((s, i) => {
    const nums = inDrive(s.prs).sort((a, b) => prRank(a) - prRank(b));
    const done = nums.filter((n) => prState(n) === 'landed').length;
    return `<li id="pstep-${i}" class="pstep${nums.length && done === nums.length ? ' done' : ''}"><span class="n">${i + 1}</span><div><div class="pt">${esc(s.title)}${nums.length ? `<span class="dim num"> ${done} of ${nums.length} landed</span>` : ''}</div>${nums.length ? dots(nums) : ''}${full && s.text ? `<p>${inline(s.text)}</p>` : ''}</div></li>`;
  }).join('');
}
function activeLanes() {
  const since = Date.now() - 3 * 3600 * 1000;
  const holds = {};
  for (const pr of Object.values(state.model.prs)) if (pr.state === 'open' && pr.lane) holds[pr.lane] = (holds[pr.lane] ?? 0) + 1;
  return state.model.lanes
    .filter((l) => new Date(l.at) > since || holds[l.lane])
    .map((l) => ({ ...l, holds: holds[l.lane] ?? 0, stall: Boolean(holds[l.lane]) && minutesSince(l.at) >= STALL_MIN }))
    .sort((a, b) => b.stall - a.stall || new Date(b.at) - new Date(a.at));
}
function workers() {
  const live = state.model.workers.filter((w) => w.live);
  const busy = new Set(live.map((w) => w.lane));
  const lanes = activeLanes().filter((l) => !busy.has(l.lane));
  const quiet = lanes.filter((l) => l.stall);
  const rest = lanes.filter((l) => !l.stall);
  const fold = (id, label, list) => list.length ? `<details class="wfold" data-id="${id}"${state.open.has(id) ? ' open' : ''}><summary>${label}</summary><div class="lives">${list.map(liveBox).join('')}</div></details>` : '';
  const onStep = [...state.model.steps, { id: null, label: 'Other work', prs: [] }].map((st) => [st, live.filter((w) => w.step === st.id)]).filter(([, ws]) => ws.length);
  return `<div class="ck"><section class="crew"><div class="crew-h"><b>Working now</b><span>${plural(live.length, 'worker')} wrote in the last 20 minutes, on the step they serve</span></div>
    ${onStep.map(([st, ws]) => `<div class="wstep"${st.id ? ` data-track="${esc(st.id)}"` : ''}><div class="wstep-h"><b>${esc(st.label)}</b><span class="tdots">${byLight(st.prs).map((e) => dot(e.number)).join('')}</span></div><div class="wgrid">${ws.map((w) => workerTile(w, w.lane)).join('')}</div></div>`).join('') || '<span class="dim">Nobody wrote in the last 20 minutes.</span>'}</section>
    ${fold('w-quiet', `${plural(quiet.length, 'worker')} quiet for ${STALL_MIN}+ minutes while holding a pull request`, quiet)}
    ${fold('w-rest', `${plural(rest.length, 'worker')} finished or idle in the last 3 hours`, rest)}</div>`;
}

function hourDigest() {
  const m = state.model;
  const from = Date.now() - 3600 * 1000;
  const recent = timeline().filter((e) => new Date(e.at) > from);
  const landed = Object.entries(m.prs).filter(([, pr]) => pr.state === 'landed' && pr.landed_at && new Date(pr.landed_at) > from).sort((a, b) => new Date(b[1].landed_at) - new Date(a[1].landed_at));
  const said = (kinds) => recent.filter((e) => kinds.includes(e.kind));
  const settled = (e) => { const named = [...String(e.text).matchAll(/#(\d{5})\b/g)].map((x) => x[1]); return named.length && named.every((n) => new Date(m.prs[n]?.landed_at ?? 0) > new Date(e.at)); };
  const onePerPr = (list) => {
    const seen = new Set();
    return list.filter((e) => {
      const key = /#(\d{5})\b/.exec(e.text)?.[1] ?? e.id;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  };
  const broke = onePerPr(said(['incident', 'blocker']).filter((e) => !settled(e)));
  const freed = onePerPr(said(['fix-live', 'lift', 'go']));
  const line = (e) => `<li><span class="time">${clock(e.at)}</span><span>${inline(firstSentences(shortShas(e.text), 140))}</span></li>`;
  const rows = [
    ['Landed', landed.length, landed.slice(0, 5).map(([n, pr]) => `<li><span class="time">${clock(pr.landed_at)}</span><span>${prLink(n)} ${esc(plainTitle(n))} <em class="dim">${{ 'New tree': 'new app', 'Old tree': 'old app' }[driveEntry(n)?.tree] ?? 'shared'}</em></span></li>`)],
    ['Cleared to land or fixed', freed.length, freed.slice(0, 4).map(line)],
    ['Newly broke or blocked', broke.length, broke.slice(0, 4).map(line)],
    ['You said', said(['owner']).length, said(['owner']).slice(0, 3).map(line)],
  ];
  return `<div class="card digest"><div class="kick muted">Since ${clock(new Date(from).toISOString())}</div>${rows.map(([label, n, items]) => `<section class="dg${n ? '' : ' none'}"><h4><b>${n}</b> ${label}</h4>${items.length ? `<ul>${items.join('')}</ul>` : ''}</section>`).join('')}</div>`;
}
function timelineScreen() {
  const filter = TIMELINE_FILTERS.find((f) => f[0] === route().id) ?? TIMELINE_FILTERS[0];
  const events = timeline().filter(filter[2]);
  return `<h2 class="sec">Timeline <span class="dim">last 24 hours, newest first</span></h2>
    ${filter[0] === 'hour' ? hourDigest() : ''}
    <div class="filters">${TIMELINE_FILTERS.map(([id, label]) => `<button data-filter="${id}" class="${id === filter[0] ? 'on' : ''}">${label}</button>`).join('')}</div>
    <div class="card"><ul class="tl">${timelineRows(events, true) || '<li class="dim">Nothing yet.</li>'}</ul></div>`;
}

const FACTS = [['change', 'What changes for a user'], ['plan', 'Against the plan'], ['risk', 'Risk'], ['review', 'Review'], ['blocker', 'What blocks it']];
function needsTotal() {
  return (state.model?.needs ?? []).reduce((n, item) => n + (item.kind === 'prs' ? item.prs.length : 1), 0);
}
function sourceLink(source) {
  return source.href ? `<a class="nsrc" href="${esc(source.href)}" target="_blank" rel="noopener">${esc(source.label)} ›</a>` : `<span class="nsrc none">${esc(source.label)}</span>`;
}
function needRow(r) {
  const row = boardRow(r.number);
  const busy = row?.pending ? ' disabled' : '';
  const acts = r.state === 'you'
    ? `<button class="btn pri" data-tap="approved" data-n="${r.number}" data-head="${esc(r.head)}"${busy}>✓ Approve ${esc(r.head.slice(0, 7))}</button><button class="btn hold" data-tap="rejected" data-n="${r.number}" data-head="${esc(r.head)}"${busy}>‖ Hold</button>`
    : `<span class="pill ${r.state === 'approved' ? 'ok' : 'warn'}">${r.state === 'approved' ? '✓ Approved' : '‖ Held'}</span>`;
  const stuck = r.blocker && !/^nothing/i.test(r.blocker);
  return `<li class="nrow ${r.state}"><div class="nt"><a href="#/review/${r.number}">#${r.number} ${esc(shortName(r.number) ?? r.title)}</a><span class="dim">at ${esc(r.head.slice(0, 7))}</span></div>
    <p class="nline">${inline(r.change)}</p>${stuck ? `<p class="nline bad">⚠ ${inline(r.blocker)}</p>` : ''}
    <details data-id="facts-${r.number}"${state.open.has(`facts-${r.number}`) ? ' open' : ''}><summary>Plan, risk and review</summary><dl>${FACTS.filter(([key]) => key !== 'change' && (key !== 'blocker' || !stuck)).map(([key, label]) => `<dt>${label}</dt><dd>${inline(r[key])}</dd>`).join('')}</dl></details>
    ${r.md ? `<details class="nmd" data-id="note-${r.number}"${state.open.has(`note-${r.number}`) ? ' open' : ''}><summary>The board's note</summary><div class="md">${markdown(r.md)}</div></details>` : ''}
    ${row?.failed ? `<div class="nerr">${esc(row.failed)}</div>` : ''}<div class="acts">${acts}</div></li>`;
}
function needGroup(item) {
  return `<section class="card ngroup"><h3>${esc(item.group)}</h3><p class="nrel">${esc(item.relation)}</p><p class="nwhy">${esc(item.why)} ${sourceLink(item.source)}</p>
    <ul class="nrows">${[...item.prs, ...item.others].map(needRow).join('')}</ul></section>`;
}
function needOther(item) {
  const record = item.ask?.record;
  return `<li class="nother ${item.kind}"><span class="nmark">${item.kind === 'card' ? '?' : '!'}</span><div><div class="nt">${esc(item.kind === 'card' ? item.card.title : item.ask.what)}</div><p class="nwhy">${esc(item.why)}</p>
    ${record ? `<details data-id="ask-${record.seq}"${state.open.has(`ask-${record.seq}`) ? ' open' : ''}><summary>Full ask from ${esc(record.lane)} at ${clock(record.at)}</summary><div class="nfull">${inline(record.text)}</div></details>` : ''}</div>${sourceLink(item.source)}</li>`;
}
const factGrid = (facts) => facts.length ? `<dl class="bfacts">${facts.map((f) => `<dt>${inline((f.label ?? '').replace(/\.$/, ''))}</dt><dd${f.tone ? ` class="t-${esc(f.tone)}"` : ''}>${inline(f.value)}</dd>`).join('')}</dl>` : '';
const prosCons = (d) => `${d?.pros?.length ? `<ul class="pros">${d.pros.map((x) => `<li>${inline(x)}</li>`).join('')}</ul>` : ''}${d?.cons?.length ? `<ul class="cons">${d.cons.map((x) => `<li>${inline(x)}</li>`).join('')}</ul>` : ''}`;
function pickedLabel(part) {
  const a = part.answer;
  return [...a.optionIds.map((id) => (part.options.find((o) => o.id === id)?.label ?? id).replace(/\s*\(Recommended\)\s*$/i, '')), ...(a.other ? [`“${a.other}”`] : [])].join(', ');
}
function boardPart(part, card) {
  const busy = sending(part.id) ? ' disabled' : '';
  if (part.kind === 'record') return part.facts.length > 2
    ? `<details class="brec" data-id="rec-${esc(part.id)}"${state.open.has(`rec-${part.id}`) ? ' open' : ''}><summary><h4>${inline(part.title || 'Background')}</h4><span class="dim">${part.facts.length} facts</span></summary>${factGrid(part.facts)}</details>`
    : `<section class="brec">${part.title ? `<h4>${inline(part.title)}</h4>` : ''}${factGrid(part.facts)}</section>`;
  if (part.kind === 'page') return `<section class="brec prose">${markdown(part.md)}</section>`;
  if (part.kind === 'choice') {
    if (part.answer) return `<section class="bpick done"><span class="pill ok">✓ You picked</span> ${esc(pickedLabel(part))}</section>`;
    return `<section class="bpick">${part.prompt ? `<h4>${inline(part.prompt)}</h4>` : ''}<div class="bopts">${part.options.map((o) => `<article class="bopt${o.recommended ? ' rec' : ''}">
      ${o.recommended ? '<span class="bstamp">★ Recommended</span>' : ''}<b>${inline(o.label.replace(/\s*\(Recommended\)\s*$/i, ''))}</b>${factGrid(o.facts)}
      ${o.detail?.pros?.length || o.detail?.cons?.length ? `<details data-id="pc-${esc(o.id)}"${state.open.has(`pc-${o.id}`) ? ' open' : ''}><summary>Pros and cons</summary>${prosCons(o.detail)}</details>` : ''}
      ${o.hint ? `<details data-id="hint-${esc(o.id)}"${state.open.has(`hint-${o.id}`) ? ' open' : ''}><summary>What it means</summary><p>${inline(o.hint)}</p></details>` : ''}
      <button class="btn${o.recommended ? ' pri' : ''}" data-choose="${esc(o.id)}" data-choice="${esc(part.id)}"${busy}>Pick this</button></article>`).join('')}</div>
      <form class="note" data-answer="${esc(card.id)}" data-choice="${esc(part.id)}"><input name="text" data-draft="${esc(part.id)}" value="${esc(ov.drafts[part.id] ?? '')}" placeholder="Something else: write your answer"><button class="btn ghost"${busy}>Send</button></form>${sentNote(part.id)}</section>`;
  }
  if (part.kind === 'approval') {
    if (part.answer) return `<section class="bpick done"><span class="pill ${part.answer.verdict === 'approved' ? 'ok' : 'warn'}">${part.answer.verdict === 'approved' ? '✓ You approved' : '✕ You rejected'}</span>${part.answer.note ? ` “${esc(part.answer.note)}”` : ''}</section>`;
    const d = part.detail;
    return `<section class="bpick"><h4>${inline(part.prompt || 'Approve?')}</h4>${prosCons(d)}${d.md ? markdown(d.md) : ''}
      <form class="note" data-approval="${esc(part.id)}"><input name="text" data-draft="${esc(part.id)}" value="${esc(ov.drafts[part.id] ?? '')}" placeholder="Note, optional"><button class="btn pri" value="approved"${busy}>✓ Approve</button><button class="btn ghost" value="rejected"${busy}>✕ Reject</button></form>${sentNote(part.id)}</section>`;
  }
  if (part.answer) return `<section class="bpick done"><span class="pill ok">✓ You answered</span> “${esc(part.answer.text)}”</section>`;
  const field = part.multiline
    ? `<textarea name="text" rows="3" data-draft="${esc(part.id)}" placeholder="${esc(part.placeholder || 'Your answer')}">${esc(ov.drafts[part.id] ?? '')}</textarea>`
    : `<input name="text" data-draft="${esc(part.id)}" value="${esc(ov.drafts[part.id] ?? '')}" placeholder="${esc(part.placeholder || 'Your answer')}">`;
  return `<section class="bpick"><h4>${inline(part.label)}</h4><form class="note" data-input="${esc(part.id)}">${field}<button class="btn pri"${busy}>Send</button></form>${sentNote(part.id)}</section>`;
}
function boardCard(item) {
  const c = item.card;
  const body = c.embed
    ? `<div class="acts"><button class="btn pri" data-embed-card="${esc(c.id)}">${ov.embedCard === c.id ? 'Showing the card ›' : 'Answer it here ›'}</button></div>`
    : c.parts.map((part) => boardPart(part, c)).join('');
  return `<article class="card bcard" id="card-${esc(c.id)}"><header><span class="nmark">?</span><h3>${inline(c.title)}</h3>${c.chips.map((chip) => `<span class="pill${chip.tone === 'flag' ? ' warn' : ''}">${esc(chip.label)}</span>`).join('')}<a class="nsrc" href="${esc(state.model.board.url)}" target="_blank" rel="noopener">On your board ›</a></header>
    ${c.summary ? `<p class="nwhy">${inline(c.summary)}</p>` : ''}${body}${c.embed ? '' : sentNote(c.id)}</article>`;
}
function syncCardEmbed(r) {
  const host = $('#cardembed');
  const card = r.screen === 'now' && state.model.board.cards.find((c) => c.id === ov.embedCard && c.embed);
  host.hidden = !card;
  if (!card) {
    ov.embedCard = null;
    delete host.dataset.card;
    host.replaceChildren();
    return;
  }
  if (host.dataset.card === card.id) return;
  host.dataset.card = card.id;
  host.innerHTML = `<header><b>${inline(card.title)}</b><button class="btn ghost" data-embed-close>Close</button></header><iframe title="${esc(card.title)}" src="${esc(card.embed)}"></iframe>`;
}
window.addEventListener('message', (e) => {
  const frame = $('#cardembed iframe');
  if (e.data?.type === 'cc-present.height' && frame && e.source === frame.contentWindow) frame.style.height = `${e.data.px}px`;
});
function needsScreen() {
  const items = state.model.needs;
  const cards = items.filter((item) => item.kind === 'card');
  const others = items.filter((item) => item.kind === 'ask');
  return `<h2 class="sec">Needs you ${needsTotal() ? `<span class="pill bad">${needsTotal()}</span>` : ''}</h2>
    ${items.filter((item) => item.kind === 'prs').map(needGroup).join('')}
    ${cards.length ? `<h2 class="sec">Questions on your board <span class="dim">${cards.length} open; a pick here is recorded on the board</span></h2>${cards.map(boardCard).join('')}` : ''}
    ${others.length ? `<h2 class="sec">Other asks</h2><ul class="card nothers">${others.map(needOther).join('')}</ul>` : ''}
    ${items.length ? '' : `<div class="card nzero"><b>No decisions now.</b> When a pull request or a question waits on you, it shows here and on the Overview with its buttons.</div>`}
    ${mergeCard()}`;
}
function mergeCard() {
  const merge = state.model.board.merge;
  if (!merge) return '';
  const table = (t) => `<div class="tablewrap"><table><thead><tr>${t.columns.map((c) => `<th${c.align === 'right' ? ' class="num"' : ''}>${esc(c.label)}</th>`).join('')}</tr></thead><tbody>${t.rows.map((r) => `<tr>${t.columns.map((c) => `<td data-h="${esc(c.label)}"${c.align === 'right' ? ' class="num"' : ''}>${esc(r[c.key] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  return `<div class="card prose mergecard bcard"><header><span class="nmark">⇄</span><h3>${inline(merge.title || 'Merge board')}</h3>${merge.chips.map((chip) => `<span class="pill${chip.tone === 'flag' ? ' warn' : ''}">${esc(chip.label)}</span>`).join('')}<a class="nsrc" href="${esc(state.model.board.url)}" target="_blank" rel="noopener">On your board ›</a></header><p><b>${esc(merge.summary)}</b></p>
    <details data-id="merge-tables"${state.open.has('merge-tables') ? ' open' : ''}><summary>By feature</summary>${merge.tables.map(table).join('')}</details>
    ${merge.pages.map((md) => markdown(md)).join('')}</div>`;
}

function releases() {
  const m = state.model;
  const r = m.releases;
  const rels = m.now.releases ?? [];
  const next = rels.findIndex((x) => !RELEASED.test(x.state));
  const row = (x, i) => {
    const [cls, word] = releaseWord(rels, i);
    const open = m.drive.prs.filter((e) => x.parts.includes(e.part) && e.state !== 'landed' && e.tree === m.tree_names[0]).length;
    return `<li class="relrow ${cls}" data-release="${esc(x.id)}"><b>${esc(x.id)}</b><span class="rw">${word}</span><span class="rn">${esc(x.name)}${x.tonight ? '' : ' <em>after tonight</em>'}</span><span class="rs">${esc(x.state)}</span><span class="ro">${plural(open, 'open pull request')}</span></li>`;
  };
  return `<h2 class="sec">Releases <span class="dim">state from the progress desk, ${clock(m.steps_at)}</span></h2><div class="ck">${routeStrip()}</div><ol class="rellist">${rels.map(row).join('')}</ol>
    <details class="reldoc" data-id="rel-doc"${state.open.has('rel-doc') ? ' open' : ''}><summary class="sec">The full release plan ${r.updated ? `<span class="dim">revised ${clock(r.updated)}</span>` : ''}</summary><div class="prose">${markdown(r.markdown)}</div></details>`;
}

function chatTurns() {
  return state.chat.map((t) => `<div class="turn ${t.role}">${t.role === 'user' ? esc(t.content) : markdown(t.content) || '<span class="dim">Reading the drive…</span>'}</div>`).join('');
}
function askScreen() {
  const turns = chatTurns();
  return `<h2 class="sec">Ask about the drive <span class="dim">answers come only from the drive's records, the plan and the merge board</span></h2>
    <div class="chat">${turns || `<div class="suggest">${SUGGESTED.map((q) => `<button class="btn" data-ask="${esc(q)}">${esc(q)}</button>`).join('')}</div>`}</div>
    <form class="ask" id="ask"><textarea name="q" rows="2" placeholder="Ask anything about the drive…"${state.asking ? ' disabled' : ''}></textarea><button class="btn pri"${state.asking ? ' disabled' : ''}>Ask</button></form>`;
}
async function ask(question) {
  if (!question.trim() || state.asking) return;
  const history = state.chat.map(({ role, content }) => ({ role, content }));
  state.chat.push({ role: 'user', content: question }, { role: 'assistant', content: '' });
  state.asking = true;
  render();
  fetch('/api/asked', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: question }) });
  const answer = state.chat[state.chat.length - 1];
  try {
    answer.content = await assistAsk(question, history, (text) => {
      answer.content = text;
      if (route().screen === 'ask') $('#main .chat').innerHTML = chatTurns();
    });
  } catch (error) {
    answer.content += `\n\nThe answer stopped: ${error.message}`;
  } finally {
    state.asking = false;
    render();
  }
}

function route() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  return { screen: parts[0] ?? 'overview', id: parts[1], tab: parts[2] };
}
function overRoute(r) {
  return ['now', 'releases', 'workers', 'timeline', 'ask'].includes(r.screen) || (r.screen === 'review' && !/^\d+$/.test(r.id ?? ''));
}
function render() {
  const m = state.model;
  if (!m || !m.prs) return;
  const r = route();
  if (state.overlay && !history.state?.sheet) history.pushState({ sheet: true }, '');
  document.title = m.title;
  $('.brand').textContent = m.title;
  const waiting = m.board.rows.filter((row) => verdictState(row) === 'you').length;
  const tabs = [['overview', 'Overview'], ['now', 'Needs you', needsTotal()], ['review', 'Review', waiting], ['releases', 'Releases'], ['workers', 'Workers'], ['timeline', 'Timeline'], ['ask', 'Ask']];
  $('#tabs').innerHTML = tabs.map(([id, label, count]) => `<a class="tab${r.screen === id ? ' on' : ''}" href="#/${id === 'overview' ? '' : id}">${label}${count ? `<span class="pill bad count">${count}</span>` : ''}</a>`).join('');
  const over = (body) => () => `${overview()}<div class="overlay route" data-close="1" data-route="${esc(location.hash)}"><div class="sheet route"><button class="btn close" data-close="1">Close</button>${body()}</div></div>`;
  const screens = { overview, now: over(needsScreen), review: overRoute(r) ? over(reviewList) : () => reviewPR(r.id, r.tab), releases: over(releases), workers: over(workers), timeline: over(timelineScreen), ask: over(askScreen) };
  const y = window.scrollY;
  const kept = [...document.querySelectorAll('[data-keep-scroll]')].map((el) => [el.dataset.keepScroll, el.scrollLeft, el.scrollTop]);
  const sheetY = $('.overlay.route')?.dataset.route === location.hash ? [$('.overlay.route').scrollTop, $('.sheet.route').scrollTop] : [0, 0];
  const typing = document.activeElement?.matches('#main input, #main textarea') ? document.activeElement : null;
  const caret = typing && [typing.name, typing.dataset.draft ?? '', typing.selectionStart, typing.selectionEnd];
  $('#main').innerHTML = (screens[r.screen] ?? overview)() + `<div class="foot">${m.errors && Object.keys(m.errors).length ? `Some sources failed: ${esc(Object.values(m.errors).join('; '))}. ` : ''}All times Pacific.</div>`;
  decorate(r);
  const again = caret && [...$('#main').querySelectorAll(`[name="${caret[0]}"]`)].find((el) => (el.dataset.draft ?? '') === caret[1]);
  if (again) { again.focus(); again.setSelectionRange(caret[2], caret[3]); }
  window.scrollTo(0, y);
  for (const [id, left, top] of kept) { const el = document.querySelector(`[data-keep-scroll="${id}"]`); if (el) [el.scrollLeft, el.scrollTop] = [left, top]; }
  if ($('.overlay.route')) [$('.overlay.route').scrollTop, $('.sheet.route').scrollTop] = sheetY;
  syncReviewEmbed(r);
  syncCardEmbed(r);
  if (overRoute(r) || (screens[r.screen] ?? overview) === overview) mountOverview();
  state.seen = new Set(timeline().map((e) => e.id));
  tickFresh();
}
function tickFresh() {
  const m = state.model;
  if (!m?.built_at) return;
  const age = Date.now() - new Date(m.built_at);
  const el = $('#fresh');
  el.classList.toggle('late', age > LATE_MS);
  el.textContent = age > LATE_MS ? `Not updated since ${clock(m.built_at)}` : `Updated ${Math.max(0, Math.round(age / 1000))} seconds ago · All times Pacific`;
}

async function load() {
  const res = await fetch('/api/model', { cache: 'no-store' });
  const model = await res.json();
  if (!model.prs) return setTimeout(load, 1000);
  state.model = model;
  state.drive = new Map(model.drive.prs.map((e) => [e.number, e]));
  state.version = model.version;
  if (state.pressing) state.owed = true;
  else render();
}
document.addEventListener('pointerdown', () => { state.pressing = true; }, true);
for (const type of ['pointerup', 'pointercancel']) {
  document.addEventListener(type, () => {
    state.pressing = false;
    if (state.owed) { state.owed = false; setTimeout(render); }
  }, true);
}
function connect() {
  const es = new EventSource('/api/stream');
  es.addEventListener('version', (e) => { if (Number(e.data) !== state.version) load(); });
  es.onerror = () => { es.close(); setTimeout(connect, 3000); };
}
document.addEventListener('toggle', (e) => {
  const id = e.target.dataset?.id;
  if (!id) return;
  if (e.target.open) state.open.add(id);
  else state.open.delete(id);
}, true);
document.addEventListener('click', (e) => {
  const toggle = e.target.closest('[data-toggle]');
  if (toggle && !e.target.closest('a')) {
    const id = toggle.dataset.toggle;
    state.open.has(id) ? state.open.delete(id) : state.open.add(id);
    render();
  }
  const close = e.target.closest('[data-close]');
  if (close && (e.target === close || close.matches('button'))) {
    if (state.overlay) closeSheet(); else if (overRoute(route())) location.hash = '#/';
    return;
  }
  const overlay = e.target.closest('[data-overlay]');
  if (overlay && !e.target.closest('a.d')) {
    e.preventDefault();
    state.overlay = overlay.dataset.overlay;
    render();
    document.getElementById(`pstep-${overlay.dataset.step ?? 0}`)?.scrollIntoView({ block: 'center' });
  }
  const suggestion = e.target.closest('[data-ask]');
  if (suggestion) ask(suggestion.dataset.ask);
  const filter = e.target.closest('[data-filter]');
  if (filter) location.hash = `#/timeline/${filter.dataset.filter}`;
});
document.addEventListener('submit', (e) => {
  if (e.target.id !== 'ask') return;
  e.preventDefault();
  ask(e.target.q.value);
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && state.overlay) closeSheet();
  else if (e.key === 'Escape' && overRoute(route())) location.hash = '#/';
  if (e.key === 'Enter' && !e.shiftKey && e.target.matches('#ask textarea')) { e.preventDefault(); ask(e.target.value); }
});
function closeSheet() {
  if (history.state?.sheet) return history.back();
  state.overlay = null;
  render();
}
window.addEventListener('popstate', (e) => {
  if (e.state?.sheet || !state.overlay) return;
  state.overlay = null;
  render();
});
window.addEventListener('hashchange', () => { window.scrollTo(0, 0); render(); });
window.addEventListener('DOMContentLoaded', () => {
  setInterval(tickFresh, 1000);
  load();
  connect();
});
