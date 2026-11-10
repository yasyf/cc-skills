const ZONE = 'America/Los_Angeles';
const STALL_MINUTES = 45;
const GAP_MINUTES = 30;
const SLACK = /^https:\/\/[\w-]+\.slack\.com\//;
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
const QUEUE_FILTERS = [
  ['all', 'All', () => true],
  ['question', 'Your picks', (item) => item.kind === 'question'],
  ['tap', 'To land', (item) => item.kind === 'tap'],
  ['late', 'Late', (item) => item.kind === 'late'],
];
const LABEL = { question: 'Your pick', tap: 'Land it', late: 'Late' };
const PR_FIELDS = [
  ['refresher', 'In plain words'],
  ['surprise', 'Biggest surprise'],
  ['hate_most', "What you'll hate most"],
  ['plan', 'Against the plan'],
  ['port', 'Port'],
  ['show_me', 'Show me'],
];
const ICONS = {
  queue: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  status: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  work: '<path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/>',
  history: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5M12 7v5l4 2"/>',
};

let model = null;
let connected = false;
let lostAt = null;
let historyFilter = 'key';
let queueFilter = 'all';
const opened = new Map();
const expanded = new Set();
const answers = new Map();

const $ = (selector) => document.querySelector(selector);
const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const link = (text, url) => (url && !SLACK.test(url) ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text));
const minutesSince = (iso) => Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
const bare = (title) => String(title ?? '').replace(/^[\w./-]+:\s*\p{Extended_Pictographic}\uFE0F?\s*/u, '');
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;

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

function inline(text) {
  const spans = [];
  const keep = (html) => `\u0000${spans.push(html) - 1}\u0000`;
  let out = String(text)
    .replace(/`([^`]+)`/g, (_, code) => keep(`<code>${esc(code)}</code>`))
    .replace(/<(https?:\/\/[^|>\s]+)\|([^>]+)>/g, (_, url, label) => keep(link(label, url)))
    .replace(/<(https?:\/\/[^|>\s]+)>/g, (_, url) => keep(link(url, url)))
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_, label, url) => keep(link(label, url)))
    .replace(/https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/(\d+)\S*/g, (url, number) => keep(link(`#${number}`, url)));
  out = esc(out)
    .replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, '$1<b>$2</b>')
    .replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,;:!?]|$)/g, '$1<i>$2</i>');
  return out.replace(/\u0000(\d+)\u0000/g, (_, n) => spans[Number(n)]);
}

function prose(text) {
  const blocks = [];
  const parts = String(text ?? '').split(/^```[^\n]*\n([\s\S]*?)^```\s*$/m);
  parts.forEach((part, i) => {
    if (i % 2) {
      blocks.push(`<pre><code>${esc(part.replace(/\n$/, ''))}</code></pre>`);
      return;
    }
    for (const para of part.split(/\n\s*\n/)) {
      const lines = para.split('\n').filter((line) => line.trim());
      if (!lines.length) continue;
      if (lines.every((line) => /^\s*(?:[-*•]|\d+[.)])\s/.test(line))) {
        blocks.push(`<ul>${lines.map((line) => `<li>${inline(line.replace(/^\s*(?:[-*•]|\d+[.)])\s+/, ''))}</li>`).join('')}</ul>`);
      } else {
        blocks.push(`<p>${lines.map(inline).join('<br>')}</p>`);
      }
    }
  });
  return blocks.join('');
}

function waited(item) {
  if (item.kind === 'late') return `promised ${clock(item.due)}, ${ago(item.due)} late`;
  return item.since ? `waiting ${ago(item.since)}, since ${clock(item.since)}` : '';
}

function choices(item) {
  const card = item.card;
  const given = answers.get(item.answer);
  if (given?.done) return `<div class="answered">✓ <b>${esc(given.label)}</b> · answered ${clock(given.at)}</div>`;
  const failed = given?.error ? `<div class="refused">${esc(given.error)}</div>` : '';
  const buttons = card.options.map((option) => {
    const recommended = option === card.recommended;
    const tone = item.kind === 'tap' ? (option === 'Approve' ? ' go' : ' hold') : recommended ? ' rec' : '';
    const busy = given?.label === option && !given.error;
    return `<button class="pick${tone}${busy ? ' busy' : ''}" data-card="${esc(item.answer)}" data-label="${esc(option)}"${given && !given.error ? ' disabled' : ''}>
      <span>${esc(option)}</span>${recommended && item.kind === 'question' ? '<em>★ Recommended</em>' : ''}
    </button>`;
  });
  return `<div class="picks${item.kind === 'tap' ? ' row' : ''}">${buttons.join('')}</div>${failed}`;
}

