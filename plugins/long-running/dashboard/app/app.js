const PACIFIC = 'America/Los_Angeles';
const STALL_MIN = 45;
const LATE_MS = 3 * 60 * 1000;
const clockFmt = new Intl.DateTimeFormat('en-US', { timeZone: PACIFIC, hour: 'numeric', minute: '2-digit' });
const dayFmt = new Intl.DateTimeFormat('en-US', { timeZone: PACIFIC, weekday: 'short' });
const SCOPES = [
  ['On the Mac', { sand: 'Mac agent', iris: 'Iris app', 'iris-desktop': 'Desktop', desktop: 'Desktop', localrun: 'Local runs', vm_run: 'Sand VM', 'sandsql-journal': 'Chat journal', 'sandsql-journal-next': 'Chat journal' }],
  ['In the cloud', { api: 'API', dashboard: 'Dashboard', sandsql: 'Database', 'sandsql-server': 'Database', 'llm-gateway': 'Gateway', 'runtime-api': 'Runtime API', infra: 'Infra', agentrunner: 'Runners', observability: 'Monitoring', 'sandsql-testkit': 'Database' }],
];
const TIMELINE_FILTERS = [
  ['key', 'Key events', (e) => ['owner', 'go', 'landed', 'release', 'incident', 'fix-live', 'hold', 'lift'].includes(e.kind) || (e.kind === 'report' && /READY/.test(e.raw))],
  ['owner', 'You said', (e) => e.kind === 'owner'],
  ['go', 'Approvals and landings', (e) => e.kind === 'go' || e.kind === 'landed'],
  ['incident', 'Releases and incidents', (e) => ['release', 'incident', 'fix-live', 'mechanism'].includes(e.kind)],
  ['all', 'Everything', () => true],
];
const KIND_WORD = { owner: 'You', go: 'Approved to land', landed: 'Landed', release: 'Release', incident: 'Incident', mechanism: 'Cause found', 'fix-live': 'Fix live', opened: 'Opened', report: 'Report', hold: 'Held', lift: 'Hold lifted', blocker: 'Blocked', defect: 'Tool problem', correction: 'Correction', decide: 'Decision', ask: 'Question', answer: 'Answer' };

