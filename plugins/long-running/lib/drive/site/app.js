const ZONE = 'America/Los_Angeles';
const STALL_MINUTES = 45;
const GAP_MINUTES = 30;
const KINDS = { question: 'Question', tap: 'Your tap', late: 'Late' };
const GROUPS = {
  blocked: 'Blocked',
  running: 'Running',
  waiting: 'Waiting',
  merged: 'Merged, not live yet',
  live: 'Live in the last day',
  'not started': 'Not started',
};
const FOLDED = new Set(['waiting', 'not started']);
const CHECKS = { pass: 'green', fail: 'red', pending: 'running', none: 'no CI' };
const JOB_STATES = { running: 'Running', waiting: 'Waiting', done: 'Done', failed: 'Failed', stopped: 'Stopped' };
const HAPPENED = { order: 'You asked', live: 'Live', merged: 'Merged', failed: 'Failed', refused: 'No card drawn' };
const FEATURE_KINDS = new Set(['order', 'live', 'failed']);
const HISTORY_FILTERS = [
  ['key', 'Key events', (entry) => ['live', 'merged', 'failed', 'tap'].includes(entry.kind)],
  ['you', 'You', (entry) => ['order', 'tap'].includes(entry.kind)],
  ['landed', 'Merged and live', (entry) => ['merged', 'live'].includes(entry.kind)],
  ['all', 'Everything', () => true],
];

let model = null;
let connected = false;
let lostAt = null;
let historyFilter = 'key';
const opened = new Map();

const $ = (selector) => document.querySelector(selector);
const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const link = (text, url) => (url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text));
const minutesSince = (iso) => Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));

function clock(iso) {
  const at = new Date(iso);
  const sameDay = at.toLocaleDateString('en-US', { timeZone: ZONE }) === new Date().toLocaleDateString('en-US', { timeZone: ZONE });
  const time = at.toLocaleTimeString('en-US', { timeZone: ZONE, hour: 'numeric', minute: '2-digit' });
  return sameDay ? time : `${at.toLocaleDateString('en-US', { timeZone: ZONE, month: 'short', day: 'numeric' })} ${time}`;
}