function fold(id, html, lines) {
  const open = expanded.has(id);
  return `<div class="fold${open ? ' open' : ''}" data-fold="${esc(id)}" style="--lines:${lines}">${html}</div>`;
}

function questionCard(item) {
  const card = item.card;
  const facts = (card.facts ?? []).map((fact) => `<dt>${esc(fact.label)}</dt><dd>${inline(fact.value)}</dd>`).join('');
  const rows = (card.rows ?? []).map((row) => `<li>${inline(row.text)}</li>`).join('');
  const body = [card.body ? prose(card.body) : '', facts ? `<dl class="facts">${facts}</dl>` : '', rows ? `<ul class="rows">${rows}</ul>` : ''].join('');
  const links = [...(card.links ?? []), ...(card.refs ?? [])].filter((each) => !SLACK.test(each.url)).map((each) => link(each.label, each.url)).join(' · ');
  return `<article class="item question">
    <span class="label">${LABEL.question}</span>
    <div class="body">
      <h3>${inline(card.title || item.title)}</h3>
      <div class="meta">${esc(waited(item))}</div>
      ${body ? fold(`q:${item.answer}`, body, 4) : ''}
      ${links ? `<div class="links">${links}</div>` : ''}
      ${choices(item)}
    </div>
  </article>`;
}

function prRow(pr, cardId, alone) {
  const fields = PR_FIELDS.filter(([key]) => pr[key])
    .map(([key, label]) => `<dt>${label}</dt><dd>${fold(`${cardId}:${pr.url}:${key}`, prose(pr[key]), 2)}</dd>`)
    .join('');
  const waits = [pr.blocks, pr.lands ? `Lands once ${pr.lands}` : ''].filter(Boolean).map(esc).join(' · ');
  return `<section class="pr">
    <div class="pr-head"><b>${alone ? '' : esc(bare(pr.title))}</b><a class="ext" href="${esc(pr.url)}" target="_blank" rel="noopener">View change ↗</a></div>
    ${fields ? `<dl class="fields">${fields}</dl>` : ''}
    ${waits ? `<div class="meta">${waits}</div>` : ''}
  </section>`;
}

function tapCard(item) {
  if (!item.card) {
    return `<article class="item tap bare">
      <span class="label">${LABEL.tap}</span>
      <div class="body"><h3>${link(item.title, item.url)}</h3><div class="meta">${esc(item.detail)}</div></div>
    </article>`;
  }
  const card = item.card;
  return `<article class="item tap">
    <span class="label">${LABEL.tap}</span>
    <div class="body">
      <h3>${esc(bare(card.title.replace(/^\w+: approve (.*)\?$/, '$1')))}</h3>
      <div class="meta">${esc(waited(item))}${item.detail ? ` · ${esc(item.detail)}` : ''}</div>
      ${card.body ? fold(`b:${item.answer}`, prose(card.body), 3) : ''}
      ${(card.prs ?? []).map((pr, _, all) => prRow(pr, item.answer, all.length === 1)).join('')}
      ${choices(item)}
    </div>
  </article>`;
}

function lateCard(item) {
  return `<article class="item late">
    <span class="label">${LABEL.late}</span>
    <div class="body"><h3>${esc(item.title)}</h3><div class="meta">${esc(waited(item))}</div>${item.detail ? `<p class="said">${esc(item.detail)}</p>` : ''}</div>
  </article>`;
}

function queueScreen() {
  const [, , keep] = QUEUE_FILTERS.find(([id]) => id === queueFilter);
  const shown = model.queue.filter(keep);
  const count = model.queue.length;
  const head = count ? `${count} ${count === 1 ? 'thing waits' : 'things wait'} on you, oldest first` : 'Nothing waits on you';
  const filters = QUEUE_FILTERS.map(([id, label, test]) => {
    const n = model.queue.filter(test).length;
    return `<button data-queue="${id}" class="${id === queueFilter ? 'on' : ''}">${label}<span class="n">${n}</span></button>`;
  }).join('');
  const cards = shown.map((item) => ({ question: questionCard, tap: tapCard, late: lateCard })[item.kind](item)).join('');
  return `<h1 class="h1">${head}</h1>
    <div class="subtabs">${filters}</div>
    <div class="queue">${cards || '<p class="empty">Nothing here.</p>'}</div>`;
}

function features() {
  return model.status.flatMap((group) => group.features);
}

