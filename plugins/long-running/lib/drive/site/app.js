const REFRESH_MS = 10000;
const LATE_MS = 60000;
const ZONE = 'America/Los_Angeles';
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

let model = null;
let fetchedAt = 0;

const $ = (selector) => document.querySelector(selector);
const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const link = (text, url) => (url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text));

function clock(iso) {
  const at = new Date(iso);
  const sameDay = at.toLocaleDateString('en-US', { timeZone: ZONE }) === new Date().toLocaleDateString('en-US', { timeZone: ZONE });
  const time = at.toLocaleTimeString('en-US', { timeZone: ZONE, hour: 'numeric', minute: '2-digit' });
  return sameDay ? time : `${at.toLocaleDateString('en-US', { timeZone: ZONE, month: 'short', day: 'numeric' })} ${time}`;
}

function ago(iso) {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)}h`;
  return `${Math.round(minutes / 1440)}d`;
}

function screen() {
  return location.hash === '#/status' ? 'status' : 'queue';
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

function prChip(pr) {
  const tone = pr.state === 'merged' ? 'merged' : pr.state === 'closed' ? 'closed' : pr.checks;
  const said = pr.state === 'open' ? CHECKS[pr.checks] ?? pr.checks : pr.state;
  const held = pr.tap === 'Hold' ? ', held by you' : pr.tap === 'wanted' ? ', wants your tap' : '';
  return `<a class="chip ${esc(tone)}" href="${esc(pr.url)}" target="_blank" rel="noopener" title="${esc(said + held)}"><i></i>${esc(pr.title)}</a>`;
}

function prChips(prs) {
  const open = prs.filter((pr) => pr.state === 'open');
  const merged = prs.filter((pr) => pr.state === 'merged').length;
  const closed = prs.length - open.length - merged;
  const settled = [merged && `${merged} merged`, closed && `${closed} closed`].filter(Boolean).join(', ');
  return open.map(prChip).join('') + (settled ? `<span class="chip settled">${settled}</span>` : '');
}

function feature(entry) {
  const due = entry.due ? `<span class="due${entry.late ? ' late' : ''}">${entry.late ? 'late, was due' : 'due'} ${clock(entry.due)}</span>` : '';
  return `<li class="feature">
    <div class="title">${link(entry.title, entry.url)} ${due}</div>
    ${entry.now ? `<div class="now">${esc(entry.now)}</div>` : ''}
    ${entry.prs.length ? `<div class="chips">${prChips(entry.prs)}</div>` : ''}
  </li>`;
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
      const open = FOLDED.has(group.state) && group.features.length > 8 ? '' : ' open';
      return `<details class="group ${id}" id="group-${id}"${open}>
        <summary><span class="dot"></span>${GROUPS[group.state]} <span class="count">${group.features.length}</span></summary>
        <ul class="features">${group.features.map(feature).join('')}</ul>
      </details>`;
    });
  return tiles() + (groups.join('') || '<p class="empty">No features yet.</p>');
}

function render() {
  if (!model) return;
  const current = screen();
  document.title = `${model.drive}: ${model.counts.queue} waiting on you`;
  $('#brand').textContent = model.drive;
  const count = model.counts.queue ? `<span class="pill">${model.counts.queue}</span>` : '';
  $('#tabs').innerHTML = `<a class="tab${current === 'queue' ? ' on' : ''}" href="#/">Your queue${count}</a><a class="tab${current === 'status' ? ' on' : ''}" href="#/status">Status</a>`;
  const warning = model.cards === 'live' ? '' : `<p class="warn">Your Slack questions are not listed here: cc-slack ${esc(model.cards)}</p>`;
  $('#main').innerHTML = warning + (current === 'queue' ? queueScreen() : statusScreen());
  tick();
}

function tick() {
  if (!model) return;
  const late = Date.now() - fetchedAt > LATE_MS;
  const fresh = $('#fresh');
  fresh.classList.toggle('late', late);
  fresh.textContent = late ? `Not updated since ${clock(model.at)}` : `Updated ${clock(model.at)} · Pacific`;
}

async function load() {
  try {
    const answer = await fetch('/api/view', { cache: 'no-store' });
    if (answer.ok) {
      model = await answer.json();
      fetchedAt = Date.now();
      render();
    }
  } finally {
    setTimeout(load, REFRESH_MS);
  }
}

window.addEventListener('hashchange', () => {
  if (location.hash.startsWith('#group-')) return;
  window.scrollTo(0, 0);
  render();
});
window.addEventListener('DOMContentLoaded', () => {
  setInterval(tick, 5000);
  load();
});