function ago(iso) {
  const minutes = minutesSince(iso);
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)}h`;
  return `${Math.round(minutes / 1440)}d`;
}

function route() {
  const [screen, id] = location.hash.replace(/^#\/?/, '').split('/');
  return { screen: screen || 'queue', id };
}

function features() {
  return model.status.flatMap((group) => group.features);
}

function featureLink(id, text) {
  return features().some((entry) => entry.id === id) ? `<a href="#/feature/${esc(id)}">${esc(text)}</a>` : esc(text);
}

function queueItem(item) {
  const when = item.kind === 'late' ? `promised ${clock(item.due)}, ${ago(item.due)} late` : item.since ? `waiting ${ago(item.since)}, since ${clock(item.since)}` : '';
  const extras = item.links.map((extra) => link(extra.label, extra.url));
  return `<li class="item ${item.kind}">
    <span class="kind">${KINDS[item.kind]}</span>
    <div class="body">
      <div class="title">${link(item.title, item.url)}</div>
      <div class="meta">${[esc(when), esc(item.detail), ...extras].filter(Boolean).join(' · ')}</div>
    </div>
  </li>`;
}

function queueScreen() {
  if (!model.queue.length) return '<p class="empty">Nothing waits on you.</p>';
  return `<ol class="queue">${model.queue.map(queueItem).join('')}</ol>`;
}

function prTone(pr) {
  return pr.state === 'open' ? pr.checks : pr.state;
}

function prSaid(pr) {
  const said = pr.state === 'open' ? CHECKS[pr.checks] ?? pr.checks : pr.state;
  const held = pr.tap === 'Hold' ? ', held by you' : pr.tap === 'wanted' ? ', wants your tap' : '';
  return said + held;
}

function prChip(pr) {
  return `<a class="chip ${esc(prTone(pr))}" href="${esc(pr.url)}" target="_blank" rel="noopener" title="${esc(prSaid(pr))}"><i></i>${esc(pr.title)}</a>`;
}

function prChips(prs) {
  const open = prs.filter((pr) => pr.state === 'open');
  const merged = prs.filter((pr) => pr.state === 'merged').length;
  const closed = prs.length - open.length - merged;
  const settled = [merged && `${merged} merged`, closed && `${closed} closed`].filter(Boolean).join(', ');
  return open.map(prChip).join('') + (settled ? `<span class="chip settled">${settled}</span>` : '');
}

function prBar(prs) {
  if (!prs.length) return '';
  const merged = prs.filter((pr) => pr.state === 'merged').length;
  const segments = prs.map((pr) => `<i class="${esc(prTone(pr))}" title="${esc(pr.title)} · ${esc(prSaid(pr))}"></i>`).join('');
  return `<span class="bar"><span class="segs">${segments}</span><span class="frac">${merged}/${prs.length} merged</span></span>`;
}

function due(entry) {
  return entry.due ? `<span class="due${entry.late ? ' late' : ''}">${entry.late ? 'late, was due' : 'due'} ${clock(entry.due)}</span>` : '';
}

function feature(entry) {
  return `<li class="feature" data-find="${esc([entry.title, ...entry.prs.map((pr) => pr.title)].join(' ').toLowerCase())}">
    <div class="title"><a href="#/feature/${esc(entry.id)}">${esc(entry.title)}</a> ${due(entry)} ${prBar(entry.prs)}</div>
    ${entry.now ? `<div class="now">${esc(entry.now)}</div>` : ''}
    ${entry.prs.length ? `<div class="chips">${prChips(entry.prs)}</div>` : ''}
  </li>`;
}

function progress() {
  const { prs, merged, open, red, running } = model.progress;
  const share = prs ? Math.round((100 * merged) / prs) : 0;
  const stalled = model.work.filter(isStalled).length;
  const facts = [
    `<b>${merged}</b> of ${prs} pull requests merged since the drive began`,
    `<b>${open}</b> open${red ? `, <b class="red">${red}</b> red` : ''}`,
    `<b>${running}</b> jobs running${stalled ? `, <a class="red" href="#/work"><b>${stalled}</b> quiet ${STALL_MINUTES} minutes or more</a>` : ''}`,
  ];
  return `<section class="progress"><span class="ring" style="--p:${share}%"><span>${share}<small>%</small></span></span><span class="facts">${facts.join('<br>')}</span></section>`;
}

function tiles() {
  const counts = model.counts;
  const tile = (label, value, tone, href) => `<a class="tile ${tone}" href="${href}"><b>${value}</b><span>${label}</span></a>`;
  return `<div class="tiles">${[
    tile('waiting on you', counts.queue, counts.queue ? 'you' : '', '#/'),
    ...Object.entries(GROUPS).map(([state, label]) => tile(label.toLowerCase(), counts[state], counts[state] ? state.replace(' ', '-') : '', `#group-${state.replace(' ', '-')}`)),
  ].join('')}</div>`;
}

function statusScreen() {
  const groups = model.status
    .filter((group) => group.features.length)
    .map((group) => {
      const id = group.state.replace(' ', '-');
      const open = opened.get(id) ?? !(FOLDED.has(group.state) && group.features.length > 8);
      return `<details class="group ${id}" id="group-${id}" data-group="${id}"${open ? ' open' : ''}>
        <summary><span class="dot"></span>${GROUPS[group.state]} <span class="count">${group.features.length}</span></summary>
        <ul class="features">${group.features.map(feature).join('')}</ul>
      </details>`;
    });
  return progress() + tiles() + (groups.join('') || '<p class="empty">No features yet.</p>');
}

function jobRow(job, withFeature) {
  const quiet = minutesSince(job.updated);
  const stall = isStalled(job) ? `<span class="badge red">quiet ${ago(job.updated)}</span>` : '';
  const owner = withFeature ? features().find((entry) => entry.id === job.feature) : null;
  const facts = [JOB_STATES[job.state] ?? job.state, job.kind, job.where, `last word ${quiet < 1 ? 'just now' : `${ago(job.updated)} ago`}`, job.prs ? `${job.prs} PRs` : ''];
  return `<li class="job ${esc(job.state)}">
    <span class="dot"></span>
    <div class="body">
      <div class="title">${esc(job.goal)} ${stall}</div>
      ${owner ? `<div class="meta">for ${featureLink(owner.id, owner.title)}</div>` : ''}
      ${job.said ? `<div class="now">${esc(job.said)}</div>` : ''}
      <div class="meta">${facts.filter(Boolean).map(esc).join(' · ')}</div>
    </div>
  </li>`;
}

function isStalled(job) {
  return job.state === 'running' && minutesSince(job.updated) >= STALL_MINUTES;
}

function workScreen() {
  const running = model.work.filter((job) => job.state === 'running');
  const stalled = running.filter(isStalled);
  const head = `<h2 class="sec">Work <span class="count">${running.length} running, ${model.work.length - running.length} waiting</span>${stalled.length ? `<span class="badge red">${stalled.length} quiet ${STALL_MINUTES} minutes or more</span>` : ''}</h2>`;
  if (!model.work.length) return `${head}<p class="empty">No job is running or waiting.</p>`;
  const ordered = [...stalled, ...model.work.filter((job) => !isStalled(job))];
  return `${head}<ul class="jobs card">${ordered.map((job) => jobRow(job, true)).join('')}</ul>`;
}

function historyRow(entry) {
  const text = FEATURE_KINDS.has(entry.kind) ? featureLink(entry.feature, entry.text) : link(entry.text, entry.url);
  return `<li class="happened ${esc(entry.kind)}">
    <span class="time">${clock(entry.at)}</span>
    <span class="dot"></span>
    <div class="body">
      <div><b class="what">${entry.kind === 'tap' ? `You tapped ${esc(entry.detail)} on` : HAPPENED[entry.kind]}</b> ${text}</div>
      ${entry.detail && entry.kind !== 'tap' ? `<div class="meta">${esc(entry.detail)}</div>` : ''}
    </div>
  </li>`;
}

function historyScreen() {
  const [, , keep] = HISTORY_FILTERS.find(([id]) => id === historyFilter);
  const shown = model.history.filter(keep);
  const rows = shown.flatMap((entry, i) => {
    const quiet = i ? Math.round((new Date(shown[i - 1].at) - new Date(entry.at)) / 60000) : 0;
    return [quiet >= GAP_MINUTES ? `<li class="gap">${quiet} min with nothing new</li>` : '', historyRow(entry)];
  });
  const filters = HISTORY_FILTERS.map(([id, label]) => `<button data-filter="${id}" class="${id === historyFilter ? 'on' : ''}">${label}</button>`).join('');
  return `<h2 class="sec">History <span class="count">last 24 hours, newest first</span></h2>
    <div class="filters">${filters}</div>
    <ul class="history card">${rows.join('') || '<li class="empty">Nothing yet.</li>'}</ul>`;
}

function featureScreen(id) {
  const entry = features().find((candidate) => candidate.id === id);
  if (!entry) return '<p class="empty">This feature is no longer on the dashboard. <a href="#/status">Back to Status</a></p>';
  const facts = [
    ['Where it is', `${GROUPS[entry.state]}${entry.now ? `: ${esc(entry.now)}` : ''}`],
    ['Asked', `${clock(entry.created)}${entry.url ? ` · ${link('Slack thread', entry.url)}` : ''}`],
    entry.due ? ['Promised', `${clock(entry.due)}${entry.late ? ' <b class="red">late</b>' : ''}`] : null,
    entry.proof ? ['Proof', esc(entry.proof)] : null,
    entry.note ? ['Note', esc(entry.note)] : null,
  ].filter(Boolean);
  const prs = entry.prs.map((pr) => `<li><a href="${esc(pr.url)}" target="_blank" rel="noopener"><i class="sq ${esc(prTone(pr))}"></i>${esc(pr.title)}</a><em>${esc(prSaid(pr))}, ${esc(pr.review === 'none' ? 'no review' : pr.review)}</em></li>`).join('');
  return `<a class="back" href="#/status">‹ Status</a>
    <div class="kick"><span class="dot ${entry.state.replace(' ', '-')}"></span>${GROUPS[entry.state]}${entry.prs.length ? ` · ${entry.prs.filter((pr) => pr.state === 'merged').length} of ${entry.prs.length} merged` : ''}</div>
    <h2 class="headline">${esc(entry.title)}</h2>
    <blockquote class="said">${esc(entry.text)}</blockquote>
    <dl class="factlist">${facts.map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`).join('')}</dl>
    ${prs ? `<h3 class="sec">Pull requests</h3><ul class="prlist card">${prs}</ul>` : ''}
    ${entry.jobs.length ? `<h3 class="sec">Jobs</h3><ul class="jobs card">${entry.jobs.map((job) => jobRow(job, false)).join('')}</ul>` : ''}`;
}

