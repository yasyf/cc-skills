const PACIFIC = 'America/Los_Angeles';
const STALL_MIN = 45;
const LATE_MS = 3 * 60 * 1000;
const clockFmt = new Intl.DateTimeFormat('en-US', { timeZone: PACIFIC, hour: 'numeric', minute: '2-digit' });
const dayFmt = new Intl.DateTimeFormat('en-US', { timeZone: PACIFIC, weekday: 'short' });
const SCOPES = [
  ['On the Mac', { sand: 'Sand runtime and Mac agent', iris: 'Iris app', 'iris-desktop': 'Desktop app', desktop: 'Desktop app', localrun: 'Local runs', vm_run: 'Sand VM runs', 'sandsql-journal': 'Chat journal', 'sandsql-journal-next': 'Chat journal' }],
  ['In the cloud', { api: 'API server', dashboard: 'Dashboard', sandsql: 'Database server', 'sandsql-server': 'Database server', 'llm-gateway': 'Model gateway', 'runtime-api': 'Runtime API', infra: 'Infrastructure', agentrunner: 'Cloud runners', observability: 'Monitoring', 'sandsql-testkit': 'Database server' }],
];
const TIMELINE_FILTERS = [
  ['key', 'Key events', (e) => ['owner', 'go', 'landed', 'release', 'incident', 'fix-live', 'hold', 'lift'].includes(e.kind) || (e.kind === 'report' && /READY/.test(e.raw))],
  ['owner', 'You said', (e) => e.kind === 'owner'],
  ['go', 'Approvals and landings', (e) => e.kind === 'go' || e.kind === 'landed'],
  ['incident', 'Releases and incidents', (e) => ['release', 'incident', 'fix-live', 'mechanism'].includes(e.kind)],
  ['all', 'Everything', () => true],
];
const KIND_WORD = { owner: 'You', go: 'Approved to land', landed: 'Landed', release: 'Release', incident: 'Incident', mechanism: 'Cause found', 'fix-live': 'Fix live', opened: 'Opened', report: 'Report', hold: 'Held', lift: 'Hold lifted', blocker: 'Blocked', defect: 'Tool problem', correction: 'Correction', decide: 'Decision', ask: 'Question', answer: 'Answer' };

