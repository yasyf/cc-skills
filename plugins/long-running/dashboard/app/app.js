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

const state = { model: null, version: -1, open: new Set(), filter: 'key', seen: new Set(), chat: [], asking: false, map: 'part', overlay: null };
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

function planNumbers() {
  return [...new Set(state.model.plan.flatMap((s) => s.prs))].filter((n) => prState(n) !== 'closed');
}
function dots(numbers) {
  return `<span class="dots">${numbers.map((n) => `<a class="d ${prState(n)}" href="${prHref(n)}" title="#${n} ${esc(plainTitle(n))} · ${STATE_WORD[prState(n)]}"></a>`).join('')}</span>`;
}
function partOf(n) {
  const scope = (state.model.prs[n]?.title ?? '').split(':')[0];
  for (const [zone, scopes] of SCOPES) if (scopes[scope]) return [zone, scopes[scope]];
  return null;
}
function liveNumbers() {
  return Object.keys(state.model.prs).filter((n) => ['open', 'landed'].includes(state.model.prs[n].state));
}
function partBoxes(numbers) {
  const parts = new Map();
  for (const n of numbers) {
    const part = partOf(n);
    if (!part) continue;
    if (!parts.has(part[1])) parts.set(part[1], []);
    parts.get(part[1]).push(n);
  }
  return [...parts.entries()].sort((a, b) => b[1].length - a[1].length).map(([name, nums]) => {
    const done = nums.filter((n) => prState(n) === 'landed').length;
    return `<div class="part"><div class="part-h"><b>${esc(name)}</b><span class="num">${Math.round((100 * done) / nums.length)}%</span></div>${dots(nums.sort((a, b) => prRank(a) - prRank(b)))}</div>`;
  }).join('');
}
const RANK = { landed: 0, work: 1, you: 2, failing: 3, held: 4, closed: 5 };
function prRank(n) {
  return RANK[prState(n)];
}
function endState() {
  const numbers = liveNumbers();
  const view = state.map;
  let body;
  if (view === 'tree') {
    const columns = state.model.tree_names.map((tree) => {
      const nums = numbers.filter((n) => (state.model.trees[n] ?? []).includes(tree));
      return `<div class="zone tree-${tree.split(' ')[0].toLowerCase()}"><div class="zone-h">${esc(tree)}<span class="dim">${TREE_NOTE[tree] ?? ''}</span></div>${partBoxes(nums) || '<div class="dim">Reading each pull request\'s files…</div>'}</div>`;
    });
    const unread = numbers.filter((n) => !state.model.trees[n]).length;
    body = `<div class="zones three">${columns.join('<div class="arrow">→</div>')}</div>${unread ? `<div class="dim">${unread} pull requests not read yet; they appear as their files are read.</div>` : ''}`;
  } else {
    body = `<div class="zones">${SCOPES.map(([zone, scopes]) => `<div class="zone"><div class="zone-h">${zone}</div>${partBoxes(numbers.filter((n) => partOf(n)?.[0] === zone))}</div>`).join('<div class="arrow">⇄</div>')}</div>`;
  }
  return `<div class="card endstate"><div class="es-h"><div><div class="kick">The end state</div><div class="es-t">${view === 'tree' ? 'At cutover the new tree replaces the old one; shared code serves both until then.' : 'Every Mac runs the new build against the new cloud services. Each dot is one pull request.'}</div></div>
    <div class="seg"><button data-map="part" class="${view === 'tree' ? '' : 'on'}">By part</button><button data-map="tree" class="${view === 'tree' ? 'on' : ''}">Old and new tree</button></div></div>${body}
    <div class="legend"><span><i class="d landed"></i>landed</span><span><i class="d work"></i>on its way</span><span><i class="d you"></i>waiting on you</span><span><i class="d failing"></i>tests failing</span><span><i class="d held"></i>held</span></div></div>`;
}
const TREE_NOTE = { 'New tree': 'what every Mac runs after cutover', 'Old tree': 'keeps working until cutover, then deleted', Shared: 'cloud and app code both trees use' };
function donut(numbers) {
  const counts = ['landed', 'work', 'you', 'failing', 'held'].map((k) => [k, numbers.filter((n) => prState(n) === k).length]);
  const total = numbers.length || 1;
  let offset = 0;
  const arcs = counts.map(([k, c]) => {
    const len = (c / total) * 100;
    const arc = `<circle class="arc ${k}" r="15.915" cx="20" cy="20" stroke-dasharray="${len} ${100 - len}" stroke-dashoffset="${25 - offset}"></circle>`;
    offset += len;
    return arc;
  });
  return `<svg viewBox="0 0 40 40" class="donut"><circle class="arc track" r="15.915" cx="20" cy="20"></circle>${arcs.join('')}</svg>`;
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
    const nums = s.prs.filter((n) => prState(n) !== 'closed').sort((a, b) => prRank(a) - prRank(b));
    const done = nums.filter((n) => prState(n) === 'landed').length;
    return `<li class="pstep${nums.length && done === nums.length ? ' done' : ''}"><span class="n">${i + 1}</span><div><div class="pt">${esc(s.title)}${nums.length ? `<span class="dim num"> ${done} of ${nums.length} landed</span>` : ''}</div>${nums.length ? dots(nums) : ''}${full && s.text ? `<p>${inline(s.text)}</p>` : ''}</div></li>`;
  }).join('');
}
function overview() {
  const m = state.model;
  const all = planNumbers();
  const landed = all.filter((n) => prState(n) === 'landed').length;
  const waiting = m.board.rows.filter((r) => verdictState(r) === 'you').length;
  const cards = m.board.cards.filter((c) => !c.answered).length;
  return `
  <section class="glance">
    ${endState()}
    <div class="card prog">
      <div class="kick">Progress</div>
      <div class="ring">${donut(all)}<div class="ring-n"><b>${landed}</b><span>of ${all.length} landed</span></div></div>
      <div class="kv"><a href="#/review" class="${waiting ? 'hot' : ''}"><b>${waiting}</b> waiting on your tap</a><a href="${esc(m.board.url)}" target="_blank" rel="noopener" class="${cards ? 'hot' : ''}"><b>${cards}</b> questions on the board</a></div>
      <div class="nextrel"><div class="kick muted">Next release</div>${esc(m.story.release.what)}<a href="#/releases"> Release plan ›</a></div>
    </div>
  </section>
  <h2 class="sec">Working now <span class="dim">each box is a worker that wrote in the last 20 minutes</span><a href="#/workers">All workers ›</a></h2>
  <div class="lives">${workingNow()}</div>
  <h2 class="sec">The plan, in order <span class="dim">${m.plan_at ? `revised ${clock(m.plan_at)}` : ''}</span><a href="#" data-overlay="plan">Open the full plan ⤢</a></h2>
  <div class="card planprev" data-overlay="plan"><ol class="psteps">${planSteps(false)}</ol></div>
  ${state.overlay === 'plan' ? `<div class="overlay" data-close="1"><div class="sheet"><button class="btn close" data-close="1">Close</button><h2>The plan of action, in order</h2><ol class="psteps">${planSteps(true)}</ol><h2>The release plan</h2><div class="prose">${markdown(m.releases.markdown)}</div></div></div>` : ''}`;
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
  const lanes = activeLanes();
  const stalls = lanes.filter((l) => l.stall).length;
  return `<h2 class="sec">Workers <span class="pill">${lanes.length}</span>${stalls ? `<span class="pill bad">${stalls} quiet while holding a pull request</span>` : ''}</h2><div class="lives">${lanes.map(liveBox).join('')}</div>`;
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
  syncReviewEmbed(r);
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
  const map = e.target.closest('[data-map]');
  if (map) { state.map = map.dataset.map; render(); }
  const close = e.target.closest('[data-close]');
  if (close && (e.target === close || close.matches('button'))) { state.overlay = null; render(); return; }
  const overlay = e.target.closest('[data-overlay]');
  if (overlay && !e.target.closest('a.d')) { e.preventDefault(); state.overlay = overlay.dataset.overlay; render(); }
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
  if (e.key === 'Escape' && state.overlay) { state.overlay = null; render(); }
  if (e.key === 'Enter' && !e.shiftKey && e.target.matches('#ask textarea')) { e.preventDefault(); ask(e.target.value); }
});
window.addEventListener('hashchange', () => { window.scrollTo(0, 0); render(); });
setInterval(tickFresh, 1000);
load();
connect();