function find() {
  const words = $('#find').value.trim().toLowerCase();
  for (const row of document.querySelectorAll('.feature[data-find]')) row.hidden = Boolean(words) && !row.dataset.find.includes(words);
  for (const group of document.querySelectorAll('.group')) group.hidden = Boolean(words) && !group.querySelector('.feature:not([hidden])');
  if (words) for (const group of document.querySelectorAll('.group:not([hidden])')) group.open = true;
}

function render() {
  if (!model) return;
  const { screen, id } = route();
  document.title = `${model.drive}: ${model.counts.queue} waiting on you`;
  $('#brand').textContent = model.drive;
  const count = model.counts.queue ? `<span class="pill">${model.counts.queue}</span>` : '';
  const tab = (name, href, label) => `<a class="tab${screen === name || (name === 'status' && screen === 'feature') ? ' on' : ''}" href="${href}">${label}</a>`;
  $('#tabs').innerHTML = tab('queue', '#/', `Your queue${count}`) + tab('status', '#/status', 'Status') + tab('work', '#/work', 'Work') + tab('history', '#/history', 'History');
  const warning = model.cards === 'live' ? '' : `<p class="warn">Your Slack questions are not listed here: cc-slack ${esc(model.cards)}</p>`;
  const screens = { queue: queueScreen, status: statusScreen, work: workScreen, history: historyScreen, feature: () => featureScreen(id) };
  $('#findbar').hidden = screen !== 'status';
  const y = window.scrollY;
  $('#main').innerHTML = warning + (screens[screen] ?? queueScreen)();
  window.scrollTo(0, y);
  if (screen === 'status') find();
  fresh();
}