const state = { model: null, version: -1, open: new Set(), filter: 'key', seen: new Set(), chat: [], asking: false };
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
  return `${h} h ${m % 60} min ago`;
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
  const v = row.verdict;
  if (!v || v.verdict === 'cleared') return 'you';
  const current = (v.head ?? '').slice(0, 7) === row.head.slice(0, 7);
  if (!current) return 'you';
  return v.verdict === 'approved' ? 'approved' : 'held';
}
function prState(n) {
  const pr = state.model.prs[n];
  if (pr?.state === 'landed') return 'landed';
  if (pr?.state === 'closed-without-squash') return 'closed';
  const row = boardRow(n);
  if (row && verdictState(row) === 'you') return 'you';
  if (pr?.test_state === 'failure') return 'failing';
  if (pr?.hold_reason && pr.hold_until && new Date(pr.hold_until) > new Date()) return 'held';
  return 'work';
}
const STATE_WORD = { landed: 'Landed', closed: 'Closed', you: 'Waiting on you', failing: 'Tests failing', held: 'Held', work: 'On its way' };
const STATE_TONE = { landed: 'ok', closed: '', you: 'bad', failing: 'bad', held: 'warn', work: 'acc' };
function prHref(n) {
  return boardRow(n) ? `#/review/${n}` : `https://github.com/${state.model.repo}/pull/${n}`;
}
function prLink(n) {
  const external = !boardRow(n);
  return `<a href="${prHref(n)}"${external ? ' target="_blank" rel="noopener"' : ''} title="${esc(plainTitle(n))} · ${STATE_WORD[prState(n)]}">#${n}</a>`;
}
function squares(numbers) {
  return `<span class="sqs">${numbers.map((n) => `<a class="sq ${prState(n)}" href="${prHref(n)}" title="#${n} ${esc(plainTitle(n))} · ${STATE_WORD[prState(n)]}"></a>`).join('')}</span>`;
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
  for (const line of md.split('\n')) {
    const item = /^\s*(?:[-*]|\d+\.)\s+(.*)$/.exec(line);
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (!item && list) { out.push(`</${list}>`); list = null; }
    if (item) {
      const kind = /^\s*\d+\./.test(line) ? 'ol' : 'ul';
      if (list !== kind) { if (list) out.push(`</${list}>`); out.push(`<${kind}>`); list = kind; }
      out.push(`<li>${inline(item[1])}</li>`);
    } else if (heading) {
      out.push(`<h${heading[1].length + 1}>${inline(heading[2])}</h${heading[1].length + 1}>`);
    } else if (line.trim()) {
      out.push(`<p>${inline(line)}</p>`);
    }
  }
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
    if (r.kind === 'owner' || owner) { kind = 'owner'; who = 'You'; text = owner ? owner[1] : r.text; }
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

function needsAttention() {
  const m = state.model;
  const items = [];
  const waiting = new Map();
  for (const row of m.board.rows) {
    if (verdictState(row) !== 'you') continue;
    if (!waiting.has(row.group)) waiting.set(row.group, []);
    waiting.get(row.group).push(row);
  }
  for (const [group, rows] of waiting) {
    items.push({
      id: `tap-${group}`,
      what: `Approve or hold ${plural(rows.length, 'pull request')}`,
      why: `${group}. ${rows[0].group_summary}`,
      cue: 'Review',
      body: `<ul>${rows.map((r) => `<li><a href="#/review/${r.number}"><b>#${r.number}</b> ${esc(r.title)}</a></li>`).join('')}</ul>`,
    });
  }
  for (const card of m.board.cards.filter((c) => !c.answered)) {
    items.push({
      id: `card-${card.id}`,
      what: card.title,
      why: card.summary,
      cue: 'Answer',
      body: `${card.recommended ? `<p>Recommended: <b>${esc(card.recommended)}</b></p>` : ''}<p><a class="btn" href="${esc(m.board.url)}" target="_blank" rel="noopener">Answer on the board</a></p>`,
    });
  }
  for (const ask of m.story.asks.filter((a) => !a.optional)) {
    items.push({ id: `ask-${ask.what}`, what: ask.what, why: ask.why, cue: 'Do', body: ask.link ? `<p><a href="${esc(ask.link)}" target="_blank" rel="noopener">${esc(ask.link_text ?? 'Open')}</a></p>` : '' });
  }
  return items;
}
function attnCard(item, i) {
  const open = state.open.has(item.id) ? ' open' : '';
  return `<details data-id="${esc(item.id)}"${open}><summary><span class="no">${i + 1}</span><span class="cue">${item.cue} ›</span>
    <span class="what">${esc(item.what)}</span><span class="why">${inline(item.why)}</span></summary>${item.body}</details>`;
}

function planNumbers() {
  return [...new Set(state.model.plan.flatMap((s) => s.prs))].filter((n) => prState(n) !== 'closed');
}
function mapCard() {
  const clusters = SCOPES.map(([title, scopes]) => {
    const nodes = new Map();
    for (const [n, pr] of Object.entries(state.model.prs)) {
      const scope = (pr.title ?? '').split(':')[0];
      const name = scopes[scope];
      if (!name || pr.state === 'closed-without-squash' || !pr.state) continue;
      if (!nodes.has(name)) nodes.set(name, { landed: 0, open: 0, failing: 0, you: 0 });
      const node = nodes.get(name);
      const s = prState(n);
      if (s === 'landed') node.landed++;
      else node.open++;
      if (s === 'failing') node.failing++;
      if (s === 'you') node.you++;
    }
    const html = [...nodes.entries()].sort((a, b) => b[1].failing - a[1].failing || b[1].open - a[1].open).map(([name, c]) => {
      const total = c.landed + c.open;
      const tone = c.failing ? 'bad' : c.open ? 'work' : 'ok';
      return `<div class="node ${tone}" title="${c.landed} landed, ${c.open} open, ${c.failing} with failing tests, ${c.you} waiting on you"><span class="name">${esc(name)}</span>
        <span class="dim num">${c.landed} of ${total} landed</span>${c.failing ? `<span class="badge" title="pull requests with failing tests">${c.failing}</span>` : '<span></span>'}
        <span class="bar"><i style="width:${(100 * c.landed) / total}%;background:var(--ok)"></i><i style="width:${(100 * (c.open - c.failing)) / total}%;background:var(--accent)"></i><i style="width:${(100 * c.failing) / total}%;background:var(--bad)"></i></span></div>`;
    }).join('');
    return `<div><h3>${title}</h3>${html}</div>`;
  });
  return `<div class="card"><div class="kick muted" style="margin-bottom:10px">Where the work is · landed of all pull requests, red badges count failing tests</div><div class="map">${clusters.join('')}</div></div>`;
}

function overview() {
  const m = state.model;
  const all = planNumbers();
  const landed = all.filter((n) => prState(n) === 'landed').length;
  const attention = needsAttention();
  const events = timeline().filter(TIMELINE_FILTERS[0][2]).slice(0, 8);
  const lanes = activeLanes();
  const stalls = lanes.filter((l) => l.stall);
  const steps = m.plan.map((s, i) => {
    const nums = s.prs.filter((n) => prState(n) !== 'closed');
    const done = nums.filter((n) => prState(n) === 'landed').length;
    const id = `step-${i}`;
    const open = state.open.has(id);
    return `<div class="step" data-toggle="${id}"><span class="n">${i + 1}</span><span class="t">${esc(s.title)}</span><span class="c">${nums.length ? `${done} of ${nums.length} landed` : ''}</span>
      ${nums.length ? squares(nums) : ''}${open ? `<div class="step-detail">${inline(s.text || s.title)}</div>` : ''}</div>`;
  }).join('');
  return `
  <section class="hero">
    <div class="card">
      <div class="kick">What the rebuild delivers</div>
      <p class="thesis">${esc(m.story.now)}</p>
      <div class="score">
        <div class="big">${landed}<small> / ${all.length}</small></div>
        <div><div style="font-weight:650">pull requests in the plan have landed</div><div class="next"><span class="pill acc">Next: ${esc(m.story.release.what)}</span><a href="#/releases">Release plan ›</a></div></div>
      </div>
      <div class="kick muted" style="margin-top:18px">Plan of action, in order${m.plan_at ? ` · revised ${clock(m.plan_at)}` : ''}</div>
      <div class="steps">${steps}</div>
      <div class="legend"><span><i class="sq landed"></i>landed</span><span><i class="sq"></i>on its way</span><span><i class="sq you"></i>waiting on you</span><span><i class="sq failing"></i>tests failing</span><span><i class="sq held"></i>held</span></div>
    </div>
    ${mapCard()}
  </section>
  <h2 class="sec">Needs your attention ${attention.length ? `<span class="pill bad">${attention.length}</span>` : '<span class="pill ok">Nothing</span>'}<a href="#/review">All pull requests ›</a></h2>
  <div class="attn">${attention.slice(0, 3).map(attnCard).join('') || '<div class="dim">Nothing waits on you.</div>'}</div>
  ${attention.length > 3 ? `<details class="more-attn" data-id="attn-more"${state.open.has('attn-more') ? ' open' : ''}><summary class="dim">${attention.length - 3} more</summary><div class="attn" style="margin-top:10px">${attention.slice(3).map((it, i) => attnCard(it, i + 3)).join('')}</div></details>` : ''}
  <section class="two">
    <div><h2 class="sec">Latest <a href="#/timeline">Timeline ›</a></h2><div class="card"><ul class="tl">${timelineRows(events, false)}</ul></div></div>
    <div><h2 class="sec">Workers ${stalls.length ? `<span class="pill bad">${stalls.length} quiet</span>` : ''}<a href="#/workers">All workers ›</a></h2><div class="card"><ul class="lanes">${lanes.slice(0, 7).map(laneRow).join('')}</ul></div></div>
  </section>`;
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
function laneRow(l) {
  return `<li><span class="name">${esc(l.lane)}${l.holds ? ` <span class="pill">${plural(l.holds, 'open pull request')}</span>` : ''}</span><span class="ago${l.stall ? ' stall' : ''}">${l.stall ? 'quiet ' : ''}${agoWords(l.at)}</span><span class="say">${esc(firstSentences(l.text, 200))}</span></li>`;
}
function workers() {
  const lanes = activeLanes();
  return `<h2 class="sec">Workers <span class="pill">${lanes.length} active in the last 3 hours or holding a pull request</span>${lanes.some((l) => l.stall) ? `<span class="pill bad">${lanes.filter((l) => l.stall).length} quiet over ${STALL_MIN} min</span>` : ''}</h2>
    <div class="card"><ul class="lanes">${lanes.map(laneRow).join('')}</ul></div>`;
}

function timelineScreen() {
  const filter = TIMELINE_FILTERS.find((f) => f[0] === state.filter) ?? TIMELINE_FILTERS[0];
  const events = timeline().filter(filter[2]);
  return `<h2 class="sec">Timeline <span class="dim">last 24 hours, newest first</span></h2>
    <div class="filters">${TIMELINE_FILTERS.map(([id, label]) => `<button data-filter="${id}" class="${id === filter[0] ? 'on' : ''}">${label}</button>`).join('')}</div>
    <div class="card"><ul class="tl">${timelineRows(events, true) || '<li class="dim">Nothing yet.</li>'}</ul></div>`;
}

function releases() {
  const r = state.model.releases;
  return `<div class="prose">${r.updated ? `<p class="dim">Last revised ${clock(r.updated)}</p>` : ''}${markdown(r.markdown)}</div>`;
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
  const answer = state.chat[state.chat.length - 1];
  const res = await fetch('/api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question, history }) });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split('\n\n');
    buffer = events.pop();
    for (const event of events) {
      const kind = /^event: (\w+)/m.exec(event)?.[1];
      const data = /^data: (.*)$/m.exec(event)?.[1];
      if (kind === 'token') answer.content += JSON.parse(data);
      if (kind === 'failed') answer.content += `\n\nThe answer stopped: ${JSON.parse(data)}`;
    }
    if (route().screen === 'ask') $('#main .chat').innerHTML = chatTurns();
  }
  state.asking = false;
  render();
}

function route() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  return { screen: parts[0] ?? 'overview', id: parts[1], tab: parts[2] };
}
function render() {
  const m = state.model;
  if (!m || !m.prs) return;
  const r = route();
  document.title = m.title;
  $('.brand').textContent = m.title;
  const waiting = m.board.rows.filter((row) => verdictState(row) === 'you').length;
  const tabs = [['overview', 'Overview'], ['review', 'Review', waiting], ['releases', 'Releases'], ['workers', 'Workers'], ['timeline', 'Timeline'], ['ask', 'Ask']];
  $('#tabs').innerHTML = tabs.map(([id, label, count]) => `<a class="tab${r.screen === id ? ' on' : ''}" href="#/${id === 'overview' ? '' : id}">${label}${count ? `<span class="pill bad count">${count}</span>` : ''}</a>`).join('');
  const screens = { overview, review: () => (r.id ? reviewPR(r.id, r.tab) : reviewList()), releases, workers, timeline: timelineScreen, ask: askScreen };
  const y = window.scrollY;
  $('#main').innerHTML = (screens[r.screen] ?? overview)() + `<div class="foot">${m.errors && Object.keys(m.errors).length ? `Some sources failed: ${esc(Object.values(m.errors).join('; '))}. ` : ''}All times Pacific.</div>`;
  window.scrollTo(0, y);
  for (const e of timeline()) state.seen.add(e.id);
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
  state.version = model.version;
  render();
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
  const suggestion = e.target.closest('[data-ask]');
  if (suggestion) ask(suggestion.dataset.ask);
  const filter = e.target.closest('[data-filter]');
  if (filter) { state.filter = filter.dataset.filter; render(); }
});
document.addEventListener('submit', (e) => {
  if (e.target.id !== 'ask') return;
  e.preventDefault();
  ask(e.target.q.value);
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && e.target.matches('#ask textarea')) { e.preventDefault(); ask(e.target.value); }
});
window.addEventListener('hashchange', () => { window.scrollTo(0, 0); render(); });
setInterval(tickFresh, 1000);
load();
connect();