function featureLink(id, text) {
  return features().some((entry) => entry.id === id) ? `<a href="#/feature/${esc(id)}">${esc(text)}</a>` : esc(text);
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
  return `<h1 class="h1">Status</h1>${progress()}${tiles()}${groups.join('') || '<p class="empty">No features yet.</p>'}`;
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
  const head = `<h1 class="h1">Work <span class="count">${running.length} running, ${model.work.length - running.length} waiting</span>${stalled.length ? `<span class="badge red">${stalled.length} quiet ${STALL_MINUTES} minutes or more</span>` : ''}</h1>`;
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
  return `<h1 class="h1">History <span class="count">last 24 hours, newest first</span></h1>
    <div class="subtabs">${filters}</div>
    <ul class="history card">${rows.join('') || '<li class="empty">Nothing yet.</li>'}</ul>`;
}

function featureScreen(id) {
  const entry = features().find((candidate) => candidate.id === id);
  if (!entry) return '<p class="empty">This feature is no longer on the dashboard. <a href="#/status">Back to Status</a></p>';
  const facts = [
    ['Where it is', `${GROUPS[entry.state]}${entry.now ? `: ${esc(entry.now)}` : ''}`],
    ['Asked', clock(entry.created)],
    entry.due ? ['Promised', `${clock(entry.due)}${entry.late ? ' <b class="red">late</b>' : ''}`] : null,
    entry.proof ? ['Proof', esc(entry.proof)] : null,
    entry.note ? ['Note', esc(entry.note)] : null,
  ].filter(Boolean);
  const prs = entry.prs.map((pr) => `<li><a href="${esc(pr.url)}" target="_blank" rel="noopener"><i class="sq ${esc(prTone(pr))}"></i>${esc(pr.title)}</a><em>${esc(prSaid(pr))}, ${esc(pr.review === 'none' ? 'no review' : pr.review)}</em></li>`).join('');
  return `<a class="back" href="#/status">‹ Status</a>
    <div class="kick"><span class="dot ${entry.state.replace(' ', '-')}"></span>${GROUPS[entry.state]}${entry.prs.length ? ` · ${entry.prs.filter((pr) => pr.state === 'merged').length} of ${entry.prs.length} merged` : ''}</div>
    <h1 class="h1">${esc(entry.title)}</h1>
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

function measure() {
  for (const el of document.querySelectorAll('.fold')) el.classList.toggle('long', el.scrollHeight > el.clientHeight + 2 || el.classList.contains('open'));
}

function render() {
  if (!model) return;
  const { screen, id } = route();
  document.title = `${model.drive}: ${model.counts.queue} waiting on you`;
  $('#brand').textContent = model.drive;
  const tab = (name, href, label, count) => {
    const on = screen === name || (name === 'status' && screen === 'feature');
    return `<a class="tab${on ? ' on' : ''}" href="${href}">${icon(name)}<span>${label}</span>${count ? `<span class="count">${count}</span>` : ''}</a>`;
  };
  $('#tabs').innerHTML = tab('queue', '#/', 'Your queue', model.counts.queue) + tab('status', '#/status', 'Status') + tab('work', '#/work', 'Work') + tab('history', '#/history', 'History');
  const warning = model.cards === 'live' ? '' : `<p class="warn">Your questions are not listed here: cc-slack ${esc(model.cards)}</p>`;
  const screens = { queue: queueScreen, status: statusScreen, work: workScreen, history: historyScreen, feature: () => featureScreen(id) };
  $('#findbar').hidden = screen !== 'status';
  const y = window.scrollY;
  $('#main').innerHTML = warning + (screens[screen] ?? queueScreen)();
  window.scrollTo(0, y);
  if (screen === 'status') find();
  measure();
  fresh();
}

function fresh() {
  const el = $('#fresh');
  el.classList.toggle('late', !connected);
  if (!model) el.textContent = connected ? '' : 'Connecting…';
  else el.textContent = connected ? `Live · updated ${clock(model.at)} · Pacific` : `Not updated since ${clock(lostAt)} · showing ${clock(model.at)}`;
}

async function answer(card, label) {
  answers.set(card, { label });
  render();
  try {
    const reply = await fetch('/api/answer', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ card, label }) });
    const said = await reply.json();
    if (!reply.ok) throw new Error(said.error);
    answers.set(card, { label: said.label || label, at: said.at || new Date().toISOString(), done: true });
  } catch (error) {
    answers.set(card, { label, error: error.message });
  }
  render();
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
  const pick = event.target.closest('button[data-card]');
  if (pick) return answer(pick.dataset.card, pick.dataset.label);
  const folded = event.target.closest('.fold.long');
  if (folded && !event.target.closest('a')) {
    const id = folded.dataset.fold;
    if (expanded.has(id)) expanded.delete(id);
    else expanded.add(id);
    return render();
  }
  const queue = event.target.closest('[data-queue]');
  if (queue) {
    queueFilter = queue.dataset.queue;
    return render();
  }
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