const state = { model: null, version: -1, open: new Set(), filter: 'key', seen: new Set(), chat: [], asking: false, tree: 'new', overlay: null, activity: {}, activityAt: -1 };
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
  const atHead = (sha) => Boolean(sha) && sha.slice(0, 7) === row.head.slice(0, 7);
  if (v && v.verdict !== 'cleared' && atHead(v.head)) return v.verdict === 'approved' ? 'approved' : 'held';
  return atHead(row.go_head) ? 'approved' : 'you';
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
const RANK = { landed: 0, doing: 1, work: 2, you: 3, failing: 4, held: 5, closed: 6 };
function prRank(n) {
  return RANK[heat(n)];
}
function activity() {
  if (state.activityAt === state.version) return state.activity;
  const since = Date.now() - 30 * 60 * 1000;
  const seen = {};
  for (const r of [...state.model.records, ...state.model.lanes]) {
    if (COORDINATORS.test(r.lane) || new Date(r.at) < since) continue;
    const numbers = new Set([...(/^\d{5}$/.test(r.topic ?? '') ? [r.topic] : []), ...[...r.text.matchAll(/#(4\d{4})\b/g)].map((m) => m[1])]);
    for (const n of numbers) if (!seen[n] || seen[n].at < r.at) seen[n] = { lane: r.lane, at: r.at };
  }
  state.activity = seen;
  state.activityAt = state.version;
  return seen;
}
const COORDINATORS = /^(root|main)$|desk|refresher|watchdog|register|landing|board|prompt|fidelity|progress|comms|tooling|finder|sweep|batch-post/;
function heat(n) {
  const s = prState(n);
  if (s !== 'work') return s;
  return activity()[n] || state.model.prs[n]?.test_state === 'pending' ? 'doing' : 'work';
}
const HEAT_WORD = { landed: 'landed', doing: 'being worked on now', work: 'queued', you: 'waiting on you', failing: 'tests failing', held: 'held', closed: 'closed' };
function square(n) {
  const h = heat(n);
  return `<a class="sq ${h}" href="${prHref(n)}" title="#${n} ${esc(plainTitle(n))} · ${HEAT_WORD[h]}"></a>`;
}
function workersOn(numbers) {
  const set = new Set(numbers.map(String));
  const since = Date.now() - 20 * 60 * 1000;
  const lanes = new Map();
  for (const [n, a] of Object.entries(activity())) if (set.has(n) && new Date(a.at) > since) lanes.set(a.lane, n);
  const bots = [...lanes.entries()].map(([lane, n]) => `<a class="bot" href="${prHref(n)}" title="${esc(lane)} is on #${n} ${esc(plainTitle(n))}"><i></i>${esc(lane)}</a>`);
  return bots.slice(0, 3).join('') + (bots.length > 3 ? `<span class="bot more">+${bots.length - 3}</span>` : '');
}
function tile(name, numbers) {
  const sorted = numbers.sort((a, b) => prRank(a) - prRank(b));
  const done = sorted.filter((n) => heat(n) === 'landed').length;
  const pct = Math.round((100 * done) / sorted.length);
  const grow = Math.max(1, Math.round(Math.sqrt(sorted.length) * 10));
  return `<div class="ptile${pct === 100 ? ' complete' : ''}" style="flex-grow:${grow}" data-tile="${esc(name)}">
    <div class="ptile-h"><b>${esc(name)}</b><span class="pct">${pct}%</span></div>
    <div class="mosaic">${sorted.map(square).join('')}</div>
    <div class="bots">${workersOn(sorted)}</div></div>`;
}
function stage() {
  const numbers = liveNumbers();
  const [target, ...rest] = state.model.tree_names;
  const trees = [...rest, target];
  const lanes = trees.map((tree) => {
    const parts = new Map();
    for (const n of numbers) {
      if (!(state.model.trees[n] ?? []).includes(tree)) continue;
      const part = partOf(n)?.[1];
      if (!part) continue;
      if (!parts.has(part)) parts.set(part, []);
      parts.get(part).push(n);
    }
    const all = [...parts.values()].flat();
    const done = all.filter((n) => heat(n) === 'landed').length;
    const key = tree.split(' ')[0].toLowerCase();
    return `<section class="lane lane-${key}${state.tree === key ? ' picked' : ''}"><header><span class="lane-name">${esc(TREE_SHORT[tree] ?? tree)}</span><span class="lane-pct">${all.length ? Math.round((100 * done) / all.length) : 0}%</span></header>
      <div class="ptiles">${[...parts.entries()].sort((a, b) => b[1].length - a[1].length).map(([name, nums]) => tile(name, nums)).join('')}</div></section>`;
  });
  return `<div class="stage">${lanes.join('<div class="flow" aria-hidden="true"></div>')}</div>`;
}
const TREE_SHORT = { 'Old tree': 'Old tree · retires at cutover', Shared: 'Shared code', 'New tree': 'New tree · the end state' };
function journey() {
  const steps = state.model.plan;
  let here = steps.findIndex((s) => s.prs.some((n) => !['landed', 'closed'].includes(prState(n))));
  if (here < 0) here = steps.length;
  return `<ol class="journey">${steps.map((s, i) => {
    const nums = s.prs.filter((n) => prState(n) !== 'closed');
    const done = nums.filter((n) => prState(n) === 'landed').length;
    const pct = nums.length ? (100 * done) / nums.length : i < here ? 100 : 0;
    const label = s.title.split(/[,(;]/)[0].split(' ').slice(0, 4).join(' ');
    return `<li class="${i < here ? 'past' : i === here ? 'here' : ''}" data-overlay="plan" data-step="${i}" title="${esc(s.title)}${nums.length ? ` · ${done} of ${nums.length} landed` : ''}">
      <span class="node" style="--p:${pct}%">${nums.length ? '' : '◆'}</span><span class="cap">${esc(label)}</span></li>`;
  }).join('')}<li class="end" title="Cutover: the old tree is deleted"><span class="node">⚑</span><span class="cap">Cutover</span></li></ol>`;
}
const TICK_ICON = { landed: '✓', go: '▲', owner: '★', release: '⇪', incident: '!', 'fix-live': '✓', hold: '‖', lift: '▶', report: '●' };
function ticker() {
  const events = timeline().filter(TIMELINE_FILTERS[0][2]).slice(0, 6);
  return events.map((e) => {
    const n = /#(4\d{4})/.exec(e.text)?.[1];
    const what = e.kind === 'owner' ? 'You said' : n ? `#${n}` : KIND_WORD[e.kind];
    return `<a class="tick k-${e.kind}" href="#/timeline" title="${esc(e.text)}"><i>${TICK_ICON[e.kind] ?? '•'}</i><b>${esc(what)}</b><span>${n ? esc(plainTitle(n)) : esc(firstSentences(e.text, 40))}</span><time>${clock(e.at)}</time></a>`;
  }).join('');
}
function overview() {
  const m = state.model;
  const all = planNumbers();
  const landed = all.filter((n) => prState(n) === 'landed').length;
  const doing = liveNumbers().filter((n) => heat(n) === 'doing').length;
  const needs = m.board.rows.filter((r) => verdictState(r) === 'you').length + m.board.cards.filter((c) => !c.answered).length;
  const pct = all.length ? Math.round((100 * landed) / all.length) : 0;
  return `<div class="ov">
  <section class="hero">
    <div class="big" title="${landed} of ${all.length} pull requests in the plan have landed"><span class="bigpct" style="--p:${pct}%"><span>${pct}<small>%</small></span></span><span class="biglab">landed<br><b>${doing}</b> moving now</span></div>
    ${journey()}
    ${needs ? `<a class="needs" href="#/now"><b>${needs}</b><span>need you</span></a>` : '<span class="needs calm"><b>✓</b><span>nothing needs you</span></span>'}
  </section>
  <div class="seg phone-only">${['old', 'shared', 'new'].map((k) => `<button data-tree="${k}" class="${(state.tree ?? 'new') === k ? 'on' : ''}">${k === 'old' ? 'Old' : k === 'shared' ? 'Shared' : 'New'}</button>`).join('')}</div>
  ${stage()}
  <div class="key"><span><i class="sq landed"></i>landed</span><span><i class="sq doing"></i>being worked on</span><span><i class="sq work"></i>queued</span><span><i class="sq you"></i>needs you</span><span><i class="sq failing"></i>tests failing</span></div>
  <div class="ticker">${ticker()}</div>
  </div>
  ${state.overlay === 'plan' ? `<div class="overlay" data-close="1"><div class="sheet"><button class="btn close" data-close="1">Close</button><h2>The plan of action, in order</h2><ol class="psteps">${planSteps(true)}</ol><h2>The release plan</h2><div class="prose">${markdown(m.releases.markdown)}</div></div></div>` : ''}`;
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
function render() {
  const m = state.model;
  if (!m || !m.prs) return;
  const r = route();
  document.title = m.title;
  $('.brand').textContent = m.title;
  const waiting = m.board.rows.filter((row) => verdictState(row) === 'you').length;
  const tabs = [['overview', 'Overview'], ['now', 'Needs you', needsCount()], ['review', 'Review', waiting], ['releases', 'Releases'], ['workers', 'Workers'], ['timeline', 'Timeline'], ['ask', 'Ask']];
  $('#tabs').innerHTML = tabs.map(([id, label, count]) => `<a class="tab${r.screen === id ? ' on' : ''}" href="#/${id === 'overview' ? '' : id}">${label}${count ? `<span class="pill bad count">${count}</span>` : ''}</a>`).join('');
  const screens = { overview, now: needsYou, review: () => (r.id ? reviewPR(r.id, r.tab) : reviewList()), releases, workers, timeline: timelineScreen, ask: askScreen };
  const y = window.scrollY;
  $('#main').innerHTML = (screens[r.screen] ?? overview)() + `<div class="foot">${m.errors && Object.keys(m.errors).length ? `Some sources failed: ${esc(Object.values(m.errors).join('; '))}. ` : ''}All times Pacific.</div>`;
  decorate(r);
  window.scrollTo(0, y);
  syncReviewEmbed(r);
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
  const tree = e.target.closest('[data-tree]');
  if (tree) { state.tree = tree.dataset.tree; render(); }
  const close = e.target.closest('[data-close]');
  if (close && (e.target === close || close.matches('button'))) { state.overlay = null; render(); return; }
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