function fresh() {
  const el = $('#fresh');
  el.classList.toggle('late', !connected);
  if (!model) el.textContent = connected ? '' : 'Connecting…';
  else el.textContent = connected ? `Live · updated ${clock(model.at)} · Pacific` : `Disconnected since ${clock(lostAt)} · showing ${clock(model.at)}`;
}

function connect() {
  const stream = new EventSource('/api/stream');
  stream.onopen = () => {
    connected = true;
    fresh();
  };
  stream.onmessage = (message) => {
    model = JSON.parse(message.data);
    connected = true;
    render();
  };
  stream.onerror = () => {
    if (connected) lostAt = new Date().toISOString();
    connected = false;
    fresh();
  };
}

document.addEventListener('toggle', (event) => {
  const id = event.target.dataset?.group;
  if (id) opened.set(id, event.target.open);
}, true);
document.addEventListener('click', (event) => {
  const filter = event.target.closest('[data-filter]');
  if (!filter) return;
  historyFilter = filter.dataset.filter;
  render();
});
window.addEventListener('hashchange', () => {
  if (location.hash.startsWith('#group-')) return;
  window.scrollTo(0, 0);
  render();
});
window.addEventListener('DOMContentLoaded', () => {
  $('#find').addEventListener('input', find);
  setInterval(render, 60000);
  connect();
});
