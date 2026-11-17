let sharedRenderers;
const ZONE = 'America/Los_Angeles';
const STALL_MINUTES = 45;
const READABLE = 0.42;
const GAP_MINUTES = 30;
const REVIEW_KEEP_MS = 5 * 60 * 1000;
const SLACK = /^https:\/\/[\w-]+\.slack\.com\//;
const RECORDING = /\.(mov|mp4|m4v|webm)$/i;
const GROUPS = {
  blocked: 'Blocked',
  running: 'Running',
  built: 'Built, waiting on your design answers',
  waiting: 'Waiting',
  merged: 'Merged, not live yet',
  live: 'Live in the last day',
  'not started': 'Not started',
};
const CHECKS = { pass: 'green', fail: 'red', pending: 'running', none: 'no CI' };
const CHECK_WORDS = { pass: ['Tests pass', 'good'], fail: ['Tests failing', 'bad'], pending: ['Tests running', 'warn'], none: ['No tests ran', ''] };
const JOB_STATES = { running: 'Running', waiting: 'Waiting', built: 'Built, no PR until you answer', done: 'Done', failed: 'Failed', stopped: 'Stopped' };
const LEVEL_IN = 2.2;
const LEVEL_OUT = 2.4;
const HISTORY_FILTERS = [
  ['all', 'Everything'],
  ['prs', 'PRs merged'],
  ['you', 'Your asks and answers'],
  ['said', 'Sent to you'],
];
const PAGE_FILTERS = [
  ['live', 'Live'],
  ['done', 'Done'],
  ['all', 'All'],
];
const NONE = '~';
const UNFILED = 'Unfiled';
const SWEEP_BARS = [
  ['answers', 'Answers', 'p90 over 2 min · last day', 'Answers slower than 2 minutes'],
  ['owed', 'Owed', 'over 10 min · now', 'Message unanswered past 10 minutes'],
  ['cards', 'Cards', 'over 48 h · now', 'Card open past 48 hours'],
  ['approved', 'Approved', 'open over 24 h · now', 'Approved pull request open past 24 hours'],
];
const RANK = ['blocked', 'built', 'running', 'waiting', 'not started', 'merged', 'live'];
const SATELLITES = 10;
const RING_LIMIT = 8;
const LANE_STATES = { working: ['Working', 'doing', 'Working'], quiet: [`Quiet ${STALL_MINUTES} minutes or more`, 'bad', 'Quiet'], waiting: ['Waiting', 'waiting', 'Waiting'], built: ['Built, waits on you', 'you', 'Built'] };
const LANE_ORDER = ['quiet', 'waiting', 'built'];
const KIND_DOING = { builder: 'building', reader: 'reading', design: 'designing', codex: 'asking Codex' };
const LINE_FROM = { transcript: 'Its newest words in its transcript', said: 'The line it last sent', report: 'The first line of its report' };
const WIDE_STAGE = { w: 1100, h: 500 };
const NARROW_WIDTH = 300;
const NARROW_ROW = 84;
const NODE = { w: 185, h: 103 };
const LANE_RATIO = 2.4;
const LANE_MAX = { w: 280, h: 104 };
const SWEAR_WORDS = 'damn, shit and fuck';
const QUEUE_FILTERS = [
  ['all', 'All', () => true],
  ['tap', 'PRs', (item) => item.kind === 'tap'],
  ['question', 'Decisions', (item) => item.kind === 'question'],
];
const NONE_LEFT = { tap: 'No PR waits on your tap.', question: 'No decision waits on you.' };
const LABEL = { question: 'Decision', tap: 'Pull request' };
const TAP_LABELS = ['Approve', 'Hold'];
const PR_TABS = [['show', 'Show me'], ['change', 'The code'], ['context', 'Its description']];
const TALK_FOLLOW_MS = 15 * 60 * 1000;
const FRAMES_KEPT = 4;
const GONE_ROWS = { merged: 'Landed', live: 'Landed', folded: 'Merged into another open PR, lands with it', closed: 'Closed without merging', dropped: 'Dropped from the drive' };
const BUG_STAGES = [
  ['reported', 'No work on it yet', 'reported'],
  ['fixing', 'Being fixed', 'being fixed'],
  ['merged', 'Fix merged, waits on proof it is live', 'merged'],
  ['live', 'Fix live, its reporter not told yet', 'live, reporter not told'],
  ['told', 'Its reporter was told it is fixed', 'told'],
];
const SPOTS = 'h4.hd, .shot, figure.visual';
const SLICES_KEPT = 40;
const JUMP_ROWS = 40;
const KEY_SHEET = [
  ['Anywhere', [[['⌘K'], 'Jump to a screen, question, pull request, task or job'], [['?'], 'Show these keys'], [['Esc'], 'Close what is open, or step back up a level']]],
  ['Your queue', [
    [['j', '→'], 'Next card'], [['k', '←'], 'Previous card'], [['1–9'], 'Select a choice; review before sending'], [['a'], 'Approve the pull request'], [['h'], 'Hold the pull request'],
    [['o'], 'Open the pull request on GitHub'], [['v'], 'Enlarge the first picture of its Show me'], [['q'], 'Ask about it, or answer in your own words'], [['e'], 'Open or close evidence'],
    [['f'], 'Next filter: All, PRs, Decisions'], [['[', ']'], 'Previous or next project'], [['⌘Z'], 'Take back the newest answer that still shows Undo'],
  ]],
  ['Now', [[['scroll', 'pinch'], 'Zoom around the pointer, into a project or workstream and back out'], [['⇧ scroll', '←', '→', '↑', '↓'], 'Pan'], [['+', '−'], 'Zoom in or out']]],
  ['A decision', [[['1–9'], 'Select a path or priority'], [['←', '→'], 'Select a path when a choice is focused'], [['↵'], 'Review the selected choice']]],
  ['A pull request card', [[['←', '→'], 'Switch between Show me, The change and Context, with a tab focused']]],
  ['Writing to Claude', [[['↵'], 'Send'], [['⇧↵'], 'Start a new line']]],
  ['Jump', [[['↑', '↓'], 'Choose a match'], [['↵'], 'Open it']]],
  ['An enlarged picture', [[['←', '→'], 'The previous or next picture on the same card']]],
];
const STOP_WORDS = { blocked: 'Blocked', running: 'Being built', built: 'Built, waits on your answers', waiting: 'Waiting', merged: 'Merged, not live yet', live: 'Live', 'not started': 'Not started' };
const STOP_TONES = { blocked: 'bad', running: 'doing', built: 'you', waiting: 'waiting', merged: 'merged', live: 'done', 'not started': 'not-started' };
const HEADS = { ...GROUPS, built: 'Built', merged: 'Merged', live: 'Live' };
const JOB_TONES = { running: 'doing', waiting: 'waiting', built: 'you', done: 'done', failed: 'bad', stopped: 'not-started' };
const REPORTS = {
  'NOT READY': ['Not ready', 'bad', 'waiting'],
  BLOCKED: ['Blocked', 'bad', 'failed'],
  PUSHED: ['PR up', 'you', 'waiting'],
  READY: ['Ready', 'you', 'waiting'],
  CHECKED: ['Checked', 'done', 'waiting'],
  DONE: ['Done', 'done', 'done'],
  DESIGN: ['Design up', 'you', 'waiting'],
  ASK: ['Asks you', 'you', 'waiting'],
  WAITING: ['Waiting', 'waiting', 'waiting'],
  BUILT: ['Built', 'you', 'built'],
  HANDOFF: ['Handed off', 'not-started', 'stopped'],
};
const REPORT_WORD = new RegExp(`^(${Object.keys(REPORTS).join('|')})\\b[:,.]?\\s*`);
const FILE_WORDS = { brief: 'Brief', report: 'Report', question: 'Question', repro: 'Reproduction', transcript: 'Transcript' };
const PR_FIELDS = [
  ['impact', 'FDE impact'],
  ['refresher', 'In plain words'],
  ['surprise', 'Biggest surprise'],
  ['hate_most', "What you'll hate most"],
  ['plan', 'Against the plan'],
  ['port', 'Port'],
  ['show_me', 'Show me'],
];
const SCREENS = [
  ['queue', '#/', 'Your queue'],
  ['now', '#/now', 'Now'],
  ['work', '#/work', 'Work'],
  ['history', '#/history', 'History'],
  ['pages', '#/pages', 'Pages'],
];
const ICONS = {
  queue: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  now: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  work: '<path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/>',
  history: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5M12 7v5l4 2"/>',
  pages: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 9v11"/>',
  enlarge: '<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>',
};

let model = null;
let connected = false;
let lostAt = null;
let paused = false;
let laneFilter = { at: null, words: '' };
let lanesRead = null;
let laneSeen = new Map();
const packets = [];
let narrow = null;
let shownHash = null;
let queueAt = null;
let queueSlot = 0;
let picked = null;
let touched = false;
const expanded = new Set();
const decisionStates = new Map();
const answers = new Map();
const drafts = new Map();
const sent = new Map();
const toasts = new Map();
const restored = new Set();
let outdated = false;
let placeAfterLoad = null;
const prTabs = new Map();
const prChapters = new Map();
const PR_CHAPTERS = [['problem', 'The problem'], ['change', 'The change'], ['runtime', 'At runtime'], ['evidence', 'The evidence'], ['choice', 'Your call']];
const prRows = new Map();
const prReads = new Map();
const reviews = new Map();
const diffs = new Map();
const diffOpen = new Map();
const pointed = new Map();
const notes = new Map();
const told = new Map();
let noteCount = 0;
const talked = new Map();
const reading = new Set();
const again = new Set();
const slotted = new Map();
const followed = new Map();
const slices = new Map();
const sources = new Map();
let reviewing = null;
let viewing = null;

const $ = (selector) => document.querySelector(selector);
const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const link = (text, url) => (url && !SLACK.test(url) ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text));
const minutesSince = (iso) => Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
const bare = (title) => named(String(title ?? '').replace(/^[\w./-]+:\s*\p{Extended_Pictographic}\uFE0F?\s*/u, ''));
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;
function counted(n, one, many) { return sharedRenderers.counted(n, one, many); }
const fileKey = (name) => name ?? NONE;
const sameChannel = (key) => (key === 'The drive' ? 'Drive tooling' : key);
const fileName = (key) => (key === NONE ? UNFILED : key);
const byName = ([a], [b]) => (a === NONE) - (b === NONE) || a.localeCompare(b);
const zoomHref = (screen, ...keys) => [`#/${screen}`, ...keys.map(encodeURIComponent)].join('/');
const dayOf = (iso) => new Date(iso).toLocaleDateString('en-CA', { timeZone: ZONE });
const dayName = (day, options = { month: 'short', day: 'numeric' }) => new Date(`${day}T12:00:00Z`).toLocaleDateString('en-US', { timeZone: 'UTC', ...options });
const timeOf = (iso) => new Date(iso).toLocaleTimeString('en-US', { timeZone: ZONE, hour: 'numeric', minute: '2-digit' });

function perModel(build) {
  let seen = null;
  let value = null;
  return () => {
    if (seen !== model) [seen, value] = [model, build()];
    return value;
  };
}

function clock(iso) {
  const at = new Date(iso);
  const sameDay = at.toLocaleDateString('en-US', { timeZone: ZONE }) === new Date().toLocaleDateString('en-US', { timeZone: ZONE });
  const time = at.toLocaleTimeString('en-US', { timeZone: ZONE, hour: 'numeric', minute: '2-digit' });
  return sameDay ? time : `${at.toLocaleDateString('en-US', { timeZone: ZONE, month: 'short', day: 'numeric' })} ${time}`;
}

function ago(iso) {
  const minutes = minutesSince(iso);
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 48 * 60) return `${Math.floor(minutes / 60)}h`;
  const hours = Math.floor(minutes / 60) % 24;
  return `${Math.floor(minutes / 1440)}d${hours ? ` ${hours}h` : ''}`;
}

function route() {
  const [where, query = ''] = location.hash.replace(/^#\/?/, '').split('?');
  const [screen, ...rest] = where.split('/');
  const path = rest.filter(Boolean).map(decodeURIComponent);
  return { screen: screen || 'queue', id: path[0], path, query: new URLSearchParams(query) };
}

function shownFilter() {
  const asked = route().query.get('show');
  return HISTORY_FILTERS.some(([id]) => id === asked) ? asked : 'all';
}

function historyPage() {
  return Math.max(1, parseInt(route().query.get('page'), 10) || 1);
}

function historyHref(day, filter = shownFilter(), page = 1) {
  const query = new URLSearchParams([...(filter === 'all' ? [] : [['show', filter]]), ...(page > 1 ? [['page', page]] : [])]);
  return `#/history${day ? `/${day}` : ''}${query.size ? `?${query}` : ''}`;
}

function up() {
  const { screen, path } = route();
  if (screen === 'feature') {
    const entry = featureOf(path[0]);
    return entry ? zoomHref('now', fileKey(entry.project), fileKey(entry.workstream)) : '#/now';
  }
  if (screen === 'history') return path.length ? historyHref() : null;
  if (!path.length || !['now', 'work'].includes(screen)) return null;
  return zoomHref(screen, ...path.slice(0, -1));
}

const VISUAL_WORDS = { arch: 'Map of the parts', mermaid: 'Diagram', dot: 'Diagram', chart: 'Chart', compare: 'Side by side' };
const FRAMED = new Set(['mermaid', 'dot', 'chart', 'compare']);

function urlName(url) { return sharedRenderers.urlName(url); }
function named(text) { return sharedRenderers.named(text); }
function inline(text, cite) { return sharedRenderers.inline(text, cite); }
function prose(text, cite) { return sharedRenderers.prose(text, cite); }
function table(lines, cite) { return sharedRenderers.table(lines, cite); }
function optionParts(option) { return sharedRenderers.optionParts(option); }
function decisionPlain(text) { return sharedRenderers.decisionPlain(text); }
function decisionCause(body) { return sharedRenderers.decisionCause(body); }

function sourceLink(cite, path, from, to) {
  const [repo] = cite.split('#');
  const head = model.review.find((entry) => entry.key === cite)?.head ?? 'HEAD';
  const span = to ? `${from}-${to}` : from;
  return `<a class="src" href="https://github.com/${esc(repo)}/blob/${esc(head)}/${esc(path)}#L${from}${to ? `-L${to}` : ''}" target="_blank" rel="noopener" data-src="${esc(cite)}" data-path="${esc(path)}" data-line="${from}" data-to="${to ?? from}">${esc(path.split('/').pop())}:${span}</a>`;
}




function shown(path, caption) {
  const drawn = sources.get(path);
  return drawn && drawn.kind !== 'image' ? visual(drawn.kind, drawn.source, caption) : shot(path, caption);
}

function shot(path, caption) {
  const src = `/api/picture?path=${encodeURIComponent(path)}`;
  return RECORDING.test(path) ? recording(src, caption) : picture(src, caption);
}

function recording(src, caption = '', enlarged = false) {
  return `<figure class="visual recording" data-recording="${esc(src)}" data-caption="${esc(caption)}"><video src="${esc(src)}" controls playsinline preload="metadata" aria-label="${esc(caption || 'Screen recording')}"></video>${enlarged ? '' : `<button type="button" class="enlarge" data-enlarge aria-label="${esc(caption ? `Enlarge recording: ${caption}` : 'Enlarge recording')}" title="Enlarge recording">${icon('enlarge')}</button>`}<p class="recording-error" role="status" hidden>Recording unavailable.<a class="ext" href="${esc(src)}" target="_blank" rel="noopener">Open the original ↗</a></p>${!enlarged && caption ? `<figcaption>${esc(caption)}</figcaption>` : ''}</figure>`;
}

function picture(src, caption) {
  return `<button type="button" class="shot" data-shot="${esc(src)}" data-caption="${esc(caption)}" aria-label="${esc(caption ? `Enlarge ${caption}` : 'Enlarge the picture')}"><img src="${esc(src)}" alt="${esc(caption)}" loading="lazy"></button>`;
}

function visual(kind, source, caption = '') {
  return `<figure class="visual"><div class="visual-host" data-visual="${esc(kind)}" data-source="${esc(source)}"></div><button type="button" class="enlarge" data-enlarge aria-label="Enlarge${caption ? ` ${esc(caption)}` : ''}" title="Enlarge">${icon('enlarge')}</button>${caption ? `<figcaption>${esc(caption)}</figcaption>` : ''}</figure>`;
}

function gallery(item) {
  const group = item.closest('article, li, aside, section, .pr-pane, .ask-box') ?? item.parentElement;
  return [...group.querySelectorAll('[data-shot], figure.visual')];
}

function openLightbox(item) {
  const items = gallery(item);
  const inline = item.querySelector('video');
  const playing = inline && !inline.paused;
  inline?.pause();
  viewing = { items, at: items.indexOf(item), opener: document.activeElement };
  drawLightbox();
  $('#lightbox').showModal();
  if (inline) handOver(inline, $('#lightbox video'), playing);
}

function drawLightbox() {
  const { items, at } = viewing;
  const item = items[at];
  const clip = item.matches('figure.recording');
  const host = item.querySelector('.visual-host');
  const src = clip ? item.dataset.recording : item.dataset.shot;
  const caption = host ? item.querySelector('figcaption')?.textContent ?? '' : item.dataset.caption;
  const stage = clip ? recording(src, caption, true) : host ? visual(host.dataset.visual, host.dataset.source) : `<img src="${esc(src)}" alt="${esc(caption)}">`;
  const original = host ? '' : `<a class="ext" href="${esc(src)}" target="_blank" rel="noopener">Open the original ↗</a>`;
  const steps = items.length > 1 ? `<button type="button" class="lb-step" data-step="-1" aria-label="Previous"${at === 0 ? ' disabled' : ''}>‹</button><button type="button" class="lb-step next" data-step="1" aria-label="Next"${at === items.length - 1 ? ' disabled' : ''}>›</button>` : '';
  $('#lightbox').innerHTML = `<div class="lb-bar"><span class="meta">${items.length > 1 ? `${at + 1} of ${items.length}` : ''}</span><span class="acts">${original}<button type="button" class="linkish" data-close autofocus>Close</button></span></div>
    <div class="lb-stage${clip ? ' recording-stage' : host ? '' : ' photo'}">${stage}</div>
    ${caption ? `<p class="lb-caption">${esc(caption)}</p>` : ''}${steps}`;
  drawFigures($('#lightbox'));
}

function stepLightbox(delta) {
  const at = viewing.at + delta;
  if (!viewing.items[at]) return;
  handBack();
  viewing.at = at;
  drawLightbox();
  const inline = viewing.items[at].querySelector('video');
  if (inline) handOver(inline, $('#lightbox video'), false);
  $('#lightbox [data-close]').focus();
}

function handOver(from, to, playing) {
  Object.assign(to, { volume: from.volume, muted: from.muted, playbackRate: from.playbackRate });
  const resume = () => {
    to.currentTime = from.currentTime;
    if (playing) to.addEventListener('seeked', () => to.play(), { once: true });
  };
  if (to.readyState) resume();
  else to.addEventListener('loadedmetadata', resume, { once: true });
}

function handBack() {
  const shown = $('#lightbox video');
  const inline = viewing.items[viewing.at].querySelector('video');
  if (!shown || !inline) return;
  shown.pause();
  Object.assign(inline, { currentTime: shown.currentTime, volume: shown.volume, muted: shown.muted, playbackRate: shown.playbackRate });
}

async function drawFigures(root) {
  const hosts = [...root.querySelectorAll('.visual-host:not(.drawn)')];
  if (!hosts.length) return;
  for (const host of hosts) host.classList.add('drawn');
  const loaded = import('/visuals.js');
  for (const host of hosts) {
    try {
      await (await loaded).draw(host, host.dataset.visual, host.dataset.source);
      if (FRAMED.has(host.dataset.visual) && !host.closest('.thumbs, .pr-example')) frame(host);
      if (host.dataset.visual.startsWith('code:')) host.insertAdjacentHTML('afterbegin', `<div class="code-bar"><span>${esc(host.dataset.visual.slice(5) || 'code')}</span><span class="acts"><button type="button" class="linkish" data-copy>Copy</button><button type="button" class="linkish" data-enlarge>Enlarge</button></span></div>`);
    } catch (failure) {
      host.innerHTML = sharedRenderers.refusedVisual(host.dataset.visual, host.dataset.source, failure);
    }
  }
}

function posted(body) {
  return { method: 'POST', headers: { 'Content-Type': 'application/json', Webdriver: String(navigator.webdriver) }, body: JSON.stringify(body) };
}

async function ask(url, options) {
  try {
    const reply = await fetch(url, options);
    return { ok: reply.ok, ...(await reply.json()) };
  } catch (failure) {
    return { ok: false, error: `Could not reach the dashboard server (${failure.message}).` };
  }
}

function slice(url) {
  const held = slices.get(url) ?? {};
  slices.delete(url);
  slices.set(url, held);
  if (held.asked !== model.version) {
    const version = (held.asked = model.version);
    ask(url).then((read) => {
      if (version < held.version) return;
      for (const [path, source] of Object.entries(read.visuals ?? {})) sources.set(path, source);
      Object.assign(held, { version, read });
      render();
    });
  }
  return held.read;
}

function opened(item) {
  const read = item.answer && slice(`/api/card?${new URLSearchParams({ answer: item.answer })}`);
  return read?.ok ? { ...item, card: read.card, rail: read.rail, told: read.told, option_pictures: read.option_pictures } : item;
}

function notRead(read) {
  return read ? `<p class="refused">${esc(read.error)}</p>` : '<p class="empty small">Reading…</p>';
}

function untold(told) {
  return told.writing ? '<p class="empty small" role="status">Writing its story…</p>' : `<p class="refused">No story yet: ${esc(told.error)}</p>`;
}

function prKey(url) {
  const found = String(url).match(/^https:\/\/github\.com\/([\w.-]+\/[\w.-]+)\/pull\/(\d+)/);
  return found ? `${found[1]}#${found[2]}` : null;
}

function answerKey(item) {
  return item.answer ?? (item.kind === 'tap' ? `tap:${prKey(item.url)}` : null);
}

function choices(item, keyed = false) {
  const card = answerKey(item);
  return sharedRenderers.choices({ ...item, answer: card }, { given: answers.get(card), keyed, shown: picked?.card === card ? picked.index : -1, restored: restored.has(card) });
}

function sayBox(key, under, placeholder, about = '', thread = '') {
  const was = sent.get(key);
  const note = was?.busy ? '<span class="meta">Sending…</span>'
    : was?.error ? `<span class="refused">Not sent: ${esc(was.error)}</span>`
    : was?.at ? `<span class="meta">✓ Sent ${clock(was.at)}. The drive has it, and your DM shows it.</span>` : '';
  return `<form class="say" data-say="${esc(key)}" data-under="${esc(under)}" data-about="${esc(about)}" data-thread="${esc(thread)}" data-placeholder="${esc(placeholder)}">
    <textarea data-draft="${esc(key)}" rows="1" placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}"${was?.busy ? ' disabled' : ''}>${esc(drafts.get(key) ?? '')}</textarea>
    <button class="btn"${was?.busy ? ' disabled' : ''}>Send</button>
    ${note}
  </form>`;
}

function pictures(paths) {
  return paths?.length ? `<div class="pics">${paths.map((path) => shown(path, '')).join('')}</div>` : '';
}

function fold(id, html, lines, key = '') {
  const open = expanded.has(id);
  return `<div class="fold${open ? ' open' : ''}" id="fold:${esc(id)}" data-fold="${esc(id)}" style="--lines:${lines}">${html}</div><button type="button" class="linkish fold-more" data-more="${esc(id)}" aria-controls="fold:${esc(id)}" aria-expanded="${open}"${key ? ` data-key="${key}"` : ''}>${open ? 'Show less' : 'Explain more'}</button>`;
}

function fact(html, tip = '', tone = '') {
  return sharedRenderers.fact(html, tip, tone);
}

function cardTop(orders, at, total, where) {
  const task = orders.map(featureOf).find(Boolean);
  const order = task ? `<a class="card-for" href="#/feature/${esc(task.id)}">For ${esc(bare(task.title))}</a>` : '';
  return `${order}${where ? `<span class="counter card-place"><span>${at + 1} of ${total} ${esc(where)}</span></span>` : counter(at, total)}`;
}


function counter(at, total) {
  return `<div class="counter"><span>${at + 1} of ${total}</span><button data-move="-1" aria-label="Previous"${at === 0 ? ' disabled' : ''}>‹</button><button data-move="1" aria-label="Next"${at === total - 1 ? ' disabled' : ''}>›</button></div>`;
}

function filterOf(id) {
  return QUEUE_FILTERS.some(([filter]) => filter === id) ? id : 'all';
}

function shownQueue(filter, project = route().query.get('project'), stream = route().query.get('stream'), priority = route().query.get('priority'), from = queueFrom()) {
  const [, , keep] = QUEUE_FILTERS.find(([id]) => id === filter);
  const members = priority === null ? null : new Set(lensOf(priority)?.members ?? []);
  const cards = from ? pageCards(from) : null;
  const inScope = (item) => (project === null || sameChannel(fileKey(item.project)) === sameChannel(project)) && (stream === null || fileKey(item.workstream) === stream) && (members === null || item.tasks.some((id) => members.has(id))) && (cards === null || cards.has(answerKey(item)));
  const shown = model.queue.filter((item) => keep(item) && inScope(item) && !(answers.has(answerKey(item)) && !answers.get(answerKey(item)).error));
  return filter === 'question' ? byNeeds(shown) : shown;
}

function queueFrom() {
  const slug = route().query.get('page');
  return slug === null ? null : model.pages.find((page) => page.slug === slug) ?? null;
}

function scopeQuery(project = route().query.get('project'), stream = route().query.get('stream'), priority = project === null && stream === null ? route().query.get('priority') : null, page = route().query.get('page')) {
  const query = new URLSearchParams([...(project === null ? [] : [['project', project]]), ...(stream === null ? [] : [['stream', stream]]), ...(priority === null ? [] : [['priority', priority]]), ...(page === null ? [] : [['page', page]])]);
  return query.size ? `?${query}` : '';
}

function scopeKeys(filter, project = null) {
  const chosen = route().query.get(project === null ? 'project' : 'stream');
  const keys = shownQueue(filter, project, null).map((item) => fileKey(project === null ? item.project : item.workstream));
  return [...new Set([...(chosen === null ? [] : [chosen]), ...keys])].sort((a, b) => byName([a], [b]));
}

function queueScope(filter) {
  const project = route().query.get('project');
  const stream = route().query.get('stream');
  const choice = (label, href, on, n, parent = false) => `<a href="${esc(href)}"${on ? ' class="on" aria-current="true"' : ''}${parent ? ' data-scope-project' : ''}><span>${esc(label)}</span><span class="n">${n}</span></a>`;
  const priority = route().query.get('priority');
  const rows = [choice('All projects', `#/queue/${filter}`, project === null && priority === null, shownQueue(filter, null, null, null).length), ...scopeKeys(filter).map((key) => choice(fileName(key), `#/queue/${filter}${scopeQuery(key, null)}`, key === project, shownQueue(filter, key, null, null).length, true))];
  const lenses = model.priorities.length ? `<nav class="scope-priorities" aria-label="Priority"><h2>★ Priorities <span>across projects</span></h2>${model.priorities.map((lens) => choice(`★ ${lens.name}`, `#/queue/${filter}?priority=${encodeURIComponent(lens.id)}`, lens.id === priority, shownQueue(filter, null, null, lens.id).length)).join('')}</nav>` : '';
  const streams = project === null ? '' : `<nav class="scope-streams" aria-label="Workstream"><h2>${esc(fileName(project))} <span>workstreams</span></h2>${choice('All workstreams', `#/queue/${filter}${scopeQuery(project, null)}`, stream === null, shownQueue(filter, project, null).length)}${scopeKeys(filter, project).map((key) => choice(fileName(key), `#/queue/${filter}${scopeQuery(project, key)}`, key === stream, shownQueue(filter, project, key).length)).join('')}</nav>`;
  return `<details class="queue-scope" id="queue-scope">
    <summary aria-label="Filter by project and workstream: ${esc(project === null ? 'All projects' : fileName(project))}${stream === null ? '' : ` / ${esc(fileName(stream))}`}, ${counted(shownQueue(filter).length, 'item')}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M7 12h10M10 17h4"/></svg><span class="scope-label">${priority !== null && lensOf(priority) ? `★ ${esc(lensOf(priority).name)}` : project === null ? 'All projects' : `${esc(fileName(project))}${stream === null ? '' : `<span class="scope-divider">/</span>${esc(fileName(stream))}`}`}</span><span class="n">${shownQueue(filter).length}</span><svg class="scope-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m8 10 4 4 4-4"/></svg></summary>
    <div class="scope-panel${project === null ? '' : ' has-project'}"><div class="scope-col"><nav class="scope-projects" aria-label="Project"><h2>Projects</h2>${rows.join('')}</nav>${lenses}</div>${streams}</div>
  </details>`;
}

function current(list) {
  const named = route().path[1];
  let at = named ? list.findIndex((item) => item.answer && cardTs(item.answer) === named) : -1;
  if (at < 0) at = list.findIndex((item) => noticeKey(item) === queueAt);
  if (at < 0) at = Math.min(queueSlot, list.length - 1);
  queueSlot = Math.max(at, 0);
  queueAt = list[at] ? noticeKey(list[at]) : null;
  return at;
}

function queueHref(filter, item, query = scopeQuery()) {
  if (!item?.answer) return `#/queue/${filter}${query}`;
  const rows = item.card?.prs ?? [];
  return `#/queue/${filter}/${cardTs(item.answer)}${rows.length > 1 ? `/${prKey(rows[chosenRow(item)].url).split('#')[1]}` : ''}${query}`;
}

function chosenRow(item) {
  const open = (item.prs ?? []).flatMap((pr, i) => (pr.state === 'open' ? [i] : []));
  const picked = prRows.get(noticeKey(item));
  return open.includes(picked) ? picked : open[0] ?? 0;
}

function move(delta) {
  const list = shownQueue(filterOf(route().id));
  const at = Math.max(0, Math.min(list.length - 1, current(list) + delta));
  if (!list[at] || noticeKey(list[at]) === queueAt) return;
  queueAt = noticeKey(list[at]);
  queueSlot = at;
  history.replaceState(history.state, '', queueHref(filterOf(route().id), list[at]));
  picked = null;
  window.scrollTo(0, 0);
  render();
}

function heldRow(item, entry, held) {
  const finished = held.filter((each) => each.feature === entry.id).length;
  const others = entry.cards.filter((each) => each.url !== item.answer).length;
  const small = [finished ? `${counted(finished, 'finished change')} ${finished === 1 ? 'waits' : 'wait'} on it` : '', others ? `also waits on ${counted(others, 'other decision')} of yours` : ''].filter(Boolean).join('; ');
  return `<li class="held">
    <a class="held-title" href="#/feature/${esc(entry.id)}">${esc(bare(entry.title))}</a>
    ${pill(STOP_WORDS[entry.state], STOP_TONES[entry.state])}
    ${small ? `<small>${esc(small.replace(/^./, (c) => c.toUpperCase()))}</small>` : ''}
  </li>`;
}

function earlierWords(answer) {
  const read = answer.mark === 'stands' ? 'The drive read these words against this question and found it still open.' : 'The drive has not read these words against this question yet.';
  return `<li tabindex="0" data-tip="${esc(`“${answer.words}” ${read}`)}"><q>${esc(answer.words)}</q><span class="meta">${clock(answer.at)}</span></li>`;
}

const NEEDS = [
  ['dangerous act', 'A risky step only you can let run'],
  ['customer data', 'Touches customer data'],
  ['money', 'Spends money'],
  ['his hands', 'Needs you to do something'],
  ['his own words', 'Needs a ruling in your words'],
  ['only he knows', 'Only you know the answer'],
];
const NEEDS_LABEL = new Map(NEEDS);
const NEEDS_ORDER = new Map(NEEDS.map(([key], i) => [key, i]));

function byNeeds(list) {
  return list.map((item, i) => [item, i]).sort(([a, i], [b, j]) => (NEEDS_ORDER.get(a.needs) ?? NEEDS.length) - (NEEDS_ORDER.get(b.needs) ?? NEEDS.length) || i - j).map(([item]) => item);
}

function needsChip(item) {
  return item.kind === 'question' && item.needs ? `<span class="needs-chip">${esc(NEEDS_LABEL.get(item.needs))}</span>` : '';
}

function priorityChips(item) {
  const { priority, reason } = item;
  return `<span class="priority-chips"><span class="rank-chip" aria-label="Rank ${priority.rank}">${priority.rank}</span><span class="reason-chip ${esc(reason.tone)}" data-tip="${esc(reason.text)}">${esc(reason.label)}</span>${item.since ? `<span class="age">${esc(ago(item.since))}</span>` : ''}</span>`;
}

function entryOf(key) {
  return model.review.find((each) => each.key === key);
}

function prWord(entry, row = {}) {
  if (row.state && row.state !== 'open') return { word: GONE_ROWS[row.state] ?? row.state, tone: GONE_ROWS[row.state] === 'Landed' ? 'good' : '' };
  if (!entry) return { word: (CHECK_WORDS[row.checks] ?? CHECK_WORDS.none)[0], tone: (CHECK_WORDS[row.checks] ?? CHECK_WORDS.none)[1] };
  if (entry.conflict) return { word: 'Needs a rebase', tone: 'bad' };
  if (entry.checks === 'fail') return { word: 'Tests failing', tone: 'bad' };
  if (entry.draft) return { word: 'Still a draft', tone: 'warn' };
  if (entry.checks === 'pending') return { word: 'Tests running', tone: 'warn' };
  if (entry.staging_plain.tone === 'bad' || entry.staging_plain.tone === 'warn') return entry.staging_plain;
  if (entry.approve_waits === '') return { word: 'Ready to land', tone: 'good' };
  return entry.staging_plain;
}

function dot({ word, tone }) {
  return `<i class="dot ${esc(tone)}" title="${esc(word)}" aria-hidden="true"></i>`;
}

function itemStatus(item) {
  if (item.kind !== 'tap') {
    const tasks = item.blocks?.length ?? 0;
    return tasks ? `<span class="qrow-status">Holds up ${esc(counted(tasks, 'task'))}${item.built?.length ? `, ${esc(counted(item.built.length, 'build'))} done` : ''}</span>` : '';
  }
  const open = item.prs.filter((pr) => pr.state === 'open');
  const words = open.map((pr) => prWord(entryOf(prKey(pr.url)), pr));
  const worst = ['bad', 'warn', '', 'good'].map((tone) => words.find((each) => each.tone === tone)).find(Boolean);
  return `<span class="qrow-status">${words.map(dot).join('')}<span>${open.length > 1 ? `${esc(counted(open.length, 'PR'))} · ` : ''}${esc(worst?.word ?? '')}</span></span>`;
}

function decisionRail(item) {
  const card = item.card;
  const rail = item.rail ?? { held: [], asker: null, answers: [] };
  const tasks = item.blocks.map(({ id }) => featureOf(id)).filter(Boolean).sort((a, b) => (b.id === rail.asker?.feature) - (a.id === rail.asker?.feature));
  const links = [...(card.links ?? []), ...(card.refs ?? [])].filter((each) => !SLACK.test(each.url)).map((each) => `<li>${link(each.label, each.url)}</li>`).join('');
  return `<aside class="context-rail decision-sidebar" aria-label="About this decision">
    <div class="rail-block"><div class="eyebrow">Why it is #${item.priority.rank}</div><p class="rank-why"><b class="${esc(item.reason.tone)}">${esc(item.reason.label)}.</b> ${esc(item.reason.text)}${item.since ? `. Asked ${esc(ago(item.since))} ago.` : '.'}</p></div>
    ${item.told?.story?.needs_him ? `<div class="rail-block"><div class="eyebrow">Why it needs you</div><p class="rank-why">${esc(NEEDS_LABEL.get(item.told.story.needs_him))}.</p></div>` : ''}
    ${item.told?.story?.waits || tasks.length ? `<div class="rail-block"><div class="eyebrow">What waits on your answer</div>${item.told?.story?.waits ? `<p class="rank-why">${esc(item.told.story.waits)}</p>` : ''}${tasks.length ? `<ul class="held-list">${tasks.map((entry) => heldRow(item, entry, rail.held)).join('')}</ul>` : ''}</div>` : ''}
    <div class="rail-block"><div class="eyebrow">Not sure yet?</div>${sayBox(`q:${item.answer}`, item.answer, 'Ask a question, or answer in your own words', '', item.thread ?? '')}</div>
    ${item.thread ? `<details class="story-disclosure"><summary>Conversation so far</summary>${talkPane(item.thread, cardTs(item.answer))}</details>` : ''}
    <details class="story-disclosure" data-story-evidence><summary>The card as the drive wrote it</summary>${prose(card.body)}${links ? `<ul class="rail-list">${links}</ul>` : ''}${pictures(card.pictures)}${card.options.map((label) => card.hints?.[label] ? `<details><summary>${esc(decisionPlain(optionParts(label)[0]))}</summary>${prose(card.hints[label])}</details>` : '').join('')}</details>
    ${rail.answers.length ? `<details class="story-disclosure"><summary>Your earlier words</summary><ul class="said-list">${rail.answers.map(earlierWords).join('')}</ul></details>` : ''}
  </aside>`;
}

function decisionChapters(item, key, story, state, cause) {
  const card = item.card;
  const links = [...(card.links ?? []), ...(card.refs ?? [])].filter((each) => !SLACK.test(each.url));
  const parts = [
    ['happened', 'What happened', `<ol class="story-beats">${story.beats.map((beat) => `<li><div class="beat-head"><strong>${esc(beat.label)}</strong>${beat.when ? `<time>${esc(beat.when)}</time>` : ''}</div><p>${inline(beat.text)}</p>${beat.picture ? shown(beat.picture, beat.label) : ''}</li>`).join('')}</ol>${cause ? `<p class="story-cause"><b>Root cause:</b> ${esc(cause)}</p>` : ''}`],
    ['today', 'If you wait', story.today || story.diagram || story.waits ? `${story.today ? `<p class="ch-lead">${inline(story.today)}</p>` : ''}${story.waits ? `<p><b>What waits on you:</b> ${inline(story.waits)}</p>` : ''}${story.diagram ? visual('mermaid', story.diagram, 'How the paths part') : ''}` : ''],
    ['evidence', 'The evidence', card.body || card.pictures?.length || links.length ? `${prose(card.body)}${pictures(card.pictures)}${links.length ? `<ul class="rail-list">${links.map((each) => `<li>${link(each.label, each.url)}</li>`).join('')}</ul>` : ''}` : ''],
  ].filter(([, , body]) => body);
  const at = Math.min(state.chapter ?? 0, parts.length - 1);
  const nav = parts.length > 1 ? `<nav class="ch-nav" aria-label="Its story, step by step"><ol>${parts.map(([, label], i) => `<li><button class="${i === at ? 'on' : ''}${i < at ? ' seen' : ''}" data-dchapter="${i}" data-dchapter-of="${esc(key)}" aria-current="${i === at}"><span class="n">${i + 1}</span>${label}</button></li>`).join('')}<li><span class="ch-then">then your call below</span></li></ol></nav>` : '';
  return `${nav}<section class="ch-body ch-${parts[at][0]}">${parts[at][2]}</section>`;
}

function decisionCard(item, at, total, embedded = '') {
  const key = item.answer;
  const story = item.told?.story;
  if (!story) return `<div class="queue-layout story-layout"><article class="focus-card decision-story" aria-labelledby="item-title" tabindex="-1"><div class="card-content"><div class="card-top"><span class="type-label">Decision</span>${cardTop(item.tasks, at, total, embedded)}</div><h2 class="card-title" id="item-title">${esc(decisionPlain(item.card.title))}</h2>${item.told ? `${untold(item.told)}${choices(item, true)}` : notRead(slice(`/api/card?${new URLSearchParams({ answer: key })}`))}</div></article>${decisionRail(item)}</div>`;
  if (!decisionStates.has(key)) decisionStates.set(key, { selected: Math.max(0, item.card.options.indexOf(item.card.recommended)), step: 0, compare: false, chapter: 0 });
  const state = decisionStates.get(key);
  const showing = picked?.card === key ? picked.index : state.selected;
  const option = story.options[showing];
  const recommended = option.label === item.card.recommended;
  const cause = decisionCause(item.card.body).replace(/^none\b.*/i, '');
  const metrics = option.metrics.length ? `<dl class="story-metrics">${option.metrics.map((metric) => `<div><dd>${esc(metric.value)}</dd><dt>${esc(metric.label)}</dt></div>`).join('')}</dl>` : '';
  const pictured = item.option_pictures[showing].map((each) => (each.kind === 'image' ? shot(each.source, each.caption) : visual(each.kind, each.source, each.caption)));
  const media = pictured.length ? `<div class="story-outcome-media" data-option="${showing}"${pictured.length > 1 ? ` tabindex="0" aria-label="${pictured.length} pictures of this path"` : ''}>${pictured.join('')}</div>` : '';
  const outcome = `<section class="story-outcome" aria-live="polite"><span class="eyebrow">${recommended ? 'Recommended outcome' : showing === state.selected ? 'Selected outcome' : 'Previewed outcome'}</span><h3>${esc(option.title)}</h3>${option.consequence ? `<p>${inline(option.consequence)}</p>` : ''}${option.after ? `<p class="story-after"><b>After you pick</b> ${inline(option.after)}</p>` : ''}${media}${metrics}${item.card.hints?.[option.label] ? (option.consequence ? `<details class="story-more"><summary>In the card's own words</summary><div class="story-hint">${prose(item.card.hints[option.label])}</div></details>` : `<div class="story-hint">${prose(item.card.hints[option.label])}</div>`) : ''}</section>`;
  const options = `<div class="story-options" role="group" aria-label="Choose a path">${story.options.map((choice, i) => `<button class="story-option" data-story-pick="${i}" data-story-card="${esc(key)}" aria-pressed="${i === state.selected}"><kbd>${i + 1}</kbd><span><strong>${esc(choice.title)}</strong>${choice.label === item.card.recommended ? '<em>Recommended</em>' : ''}${story.format === 'lab' ? `<span class="option-metrics">${choice.metrics.map((metric) => `<span><b>${esc(metric.value)}</b>${esc(metric.label)}</span>`).join('')}</span>` : ''}</span><span aria-hidden="true">→</span></button>`).join('')}</div>`;
  const given = answers.get(key);
  const action = given?.done ? `<p class="answered">✓ ${esc(given.label)}</p>` : `<div class="story-action"><button class="btn" data-story-review="${esc(key)}"${given && !given.error ? ' disabled' : ''}>Review this choice <kbd>↵</kbd></button><small>Choose first. Review before sending.</small>${given?.error ? `<p role="alert">${esc(given.error)}</p>` : ''}</div>`;
  const lab = `<div class="story-lab">${options}${action}${outcome}<button class="linkish" data-story-compare="${esc(key)}" aria-expanded="${state.compare}">${state.compare ? 'Close comparison' : 'Compare every path'}</button>${state.compare ? table([['Path', 'What changes', ...story.options[0].metrics.map((metric) => metric.label)], ...story.options.map((choice) => [choice.title, choice.consequence, ...choice.metrics.map((metric) => metric.value)])]) : ''}</div>`;
  const interview = `<div class="story-interview"><div class="story-steps" aria-label="Step ${state.step + 1} of 2"><span class="on">1 · Your priority</span><span class="${state.step ? 'on' : ''}">2 · Your path</span></div>${state.step ? `<button class="linkish" data-story-back="${esc(key)}">← Change my priority</button>${outcome}${action}` : `<h3>${esc(story.ask)}</h3><div class="story-options">${story.options.map((choice, i) => `<button class="story-option" data-story-priority="${i}" data-story-card="${esc(key)}"><kbd>${i + 1}</kbd><span><strong>${esc(choice.priority)}</strong><small>${esc(choice.title)}</small></span><span aria-hidden="true">→</span></button>`).join('')}</div>`}</div>`;
  const call = story.format === 'lab' ? lab : story.format === 'interview' ? interview : `<div class="story-cockpit">${options}<div>${outcome}${action}</div></div>`;
  if (story.lead) return `<div class="queue-layout story-layout told-first"><article class="focus-card decision-story ${story.format}" aria-labelledby="item-title" tabindex="-1" data-story="${esc(key)}" data-format="${story.format}"><div class="card-content"><div class="card-top"><span class="type-label">Decision</span>${cardTop(item.tasks, at, total, embedded)}</div><h2 class="card-title" id="item-title">${esc(story.headline)}</h2>${story.needs_him ? `<p class="card-glance"><b>${esc(NEEDS_LABEL.get(story.needs_him))}</b></p>` : ''}${toldSections(story.lead, [['What it does today', story.now.text, story.now.sketch]], story.matters)}${restored.has(key) ? '<p class="restored" role="status">Restored. This is being asked again.</p>' : ''}<h3 class="eyebrow ch-call">Your call</h3>${call}<details class="told-more"><summary>What happened, step by step, and the evidence</summary>${decisionChapters(item, key, story, state, cause)}</details><details class="told-more"><summary>Why it ranks here, ask a question first, or read the card as written</summary>${decisionRail(item)}</details></div></article></div>`;
  return `<div class="queue-layout story-layout"><article class="focus-card decision-story ${story.format}" aria-labelledby="item-title" tabindex="-1" data-story="${esc(key)}" data-format="${story.format}"><div class="card-content"><div class="card-top"><span class="type-label">${{ cockpit: 'Decision', lab: 'Decision: compare the paths', interview: 'Decision: what matters most' }[story.format]}</span>${cardTop(item.tasks, at, total, embedded)}</div><h2 class="card-title" id="item-title">${esc(story.headline)}</h2>${story.needs_him || story.waits ? `<p class="card-glance">${story.needs_him ? `<b>${esc(NEEDS_LABEL.get(story.needs_him))}</b>` : ''}${story.needs_him && story.waits ? ' · ' : ''}${story.waits ? inline(story.waits) : ''}</p>` : ''}${decisionChapters(item, key, story, state, cause)}${restored.has(key) ? '<p class="restored" role="status">Restored. This is being asked again.</p>' : ''}<h3 class="eyebrow ch-call">Your call</h3>${story.format === 'lab' ? lab : story.format === 'interview' ? interview : `<div class="story-cockpit">${options}<div>${outcome}${action}</div></div>`}</div></article>${decisionRail(item)}</div>`;
}

function toldSections(lead, sections, matters, closing = []) {
  const sketch = (code) => (code ? `<pre class="told-sketch"><code>${esc(code)}</code></pre>` : '');
  const parts = sections.filter(([, text]) => text).map(([label, text, code]) => `<p><b>${esc(label)}:</b> ${inline(text)}</p>${sketch(code)}`);
  const why = matters?.mechanism ? `<p><b>Why it matters:</b> ${inline(matters.mechanism)}</p>${matters.consequences.length ? `<ul>${matters.consequences.map((each) => `<li>${inline(each)}</li>`).join('')}</ul>` : ''}` : '';
  const last = closing[1] ? `<p><b>${esc(closing[0])}:</b> ${inline(closing[1])}</p>` : '';
  return `<p class="card-lead">${inline(lead)}</p><section class="told">${parts.join('')}${why}${last}</section>`;
}

function reviewDecision(key) {
  const item = model.queue.find((each) => each.answer === key);
  if (!item) return;
  const story = opened(item).told.story;
  const option = story.options[decisionStates.get(key).selected];
  const dialog = $('#decision-review');
  dialog.innerHTML = `<span class="eyebrow">Review your decision</span><h2 id="decision-review-title">${esc(story.headline)}</h2><h3>${esc(option.title)}</h3><p>${inline(option.consequence)}</p>${option.after ? `<p class="story-after"><b>After you pick</b> ${inline(option.after)}</p>` : ''}<details><summary>The exact answer</summary><p>${esc(option.label)}</p></details><div class="story-action"><button class="btn" data-card="${esc(key)}" data-label="${esc(option.label)}">Send this answer</button><button class="btn ghost" data-story-close>Keep thinking</button></div>`;
  dialog.dataset.card = key;
  dialog.showModal();
  dialog.querySelector('[data-story-close]').focus();
}

function unjobbed(text) {
  return text.replace(/\b[jo]\d{2,5}'s\b/g, 'its').replace(/\b[jo]\d{2,5}\b/g, 'a drive job');
}

function approvePath(entry, row) {
  const checks = { pass: ['Pass', 'good'], fail: ['Failing', 'bad'], pending: ['Running', 'warn'] }[entry?.checks ?? row.checks] ?? ['None ran', ''];
  const review = { approved: ['Approved', 'good'], changes: ['Changes asked', 'bad'], stale: ['On an older commit', 'warn'] }[entry?.review ?? row.review] ?? ['Not yet', 'warn'];
  const staged = entry?.staging_plain ?? { word: 'Unknown', tone: '' };
  const tapped = (entry?.tap ?? row.tap) === 'Approve';
  const merge = entry?.landing ? ['Merging', 'warn'] : entry?.approve_waits === '' ? ['On your Approve', ''] : ['Waits', ''];
  const steps = [['Tests', ...checks], ['Code review', ...review], ['Staging', staged.word, staged.tone], ['You', tapped ? 'Approved' : 'Your call', tapped ? 'good' : 'now'], ['Merge', ...merge]];
  return `<ol class="approve-path" aria-label="From tests to merge">${steps.map(([step, word, tone]) => `<li class="${esc(tone)}"><span>${esc(step)}</span><b>${esc(word)}</b></li>`).join('')}</ol>
    <p class="approve-says">${esc(entry?.approve_plain ?? 'Approve records your yes.')}${row.changed ? ` <span class="warn">It changed since your tap: ${esc(decisionPlain(row.changed))}</span>` : ''}</p>`;
}

function showMe(row, pr, read) {
  const shown = read?.pr?.show_me?.trim();
  if (shown) return prose(shown, pr);
  const drawn = `${row.show_me ? prose(row.show_me, pr) : ''}${pictures(row.pictures)}`;
  if (read?.error) return `${drawn}<p class="refused">${esc(read.error)}</p>`;
  if (!read?.pr) return drawn || '<p class="meta">Reading its Show me…</p>';
  return drawn || '<p class="meta">Its description has no Show me section.</p>';
}

function reviewWait(pr, head) {
  const review = reviews.get(pr);
  if (review?.error) return `<div class="review-wait" role="alert"><p class="refused">cc-review could not open ${esc(pr)}: ${esc(review.error)}</p><button class="btn" data-retry="${esc(pr)}">Retry</button></div>`;
  const step = review?.url ? 1 : 0;
  const steps = [`Start cc-review for ${pr} at ${head.slice(0, 7)}`, 'Load its diff'].map((words, i) => `<li class="${i < step ? 'done' : i === step ? 'on' : ''}">${esc(words)}</li>`).join('');
  return `<div class="review-wait" role="status"><ol class="steps">${steps}</ol><div class="progress" aria-hidden="true"><i></i></div><p class="meta">cc-review reads the PR from GitHub. If it cannot, the reason shows here, with Retry.</p></div>`;
}

function prPane(tab, row, pr, head, read, inline) {
  if (tab === 'change') {
    const review = reviews.get(pr);
    const frame = inline && review?.url ? `<iframe class="review-frame${review.loaded ? '' : ' waiting'}" data-review="${esc(pr)}" title="Code and review of ${esc(pr)}" src="${esc(review.url)}" allow="clipboard-write"></iframe>` : '';
    return `<div class="review-slot" data-slot="${esc(pr)}" data-head="${esc(head)}">${frame}${review?.loaded ? '' : reviewWait(pr, head)}</div>`;
  }
  if (tab === 'context') {
    return read?.pr ? `<div class="whole">${prose(read.pr.body, pr)}</div>` : notRead(read);
  }
  return showMe(row, pr, read);
}

function stackRow(row, i, on, key) {
  const entry = entryOf(prKey(row.url));
  const status = prWord(entry, row);
  const name = `<span class="n">${i + 1}</span><span class="name">${esc(entry?.headline ?? bare(row.title))}</span>`;
  if (row.state !== 'open') return `<li class="gone">${name}<span class="row-word ${esc(status.tone)}">${esc(status.word)}</span></li>`;
  return `<li><button class="row${on ? ' on' : ''}" data-row="${i}" data-item="${esc(key)}" aria-current="${on}">${name}<span class="row-word ${esc(status.tone)}">${dot(status)}${esc(status.word)}</span></button></li>`;
}

function spots(html) {
  return [...Object.assign(document.createElement('template'), { innerHTML: html }).content.querySelectorAll(SPOTS)];
}

function leadOf(el) {
  return el.previousElementSibling?.matches('p') ? el.previousElementSibling.textContent.trim() : '';
}

function spotLabel(el, i) {
  const kind = el.querySelector('[data-visual]')?.dataset.visual;
  return el.textContent.trim() || el.dataset.caption || leadOf(el) || (kind && (VISUAL_WORDS[kind] ?? `${kind.replace(/^code:/, '')} code`)) || `Picture ${i + 1}`;
}

function firstPicture(key, row, pr, read) {
  const shown = spots(showMe(row, pr, read));
  const [first, next, ...rest] = shown.filter((el) => !el.matches('h4.hd'));
  if (!first) return '';
  const caption = first.dataset.caption || first.querySelector('figcaption')?.textContent.trim() || leadOf(first);
  return `<section class="pr-example" aria-label="${esc(caption || 'The first picture of its Show me')}">${first.outerHTML}${caption ? `<p class="caption">${esc(caption)}</p>` : ''}${next ? `<button type="button" class="linkish" data-spot="${shown.indexOf(next)}" data-item="${esc(key)}">${rest.length + 1} more in Show me →</button>` : ''}</section>`;
}

function jumps(key, row, pr, read) {
  const shown = spots(showMe(row, pr, read)).map((el, i) => `<li><button class="linkish" data-spot="${i}" data-item="${esc(key)}">${esc(spotLabel(el, i))}</button></li>`).join('');
  const words = [row.summary, read?.pr?.show_me, ...PR_FIELDS.map(([field]) => row[field])].join('\n');
  const cites = [...new Map(sharedRenderers.sourceCites(words).map((cite) => [`${cite.path}:${cite.line}`, cite])).values()];
  const point = (path, line, label) => `<li><button class="linkish" data-point="${esc(pr)}" data-item="${esc(key)}" data-path="${esc(path)}"${line ? ` data-line="${line}"` : ''}>${esc(label)}</button></li>`;
  const files = reviews.get(pr)?.files;
  return `${shown ? `<p class="spot-head">Show me</p><ul class="spots">${shown}</ul>` : ''}
    ${cites.length ? `<p class="spot-head">Lines it cites</p><ul class="spots">${cites.map(({ path, line }) => point(path, line, `${path}:${line}`)).join('')}</ul>` : ''}
    <p class="spot-head">Changed files</p>${files ? `<ul class="spots">${files.map((path) => point(path, '', path)).join('')}</ul>` : '<p class="rail-note">They list here once the review opens.</p>'}`;
}

function noteBox(key, pr) {
  const point = pointed.get(pr);
  const was = notes.get(pr);
  const status = was?.busy ? `<span class="meta">${reviews.get(pr)?.files ? 'Adding it to the review…' : 'Waiting for the review to open, then adding it…'}</span>`
    : was?.error ? `<span class="refused">Not added: ${esc(was.error)}</span>`
    : was?.at ? `<span class="meta">✓ Added ${clock(was.at)}${was.where ? ` on ${esc(was.where)}` : ''}. The review's assistant answers it in The change.</span>` : '';
  const at = point ? `<span class="point">On <code>${esc(point.line ? `${point.path}:${point.line}` : point.path)}</code><button type="button" class="linkish" data-unpoint="${esc(pr)}" aria-label="Ask about the whole change instead">×</button></span>` : '<span class="meta">About the whole change. Pick a file or line above to comment on it there.</span>';
  return `<form class="say note" data-note="${esc(pr)}" data-item="${esc(key)}">${at}
    <textarea data-draft="n:${esc(pr)}" rows="2" placeholder="Ask a question, or comment on that line" aria-label="Question or line comment"${was?.busy ? ' disabled' : ''}>${esc(drafts.get(`n:${pr}`) ?? '')}</textarea>
    <button class="btn"${was?.busy ? ' disabled' : ''}>Add to review</button>
    ${status}
  </form>`;
}

const RISK_FIELDS = [['hate_most', "What you'll hate most"], ['surprise', 'Biggest surprise'], ['impact', 'Who else it touches'], ['plan', 'Where it left the plan']];

function risks(entry, row, pr, read) {
  const size = read?.pr ? `<li><span class="mark">±</span><span><b><span class="add">+${read.pr.additions}</span> <span class="del">−${read.pr.deletions}</span></b> across ${esc(counted(read.pr.changedFiles, 'file'))}</span></li>` : '';
  const failed = entry?.failed.length ? `<li class="bad"><span class="mark">✕</span><span><b>Failing:</b> ${entry.failed.map((name) => link(name, `${entry.url}/checks`)).join(', ')}</span></li>` : '';
  const conflict = entry?.conflict ? `<li class="bad"><span class="mark">✕</span><span><b>Needs a rebase.</b> GitHub cannot merge it onto ${esc(entry.base)} as it stands.</span></li>` : '';
  const proof = (entry?.staging ?? '').match(/\[proof\]\((https?:[^)\s]+)\)/)?.[1];
  const staged = proof ? `<li class="good"><span class="mark">✓</span><span>${link('Open its staging proof', proof)}</span></li>` : '';
  const said = RISK_FIELDS.filter(([field]) => row[field]).map(([field, label]) => {
    const text = unjobbed(row[field].replace(/^\s*[-*•]\s*/, '').trim());
    const [lead, rest] = firstSentence(text);
    const body = `<b>${esc(label)}.</b> ${inline(lead, pr)}`;
    return `<li><span class="mark">!</span>${rest ? `<details><summary>${body}</summary><p>${inline(rest, pr)}</p></details>` : `<span>${body}</span>`}</li>`;
  }).join('');
  const html = conflict + failed + said + staged + size;
  return html ? `<div class="rail-block"><div class="eyebrow">Before you approve</div><ul class="checklist">${html}</ul></div>` : '';
}

function firstSentence(text) {
  for (const found of text.matchAll(/(?<=[.;])\s+(?=[A-Z])/g)) {
    const lead = text.slice(0, found.index);
    const count = (open, shut) => lead.split(open).length - lead.split(shut).length;
    if (!count('(', ')') && !count('[', ']') && !(lead.split('`').length % 2 === 0)) return [lead, text.slice(found.index + found[0].length)];
  }
  return [text, ''];
}

function serves(entry) {
  const task = featureOf(entry.feature);
  if (!task && !entry.feature_title) return '';
  return `<div class="rail-block"><div class="eyebrow">Part of</div><p class="serves">${task ? `<a href="#/feature/${esc(task.id)}">${esc(bare(task.title))}</a>` : esc(bare(entry.feature_title))}</p></div>`;
}

function chapterBody(id, { item, key, row, pr, head, read, entry, lead, told, beats, pictured }) {
  const story = told?.story;
  if (id === 'problem') {
    const text = story ? `<p class="ch-who"><span>Who feels it</span><b>${esc(story.problem.who)}</b></p><p class="ch-lead">${inline(story.problem.text)}</p>` : `<div class="pr-story">${beats}</div>`;
    return pictured ? `<div class="pr-lede"><div>${text}</div>${pictured}</div>` : text;
  }
  if (id === 'change') return `${story ? '' : '<p class="meta">The why of each part is still being written; the change itself is below.</p>'}${diffBlock(pr, head, story?.hunks ?? [])}`;
  if (id === 'runtime') {
    if (!story) return `<p>${lead?.what ? inline(unjobbed(lead.what), pr) : esc(row.summary ?? 'Its runtime story is still being written.')}</p>${lead?.downstream ? `<p><b>Downstream:</b> ${inline(unjobbed(lead.downstream), pr)}</p>` : ''}`;
    return `<div class="ap-ba"><div><span>Before</span><p>${inline(story.runtime.before)}</p></div><i aria-hidden="true">→</i><div class="after"><span>After</span><p>${inline(story.runtime.after)}</p></div></div>${story.runtime.diagram ? visual('mermaid', story.runtime.diagram, 'What happens after the change') : ''}`;
  }
  if (id === 'evidence') {
    const status = prWord(entry, row);
    const staged = entry?.staging_plain;
    const tiles = `<div class="ch-tiles"><div class="ap-stage ${esc(status.tone)}"><span>Now</span><b>${esc(status.word)}</b></div>${staged ? `<div class="ap-stage ${esc(staged.tone)}"><span>Staging</span><b>${esc(staged.word)}</b></div>` : ''}<div class="ap-stage ${esc({ pass: 'good', fail: 'bad', pending: 'warn' }[entry?.checks ?? row.checks] ?? '')}"><span>Tests</span><b>${esc({ pass: 'Pass', fail: 'Failing', pending: 'Running' }[entry?.checks ?? row.checks] ?? 'None ran')}</b></div></div>`;
    return `${tiles}${story?.tested ? `<p class="ch-lead">${inline(story.tested)}</p>` : entry?.staging ? `<p>${inline(unjobbed(entry.staging), pr)}</p>` : ''}${risks(entry, row, pr, read)}`;
  }
  const sides = story ? `<div class="ap-compare"><div class="ap-choice recommended"><b>If you approve</b><p>${inline(story.approve)}</p></div><div class="ap-choice"><b>If you hold</b><p>${inline(story.hold)}</p></div></div>` : '';
  return `${item.untapped ? `<p class="approve-says">${esc(item.untapped)}</p>` : approvePath(entry, row)}${sides}`;
}

function cardGlance(entry, row) {
  const status = prWord(entry, row);
  const staged = entry?.staging_plain;
  return `<p class="card-glance">${dot(status)}<b>${esc(status.word)}</b>${staged && staged.word !== status.word ? ` · ${esc(staged.word)}` : ''}${entry?.approve_plain ? ` · ${esc(entry.approve_plain)}` : ''}</p>`;
}

function chapters(key, pr, parts) {
  const at = prChapters.get(pr) ?? 0;
  const nav = PR_CHAPTERS.map(([id, label], i) => `<li><button class="${i === at ? 'on' : ''}${i < at ? ' seen' : ''}" data-chapter="${i}" data-chapter-of="${esc(pr)}" aria-current="${i === at}"><span class="n">${i + 1}</span>${label}</button></li>`).join('');
  const [id, label] = PR_CHAPTERS[at];
  const next = PR_CHAPTERS[at + 1];
  const foot = `<div class="ch-foot">${at ? `<button class="btn ghost" data-chapter="${at - 1}" data-chapter-of="${esc(pr)}" data-chapter-step="-1">‹ ${PR_CHAPTERS[at - 1][1]}</button>` : '<span></span>'}${next ? `<button class="btn" data-chapter="${at + 1}" data-chapter-of="${esc(pr)}" data-chapter-step="1">Next: ${next[1]} ›</button>` : ''}</div>`;
  return `<nav class="ch-nav" aria-label="Its story, step by step"><ol>${nav}</ol></nav><section class="ch-body ch-${id}" aria-label="${label}">${chapterBody(id, parts)}${foot}</section>`;
}

function prRail(item, key, rows, row, pr, head, read, inline) {
  const entry = entryOf(pr);
  return `<aside class="context-rail pr-rail" aria-label="About this change">
    ${entry ? serves(entry) : ''}
    <details class="story-disclosure"><summary>Pictures, lines and files</summary>${jumps(key, row, pr, read)}</details>
    <div class="rail-block"><div class="eyebrow">Question or line comment</div>${noteBox(key, pr)}</div>
    <div class="rail-block talk"><div class="eyebrow"${inline ? '' : ' data-key="q"'}>Tell the drive what to change</div>${item.thread ? `<details class="story-disclosure"><summary>Conversation</summary>${talkPane(item.thread, cardTs(item.answer))}</details>` : ''}
      ${sayBox(item.answer ? `t:${item.answer}` : `r:${pr}`, item.answer ?? '', 'Ask the drive, or tell Claude what to change', item.answer ? '' : `About ${row.url}`, item.thread ?? '')}
    </div>
  </aside>`;
}

function prCard(item, at, total, embedded = '') {
  const key = noticeKey(item);
  const rows = (item.card?.prs ?? [{ url: item.url, title: item.title }]).map((row, i) => ({ ...prReads.get(prKey(row.url))?.pr?.row, ...item.prs?.[i], ...row }));
  const chosen = chosenRow(item);
  const row = rows[chosen];
  const pr = prKey(row.url);
  const entry = model.review.find((each) => each.key === pr);
  const head = entry?.head ?? row.head ?? '';
  const lead = entry?.lead;
  const read = prReads.get(pr);
  const tab = prTabs.get(key) ?? 'show';
  const told = slice(`/api/told?${new URLSearchParams({ pr })}`)?.told;
  const title = told?.story?.headline ?? lead?.headline ?? decisionPlain(read?.pr?.title ?? row.title).replace(/^./, (c) => c.toUpperCase());
  const context = read?.pr?.body?.match(/^## Context\s*\n([\s\S]*?)(?=^## |$(?![\s\S]))/m)?.[1]?.trim();
  const beats = [['Why this exists', lead?.why ?? context ?? row.refresher], ['What changes', lead?.what ?? row.summary], ['Downstream', lead?.downstream ?? row.hate_most]].map(([label, text]) => [label, text && unjobbed(text)]);
  const story = beats.map(([label, text]) => `<div><strong>${esc(label)}</strong><p>${text ? inline(text, pr) : 'The author has not supplied this explanation yet.'}</p></div>`).join('');
  const walk = chapters(key, pr, { item, key, row, pr, head, read, entry, lead, told, beats: story, pictured: firstPicture(key, row, pr, read) });
  const stack = rows.length > 1 ? `<p class="spot-head stack-head">Approve lands these ${rows.length} together, bottom first</p><ol class="stack" aria-label="The PRs on this card, in the order they land">${rows.map((each, i) => stackRow(each, i, i === chosen, key)).join('')}</ol>` : '';
  const tabs = PR_TABS.map(([id, label]) => `<button role="tab" class="${id === tab ? 'on' : ''}" aria-selected="${id === tab}" data-prtab="${id}" data-item="${esc(key)}">${label}</button>`).join('');
  const panes = PR_TABS.map(([id]) => `<div class="pr-pane ${id}" role="tabpanel"${id === tab ? '' : ' hidden'}>${prPane(id, row, pr, head, read, embedded)}</div>`).join('');
  const written = told?.story;
  if (written?.lead) return `<div class="pr-layout told-first" data-read="${esc(pr)}" data-head="${esc(head)}">
    <article class="focus-card pr-card" aria-labelledby="item-title" tabindex="-1">
      <div class="card-content">
        <div class="card-top"><span class="type-label">Pull request <a href="${esc(row.url)}" target="_blank" rel="noopener" data-github data-tip="Open it on GitHub"${embedded ? '' : ' data-key="o"'}>${esc(pr)}</a></span>${cardTop(entry ? [entry.feature] : [], at, total, embedded)}</div>
        <h2 class="card-title" id="item-title">${esc(title)}</h2>
        ${cardGlance(entry, row)}
        ${toldSections(written.lead, [['What it does', written.does.text, written.does.sketch], ["What's decided", written.decided]], written.matters, ['Before you approve', written.check])}
      </div>
      <div class="pr-actions">${choices(item, !embedded)}${rows.filter((each) => each.state === 'open').length > 1 ? `<span class="meta">Approve lands all ${rows.filter((each) => each.state === 'open').length} open PRs, bottom first</span>` : ''}</div>
      <details class="told-more"><summary>The change file by file, its tests and history</summary><div class="card-content">${walk}${stack}</div><div class="pr-tabs" role="tablist" aria-label="About this change">${tabs}</div>${panes}</details>
      <details class="told-more"><summary>Comment on a line, or tell the drive what to change</summary>${prRail(item, key, rows, row, pr, head, read, embedded)}</details>
    </article>
  </div>`;
  return `<div class="pr-layout" data-read="${esc(pr)}" data-head="${esc(head)}">
    <article class="focus-card pr-card" aria-labelledby="item-title" tabindex="-1">
      <div class="card-content">
        <div class="card-top"><span class="type-label">Pull request <a href="${esc(row.url)}" target="_blank" rel="noopener" data-github data-tip="Open it on GitHub"${embedded ? '' : ' data-key="o"'}>${esc(pr)}</a></span>${cardTop(entry ? [entry.feature] : [], at, total, embedded)}</div>
        <h2 class="card-title" id="item-title">${esc(title)}</h2>
        ${cardGlance(entry, row)}
        ${walk}
        ${told && !told.story ? untold(told) : ''}
        ${stack}
      </div>
      <div class="pr-tabs" role="tablist" aria-label="About this change">${tabs}</div>
      ${panes}
      <div class="pr-actions">${choices(item, !embedded)}${rows.filter((each) => each.state === 'open').length > 1 ? `<span class="meta">Approve lands all ${rows.filter((each) => each.state === 'open').length} open PRs, bottom first</span>` : ''}</div>
    </article>
    ${prRail(item, key, rows, row, pr, head, read, embedded)}
  </div>`;
}

function queueScreen(id) {
  const filter = filterOf(id);
  const list = shownQueue(filter);
  const at = current(list);
  const item = list[at];
  const [, named, number] = route().path;
  const picked = number && item?.answer && cardTs(item.answer) === named ? (item.card?.prs ?? []).findIndex((row) => row.url.endsWith(`/pull/${number}`)) : -1;
  if (picked >= 0) prRows.set(noticeKey(item), picked);
  if (location.hash !== queueHref(filter, item)) history.replaceState(history.state, '', queueHref(filter, item));
  const filters = QUEUE_FILTERS.map(([each, label]) => `<a href="#/queue/${each}${scopeQuery()}" class="${each === filter ? 'on' : ''}"${each === filter ? ' aria-current="true"' : ''}>${label}<span class="n">${shownQueue(each).length}</span></a>`).join('');
  const unread = model.cards === 'live' ? '' : `<p class="refused">Your questions are not listed here: cc-slack ${esc(model.cards)}</p>`;
  const rest = shownQueue('all').length;
  const done = `<div class="focus-card done"><span class="bigcheck" aria-hidden="true">✓</span><h2>${filter === 'all' ? 'Nothing waits on you.' : NONE_LEFT[filter]}</h2>${filter !== 'all' && rest ? `<p><a href="#/queue/all">${rest} other ${rest === 1 ? 'thing waits' : 'things wait'} in All ›</a></p>` : ''}</div>`;
  const shown = item ? { question: decisionCard, tap: prCard }[item.kind](opened(item), at, list.length) : done;
  const from = queueFrom();
  const whole = `#/queue/${filter}${item?.answer ? `/${cardTs(item.answer)}` : ''}${scopeQuery(undefined, undefined, undefined, null)}`;
  return `<div class="page-head">
      <div><div class="eyebrow">Your attention</div><h1 class="h1">Priority queue <span class="total-chip">${list.length}</span></h1></div>
      <div class="queue-tools">${queueScope(filter)}<nav class="subtabs" aria-label="Show">${filters}</nav></div>
    </div>
    ${from ? `<p class="queue-from" role="status"><span>Showing ${esc(counted(list.length, 'card'))} from <a href="#/pages/${encodeURIComponent(from.slug)}">${esc(from.layout?.composed?.headline ?? from.title)}</a></span><a class="chip-x" href="${esc(whole)}">Show all ›</a></p>` : ''}
    <p class="queue-order">${filter === 'question' ? 'Grouped by why each decision needs you, riskiest first; inside a group, ' : ''}${filter === 'question' ? 'most' : 'Most'} urgent first: fixes for live incidents, then finished work waiting on your answer, then what frees the most work, then the oldest. What the drive is answering you on sits last.</p>
    ${unread}
    ${list.length === 1 ? `<div class="queue-current">${priorityChips(item)}</div>` : ''}
    <div class="queue-shell${list.length > 1 ? '' : ' alone'}">${list.length > 1 ? `<details class="queue-picker"${narrow.matches ? '' : ' open'}><summary>Browse ranked queue <span>${list.length}</span></summary>${queueList(list, at, filter)}</details>` : ''}${shown}</div>
    <footer class="qfoot"><span>As of ${clock(model.at)}</span><span><kbd>j</kbd><kbd>k</kbd> move <kbd>1–9</kbd> select <kbd>↵</kbd> review <kbd>a</kbd> approve <kbd>h</kbd> hold <kbd>o</kbd> GitHub <kbd>q</kbd> ask <kbd>e</kbd> explain <kbd>f</kbd> filter <kbd>[</kbd><kbd>]</kbd> project <kbd>⌘Z</kbd> undo <button type="button" class="linkish" data-keys><kbd>?</kbd> every key</button></span></footer>`;
}

function queueList(list, at, filter) {
  const grouped = filter === 'question';
  return `<ol class="queue-list" aria-label="${grouped ? 'Your decisions, by why each needs you' : 'Everything here, most urgent first'}">${list.map((item, i) => {
    const head = grouped && (i === 0 || list[i - 1].needs !== item.needs) ? `<li class="qgroup" aria-hidden="true">${esc(NEEDS_LABEL.get(item.needs) ?? 'Still being written up')} <span>${list.filter((each) => each.needs === item.needs).length}</span></li>` : '';
    return `${head}${queueRow(item, i === at, grouped, `a class="qrow-main" href="${esc(queueHref(filter, item))}" data-queue="${esc(noticeKey(item))}"`, 'a')}`;
  }).join('')}</ol>`;
}

function queueRow(item, on, grouped, open, tag) {
  const why = item.reason.tone === 'bad' ? `<span class="qrow-why">${esc(item.reason.text)}</span>` : '';
  return `<li class="qrow${on ? ' on' : ''} ${esc(item.reason.tone)}" data-rank="${item.priority.rank}">
      <${open}${on ? ' aria-current="true"' : ''}>${priorityChips(item)}<span class="qrow-kind">${LABEL[item.kind]} · ${esc(fileName(fileKey(item.project)))}</span><span class="qrow-title">${esc(item.headline ?? bare(item.title))}</span>${why}${grouped ? '' : needsChip(item)}${itemStatus(item)}</${tag}>
      ${item.kind === 'question' ? '' : `<span class="qrow-acts">${rowActions(item)}</span>`}
    </li>`;
}

function rowActions(item) {
  return (item.card?.options ?? TAP_LABELS).map((option) => `<button class="mini${option === 'Approve' ? ' go' : ''}" data-card="${esc(answerKey(item))}" data-label="${esc(option)}">${esc(option)}</button>`).join('');
}

function nowScreen() {
  const [project, stream, taskId] = route().path;
  const { tree } = index();
  const streams = project === undefined ? null : tree.get(project);
  if (project !== undefined && !streams) return gone('This project');
  const list = stream === undefined ? null : streams.get(stream);
  if (stream !== undefined && !list) return gone('This workstream');
  const task = list?.find((entry) => entry.id === taskId) ?? null;
  const keys = [project, stream].filter((key) => key !== undefined);
  const scope = task ? [task] : list ?? (streams ? [...streams.values()].flat() : null);
  const entries = scope ?? features();
  const queued = shownQueue('all', project ?? null, stream ?? null);
  const picks = queued.filter((item) => item.kind === 'question').length;
  const taps = queued.filter((item) => item.kind === 'tap').length;
  const metrics = [['Decisions', picks, 'warm', `#/queue/question${scopeQuery(project ?? null, stream ?? null)}`], ['PRs to approve', taps, 'you', `#/queue/tap${scopeQuery(project ?? null, stream ?? null)}`], ['Moving', entries.filter((entry) => entry.state === 'running').length, 'doing', null], ['Blocked', entries.filter((entry) => entry.state === 'blocked').length, 'bad', null], ['Live', entries.filter((entry) => entry.state === 'live').length, 'done', '#/history']];
  const side = task ? '' : `<aside class="now-side" aria-label="What is happening here">${scopeWork(scope, project ?? null, stream ?? null)}${story(scope, 'Latest movement')}</aside>`;
  return `<div class="now-head"><div><div class="eyebrow">Now · ${keys.length ? 'In focus' : 'Across the drive'}</div><h1 class="h1 zoom-title">${esc(keys.length ? fileName(keys.at(-1)) : 'What needs you')}</h1></div><div class="now-actions">${project !== undefined ? `<a class="map-btn" href="${zoomHref('now', ...keys.slice(0, -1))}">← Back</a>` : ''}<a class="map-btn primary" href="#/queue/all${scopeQuery(project ?? null, stream ?? null)}">Open queue →</a></div></div>
    <div class="now-metrics">${metrics.map(([label, count, tone, href]) => `<${href ? 'a' : 'div'} class="metric t-${tone}"${href ? ` href="${esc(href)}"` : ''}><span>${label}</span><b>${count}</b>${href ? '<span class="metric-arrow" aria-hidden="true">›</span>' : ''}</${href ? 'a' : 'div'}>`).join('')}</div>
    ${stream === undefined ? priorityLenses(project ?? null) : ''}
    ${slackBars(project)}
    ${prStatus(scope)}
    ${nowCanvas(tree, project, stream, task, side)}`;
}

function lensOf(id) {
  return model.priorities.find((lens) => lens.id === id);
}

function lensQueue(lens) {
  const members = new Set(lens.members);
  return model.queue.filter((item) => item.tasks.some((id) => members.has(id)));
}

function prioChips(ids) {
  return ids.map(lensOf).filter(Boolean).map((lens) => `<span class="prio-chip" title="${esc(`Also in the ${lens.name} priority`)}">★ ${esc(lens.name)}</span>`).join('');
}

function priorityLenses(project) {
  const lenses = model.priorities.filter((lens) => project === null || lens.projects.includes(project));
  if (!lenses.length) return '';
  return `<section class="prio-lenses" aria-labelledby="prio-title"><div class="eyebrow" id="prio-title">Priorities <span class="reason-chip">${lenses.length}</span></div><p class="meta">Each cuts across projects; its work stays filed under its own project too.</p>${lenses.map((lens) => {
    const asks = lensQueue(lens);
    const decisions = asks.filter((item) => item.kind === 'question').length;
    const taps = asks.length - decisions;
    return `<a class="prio-lens" href="#/priority/${esc(lens.id)}"><span class="prio-star" aria-hidden="true">★</span><span class="prio-body"><b>${esc(lens.name)}</b><small>${esc(counted(lens.open, 'open order'))} across ${esc(lens.projects.join(', '))}</small>${decisions || taps ? `<small class="prio-asks">${[decisions && counted(decisions, 'decision'), taps && counted(taps, 'PR card')].filter(Boolean).join(' · ')} for you</small>` : ''}</span><span class="arrow" aria-hidden="true">›</span></a>`;
  }).join('')}</section>`;
}

function priorityScreen(id) {
  const lens = lensOf(id);
  if (!lens) return gone('This priority');
  const shown = lens.members.map(featureOf).filter(Boolean);
  const byProject = new Map();
  for (const entry of shown) byProject.set(fileKey(entry.project), [...(byProject.get(fileKey(entry.project)) ?? []), entry]);
  const asks = lensQueue(lens);
  const page = model.pages.find((each) => each.orders?.includes(lens.id));
  const metrics = [['Open orders', lens.open, 'doing'], ['Projects', lens.projects.length, ''], ['Decisions', asks.filter((item) => item.kind === 'question').length, 'warm'], ['PR cards', asks.filter((item) => item.kind !== 'question').length, 'you']];
  return `<div class="now-head"><div><div class="eyebrow prio-eyebrow">★ Priority · across ${esc(counted(lens.projects.length, 'project'))}</div><h1 class="h1 zoom-title">${esc(lens.name)}</h1><p class="meta">${esc(bare(lens.title))}</p></div><div class="now-actions">${page ? `<a class="map-btn" href="#/pages/${esc(page.slug)}">Its page ›</a>` : ''}<a class="map-btn primary" href="#/feature/${esc(lens.id)}">The priority order ›</a></div></div>
    <div class="now-metrics">${metrics.map(([label, count, tone]) => `<div class="metric t-${tone}"><span>${label}</span><b>${count}</b></div>`).join('')}</div>
    ${asks.length ? `<section class="scope-work prio-asks-list"><div class="eyebrow">Waiting on you <span class="reason-chip">${asks.length}</span></div>${queueList(asks, -1, 'all')}</section>` : ''}
    ${[...byProject].sort(byName).map(([project, entries]) => `<section class="prio-project"><h2 class="eyebrow">${esc(fileName(project))} <span class="reason-chip">${entries.length}</span></h2><div class="task-list">${entries.map((entry) => taskRow(entry)).join('')}</div></section>`).join('')}
    ${lens.members.length > shown.length ? `<p class="meta">${esc(counted(lens.members.length - shown.length, 'finished order'))} no longer shown.</p>` : ''}`;
}

function slackBars(project) {
  const { rows, owed } = model.sweep;
  const shown = rows.filter((row) => project === undefined || (row.priority ? lensOf(row.priority)?.projects.includes(project) : fileKey(row.project) === project));
  if (!shown.length) return '';
  const unread = owed !== 'live';
  const crossed = shown.reduce((sum, row) => sum + SWEEP_BARS.filter(([key]) => row.bars[key].crossed).length, 0);
  const summary = [crossed ? `${counted(crossed, 'bar')} crossed` : unread ? 'Other bars clear' : 'All clear: no bar crossed', unread && 'Owed unread'].filter(Boolean).join(' · ');
  return `<section class="slack-bars" aria-labelledby="slack-bars-title">
    <div class="sb-head"><h2 class="eyebrow" id="slack-bars-title">Drive follow-through</h2><span id="slack-bars-summary">${esc(summary)}</span></div>
    <div class="sb-scroll" role="region" aria-label="Follow-through by project" tabindex="0"><table aria-describedby="slack-bars-summary">
      <thead><tr><th scope="col">Project</th>${SWEEP_BARS.map(([, label, hint, title]) => `<th scope="col" title="${esc(title)}">${label}<small>${esc(hint)}</small></th>`).join('')}</tr></thead>
      <tbody>${shown.map((row, i) => `${row.priority && !shown[i - 1]?.priority ? `<tr class="sb-group"><th scope="rowgroup" colspan="${SWEEP_BARS.length + 1}">Priorities</th></tr>` : ''}<tr${row.priority ? ' class="sb-prio"' : ''}><th scope="row">${row.priority ? `<a href="#/priority/${esc(row.priority)}">★ ${esc(row.project)}</a>` : `<span>${esc(fileName(fileKey(row.project)))}</span>`}</th>${SWEEP_BARS.map(([key]) => `<td>${sweepCell(key, row.bars[key], unread && key === 'owed' ? owed : '')}</td>`).join('')}</tr>`).join('')}</tbody>
    </table></div>
  </section>`;
}

function sweepCell(key, cell, unread) {
  if (unread) return `<span class="sb-cell is-unknown"><b>Unread: ${esc(unread)}</b></span>`;
  if (!cell.count) return `<span class="sb-cell is-empty"><b>${key === 'answers' ? 'No answers' : 'None'}</b></span>`;
  const value = key === 'answers' ? `${(cell.p90 / 60).toFixed(1)} min` : String(cell.count);
  const note = key === 'answers' ? counted(cell.count, 'answer') : `oldest ${ago(cell.since)}`;
  return `<a class="sb-cell${cell.crossed ? ' is-crossed' : ''}" href="${esc(cell.link)}" target="_blank" rel="noopener" title="${key === 'answers' ? 'Open the slowest answer' : 'Open the oldest'}"><b>${esc(value)}</b><small>· ${esc(note)}</small></a>`;
}

function prStatus(scope) {
  const urls = scope && new Set(scope.flatMap((entry) => entry.prs.map((pr) => pr.url)));
  const since = (entry) => (entry.stage.since ? Date.parse(entry.stage.since) : Infinity);
  const rows = model.review.filter((entry) => entry.on_him && (!urls || urls.has(entry.url))).sort((a, b) => since(a) - since(b));
  if (!rows.length) return '';
  return `<section class="pr-status" aria-labelledby="pr-status-title">
    <div class="pr-status-head"><h2 class="eyebrow" id="pr-status-title">Approved or waiting on you <span class="reason-chip">${rows.length}</span></h2><span>Oldest stage first</span></div>
    <div class="pr-status-scroll" role="region" aria-label="Open pull requests" tabindex="0"><table>
      <thead><tr><th scope="col">PR</th><th scope="col">Stage</th><th scope="col">What holds it</th><th scope="col">Owner</th><th scope="col">Since</th></tr></thead>
      <tbody>${rows.map(({ key, title, url, tap, stage }) => `<tr>
        <td><a href="${esc(url)}">${esc(bare(title))}</a><small>${esc(key)}${tap === 'Approve' ? ' · Approved' : ''} · <button type="button" class="linkish" data-pr="${esc(key)}">Details</button></small></td>
        <td><span class="state-pill">${esc(stage.name)}</span></td>
        <td>${esc(stage.why)}</td>
        <td${stage.owner === 'him' ? ' class="pr-owner-you"' : ''}>${esc(stage.owner === 'him' ? 'You' : stage.owner)}</td>
        <td>${stage.since ? `<time datetime="${esc(stage.since)}">${esc(ago(stage.since))} ago</time><small>${esc(clock(stage.since))}</small>` : 'Just now'}</td>
      </tr>`).join('')}</tbody>
    </table></div>
  </section>`;
}

function scopeWork(scope, project, stream) {
  const asks = shownQueue('all', project, stream);
  const queued = new Set(asks.flatMap((item) => item.prs?.map((pr) => pr.url) ?? []));
  const open = new Map((scope ?? []).flatMap((entry) => entry.prs.filter((pr) => pr.state === 'open' && !queued.has(pr.url)).map((pr) => [pr.url, pr])));
  const rows = model.review.filter((entry) => open.has(entry.url)).map((entry) => `<li class="qrow"><button class="qrow-main linkish" data-pr="${esc(entry.key)}" data-tip="${esc([entry.title, prSaid(open.get(entry.url)), entry.job_said].filter(Boolean).join('\n'))}"><span class="qrow-kind">${LABEL.tap}</span><span class="qrow-title">${esc(bare(entry.title))}</span><small>${esc(prSaid(open.get(entry.url)))}</small></button></li>`);
  return `<section class="scope-work">
    <div class="eyebrow">Waiting on you <span class="reason-chip">${asks.length}</span></div>
    ${asks.length ? queueList(scope ? asks : asks.slice(0, 2), -1, 'all') : ''}
    ${rows.length ? `<div class="eyebrow">Other open PRs <span class="reason-chip">${rows.length}</span></div><ol class="queue-list">${rows.join('')}</ol>` : ''}
    <a class="more" href="#/queue/all${scopeQuery(project, stream)}">Open ranked queue ›</a>
  </section>`;
}

function pagesScreen(slug) {
  if (slug) return pageScreen(model.pages.find((page) => page.slug === slug));
  const { query } = route();
  const show = PAGE_FILTERS.some(([id]) => id === query.get('show')) ? query.get('show') : 'live';
  const project = query.get('project');
  const stream = query.get('stream');
  const href = (changes) => `#/pages?${new URLSearchParams(Object.entries({ show, project, stream, ...changes }).filter(([, value]) => value !== null))}`;
  const chip = (label, to, on, n) => `<a href="${esc(to)}" class="${on ? 'on' : ''}"${on ? ' aria-current="true"' : ''}>${esc(label)}${n === undefined ? '' : `<span class="n">${n}</span>`}</a>`;
  const shows = (page) => show === 'all' || page.live === (show === 'live');
  const inScope = (page, key = project, sub = stream) => (key === null || fileKey(page.project) === key) && (sub === null || fileKey(page.workstream) === sub);
  const projects = [...new Set(model.pages.filter(shows).map((page) => fileKey(page.project)))].sort((a, b) => byName([a], [b]));
  const streams = project === null ? [] : [...new Set(model.pages.filter((page) => shows(page) && inScope(page, project, null)).map((page) => fileKey(page.workstream)))].sort((a, b) => byName([a], [b]));
  const count = (key, sub) => model.pages.filter((page) => shows(page) && inScope(page, key, sub)).length;
  const scopes = projects.length > 1 ? `<nav class="subtabs scopes" aria-label="Project">${[chip('All projects', href({ project: null, stream: null }), project === null, count(null, null)), ...projects.map((key) => chip(fileName(key), href({ project: key, stream: null }), key === project && stream === null, count(key, null)))].join('')}</nav>` : '';
  const subs = streams.length > 1 ? `<nav class="subtabs scopes" aria-label="Workstream">${streams.map((key) => chip(fileName(key), href({ stream: key }), key === stream, count(project, key))).join('')}</nav>` : '';
  const listed = model.pages.filter((page) => shows(page) && inScope(page));
  const rows = listed.map((page) => `<li><a class="page-row" href="#/pages/${encodeURIComponent(page.slug)}" data-tip="${esc([page.title, page.goal, `For ${page.order_title}`].join('\n'))}">
      <span class="page-title">${esc(page.title)}</span><span class="state-pill ${page.live ? 'live' : 'done'}">${page.live ? 'Live' : 'Done'}</span>
      <span class="page-goal">${esc(page.goal)}</span>
      <small class="meta">${esc([fileName(fileKey(page.project)), fileName(fileKey(page.workstream)), page.order_title].join(' · '))} · made ${esc(clock(page.created))}</small>
    </a></li>`);
  return `<div class="page-head">
      <div><div class="eyebrow">Pages</div><h1 class="h1">Live pages built for your orders</h1><p class="lead">Each page shows its tasks as they move, and anything you answer or approve on one is sent as the queue sends it.</p></div>
      <nav class="subtabs" aria-label="Show">${PAGE_FILTERS.map(([id, label]) => chip(label, href({ show: id }), id === show)).join('')}</nav>
    </div>
    ${scopes}${subs}
    ${rows.length ? `<ol class="page-list">${rows.join('')}</ol>` : `<p class="empty">${model.pages.length ? 'No page here.' : 'No page yet. A page job puts one here with <code>drive page add</code>.'}</p>`}`;
}

const DONE_STATES = new Set(['merged', 'live']);
const STAGE_ORDER = [['not started', 'Not started'], ['waiting', 'Waiting'], ['blocked', 'Blocked'], ['running', 'Being built'], ['built', 'Built, waits on you'], ['merged', 'Merged'], ['live', 'Live']];
const BLOCK_DEFAULTS = { title: '', text: '', tone: 'plain', fold: false, group: '', prs: [], questions: [], orders: [], beats: [], steps: [], options: [], before: '', after: '', diagram: '' };
const appletSteps = new Map();
const appletTabs = new Map();
const appletPicks = new Map();
const appletCards = new Map();
const TOLD_MOMENTS = new Set(['order', 'answer', 'answered', 'tap', 'merged', 'live', 'failed']);

function defaultLayout(page, shown) {
  const waiting = shown.prs.filter((entry) => entry.group === 'you').map((entry) => entry.key);
  const steps = STAGE_ORDER.map(([state, label]) => ({ label, orders: shown.features.filter((entry) => entry.state === state).map((entry) => entry.id) })).filter((step) => step.orders.length);
  return {
    headline: page.title,
    summary: page.goal,
    blocks: [
      ...(shown.questions.length || waiting.length ? [{ kind: 'waits', title: 'Waiting on you', questions: shown.questions.map((item) => item.answer), prs: waiting }] : []),
      { kind: 'metrics', title: 'Where it stands' },
      ...(steps.length ? [{ kind: 'progress', title: 'The path to done', steps }] : []),
      { kind: 'timeline', title: 'What moved lately', fold: true, text: 'The newest changes, merges and answers' },
    ],
  };
}

function appletScreen(page) {
  const shown = pageSlice(page);
  const layout = page.layout ?? {};
  const composed = layout.composed ?? defaultLayout(page, shown);
  const status = layout.writing ? '<span class="ap-status">Composing this page from the latest work…</span>' : layout.error ? `<span class="ap-status bad">Its last compose failed, so it shows the one before: ${esc(layout.error)}</span>` : '';
  const blocks = appletBlocks(page, shown, composed.blocks.map((block) => ({ ...BLOCK_DEFAULTS, ...block })));
  return `<div class="page-head applet-head">
      <div><div class="eyebrow">Page · ${esc([fileName(fileKey(page.project)), fileName(fileKey(page.workstream))].join(' / '))}</div><h1 class="h1">${esc(composed.headline)}</h1><p class="lead">${esc(composed.summary)}</p>${status}</div>
      <a class="map-btn" href="#/feature/${esc(page.orders[0])}">Open the request ›</a>
    </div>
    ${composed.glance ? glanceHero(composed.glance) : ''}
    <div class="applet" data-applet="${esc(page.slug)}">${blocks}</div>`;
}

function glanceHero(glance) {
  return `<section class="ap-glance ${esc(glance.tone)}" aria-label="At a glance"><div class="ap-glance-status"><span class="dot-big" aria-hidden="true"></span><b>${esc(glance.status)}</b></div><dl><div><dt>What changed</dt><dd>${esc(glance.changed)}</dd></div><div class="next"><dt>What you do next</dt><dd><b>${esc(glance.next)}</b> ${esc(glance.why)}</dd></div></dl></section>`;
}

function appletBlocks(page, shown, blocks) {
  const drawn = [];
  const grouped = new Set();
  for (const block of blocks) {
    if (block.group) {
      if (grouped.has(block.group)) continue;
      grouped.add(block.group);
      const members = blocks.filter((each) => each.group === block.group).map((each) => [each, appletBlock(page, shown, each)]).filter(([, html]) => html);
      if (!members.length) continue;
      const key = `${page.slug}:${block.group}`;
      const at = Math.min(appletTabs.get(key) ?? 0, members.length - 1);
      drawn.push(`<section class="ap-block wide ap-tabs"><h2>${esc(block.group)}</h2><div class="ap-tablist" role="tablist">${members.map(([each], i) => `<button role="tab" aria-selected="${i === at}" class="${i === at ? 'on' : ''}" data-ap-tab="${esc(key)}" data-index="${i}">${esc(each.title || sentence(each.kind.replace('_', ' ')))}</button>`).join('')}</div><div class="ap-tabpanel" role="tabpanel">${members[at][1]}</div></section>`);
      continue;
    }
    const html = appletBlock(page, shown, block);
    if (!html) continue;
    drawn.push(block.fold ? `<details class="ap-block wide ap-fold"><summary><b>${esc(block.title || sentence(block.kind.replace('_', ' ')))}</b>${block.text ? `<span>${esc(block.text)}</span>` : ''}</summary>${html}</details>` : html);
  }
  return drawn.join('');
}

function blockShell(block, kind, body, wide = true) {
  return `<section class="ap-block kind-${kind}${wide ? ' wide' : ''}">${block.title ? `<h2>${esc(block.title)}</h2>` : ''}${block.text ? `<p class="ap-text">${esc(block.text)}</p>` : ''}${body}</section>`;
}

function sentence(text) {
  return String(text).replace(/^./, (c) => c.toUpperCase());
}

function blockPRs(shown, keys) {
  return keys.map((key) => shown.prs.find((entry) => entry.key === key)).filter(Boolean);
}

function blockCards(shown, block) {
  return new Set([...block.questions, ...blockPRs(shown, block.prs).filter((entry) => entry.group === 'you').map((entry) => entry.answer)]);
}

function embeddedCards(page, block, items) {
  const key = `${page.slug}:${block.title}`;
  const at = Math.max(0, items.findIndex((item) => noticeKey(item) === appletCards.get(key)));
  const item = items[at];
  const step = (delta, label, glyph) => `<button data-ap-card="${esc(key)}" data-item="${esc(noticeKey(items[at + delta] ?? item))}" aria-label="${label}"${items[at + delta] ? '' : ' disabled'}>${glyph}</button>`;
  const head = `<div class="ap-cards-head"><div class="counter"><span>${at + 1} of ${items.length}</span>${step(-1, 'Previous', '‹')}${step(1, 'Next', '›')}</div><a class="linkish" href="${esc(queueHref('all', item, scopeQuery(null, null, null, page.slug)))}">Open ${items.length === 1 ? 'it' : `these ${items.length}`} in the queue ›</a></div>`;
  const list = items.length > 1 ? `<details class="ap-card-list"><summary>Every one, most urgent first</summary><ol class="queue-list">${items.map((each, i) => queueRow(each, i === at, false, `button type="button" class="qrow-main" data-ap-card="${esc(key)}" data-item="${esc(noticeKey(each))}"`, 'button')).join('')}</ol></details>` : '';
  return `<div class="ap-cards">${head}${list}${{ question: decisionCard, tap: prCard }[item.kind](opened(item), at, items.length, 'on this page')}</div>`;
}

function appletBlock(page, shown, block) {
  const draw = APPLET_BLOCKS[block.kind];
  return draw ? draw(page, shown, block) : '';
}

const APPLET_BLOCKS = {
  metrics(page, shown, block) {
    const ready = shown.prs.filter((entry) => prWord(entry).word === 'Ready to land').length;
    const yours = shown.prs.filter((entry) => entry.group === 'you').length;
    const done = shown.features.filter((entry) => DONE_STATES.has(entry.state)).length;
    const total = shown.features.length;
    const tile = (n, label, tone = '') => `<div class="ap-tile ${tone}"><b>${n}</b><span>${esc(label)}</span></div>`;
    const days = [...Array(14)].map((_, i) => dayOf(new Date(Date.now() - (13 - i) * 86400000).toISOString()));
    const landed = days.map((day) => shown.story.filter((entry) => (entry.kind === 'merged' || entry.kind === 'live') && dayOf(entry.at) === day).length);
    const top = Math.max(1, ...landed);
    const spark = `<div class="ap-tile ap-spark"><svg viewBox="0 0 140 36" preserveAspectRatio="none" aria-hidden="true"><polyline points="${landed.map((n, i) => `${i * (140 / 13)},${34 - (n / top) * 30}`).join(' ')}"/></svg><span>${landed.reduce((a, b) => a + b, 0)} landed in 14 days</span></div>`;
    return blockShell(block, 'metrics', `<div class="ap-tiles">${spark}${tile(shown.questions.length, 'decisions for you', shown.questions.length ? 'warm' : '')}${tile(yours, 'changes awaiting your approval', yours ? 'warm' : '')}${tile(ready, 'ready to land on Approve', ready ? 'good' : '')}<div class="ap-tile ap-done"><b>${done}<small>/${total}</small></b><span>orders finished</span><i style="--done:${total ? Math.round((done / total) * 100) : 0}%"></i></div></div>`);
  },
  story(page, shown, block) {
    if (!block.beats.length) return '';
    const key = `${page.slug}:${block.title}`;
    const at = Math.min(appletSteps.get(key) ?? 0, block.beats.length - 1);
    const beat = block.beats[at];
    const dots = block.beats.map((each, i) => `<button class="${i === at ? 'on' : i < at ? 'seen' : ''}" data-ap-step="${esc(key)}" data-index="${i}" aria-current="${i === at}"><span class="n">${i + 1}</span>${esc(each.label)}</button>`).join('');
    const foot = `<div class="ch-foot">${at ? `<button class="btn ghost" data-ap-step="${esc(key)}" data-index="${at - 1}">‹ Back</button>` : '<span></span>'}${at < block.beats.length - 1 ? `<button class="btn" data-ap-step="${esc(key)}" data-index="${at + 1}">Next: ${esc(block.beats[at + 1].label)} ›</button>` : ''}</div>`;
    return blockShell({ ...block, text: '' }, 'story', `<nav class="ch-nav ap-stepper">${dots}</nav><div class="ap-step-body"><p class="ch-lead">${inline(beat.text)}</p></div>${foot}`);
  },
  waits(page, shown, block) {
    const keys = blockCards(shown, block);
    const items = shownQueue('all', null, null, null, null).filter((item) => keys.has(answerKey(item)));
    return blockShell(block, 'waits', items.length ? embeddedCards(page, block, items) : '<p class="ap-empty">✓ Nothing here waits on you now.</p>');
  },
  risk(page, shown, block) {
    return block.text ? `<section class="ap-block wide ap-risk ${esc(block.tone === 'bad' ? 'bad' : 'warn')}"><span class="ap-risk-mark" aria-hidden="true">!</span><div>${block.title ? `<b>${esc(block.title)}</b>` : '<b>The risk</b>'}<p>${esc(block.text)}</p></div></section>` : '';
  },
  progress(page, shown, block) {
    const steps = block.steps.map((step) => ({ ...step, entries: step.orders.map((id) => shown.features.find((entry) => entry.id === id)).filter(Boolean) })).filter((step) => step.entries.length);
    if (!steps.length) return '';
    const on = steps.findIndex((step) => step.entries.some((entry) => !DONE_STATES.has(entry.state)));
    return blockShell(block, 'progress', `<ol class="ap-progress">${steps.map((step, i) => {
      const done = step.entries.filter((entry) => DONE_STATES.has(entry.state)).length;
      const state = done === step.entries.length ? 'done' : i === on ? 'now' : step.entries.some((entry) => entry.state === 'blocked') ? 'bad' : '';
      return `<li class="${state}"><i style="--done:${Math.round((done / step.entries.length) * 100)}%"></i><b>${esc(step.label)}</b><small>${done} of ${step.entries.length}</small></li>`;
    }).join('')}</ol>`);
  },
  approve(page, shown, block) {
    const entries = blockPRs(shown, block.prs).filter((entry) => !answers.get(entry.answer)?.label || answers.get(entry.answer)?.error);
    if (!entries.length) return blockShell(block, 'approve', '<p class="ap-empty">✓ Nothing here waits on your Approve now.</p>');
    const yours = (entry) => entry.group === 'you';
    const picked = appletPicks.get(`${page.slug}:${block.title}`) ?? new Set(entries.filter((entry) => yours(entry) && prWord(entry).tone !== 'bad').map((entry) => entry.answer));
    appletPicks.set(`${page.slug}:${block.title}`, picked);
    const rows = entries.map((entry) => {
      const status = prWord(entry);
      return `<li${yours(entry) ? '' : ' class="moving"'}><label>${yours(entry) ? `<input type="checkbox" data-ap-pick="${esc(page.slug)}:${esc(block.title)}" value="${esc(entry.answer)}"${picked.has(entry.answer) ? ' checked' : ''}>` : '<span class="ap-moving" title="Needs nothing from you">↻</span>'}<span class="ap-pr"><b>${esc(sentence(entry.headline ?? bare(entry.title)))}</b><small>${dot(status)} ${esc(status.word)} · ${esc(yours(entry) ? entry.approve_plain : 'Needs nothing from you')}</small></span></label><button class="linkish" data-pr="${esc(entry.key)}">Review</button></li>`;
    }).join('');
    const count = entries.filter((entry) => yours(entry) && picked.has(entry.answer)).length;
    const mine = entries.filter(yours).length;
    return blockShell(block, 'approve', `<ul class="ap-approve">${rows}</ul><div class="ap-actions"><button class="btn" data-ap-approve="${esc(page.slug)}:${esc(block.title)}"${count ? '' : ' disabled'}>Approve ${count === mine && count > 1 ? `all ${count}` : count}</button><small>${entries.some((entry, i) => yours(entry) && entries.findIndex((other) => other.answer === entry.answer) !== i) ? 'Changes of one stack tick together, since Approve lands a stack whole. ' : ''}Each sends at once, with Undo, as the queue sends it.</small></div>`);
  },
  stack(page, shown, block) {
    const entries = blockPRs(shown, block.prs);
    return entries.length ? blockShell(block, 'stack', `<ol class="ap-stack">${entries.map((entry, i) => { const status = prWord(entry); return `<li class="${esc(status.tone)}"><span class="n">${i + 1}</span><span><b>${esc(entry.headline ?? bare(entry.title))}</b><small>${esc(status.word)}</small></span><button class="linkish" data-pr="${esc(entry.key)}">Review</button></li>`; }).join('')}</ol><p class="ap-note">Bottom first: each lands only after the one under it.</p>`) : '';
  },
  staging(page, shown, block) {
    const entries = blockPRs(shown, block.prs.length ? block.prs : shown.prs.map((entry) => entry.key));
    const tile = (entry) => { const proof = (entry.staging ?? '').match(/\[proof\]\((https?:[^)\s]+)\)/)?.[1]; const status = prWord(entry); return `<div class="ap-stage ${esc(status.tone)}"><b>${esc(status.word)}</b><span>${esc(sentence(entry.headline ?? bare(entry.title)))}</span>${status.word === entry.staging_plain.word ? '' : `<small>${esc(entry.staging_plain.word)}</small>`}${proof ? link('Its proof', proof) : ''}</div>`; };
    const [first, rest] = [entries.slice(0, 8), entries.slice(8)];
    return entries.length ? blockShell(block, 'staging', `<div class="ap-staging">${first.map(tile).join('')}</div>${rest.length ? `<details class="ap-more"><summary>${rest.length} more</summary><div class="ap-staging">${rest.map(tile).join('')}</div></details>` : ''}`) : '';
  },
  path(page, shown, block) {
    const steps = block.steps.map((step) => ({ ...step, entries: step.orders.map((id) => shown.features.find((entry) => entry.id === id)).filter(Boolean) })).filter((step) => step.entries.length);
    if (!steps.length) return '';
    return blockShell(block, 'path', `<ol class="ap-path">${steps.map((step) => {
      const done = step.entries.filter((entry) => DONE_STATES.has(entry.state)).length;
      const tone = done === step.entries.length ? 'good' : step.entries.some((entry) => entry.state === 'blocked') ? 'bad' : done ? 'warn' : '';
      return `<li class="${tone}"><div class="ap-step"><b>${esc(step.label)}</b><span>${done} of ${step.entries.length} done</span><i style="--done:${Math.round((done / step.entries.length) * 100)}%"></i></div><ul>${step.entries.map((entry) => `<li><a href="#/feature/${esc(entry.id)}">${esc(bare(entry.title))}</a>${pill(STOP_WORDS[entry.state], STOP_TONES[entry.state])}</li>`).join('')}</ul></li>`;
    }).join('')}</ol>`);
  },
  timeline(page, shown, block) {
    const moments = shown.story.filter((entry) => TOLD_MOMENTS.has(entry.kind));
    return moments.length ? blockShell(block, 'timeline', `<ol class="ap-timeline">${moments.slice(0, 12).map((entry) => `<li><time>${clock(entry.at)}</time><p>${momentText(entry)}</p></li>`).join('')}</ol>`, false) : '';
  },
  compare(page, shown, block) {
    return block.options.length ? blockShell(block, 'compare', `<div class="ap-compare">${block.options.map((option) => `<div class="ap-choice${option.recommended ? ' recommended' : ''}">${option.recommended ? '<em>Recommended</em>' : ''}<b>${esc(option.title)}</b><p>${inline(option.consequence)}</p></div>`).join('')}</div>`) : '';
  },
  diff(page, shown, block) {
    const entries = blockPRs(shown, block.prs).slice(0, 3);
    return entries.length ? blockShell(block, 'diff', entries.map((entry) => `${entries.length > 1 ? `<h3 class="df-title">${esc(sentence(entry.headline ?? bare(entry.title)))}</h3>` : ''}${diffBlock(entry.key, entry.head)}`).join('')) : '';
  },
  before_after(page, shown, block) {
    return block.before && block.after ? blockShell(block, 'before-after', `<div class="ap-ba"><div><span>Before</span><p>${esc(block.before)}</p></div><i aria-hidden="true">→</i><div class="after"><span>After</span><p>${esc(block.after)}</p></div></div>`) : '';
  },
  diagram(page, shown, block) {
    return block.diagram ? blockShell(block, 'diagram', visual('mermaid', block.diagram, block.title)) : '';
  },
  callout(page, shown, block) {
    return block.text ? `<section class="ap-block kind-callout wide ${esc(block.tone)}">${block.title && !block.text.toLowerCase().startsWith(block.title.toLowerCase()) ? `<b>${esc(block.title)}.</b> ` : ''}${esc(block.text)}</section>` : '';
  },
};

function pageScreen(page) {
  if (!page) return gone('This page', 'pages');
  if (page.kind === 'applet') return appletScreen(page);
  return `<div class="page-head">
      <div><div class="eyebrow">Page · ${esc([fileName(fileKey(page.project)), fileName(fileKey(page.workstream))].join(' / '))}</div><h1 class="h1">${esc(page.title)}</h1><p class="lead">${esc(page.goal)}</p></div>
      <a class="map-btn" href="#/feature/${esc(page.orders[0])}">Open its task ›</a>
    </div>
    <iframe class="page-frame" data-page="${esc(page.slug)}" sandbox="allow-scripts" src="/pages/${encodeURIComponent(page.slug)}/" title="${esc(page.title)}"></iframe>`;
}

function pageSlice(page) {
  const all = model.status.flatMap((group) => group.features);
  const scope = page.scope;
  const every = scope === 'drive';
  const matches = (entry) => every || (scope && ((scope.projects ?? []).includes(entry.project) || (scope.workstreams ?? []).includes(entry.workstream) || (scope.words ?? []).some((word) => entry.title.toLowerCase().includes(word.toLowerCase()))));
  const orders = new Set([...page.orders, ...all.filter(matches).map((entry) => entry.id)]);
  const prs = new Set(page.prs);
  const detailed = slice(`/api/page?${new URLSearchParams({ slug: page.slug })}`)?.slice;
  const details = new Map([...(detailed?.prs ?? []), ...(detailed?.questions ?? [])].map((entry) => [entry.answer, entry]));
  const actable = (item, card, options) => ({ ...item, card: details.get(card)?.card ?? item.card, answer: card, options, answered: answers.get(card) ?? null });
  return {
    page,
    at: model.at,
    features: all.filter((entry) => orders.has(entry.id)),
    edges: detailed?.edges ?? [],
    prs: model.review.filter((entry) => every || orders.has(entry.feature) || prs.has(entry.key)).map((entry) => actable(entry, entry.answer ?? `tap:${entry.key}`, entry.card?.options ?? ['Approve', 'Hold'])),
    questions: model.queue.filter((item) => item.kind === 'question' && (every || item.tasks.some((id) => orders.has(id)))).map((item) => ({ ...actable(item, item.answer, item.card.options), href: queueHref('all', item, scopeQuery(null, null, null, page.slug)) })),
    story: slice(every ? '/api/story' : `/api/story?${new URLSearchParams({ ids: [...orders].join(',') })}`)?.moments ?? [],
    asked: sent.get(`page:${page.slug}`) ?? null,
  };
}

const pageCardsSeen = new Map();

function pageCards(page) {
  const seen = pageCardsSeen.get(page.slug);
  if (seen?.model === model) return seen.keys;
  const shown = pageSlice(page);
  const composed = page.layout?.composed ?? defaultLayout(page, shown);
  const keys = new Set(composed.blocks.filter((block) => ['waits', 'approve'].includes(block.kind)).flatMap((block) => [...blockCards(shown, { ...BLOCK_DEFAULTS, ...block })]));
  pageCardsSeen.set(page.slug, { model, keys });
  return keys;
}

function feedPages() {
  for (const frame of document.querySelectorAll('#main iframe.page-frame')) {
    const page = model.pages.find((each) => each.slug === frame.dataset.page);
    if (page) frame.contentWindow?.postMessage({ type: 'drive:model', slice: pageSlice(page) }, '*');
  }
}

function heardPage(event) {
  const frame = [...document.querySelectorAll('iframe.page-frame')].find((each) => each.contentWindow === event.source);
  const page = frame && model.pages.find((each) => each.slug === frame.dataset.page);
  if (!page) return;
  const { type, answer: card, label, text } = event.data;
  if (type === 'drive:complete') return completePage(frame, page, event.data);
  if (!navigator.userActivation.isActive) return;
  if (type === 'drive:open' && /^https:\/\//.test(event.data.route)) return void window.open(event.data.route, '_blank', 'noopener');
  if (type === 'drive:open' && shownPageRoute(event.data.route, pageSlice(page))) return go(event.data.route);
  const shown = pageSlice(page);
  if (type === 'drive:answers' && Array.isArray(event.data.picks) && event.data.picks.length && event.data.picks.every((pick) => [...shown.prs, ...shown.questions].some((item) => (!item.answered || item.answered.error) && item.answer === pick.card && item.options.includes(pick.label)))) return answerBatch(event.data.picks);
  if (type === 'drive:answer'  && [...shown.prs, ...shown.questions].some((item) => item.answer === card && item.options.includes(label))) return answer(card, label);
  if (type === 'drive:ask' && typeof text === 'string') say(`page:${page.slug}`, null, text, page.title);
}

function shownPageRoute(route, shown) {
  return shown.features.some((entry) => route === `#/feature/${entry.id}`) || shown.questions.some((entry) => route === entry.href);
}

async function completePage(frame, page, request) {
  const replyTo = (result) => frame.contentWindow?.postMessage({ type: 'drive:completion', id: request.id, ...result }, '*');
  try {
    const reply = await fetch('/api/complete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ page: page.slug, messages: request.messages }) });
    if (!reply.ok) throw new Error((await reply.json()).error);
    const reader = reply.body.getReader();
    const decoder = new TextDecoder();
    let pending = '';
    while (true) {
      const { value, done } = await reader.read();
      pending += decoder.decode(value, { stream: !done });
      const lines = pending.split('\n');
      pending = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ') || line.slice(6).trim() === '[DONE]') continue;
        const data = JSON.parse(line.slice(6));
        if (data.error) throw new Error(data.error.message);
        const delta = data.choices?.[0]?.delta?.content;
        if (delta) replyTo({ delta });
      }
      if (done) break;
    }
    replyTo({ done: true });
  } catch (error) { replyTo({ error: error.message }); }
}

function waiting() {
  const kinds = Map.groupBy(model.queue, (item) => item.kind);
  const since = model.queue.map((item) => item.since).filter(Boolean).sort((a, b) => new Date(a) - new Date(b));
  return { picks: kinds.get('question')?.length ?? 0, taps: kinds.get('tap')?.length ?? 0, oldest: since[0] };
}

function gone(what, screen = 'now') {
  return `<p class="empty">${what} has no open work on the dashboard now. <a href="#/${screen}">All projects</a></p>`;
}

function trail() {
  const { screen, path } = route();
  const at = { feature: 'now', file: 'work', priority: 'now' }[screen] ?? (SCREENS.some(([name]) => name === screen) ? screen : 'queue');
  const [, href, label] = SCREENS.find(([name]) => name === at);
  const zooms = (where, keys) => keys.map((key, i) => [fileName(key), zoomHref(where, ...keys.slice(0, i + 1))]);
  const entry = screen === 'feature' ? featureOf(path[0]) : screen === 'now' && path[2] ? featureOf(path[2]) : null;
  const steps = [[model.drive, '#/'], [label, href]];
  if (screen === 'now' || screen === 'work') steps.push(...zooms(screen, path.slice(0, 2)));
  if (screen === 'feature' && entry) steps.push(...zooms('now', [fileKey(entry.project), fileKey(entry.workstream)]));
  if (entry) steps.push([entry.title, location.hash]);
  if (screen === 'work' && path[2]) steps.push([path[2], location.hash]);
  if (screen === 'history' && path[0]) steps.push([dayName(path[0]), historyHref(path[0])]);
  if (screen === 'priority' && lensOf(path[0])) steps.push([`★ ${lensOf(path[0]).name}`, location.hash]);
  if (screen === 'pages' && path[0]) steps.push([model.pages.find((page) => page.slug === path[0])?.title ?? path[0], location.hash]);
  if (screen === 'file') steps.push([`${path[0]} ${(FILE_WORDS[path[1]] ?? 'file').toLowerCase()}`, location.hash]);
  return steps.map(([name, to], i) => (i === steps.length - 1 ? `<strong aria-current="page">${esc(name)}</strong>` : `<a href="${esc(to)}">${esc(name)}</a>`)).join('<span aria-hidden="true">/</span>');
}

function nowCanvas(tree, project, stream, task, side) {
  const level = stream !== undefined ? 'stream' : project !== undefined ? 'project' : 'all';
  const ranks = new Map();
  for (const item of model.queue) {
    const key = fileKey(item.project);
    if (!ranks.has(key)) ranks.set(key, item.priority.rank);
  }
  const regions = [...tree].toSorted(([a], [b]) => (ranks.get(a) ?? Infinity) - (ranks.get(b) ?? Infinity) || a.localeCompare(b)).map(([key, streams]) => region(key, streams, project, stream)).join('');
  const live = model.counts.live;
  return `<section class="canvas now" data-level="${level}" style="--cols:${tree.size}" aria-label="Projects, workstreams and tasks">
    <div class="canvas-bar"><span class="meta">${level === 'all' ? 'Projects · priority first' : level === 'project' ? 'Workstreams · click to focus' : 'Tasks · Esc to step back'}</span>
      <div class="canvas-tools"><button class="map-btn" data-cam="out" aria-label="Zoom out">−</button><button class="map-btn" data-cam="in" aria-label="Zoom in">+</button><button class="map-btn" data-cam="fit" aria-label="Fit to this level" title="Fit">⤢</button></div></div>
    <div class="viewport" data-at="${esc([project, stream].filter((key) => key !== undefined).join('/'))}" data-view="${esc(task?.id ?? '')}">
      <div class="world" style="${placed(nowCamera.at)}">${regions || `<p class="empty small">Nothing is open now${live ? `; ${counted(live, 'feature')} went live in the last day, in History` : ''}.</p>`}</div>
      ${task ? taskSheet(task, project, stream) : ''}
    </div>
    ${side}
  </section>`;
}

function region(key, streams, project, stream) {
  const list = [...streams.values()].flat();
  const here = key === project;
  const open = list.filter((entry) => entry.state !== 'live').length;
  return `<div class="region${key === NONE ? ' unfiled' : ''}${project !== undefined && !here ? ' aside' : ''}" data-cam-key="${esc(key)}" data-href="${zoomHref('now', key)}">
    <a class="region-head" href="${zoomHref('now', key)}" data-tip="${esc(groupTip(fileName(key), list))}"${project === undefined || !here ? '' : ' tabindex="-1"'}>
      <span class="zoom-mark" aria-hidden="true">›</span>
      <span class="region-name"><b>${esc(fileName(key))}</b><small>${esc(counted(streams.size, 'workstream'))}</small></span>
      ${needs(list)}
      <span class="region-open"><b>${open}</b><small>active</small>${stateBar(list)}</span>
    </a>
    <div class="streams" style="--n:${Math.min(streams.size, 2)}"${here ? '' : ' inert'}>${[...streams].map(([skey, slist]) => streamBox(key, skey, slist, here && skey === stream)).join('')}</div>
  </div>`;
}

function streamBox(project, key, list, here) {
  const asks = asksOf(list).sort((a, b) => new Date(a.asked) - new Date(b.asked));
  const tasks = list.toSorted((a, b) => RANK.indexOf(a.state) - RANK.indexOf(b.state) || new Date(b.updated) - new Date(a.updated));
  return `<div class="stream${key === NONE ? ' unfiled' : ''}${here ? ' here' : ''}" data-cam-key="${esc(project)}/${esc(key)}" data-href="${zoomHref('now', project, key)}">
    <a class="stream-head" href="${zoomHref('now', project, key)}" data-tip="${esc(groupTip(fileName(key), list))}"${here ? ' tabindex="-1"' : ''}><b>${esc(fileName(key))}</b><small class="reason-chip">${esc(counted(list.length, 'task'))}</small><small class="reason-chip">${list.filter((entry) => entry.state !== 'live').length} active</small>${needs(list)}${stateBar(list)}</a>
    <div class="stream-body"${here ? '' : ' inert'}>
      ${asks.length ? `<h3 class="sec">Your asks <span class="count">${asks.length}</span></h3><div class="task-list">${asks.map(askRow).join('')}</div>` : ''}
      <h3 class="sec">Tasks <span class="count">${tasks.length}</span></h3><div class="task-list">${tasks.map((entry) => taskRow(entry, zoomHref('now', project, key, entry.id))).join('')}</div>
    </div>
  </div>`;
}

function groupTip(name, list) {
  const states = RANK.map((state) => [state, list.filter((entry) => entry.state === state).length]).filter(([, n]) => n).map(([state, n]) => `${n} ${STOP_WORDS[state].toLowerCase()}`);
  const asks = asksOf(list).length;
  const newest = list.toSorted((a, b) => new Date(b.updated) - new Date(a.updated))[0];
  return [name, states.join(' · '), asks ? `${counted(asks, 'decision')} for you` : '', newest ? `Newest: ${newest.title}${newest.now ? `: ${newest.now}` : ''}, ${clock(newest.updated)}` : ''].filter(Boolean).join('\n');
}

function taskTip(entry) {
  const prs = entry.prs.filter((pr) => pr.state === 'open').map((pr) => `${prKey(pr.url)}: ${prSaid(pr)}, ${pr.review === 'none' ? 'no review' : pr.review}`);
  return [entry.title, `${STOP_WORDS[entry.state]}${entry.now ? `: ${entry.now}` : ''}, ${clock(entry.updated)}`, ...prs, entry.cards.length ? `Waits on you: ${counted(entry.cards.length, 'decision')}` : ''].filter(Boolean).join('\n');
}

function asksOf(list) {
  const { owner } = index();
  const ids = new Set(list.map((entry) => entry.id));
  return [...new Map(list.flatMap((entry) => entry.cards.filter((card) => ids.has(owner.get(card.url))).map((card) => [card.url, card]))).values()];
}

function needs(list) {
  const asks = asksOf(list).length;
  const blocked = list.filter((entry) => entry.state === 'blocked').length;
  const flags = `${asks ? `<span class="need">${counted(asks, 'question')} for you</span>` : ''}${blocked ? `<span class="need bad">${blocked} blocked</span>` : ''}`;
  return flags ? `<span class="flags">${flags}</span>` : '';
}

function stateBar(list) {
  const counts = RANK.map((state) => [state, list.filter((entry) => entry.state === state).length]).filter(([, n]) => n);
  const said = counts.map(([state, n]) => `${n} ${STOP_WORDS[state].toLowerCase()}`);
  return `<span class="statebar" role="img" aria-label="${esc(said.join(', '))}">${counts.map(([state, n], i) => `<i class="t-${STOP_TONES[state]}" style="flex:${n}" data-tip="${esc(said[i])}"></i>`).join('')}</span>`;
}

function taskSheet(entry, project, stream) {
  return `<aside class="task-sheet" aria-label="This task">
    <div class="sheet-bar"><span class="eyebrow">Task</span><a class="linkish" href="${zoomHref('now', project, stream)}">Close</a></div>
    <h2 class="sheet-title">${esc(bare(entry.title))}</h2>
    ${taskBody(entry)}
    ${story([entry], 'Latest')}
  </aside>`;
}

const nowCamera = { at: null, flying: 0, world: null };
const cams = new WeakMap();
let nowAt = null;
let dragged = false;
let fitter = null;
let framer = null;
let gesture = null;
let stepped = 0;
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

function placed(at) {
  return at ? `transform:scale(${at.k}) translate(${-at.x}px, ${-at.y}px);--k:${at.k}` : 'visibility:hidden';
}

function panNow(dx, dy) {
  cancelAnimationFrame(nowCamera.flying);
  nowCamera.at = { ...nowCamera.at, x: nowCamera.at.x + dx / nowCamera.at.k, y: nowCamera.at.y + dy / nowCamera.at.k };
  aim(nowCamera);
}

function aim(cam) {
  cam.world.setAttribute('style', placed(cam.at));
}

function camOf(view) {
  return view.matches('.viewport') ? nowCamera : cams.get(view);
}

function boxOf(el, world) {
  const at = el.getBoundingClientRect();
  const origin = world.getBoundingClientRect();
  const k = origin.width / world.offsetWidth;
  return { x: (at.left - origin.left) / k, y: (at.top - origin.top) / k, w: at.width / k, h: at.height / k };
}

function focused(viewport) {
  const world = viewport.querySelector('.world');
  const target = viewport.dataset.at && viewport.querySelector(`[data-cam-key="${CSS.escape(viewport.dataset.at)}"]`);
  if (!target) return { box: { x: 0, y: 0, w: world.scrollWidth, h: world.scrollHeight }, unit: 1 };
  const box = boxOf(target, world);
  return { box, unit: target.offsetWidth / box.w };
}

function zoomed(view, k) {
  const unit = view.matches('.viewport') ? focused(view).unit : 1;
  return Math.min(3 * unit, Math.max(0.03 * unit, k));
}

function fitTo(box, viewport, level) {
  const pad = level === 'all' ? 0 : 0.025;
  const w = viewport.clientWidth;
  const h = viewport.clientHeight;
  const room = Math.min(w / (box.w * (1 + 2 * pad)), h / (box.h * (1 + 2 * pad)), 1.15);
  const k = level === 'all' ? Math.min(w / box.w, 1.15) : level === 'readable' ? Math.max(room, READABLE) : Math.max(room, Math.min(w / (box.w * (1 + 2 * pad)), READABLE));
  const top = box.h * k > h ? box.y - box.h * pad : box.y + box.h / 2 - h / k / 2;
  const left = box.w * k > w ? box.x - box.w * pad : box.x + box.w / 2 - w / k / 2;
  return { k, x: left, y: top };
}

function fitOf(view) {
  if (view.matches('.frame')) {
    const { offsetWidth: w, offsetHeight: h } = view.firstElementChild;
    const k = Math.min(view.clientWidth / w, view.clientHeight / h, 1);
    return { k, x: w / 2 - view.clientWidth / k / 2, y: h / 2 - view.clientHeight / k / 2 };
  }
  if (view.closest('.canvas.now[data-level="all"]')) return { k: 1, x: 0, y: 0 };
  const { box, unit } = focused(view);
  const side = view.querySelector('.task-sheet');
  const room = { clientWidth: view.clientWidth - (side && view.clientWidth - side.offsetWidth >= 420 ? side.offsetWidth : 0), clientHeight: view.clientHeight };
  const level = $('#main .canvas').dataset.level;
  const fit = fitTo({ x: 0, y: 0, w: box.w * unit, h: box.h * unit }, room, view.closest('.canvas.now') && level === 'project' ? 'readable' : level);
  const chosen = view.querySelector('.topo-node.lane.on');
  const k = (chosen ? 1 : Math.min(1, room.clientWidth / (box.w * unit * 1.05))) * unit;
  if (fit.k * unit >= k || !(chosen || (narrow.matches && view.closest('.work')))) return { k: fit.k * unit, x: box.x + fit.x / unit, y: box.y + fit.y / unit };
  const seen = { w: room.clientWidth / k, h: room.clientHeight / k };
  const mid = chosen ? boxOf(chosen, view.querySelector('.world')) : { ...box, h: seen.h };
  const span = (center, shown, start, length) => (length <= shown ? start + length / 2 - shown / 2 : Math.min(Math.max(center - shown / 2, start), start + length - shown));
  return { k, x: span(mid.x + mid.w / 2, seen.w, box.x, box.w), y: span(mid.y + mid.h / 2, seen.h, box.y, box.h) };
}

function settleCamera(arrived, held) {
  const viewport = $('#main .viewport');
  if (!viewport) {
    fitter?.disconnect();
    nowCamera.at = null;
    nowAt = null;
    return;
  }
  nowCamera.world = viewport.querySelector('.world');
  if (arrived || !nowCamera.at) {
    nowCamera.at = fitOf(viewport);
    aim(nowCamera);
    fitter?.disconnect();
    fitter = new ResizeObserver(() => {
      nowCamera.at = fitOf(viewport);
      aim(nowCamera);
    });
    fitter.observe(viewport);
  } else {
    const target = held && focusOf(held.key);
    if (target) {
      const box = boxOf(target, nowCamera.world);
      const origin = viewport.getBoundingClientRect();
      nowCamera.at = { k: held.k, x: box.x - (held.rect.left - origin.left) / held.k, y: box.y - (held.rect.top - origin.top) / held.k };
      aim(nowCamera);
    }
    if (`${viewport.dataset.at}#${viewport.dataset.view}` !== nowAt) flyTo(nowCamera, fitOf(viewport));
  }
  nowAt = `${viewport.dataset.at}#${viewport.dataset.view}`;
}

function focusOf(key) {
  return key && $(`#main .canvas.now [data-cam-key="${CSS.escape(key)}"]`);
}

function heldFocus() {
  const key = route().path.slice(0, 2).join('/');
  const target = focusOf(key);
  return target && { key, rect: target.getBoundingClientRect(), k: nowCamera.at.k };
}

function frame(host) {
  const stage = Object.assign(document.createElement('div'), { className: 'stage' });
  stage.append(...host.childNodes);
  const view = Object.assign(document.createElement('div'), { className: 'frame' });
  view.append(stage);
  host.replaceChildren(view);
  host.insertAdjacentHTML('afterbegin', `<div class="frame-tools"><button type="button" class="map-btn" data-cam="fit" aria-label="Fit the whole diagram">Fit</button><button type="button" class="map-btn" data-cam="in" aria-label="Zoom in">+</button><button type="button" class="map-btn" data-cam="out" aria-label="Zoom out">−</button><button type="button" class="map-btn" data-enlarge aria-label="Full screen" title="Full screen">${icon('enlarge')}</button></div>`);
  cams.set(view, { at: null, flying: 0, world: stage });
  fitFrame(view);
  framer ??= new ResizeObserver((seen) => {
    for (const { target } of seen) {
      if (target.isConnected) fitFrame(target.querySelector('.frame'));
      else framer.unobserve(target);
    }
  });
  framer.observe(host.closest('figure'));
}

function fitFrame(view) {
  const { offsetWidth: w, offsetHeight: h } = view.firstElementChild;
  if (!w) return;
  if (!view.closest('.lb-stage')) {
    const room = view.closest('figure').clientWidth;
    view.style.width = `${Math.min(w, room)}px`;
    view.style.height = `${Math.ceil(h * Math.min(1, room / w))}px`;
  }
  const cam = cams.get(view);
  cam.at = fitOf(view);
  aim(cam);
}

function flyTo(cam, target, ms = 560) {
  cancelAnimationFrame(cam.flying);
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
    cam.at = target;
    return aim(cam);
  }
  const from = { ...cam.at, w: 1 / cam.at.k };
  const to = { ...target, w: 1 / target.k };
  const t0 = performance.now();
  const step = (time) => {
    const t = Math.min(1, (time - t0) / ms);
    const e = ease(t);
    const w = from.w + (to.w - from.w) * e;
    cam.at = t < 1 ? { k: 1 / w, x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e } : target;
    aim(cam);
    if (t < 1) cam.flying = requestAnimationFrame(step);
  };
  cam.flying = requestAnimationFrame(step);
}

function zoomAround(view, factor, px, py, ms = 0) {
  const cam = camOf(view);
  const k = zoomed(view, cam.at.k / factor);
  const target = { k, x: cam.at.x + px / cam.at.k - px / k, y: cam.at.y + py / cam.at.k - py / k };
  if (ms) return flyTo(cam, target, ms);
  cancelAnimationFrame(cam.flying);
  cam.at = target;
  aim(cam);
}

function stepLevel(view, k, px, py) {
  const ratio = k / fitOf(view).k;
  const to = ratio >= LEVEL_IN ? openedAt(view, px, py) : ratio <= 1 / LEVEL_OUT ? up() : null;
  if (!to) return false;
  stepped = performance.now() + 600;
  go(to);
  return true;
}

function openedAt(view, px, py) {
  const { world, at } = nowCamera;
  const x = at.x + px / at.k;
  const y = at.y + py / at.k;
  const level = view.closest('.canvas').dataset.level;
  const boxes = level === 'all' ? world.querySelectorAll(':scope > .region') : level === 'project' ? world.querySelectorAll('.region:not(.aside) .stream') : [];
  return [...boxes].find((el) => {
    const box = boxOf(el, world);
    return x >= box.x && x <= box.x + box.w && y >= box.y && y <= box.y + box.h;
  })?.dataset.href;
}

function grip(points, view) {
  const r = view.getBoundingClientRect();
  const all = [...points.values()];
  const x = all.reduce((sum, point) => sum + point.x, 0) / all.length - r.left;
  const y = all.reduce((sum, point) => sum + point.y, 0) / all.length - r.top;
  return { x, y, spread: all.length > 1 ? Math.hypot(all[0].x - all[1].x, all[0].y - all[1].y) : 1 };
}

function regrip() {
  gesture.from = grip(gesture.points, gesture.view);
  gesture.camera = { ...gesture.cam.at };
}

function askRow(card) {
  const item = model.queue.find((each) => each.answer === card.url);
  const body = `<span class="task-mark ask" aria-hidden="true"></span><span class="task-body"><b>${esc(card.title)}</b><small>Asked ${clock(card.asked)}, waiting ${ago(card.asked)}</small></span>`;
  return item ? `<button class="task-row" data-queue="${esc(noticeKey(item))}">${body}<span class="task-go">Answer it ›</span></button>` : `<div class="task-row">${body}</div>`;
}

function taskRow(entry, href = `#/feature/${entry.id}`) {
  const merged = entry.prs.filter((pr) => pr.state === 'merged').length;
  const facts = [entry.prs.length ? `${merged} of ${counted(entry.prs.length, 'PR')} merged` : '', entry.cards.length ? `${counted(entry.cards.length, 'decision')} for you` : ''].filter(Boolean).join(' · ');
  return `<a class="task-row" href="${esc(href)}" data-tip="${esc(taskTip(entry))}">
    <span class="task-mark t-${STOP_TONES[entry.state]}" aria-hidden="true"></span>
    <span class="task-body"><b>${esc(bare(entry.title))}</b>${entry.priorities?.length ? `<span class="prio-chips">${prioChips(entry.priorities)}</span>` : ''}${entry.now ? `<small>${esc(named(entry.now.replace(REPORT_WORD, '')))}</small>` : ''}</span>
    <span class="task-facts">${pill(STOP_WORDS[entry.state], STOP_TONES[entry.state])}${facts ? `<small>${esc(facts)}</small>` : ''}</span>
    <span class="arrow" aria-hidden="true">›</span>
  </a>`;
}

function momentText(entry) {
  return sharedRenderers.momentText(entry);
}

function story(scope, title = 'The live story') {
  const read = slice(`/api/story${scope ? `?${new URLSearchParams({ ids: scope.map((entry) => entry.id).join(',') })}` : ''}`);
  const told = read?.ok ? read.moments : [];
  const tip = (entry) => (featureOf(entry.feature) ? ` data-tip="${esc(taskTip(featureOf(entry.feature)))}"` : '');
  return `<aside class="story" aria-label="${title}">
    <div class="eyebrow">${title}</div>
    ${!read?.ok ? notRead(read) : told.length ? `<ol>${told.map((entry) => `<li${tip(entry)}><time>${clock(entry.at)}</time><p>${momentText(entry)}</p></li>`).join('')}</ol>` : '<p class="meta">No recent movement.</p>'}
    <a class="more" href="#/history">All of history ›</a>
  </aside>`;
}

function pill(word, tone) {
  return `<span class="pill t-${tone}">${esc(word)}</span>`;
}

function go(href) {
  keepPlace();
  if (location.hash === href) render();
  else location.hash = href;
}

function openPR(key) {
  const item = model.queue.find((each) => each.kind === 'tap' && (each.card?.prs ?? [{ url: each.url }]).some((row) => prKey(row.url) === key));
  if (item) {
    queueAt = noticeKey(item);
    prRows.set(queueAt, Math.max(0, (item.card?.prs ?? []).findIndex((row) => prKey(row.url) === key)));
    return go('#/queue/all');
  }
  reviewing = key;
  slotted.get(key)?.remove();
  slotted.delete(key);
  const review = reviews.get(key);
  if (review) Object.assign(review, { loaded: false, files: null });
  drawDrawer();
  $('#drawer').showModal();
  fill();
  $('#drawer [data-prtab][aria-selected="true"]').focus();
}

function drawDrawer() {
  const entry = model.review.find((each) => each.key === reviewing);
  const [repo, number] = reviewing.split('#');
  const siblings = entry ? model.review.filter((each) => each.feature === entry.feature) : [];
  const item = entry && {
    kind: 'tap', url: entry.url, title: entry.title, since: entry.since, answer: entry.answer, thread: entry.thread, card: null,
    untapped: entry.group === 'decided' ? `You tapped ${entry.tap}. ${entry.approve_plain}` : entry.group === 'you' ? null : `It does not wait on your tap yet: ${prWord(entry).word.toLowerCase()}.`,
    prs: [{ state: 'open', checks: entry.checks, review: entry.review }],
  };
  const html = `<div class="dr-bar"><b>${esc(reviewing)}</b><span class="acts"><a class="ext" href="https://github.com/${esc(repo)}/pull/${esc(number)}" target="_blank" rel="noopener">GitHub ↗</a><button class="linkish" data-close>Close</button></span></div>
    ${item ? prCard(item, siblings.indexOf(entry), siblings.length, 'for its request') : `<p class="meta dr-gone">${esc(reviewing)} is no longer an open PR of this drive.</p>`}`;
  morphChildren($('#drawer'), Object.assign(document.createElement('div'), { innerHTML: html }));
}

async function openReview(key, head) {
  if (reviews.get(key)?.head !== head) reviews.set(key, { head });
  const [repo, number] = key.split('#');
  const review = await ask(`/api/review/${repo}/${number}`, { method: 'POST' });
  const held = reviews.get(key);
  if (held?.head !== head) return;
  const url = review.ok ? new URL(review.url) : null;
  url?.searchParams.set('embed', '1');
  if (url ? held.url === url.href : held.error === review.error) return;
  reviews.set(key, url ? { head, url: url.href } : { head, error: review.error });
  render();
}

function place() {
  for (const slot of document.querySelectorAll('#main [data-slot]')) {
    const key = slot.dataset.slot;
    const review = reviews.get(key);
    if (!review?.url) continue;
    const frame = slotted.get(key) ?? Object.assign(document.createElement('iframe'), { className: 'review-frame', title: `Code and review of ${key}`, allow: 'clipboard-write' });
    frame.dataset.review = key;
    slotted.delete(key);
    slotted.set(key, frame);
    if (frame.getAttribute('src') !== review.url) {
      Object.assign(review, { loaded: false, files: null });
      frame.src = review.url;
    }
    if (!frame.isConnected) $('#frames').append(frame);
  }
  for (const [key, frame] of [...slotted].slice(0, -FRAMES_KEPT)) {
    frame.remove();
    slotted.delete(key);
  }
  for (const [key, frame] of slotted) {
    const slot = document.querySelector(`#main [data-slot="${CSS.escape(key)}"]`);
    const review = reviews.get(key);
    frame.hidden = !slot?.checkVisibility() || !review?.url;
    frame.classList.toggle('waiting', !review?.loaded);
    if (frame.hidden) continue;
    const box = slot.getBoundingClientRect();
    Object.assign(frame.style, { top: `${box.top + scrollY}px`, left: `${box.left + scrollX}px`, width: `${box.width}px`, height: `${box.height}px` });
  }
}

function tell(pr, message) {
  const review = reviews.get(pr);
  const frame = document.querySelector(`iframe.review-frame[data-review="${CSS.escape(pr)}"]`);
  if (review?.files && frame) return frame.contentWindow.postMessage(message, new URL(review.url).origin);
  told.set(pr, [...(told.get(pr) ?? []), message]);
}

function heardReview(event) {
  const frame = [...document.querySelectorAll('iframe.review-frame')].find((each) => each.contentWindow === event.source);
  const pr = frame?.dataset.review;
  const review = reviews.get(pr);
  if (!review?.url || event.origin !== new URL(review.url).origin) return;
  if (event.data.type === 'cc-review:ready') {
    review.files = event.data.files;
    for (const message of told.get(pr)?.splice(0) ?? []) event.source.postMessage(message, event.origin);
  }
  const note = notes.get(pr);
  if (event.data.type === 'cc-review:said' && note?.id === event.data.id) {
    notes.set(pr, event.data.error ? { error: event.data.error } : { at: new Date().toISOString(), where: note.where });
    if (!event.data.error) {
      drafts.delete(`n:${pr}`);
      pointed.delete(pr);
    }
  }
  render();
}

function addNote(pr, key) {
  const text = drafts.get(`n:${pr}`) ?? '';
  if (!text.trim() || notes.get(pr)?.busy) return;
  const point = pointed.get(pr);
  const id = String((noteCount += 1));
  notes.set(pr, { busy: true, id, where: point && (point.line ? `${point.path}:${point.line}` : point.path) });
  prTabs.set(key, 'change');
  tell(pr, { type: 'cc-review:say', id, text, ...point, pr: Number(pr.split('#')[1]) });
  render();
}

function reveal(control) {
  const tabs = control.closest('.pr-layout').querySelector('.pr-tabs');
  const top = tabs.getBoundingClientRect().top;
  if (top < 0 || top > innerHeight / 2) tabs.scrollIntoView({ block: 'start', behavior: 'smooth' });
}

async function readDiff(key, head) {
  diffs.set(key, { head });
  const [repo, number] = key.split('#');
  const { ok, error, ...read } = await ask(`/api/diff/${repo}/${number}`);
  diffs.set(key, ok ? { head, read } : { head, error });
  render();
}

const DIFF_WORDS = { added: 'New', removed: 'Deleted', renamed: 'Moved', modified: '', changed: '', copied: 'Copied' };
const HUNK_LINES = 80;

function diffAuthor(said) {
  return said.his ? 'You' : said.bot ? `${said.author.replace(/\[bot\]$/, '')} (bot)` : said.author;
}

function diffNotes(notes, replies) {
  return notes.map((said) => `<div class="df-note${said.his ? ' his' : said.bot ? ' bot' : ''}"><b>${esc(diffAuthor(said))}</b>${said.url ? ` <a class="meta" href="${esc(said.url)}" target="_blank" rel="noopener">on GitHub</a>` : ''}${prose(said.body)}${diffNotes(replies.get(said.id) ?? [], replies)}</div>`).join('');
}

function diffRows(lines, placed, replies) {
  return lines.map((line) => {
    const notes = [...(line.new === null ? [] : placed.get(`RIGHT:${line.new}`) ?? []), ...(line.old === null ? [] : placed.get(`LEFT:${line.old}`) ?? [])];
    return `<tr class="df-${line.kind}"><td class="df-n">${line.old ?? ''}</td><td class="df-n">${line.new ?? ''}</td><td class="df-t"><span aria-hidden="true">${{ add: '+', del: '−', ctx: ' ' }[line.kind]}</span>${esc(line.text)}</td></tr>${notes.length ? `<tr class="df-notes"><td colspan="3">${diffNotes(notes, replies)}</td></tr>` : ''}`;
  }).join('');
}

function diffFile(key, file, open, comments, replies, whys) {
  const mine = comments.filter((said) => said.path === file.path && !said.reply_to);
  const placed = new Map();
  for (const said of mine.filter((each) => each.line !== null)) placed.set(`${said.side}:${said.line}`, [...(placed.get(`${said.side}:${said.line}`) ?? []), said]);
  const stale = mine.filter((said) => said.line === null);
  const flagged = mine.filter((said) => said.bot || said.his).length;
  const hunks = file.hunks.map((hunk, h) => {
    const why = whys.find((each) => each.file === file.path && each.hunk === h)?.why;
    const [shown, rest] = [hunk.lines.slice(0, HUNK_LINES), hunk.lines.slice(HUNK_LINES)];
    return `<div class="df-hunk">${why ? `<p class="df-why"><b>Why:</b> ${esc(why)}</p>` : ''}<div class="df-sec">Line ${hunk.new}${hunk.section ? ` · ${esc(hunk.section)}` : ''}</div><table class="df-lines"><tbody>${diffRows(shown, placed, replies)}</tbody></table>${rest.length ? `<details class="df-rest"><summary>${rest.length} more lines</summary><table class="df-lines"><tbody>${diffRows(rest, placed, replies)}</tbody></table></details>` : ''}</div>`;
  }).join('');
  const word = DIFF_WORDS[file.status] ?? file.status;
  return `<details class="df-file" data-df-file="${esc(file.path)}"${diffOpen.get(`${key}:${file.path}`) ?? open ? ' open' : ''}><summary><span class="df-path">${word ? `<em>${esc(word)}</em> ` : ''}${esc(file.path)}${file.was ? ` <small>from ${esc(file.was)}</small>` : ''}</span><span class="df-count"><b class="add">+${file.added}</b> <b class="del">−${file.removed}</b>${mine.length ? ` <span class="df-badge${flagged ? ' flagged' : ''}">${counted(mine.length, 'comment')}</span>` : ''}</span></summary>${stale.length ? `<div class="df-stale"><p class="meta">On an earlier version of this file</p>${diffNotes(stale, replies)}</div>` : ''}${file.whole ? hunks : `<p class="meta df-none">GitHub shows no line changes for this file: it is binary or too large. ${file.url ? link('Open it on GitHub', file.url) : ''}</p>`}</details>`;
}

function diffBlock(key, head, whys = []) {
  const got = diffs.get(key);
  const shell = (body) => `<div class="df" data-diff="${esc(key)}" data-head="${esc(head)}">${body}</div>`;
  if (!got?.read && !got?.error) return shell('<p class="meta">Reading the change from GitHub…</p>');
  if (got.error) return shell(`<p class="refused">${esc(got.error)}</p>`);
  const { files, comments, more } = got.read;
  const replies = new Map();
  for (const said of comments.filter((each) => each.reply_to)) replies.set(said.reply_to, [...(replies.get(said.reply_to) ?? []), said]);
  const added = files.reduce((sum, file) => sum + file.added, 0);
  const removed = files.reduce((sum, file) => sum + file.removed, 0);
  const bots = comments.filter((said) => said.bot && !said.reply_to).length;
  const his = comments.filter((said) => said.his).length;
  const top = `<div class="df-head"><b>${counted(files.length + more, 'file')} changed</b><span class="df-count"><b class="add">+${added}</b> <b class="del">−${removed}</b></span>${bots ? `<span class="df-badge flagged">${counted(bots, 'bot comment')}</span>` : ''}${his ? `<span class="df-badge his">${counted(his, 'comment')} of yours</span>` : ''}<span class="df-tools"><button class="linkish" data-df-all="open">Open all</button><button class="linkish" data-df-all="close">Close all</button></span></div>`;
  const firstFlagged = files.findIndex((file) => comments.some((said) => said.path === file.path));
  return shell(`${top}${files.map((file, i) => diffFile(key, file, i === (firstFlagged === -1 ? 0 : firstFlagged) || whys.some((each) => each.file === file.path) && files.length <= 6, comments, replies, whys)).join('')}${more ? `<p class="meta">${counted(more, 'more file')} not shown here.</p>` : ''}`);
}

async function readPR(key, head) {
  prReads.set(key, { head });
  const [repo, number] = key.split('#');
  const { ok, error, ...pr } = await ask(`/api/pr/${repo}/${number}`);
  prReads.set(key, ok ? { head, pr } : { head, error });
  render();
}

function fill() {
  for (const pane of document.querySelectorAll('[data-talk]')) if (!talked.has(pane.dataset.talk)) readTalk(pane.dataset.talk);
  for (const card of document.querySelectorAll('[data-read]')) if (prReads.get(card.dataset.read)?.head !== card.dataset.head) readPR(card.dataset.read, card.dataset.head);
  for (const block of document.querySelectorAll('[data-diff]')) if (diffs.get(block.dataset.diff)?.head !== block.dataset.head) readDiff(block.dataset.diff, block.dataset.head);
  for (const slot of document.querySelectorAll('[data-slot]')) if (reviews.get(slot.dataset.slot)?.head !== slot.dataset.head) openReview(slot.dataset.slot, slot.dataset.head);
  place();
}

function talkPane(thread, after) {
  return `<div class="thread" data-talk="${esc(thread)}" data-after="${esc(after)}">${talkLines(thread, after)}</div>`;
}

function talkLines(thread, after) {
  const read = talked.get(thread);
  if (!read) return '<p class="meta">Reading what was said about it…</p>';
  if (read.error) return `<p class="refused">${esc(read.error)}</p>`;
  const waiting = waitingCards();
  return read.messages.filter((said) => said.ts > after).map((said) => message(said, waiting)).join('') || '<p class="meta">Nothing has been asked about it yet. What you ask, and the answer, show here.</p>';
}

async function readTalk(thread) {
  if (reading.has(thread)) return again.add(thread);
  reading.add(thread);
  const read = await ask(`/api/thread/${thread}`);
  talked.set(thread, read.ok ? { messages: read.messages } : { error: read.error });
  reading.delete(thread);
  for (const pane of document.querySelectorAll(`[data-talk="${CSS.escape(thread)}"]`)) pane.innerHTML = talkLines(thread, pane.dataset.after);
  place();
  if (again.delete(thread)) readTalk(thread);
}

function followTalk() {
  for (const pane of document.querySelectorAll('[data-talk]')) if (Date.now() - (followed.get(pane.dataset.talk) ?? 0) < TALK_FOLLOW_MS) readTalk(pane.dataset.talk);
}

function slackTime(ts) {
  return clock(new Date(parseFloat(ts) * 1000).toISOString());
}

function cardTs(url) {
  return url.match(/\/p(\d+)(\d{6})(?:\?|$)/).slice(1).join('.');
}

function waitingCards() {
  return new Map(model.queue.filter((item) => item.answer).map((item) => [cardTs(item.answer), item]));
}

function message(said, waiting) {
  const item = waiting.get(said.ts);
  const buttons = item ? choices(item) : said.buttons?.length ? `<div class="chips">${said.buttons.map((label) => `<span class="chip settled">${esc(label)}</span>`).join('')}</div>` : '';
  const who = said.dashboard ? 'You, on the dashboard' : said.from_claude ? esc(said.user_name) : 'You';
  return `<div class="msg${said.from_claude ? '' : ' you'}">
    <div class="who">${who} · ${slackTime(said.ts)}</div>
    <div class="said-text">${prose(said.text)}</div>
    ${buttons}
  </div>`;
}

const index = perModel(() => {
  const list = model.status.flatMap((group) => group.features);
  const projects = [...Map.groupBy(list, (entry) => fileKey(entry.project))].filter(([, entries]) => entries.some((entry) => entry.state !== 'live')).sort(byName);
  const tree = new Map(projects.map(([project, entries]) => [project, new Map([...Map.groupBy(entries, (entry) => fileKey(entry.workstream))].sort(byName))]));
  const owner = new Map();
  for (const entry of [...tree.values()].flatMap((streams) => [...streams.values()].flat())) for (const card of entry.cards) if (!owner.has(card.url)) owner.set(card.url, entry.id);
  return { list, byId: new Map(list.map((entry) => [entry.id, entry])), tree, owner };
});

function features() {
  return index().list;
}

function featureOf(id) {
  return index().byId.get(id);
}

function featureLink(id, text) {
  return featureOf(id) ? `<a href="#/feature/${esc(id)}">${esc(bare(text))}</a>` : esc(bare(text));
}

function prTone(pr) {
  return pr.state === 'open' ? pr.checks : pr.state;
}

function prSaid(pr) {
  const said = pr.state === 'open' ? CHECKS[pr.checks] ?? pr.checks : pr.state;
  const held = { Hold: ', held by you', labeled: ', held by its hold label', wanted: ', wants your tap', 'not ready': ", a reader's check found it not ready" }[pr.tap] ?? '';
  return said + held;
}

function builtLine(built) {
  return `<div class="now">Built on <code>${esc(built.branch)}</code>, ${built.waits.length ? 'no PR yet' : 'its PR opens next'}</div>`;
}

function fileButtons(job) {
  return job.files.length ? `<div class="files">${job.files.map((kind) => `<button class="linkish" data-file="${esc(job.id)}/${kind}" data-title="${esc(`${job.id} ${FILE_WORDS[kind].toLowerCase()}: ${job.goal}`)}">${FILE_WORDS[kind]}</button>`).join('')}</div>` : '';
}

function jobRow(job) {
  const quiet = minutesSince(job.updated);
  const stall = isStalled(job) ? `<span class="badge red">quiet ${ago(job.updated)}</span>` : '';
  const facts = [JOB_STATES[job.state] ?? job.state, job.kind, job.where, `last word ${quiet < 1 ? 'just now' : `${ago(job.updated)} ago`}`, job.state === 'built' ? `built on ${job.branch}` : '', job.waits ? `waits on ${counted(job.waits, 'card or job', 'cards or jobs')}` : '', job.prs ? counted(job.prs, 'PR') : ''];
  return `<li class="job ${esc(job.state)}" data-tip="${esc([`${job.kind} ${job.id}: ${JOB_STATES[job.state] ?? job.state}`, job.goal, job.word ? `${clock(job.word.at)}: ${job.word.text}` : ''].filter(Boolean).join('\n'))}">
    <span class="dot"></span>
    <div class="body">
      <div class="title">${esc(named(job.goal))} ${stall}</div>
      ${job.said ? `<div class="now">${reportLine(job.said, job.state)}</div>` : ''}
      <div class="meta">${facts.filter(Boolean).map(esc).join(' · ')}</div>
      ${fileButtons(job)}
    </div>
  </li>`;
}

async function openFile(file, title) {
  const reader = $('#reader');
  reader.dataset.file = file;
  reader.innerHTML = `<div class="dr-bar"><b>${esc(title)}</b><span class="acts"><a class="ext" href="/#/file/${esc(file)}" target="_blank" rel="noopener">New tab ↗</a><button class="linkish" data-close>Close</button></span></div><div class="reader-body">${notRead(null)}</div>`;
  if (!reader.open) reader.showModal();
  const read = await ask(`/api/file/${file}`);
  if (reader.open && reader.dataset.file === file) reader.querySelector('.reader-body').innerHTML = fileBody(read);
}

function fileScreen(path) {
  const read = slice(`/api/file/${path.map(encodeURIComponent).join('/')}`);
  return `<div class="eyebrow">${esc(FILE_WORDS[path[1]] ?? 'File')}</div><h1 class="h1">${esc(read?.title ?? path.join(' '))}</h1><div class="reader-body">${fileBody(read)}</div>`;
}

function fileBody(read) {
  if (!read?.ok) return notRead(read);
  const facts = read.facts ? `<p class="meta">${esc(read.facts)}</p>` : '';
  if (read.turns) return `${facts}${read.turns.map(turn).join('')}`;
  if (read.code !== undefined) return `${facts}${visual(`code:${read.language}`, read.code)}`;
  return `${facts}${read.docs.map((doc) => `<div class="doc">${prose(doc)}</div>`).join('')}`;
}

function turn({ who, tone, at, blocks }) {
  const said = blocks.map((block) => {
    if (!block.fold) return prose(block.text);
    return `<details><summary>${esc(block.fold)}</summary>${block.text === undefined ? `<pre><code>${esc(block.code)}</code></pre>` : prose(block.text)}</details>`;
  });
  return `<article class="turn ${esc(tone)}"><h3>${esc(who)}${at ? `<time>${esc(at)}</time>` : ''}</h3>${said.join('')}</article>`;
}

const sourceSeen = new Map();
function sourceRows(a, found) {
  const head = `<header>${esc(a.dataset.src.split('#')[0])} · ${esc(a.dataset.path)}</header>`;
  if (!found) return `${head}<p>Reading the lines from GitHub…</p>`;
  if (found.error) return `${head}<p>${esc(found.error)}</p>`;
  return `${head}<pre>${found.lines.map((line) => `<span class="${line.n >= Number(a.dataset.line) && line.n <= Number(a.dataset.to) ? 'hl' : ''}"><i>${line.n}</i><code>${line.lit ?? esc(line.text)}</code></span>`).join('')}</pre>`;
}

function hideSource() {
  const tip = $('#srctip');
  delete tip.dataset.shows;
  if (tip.matches(':popover-open')) tip.hidePopover();
}

function showTip(el) {
  const tip = $('#srctip');
  if (tip.dataset.shows === `tip:${el.dataset.tip}` && tip.matches(':popover-open')) return;
  tip.dataset.shows = `tip:${el.dataset.tip}`;
  const [head, ...lines] = el.dataset.tip.split('\n');
  tip.innerHTML = lines.length ? `<header>${esc(named(head))}</header>${lines.map((line) => `<p>${esc(named(line))}</p>`).join('')}` : `<p>${esc(named(head))}</p>`;
  if (!tip.matches(':popover-open')) tip.showPopover();
  placeTip(tip, el.getBoundingClientRect(), el.matches('.qrow-main'));
}

function placeTip(tip, box, beside) {
  const { offsetWidth: width, offsetHeight: height } = tip;
  const right = box.right + 10 + width <= innerWidth - 8;
  if (beside && (right || box.left - 10 - width >= 8)) {
    tip.style.left = `${right ? box.right + 10 : box.left - 10 - width}px`;
    tip.style.top = `${Math.max(8, Math.min(innerHeight - height - 8, box.top + box.height / 2 - height / 2))}px`;
    return;
  }
  tip.style.left = `${Math.max(8, Math.min(innerWidth - width - 8, box.left))}px`;
  tip.style.top = `${box.bottom + 6 + height > innerHeight ? Math.max(8, box.top - height - 6) : box.bottom + 6}px`;
}

function preview(choice) {
  const card = choice.dataset.card ?? choice.dataset.storyCard;
  const index = Number(choice.dataset.preview ?? choice.dataset.storyPick);
  if (picked?.card === card && picked.index === index) return;
  picked = { card, index };
  render();
}

function showSource(a) {
  const tip = $('#srctip');
  tip.dataset.shows = a.href;
  tip.innerHTML = sourceRows(a, sourceSeen.get(a.href));
  if (!tip.matches(':popover-open')) tip.showPopover();
  placeTip(tip, a.getBoundingClientRect(), true);
  if (sourceSeen.has(a.href)) return;
  const key = a.href;
  fetch(`/api/source?${new URLSearchParams({ cite: a.dataset.src, path: a.dataset.path, line: a.dataset.line, to: a.dataset.to })}`)
    .then((reply) => reply.json())
    .catch((failure) => ({ error: `Could not reach the dashboard server (${failure.message}).` }))
    .then(async (found) => {
      if (found.lines) for (const [at, lit] of (await (await import('/visuals.js')).lines(a.dataset.path, found.lines.map((line) => line.text))).entries()) found.lines[at].lit = lit;
      sourceSeen.set(key, found);
      if (tip.dataset.shows === key && a.isConnected && tip.matches(':popover-open')) showSource(a);
    });
}

function isStalled(job) {
  return job.state === 'running' && minutesSince(job.updated) >= STALL_MINUTES;
}

function laneState(job) {
  if (isStalled(job)) return 'quiet';
  return job.state === 'running' ? 'working' : job.state;
}

function laneTone(job) {
  return LANE_STATES[laneState(job)][1];
}

function workTree(lanes) {
  const held = Map.groupBy(lanes, (job) => job.feature);
  return new Map([...index().tree].map(([project, streams]) => [project, new Map([...streams].map(([stream, entries]) => [stream, entries.flatMap((entry) => held.get(entry.id) ?? [])]))]));
}

const LANE_TONES = { running: ['Working', 'doing'], waiting: ['Waiting', 'waiting'], parked: ['Parked', 'waiting'] };

function laneStanding(lane) {
  if (lane.state === 'running') return 'Working now.';
  if (lane.state === 'waiting') return `Waiting on ${lane.waits} until ${clock(lane.until)}.`;
  return `Parked: ${lane.parked}`;
}

function laneNowScreen() {
  const lanes = model.lanes.now;
  const working = lanes.filter((lane) => lane.state === 'running').length;
  const asked = lanes.reduce((sum, lane) => sum + lane.asks.length, 0);
  const ready = lanes.reduce((sum, lane) => sum + lane.stacks.length, 0);
  return `<div class="page-head"><div><div class="eyebrow">Now</div><h1 class="h1">Where each lane stands.</h1>
      <p class="lead">${counted(lanes.length, 'lane')} in queue order, ${working} working now. ${asked ? `${counted(asked, 'message')} of yours still unanswered. ` : ''}${ready ? `${counted(ready, 'stack')} ready for your tap.` : 'Nothing is ready for your tap.'}</p></div></div>
    <ol class="lane-list">${lanes.map(laneNowCard).join('')}</ol>`;
}

function laneCost(cost) {
  if (!cost) return '';
  if (cost.dollars_per_hour != null) return ` · $${cost.dollars_per_hour.toFixed(2)}/hr at list price`;
  const total = Object.values(cost.tokens ?? {}).reduce((sum, kinds) => sum + Object.values(kinds).reduce((a, b) => a + b, 0), 0);
  return total ? ` · ${(total / 1e6).toFixed(1)}M tokens/hr` : '';
}

function laneNowCard(lane) {
  const [word, tone] = lane.hurting ? ['Hurting users', 'bad'] : LANE_TONES[lane.state];
  const rest = Object.entries(lane.rest ?? {}).map(([why, count]) => `${count} ${why}`).join(', ');
  return `<li class="card lane-card"><div class="card-content">
      <div class="card-top"><span class="eyebrow">${lane.position} · ${esc(lane.id)}${laneCost(lane.cost)}</span><span class="pill t-${tone}">${word}</span></div>
      <h2 class="lane-title">${esc(lane.title)}</h2>
      ${lane.defect || lane.blocked?.length ? `<div class="lane-block"><div class="eyebrow">Stuck, and why</div><ul>${lane.defect ? `<li>${esc(lane.defect)}</li>` : ''}${(lane.blocked ?? []).map((line) => `<li>${esc(line)}</li>`).join('')}</ul></div>` : ''}
      ${lane.replies?.length ? `<div class="lane-block"><div class="eyebrow">How fast you heard back</div><ul>${lane.replies.map((reply) => `<li>${reply.minutes} min · ${reply.link ? `<a href="${esc(reply.link)}" target="_blank" rel="noopener">${esc(reply.words.slice(0, 80))}</a>` : esc(reply.words.slice(0, 80))}</li>`).join('')}</ul></div>` : ''}
      ${lane.done?.length ? `<div class="lane-block"><div class="eyebrow">Done when</div><ul class="lane-done">${lane.done.map((check) => `<li class="${check.met ? 'met' : 'unmet'}">${check.met ? '✓' : '○'} ${esc(check.words)}${check.how ? ` <span class="meta">${esc(check.how)}</span>` : ''}</li>`).join('')}</ul></div>` : ''}
      <p class="lane-standing">${esc(laneStanding(lane))}${lane.says ? ` <span class="lane-next">${inline(lane.says)}</span>` : ''}</p>
      ${lane.asks.length ? `<div class="lane-block"><div class="eyebrow">Your words, not yet answered</div><ul>${lane.asks.map((ask) => `<li>${ask.link ? `<a href="${esc(ask.link)}" target="_blank" rel="noopener">${esc(ask.words)}</a>` : esc(ask.words)} <span class="meta">${clock(ask.at)}</span></li>`).join('')}</ul></div>` : ''}
      ${lane.stacks.length ? `<div class="lane-block"><div class="eyebrow">Ready for your tap</div><ul>${lane.stacks.map((stack) => `<li><a href="${esc(stack.url)}" target="_blank" rel="noopener">${esc(sharedRenderers.prName(stack.key))}</a> ${esc(stack.title)}${stack.above ? ` <span class="meta">and ${stack.above} stacked above</span>` : ''}</li>`).join('')}</ul></div>` : ''}
      ${lane.next || rest || lane.why ? `<details class="lane-more"><summary>The whole plan</summary>${lane.next ? `<p class="lane-plan">${esc(lane.next)}</p>` : ''}${lane.why ? `<p>Why it waits: ${esc(lane.why)}</p>` : ''}${rest ? `<p class="meta">Not ready for your tap yet: ${esc(rest)}.</p>` : ''}</details>` : ''}
    </div></li>`;
}

function laneWorkScreen() {
  const owners = model.lanes.work;
  const builders = owners.reduce((sum, owner) => sum + owner.builders, 0);
  return `<div class="page-head"><div><div class="eyebrow">Work</div><h1 class="h1">Who is working.</h1>
      <p class="lead">${counted(owners.length, 'lane owner')} working now, running ${counted(builders, 'builder')} between them.</p></div></div>
    <ol class="lane-list">${owners.map((owner) => `<li class="card lane-card"><div class="card-content">
      <div class="card-top"><span class="eyebrow">${esc(owner.id)}</span><span class="pill t-doing">${owner.builders ? counted(owner.builders, 'builder') : 'On its own'}</span></div>
      <h2 class="lane-title">${esc(owner.title)}</h2>
      <p class="lane-standing">${owner.doing ? inline(owner.doing) : 'Working.'}</p>
      <p class="meta">${owner.since ? `Started ${clock(owner.since)}` : ''}${owner.updated ? ` · last active ${ago(owner.updated)} ago` : ''}</p>
    </div></li>`).join('') || '<li class="meta">No lane owner is working right now.</li>'}</ol>
    ${laneQueue(model.lanes.queue || [])}`;
}

function laneQueue(items) {
  const open = items.filter((item) => item.state === 'queued' || item.state === 'taken');
  const shown = { queued: 'Queued', taken: 'Being worked', done: 'Done', failed: 'Failed' };
  return `<section class="lane-day"><h2 class="lane-day-title">The work queue</h2>
    <p class="lead">${counted(open.length, 'item')} queued or being worked. Workers take the top item and what goes with it.</p>
    <ol class="lane-list">${items.map((item) => `<li class="card lane-card"><div class="card-content">
      <div class="card-top"><span class="eyebrow">${esc(item.lane)} · ${esc(item.kind)}</span><span class="pill t-doing">${esc(shown[item.state] || item.state)}</span></div>
      <h2 class="lane-title">${esc(item.subject)}</h2>
      ${item.result || item.why ? `<p class="lane-standing">${inline(item.result || item.why)}</p>` : ''}
      <p class="meta">${esc(item.id)} · queued ${clock(item.queued)}${item.worker ? ` · ${esc(item.worker)} took it ${clock(item.taken)}` : ''}${item.ended ? ` · ended ${clock(item.ended)}` : ''}</p>
    </div></li>`).join('') || '<li class="meta">Nothing in the queue.</li>'}</ol></section>`;
}

function laneHistoryScreen() {
  const days = new Map();
  for (const entry of model.lanes.history) {
    const day = new Date(entry.at).toLocaleDateString('en-US', { timeZone: ZONE, weekday: 'long', month: 'short', day: 'numeric' });
    if (!days.has(day)) days.set(day, new Map());
    const lanes = days.get(day);
    if (!lanes.has(entry.lane)) lanes.set(entry.lane, []);
    lanes.get(entry.lane).push(entry);
  }
  return `<div class="page-head"><div><div class="eyebrow">History</div><h1 class="h1">What the lanes did.</h1>
      <p class="lead">${counted(model.lanes.merged_today, 'PR')} merged today. Each lane's record, newest first.</p></div></div>
    ${[...days].map(([day, lanes]) => `<section class="lane-day"><h2 class="lane-day-title">${esc(day)}</h2><ol class="lane-list">${[...lanes].map(([id, entries]) => {
      const merged = entries.filter((entry) => entry.kind === 'merged');
      return `<li class="card lane-card"><div class="card-content">
        <div class="card-top"><span class="eyebrow">${esc(id)}</span>${merged.length ? `<span class="pill t-doing">${counted(merged.length, 'merge')}</span>` : ''}</div>
        <h2 class="lane-title">${esc(entries[0].title)}</h2>
        ${merged.length ? `<ul class="lane-merges">${merged.map((entry) => `<li><a href="${esc(entry.url)}" target="_blank" rel="noopener">${esc(entry.words)}</a> <span class="meta">queued to merge ${clock(entry.at)}</span></li>`).join('')}</ul>` : ''}
        <details class="lane-more"><summary>${counted(entries.length - merged.length, 'line')} of its record</summary><ol class="lane-log">${entries.filter((entry) => entry.kind !== 'merged').map((entry) => `<li><span class="meta">${clock(entry.at)}</span> ${inline(entry.words)}</li>`).join('')}</ol></details>
      </div></li>`;
    }).join('')}</ol></section>`).join('')}`;
}

function workScreen() {
  const read = slice('/api/lanes');
  if (!read?.ok) return notRead(read);
  const [project, stream, laneId] = route().path;
  const tree = workTree(read.lanes);
  const streams = project === undefined ? null : tree.get(project);
  if (project !== undefined && !streams) return gone('This project', 'work');
  const lanes = stream === undefined ? null : streams.get(stream);
  if (stream !== undefined && !lanes) return gone('This workstream', 'work');
  listen(read, tree);
  const shown = [...tree.values()].flatMap((each) => [...each.values()].flat());
  const working = shown.filter((job) => laneState(job) === 'working').length;
  return `<div class="page-head">
      <div><div class="eyebrow">Work</div><h1 class="h1">Inside the machine.</h1><p class="lead">${counted(shown.length, 'lane')}, ${working} working, across the ${counted(tree.size, 'project')} Now lists. Click a project to fly into its workstreams, then a workstream to see each lane.</p></div>
      <div class="page-tools"><button class="secondary" data-motion aria-pressed="${paused}">${paused ? '▷ Resume motion' : 'Ⅱ Pause motion'}</button><button class="linkish" data-map-context>ⓘ Map context</button></div>
    </div>
    ${workCanvas(tree, project, stream, lanes?.find((job) => job.id === laneId) ?? null, shown)}`;
}

function workCanvas(tree, project, stream, lane, shown) {
  const level = stream !== undefined ? 'stream' : project !== undefined ? 'project' : 'all';
  const keys = [project, stream].filter((key) => key !== undefined);
  const at = keys.join('/');
  const words = laneFilter.at === at ? laneFilter.words : '';
  const lanes = level === 'stream' ? tree.get(project).get(stream) : [];
  const agents = new Set(shown.map((job) => job.agent).filter(Boolean)).size;
  return `<section class="canvas work${paused ? ' paused' : ''}" data-level="${level}" aria-label="Agent topology">
    <div class="canvas-bar">${level === 'stream' ? `<input class="lane-filter" type="search" data-lane-filter="${esc(at)}" value="${esc(words)}" placeholder="Filter ${counted(lanes.length, 'lane')}" aria-label="Filter lanes" autocomplete="off">` : `<span class="meta canvas-hint">${level === 'all' ? 'Click a project to fly in. Drag to pan; ⌘ or Ctrl and scroll, or pinch, to zoom.' : 'Click a workstream to see its lanes. Esc zooms out.'}</span>`}
      <div class="canvas-tools"><button class="map-btn" data-cam="out" aria-label="Zoom out">−</button><button class="map-btn" data-cam="in" aria-label="Zoom in">+</button><button class="map-btn" data-cam="fit" aria-label="Fit to this level" title="Fit">⤢</button></div></div>
    <div class="viewport" data-at="${esc(at)}" data-view="${esc(lane?.id ?? '')}">
      <div class="world" style="${placed(nowCamera.at)}">${driveStage(tree, project, stream, lane, words)}</div>
      ${lane ? laneSheet(lane, zoomHref('work', project, stream)) : ''}
    </div>
    <div class="canvas-foot"><div class="map-legend">${Object.values(LANE_STATES).map(([word, tone]) => `<span class="t-${tone}">${word}</span>`).join('')}<span class="packet-key">A packet is a lane's record reaching the coordinator, or the coordinator starting a lane</span></div><span class="topo-label">${counted(shown.length, 'active job record')} · ${counted(agents, 'registered agent')}</span></div>
  </section>`;
}

function driveStage(tree, project, stream, lane, words) {
  const nodes = [...tree].map(([key, streams]) => {
    const lanes = [...streams.values()].flat();
    const inner = key === project ? (box) => projectStage(key, streams, stream, lane, words, box) : null;
    return { key, name: fileName(key), unfiled: key === NONE, href: zoomHref('work', key), lanes, inner };
  });
  const hub = `<b>${esc(model.drive)}</b><small>Coordinator</small>`;
  return topoStage('', '#/work', project === undefined, hub, nodes);
}

function projectStage(project, streams, stream, lane, words, box) {
  const nodes = [...streams].map(([key, lanes]) => ({
    key: `${project}/${key}`,
    name: fileName(key),
    unfiled: key === NONE,
    href: zoomHref('work', project, key),
    lanes,
    inner: key === stream ? (inside) => laneStage(project, key, lanes, lane, words, inside) : null,
  }));
  const hub = `<b>${esc(fileName(project))}</b>${doing(nodes.flatMap((node) => node.lanes))}`;
  return topoStage(project, zoomHref('work', project), stream === undefined, hub, nodes, box);
}

function laneStage(project, stream, lanes, lane, words, box) {
  const href = zoomHref('work', project, stream);
  const terms = words.toLowerCase().split(/\s+/).filter(Boolean);
  const kept = lanes.filter((job) => terms.every((term) => `${job.goal} ${job.id} ${job.kind} ${job.where} ${LANE_STATES[laneState(job)][0]} ${job.said}`.toLowerCase().includes(term)));
  const { size, hub, spots, wire } = laneLayout(kept.length);
  const wires = spots.map((spot, i) => `<path class="wire" data-wire="${esc(`${project}/${stream}/${kept[i].id}`)}" d="${wire(spot)}"/>`).join('');
  const empty = !lanes.length ? 'No lane is running or waiting in this workstream.' : !kept.length ? `No lane here matches “${words.trim()}”.` : '';
  return `<div class="stage lanes${lane ? '' : ' here'}" data-cam-key="${esc(`${project}/${stream}`)}" data-href="${href}" style="${stageStyle(size, box)}">
    <svg class="wires" viewBox="0 0 ${size.w} ${size.h}" aria-hidden="true">${wires}<g class="packets"></g></svg>
    <div class="hub" style="${boxStyle(hub)}"><b>${esc(fileName(stream))}</b>${doing(lanes)}${terms.length ? `<small>${kept.length} of ${counted(lanes.length, 'lane')} match</small>` : ''}</div>
    ${kept.map((job, i) => laneNode(job, spots[i], i, href, lane)).join('')}
    ${empty ? `<p class="stage-empty">${esc(empty)}</p>` : ''}
  </div>`;
}

function topoStage(key, href, here, hubHtml, nodes, box = null) {
  const { size, hub, spots, wire } = ringLayout(nodes.length);
  const center = [size.w / 2, size.h / 2];
  const orbits = narrow.matches ? '' : [[0.41, 0.42], [0.29, 0.3]].map(([rx, ry]) => `<ellipse class="orbital" cx="${center[0]}" cy="${center[1]}" rx="${size.w * rx}" ry="${size.h * ry}"/>`).join('');
  const wires = spots.map((spot, i) => `<path class="wire" data-wire="${esc(nodes[i].key)}" d="${wire(spot)}"/>`).join('');
  return `<div class="stage${here ? ' here' : ''}" data-cam-key="${esc(key)}" data-href="${href}" style="${stageStyle(size, box)}">
    <svg class="wires" viewBox="0 0 ${size.w} ${size.h}" aria-hidden="true">${orbits}${wires}<g class="packets"></g></svg>
    <div class="hub" style="${boxStyle(hub)}">${hubHtml}</div>
    ${nodes.map((node, i) => topoNode(node, spots[i], i)).join('')}
    ${nodes.length ? '' : '<p class="stage-empty">No workstream is filed here.</p>'}
  </div>`;
}

function topoNode(node, spot, i) {
  const open = Boolean(node.inner);
  const tip = `${node.name}: ${node.lanes.length ? `${counted(node.lanes.length, 'lane')}, ${doingParts(node.lanes).map(([word, n]) => `${n} ${word}`).join(', ')}` : 'no lane at work'}. Click to fly in.`;
  return `<div class="topo-node${node.unfiled ? ' unfiled' : ''}${open ? ' open' : ''}${node.lanes.length ? '' : ' idle'}"${open ? '' : ` data-href="${node.href}"`} style="${boxStyle(spot)};--i:${i}">
    <a class="face" href="${node.href}" data-tip="${esc(tip)}"${open ? ' tabindex="-1"' : ''}><b>${esc(node.name)} ›</b>${doing(node.lanes)}<span class="satellites">${node.lanes.slice(0, SATELLITES).map((job) => `<i class="t-${laneTone(job)}"></i>`).join('')}${node.lanes.length > SATELLITES ? `<em>+${node.lanes.length - SATELLITES}</em>` : ''}</span></a>
    ${open ? node.inner(spot) : ''}
  </div>`;
}

function laneNode(job, spot, i, href, chosen) {
  const [word, tone, short] = LANE_STATES[laneState(job)];
  const tip = `${job.goal} · ${job.id}, ${word.toLowerCase()}, last word ${ago(job.updated)} ago`;
  return `<a class="topo-node lane${job === chosen ? ' on' : ''}${spot.h < 50 ? ' tight' : ''}" href="${href}/${encodeURIComponent(job.id)}" data-lane="${esc(job.id)}" data-tip="${esc(tip)}" style="${boxStyle(spot)};--i:${i}"${job === chosen ? ' aria-current="true"' : ''}>
    <small class="t-${tone}"><i></i>${short} · ${esc(job.kind)}</small>
    <b class="t-${tone}">${esc(job.goal)}</b>
    <span class="lane-id">${esc(job.id)} · ${esc(job.where)} · ${ago(job.updated)} ago</span>
  </a>`;
}

function doingParts(lanes) {
  const by = (working) => Map.groupBy(lanes, (job) => (laneState(job) === 'working' ? working(job) : laneState(job)));
  const kinds = by((job) => KIND_DOING[job.kind]);
  const parts = kinds.size > 3 ? by(() => 'working') : kinds;
  return [...parts].map(([word, list]) => [word, list.length]).sort(([a], [b]) => LANE_ORDER.indexOf(a) - LANE_ORDER.indexOf(b));
}

function doing(lanes) {
  const parts = doingParts(lanes).map(([word, n]) => `<span class="t-${LANE_STATES[word]?.[1] ?? 'doing'}">${n} ${esc(word)}</span>`);
  return `<span class="doing">${parts.join('') || '<span>No lane at work</span>'}</span>`;
}

function stageStyle(size, box) {
  if (!box) return `width:${size.w}px;height:${size.h}px`;
  const k = Math.min(box.w / size.w, box.h / size.h);
  return `width:${size.w}px;height:${size.h}px;transform:translate(${((box.w - size.w * k) / 2).toFixed(2)}px, ${((box.h - size.h * k) / 2).toFixed(2)}px) scale(${k.toFixed(5)})`;
}

function boxStyle(box) {
  return `left:${box.x.toFixed(1)}px;top:${box.y.toFixed(1)}px;width:${box.w.toFixed(1)}px;height:${box.h.toFixed(1)}px`;
}

function ringLayout(n) {
  const at = (box) => [box.x + box.w / 2, box.y + box.h / 2];
  if (narrow.matches) {
    const w = NARROW_WIDTH;
    const hub = { x: 12, y: 12, w: w - 24, h: 64 };
    const spots = [...Array(n).keys()].map((i) => ({ x: i % 2 ? w / 2 + 6 : 12, y: 92 + Math.floor(i / 2) * NARROW_ROW, w: w / 2 - 18, h: NARROW_ROW - 8 }));
    return { size: { w, h: 100 + Math.max(1, Math.ceil(n / 2)) * NARROW_ROW }, hub, spots, wire: (spot) => `M ${w / 2} ${hub.y + hub.h} V ${at(spot)[1].toFixed(1)} H ${(spot.x < w / 2 ? spot.x + spot.w : spot.x).toFixed(1)}` };
  }
  const { w, h } = WIDE_STAGE;
  const hub = { x: w / 2 - 100, y: h / 2 - 48, w: 200, h: 96 };
  const wire = (spot) => {
    const [x, y] = at(spot).map((v) => v.toFixed(1));
    return `M ${w / 2} ${h / 2} C ${w / 2} ${y} ${x} ${h / 2} ${x} ${y}`;
  };
  if (n <= RING_LIMIT) {
    const spots = [...Array(n).keys()].map((i) => {
      const angle = -Math.PI / 2 + (2 * Math.PI * i) / n;
      return { x: w / 2 + 0.37 * w * Math.cos(angle) - NODE.w / 2, y: h / 2 + 0.36 * h * Math.sin(angle) - NODE.h / 2, ...NODE };
    });
    return { size: WIDE_STAGE, hub, spots, wire };
  }
  let [cols, rows] = [4, 3];
  while (2 * cols + 2 * rows - 4 < n) [cols, rows] = cols / rows < 1.9 ? [cols + 1, rows] : [cols, rows + 1];
  const ring = [
    ...[...Array(cols).keys()].map((i) => [i, 0]),
    ...[...Array(rows - 2).keys()].map((j) => [cols - 1, j + 1]),
    ...[...Array(cols).keys()].map((i) => [cols - 1 - i, rows - 1]),
    ...[...Array(rows - 2).keys()].map((j) => [0, rows - 2 - j]),
  ];
  const cell = { w: w / cols, h: h / rows };
  const node = { w: Math.min(NODE.w, cell.w - 18), h: Math.min(NODE.h, cell.h - 18) };
  return { size: WIDE_STAGE, hub, spots: ring.slice(0, n).map(([i, j]) => ({ x: i * cell.w + (cell.w - node.w) / 2, y: j * cell.h + (cell.h - node.h) / 2, ...node })), wire };
}

function laneLayout(n) {
  const w = narrow.matches ? NARROW_WIDTH : WIDE_STAGE.w;
  const hub = narrow.matches ? { x: 12, y: 12, w: w - 24, h: 64 } : { x: w / 2 - 160, y: 14, w: 320, h: 74 };
  const top = hub.y + hub.h + 22;
  const h = narrow.matches ? top + Math.max(1, Math.ceil(n / 2)) * ((w - 24) / 2 / LANE_RATIO) + 12 : WIDE_STAGE.h;
  const area = { x: 12, y: top, w: w - 24, h: h - top - 12 };
  const fits = (cols) => Math.min(area.w / cols / LANE_RATIO, area.h / Math.ceil(n / cols));
  const cols = [...Array(n).keys()].reduce((best, i) => (fits(i + 1) > fits(best) ? i + 1 : best), 1);
  const cell = { w: Math.min(area.w / cols, LANE_MAX.w), h: Math.min(fits(cols), LANE_MAX.h) };
  const left = area.x + (area.w - cols * cell.w) / 2;
  const spots = [...Array(n).keys()].map((i) => ({ x: left + (i % cols) * cell.w + 5, y: area.y + Math.floor(i / cols) * cell.h + 4, w: cell.w - 10, h: cell.h - 8 }));
  const [rootX, rootY] = [w / 2, hub.y + hub.h];
  return { size: { w, h }, hub, spots, wire: (spot) => `M ${rootX} ${rootY} C ${rootX} ${(rootY + spot.y) / 2} ${(spot.x + spot.w / 2).toFixed(1)} ${(rootY + spot.y) / 2} ${(spot.x + spot.w / 2).toFixed(1)} ${spot.y.toFixed(1)}` };
}

function laneSheet(job, href) {
  const read = slice(`/api/lane?${new URLSearchParams({ id: job.id })}`);
  const [word, tone] = LANE_STATES[laneState(job)];
  const facts = [job.branch ? `on ${job.branch}` : '', job.prs ? counted(job.prs, 'PR') : '', job.waits ? `waits on ${counted(job.waits, 'card or job', 'cards or jobs')}` : ''].filter(Boolean);
  const newest = read?.ok && read.newest;
  return `<aside class="task-sheet lane-sheet" aria-label="Inside this lane">
    <div class="sheet-bar"><span class="eyebrow">Lane ${esc(job.id)}</span><a class="linkish" href="${href}">Close</a></div>
    <h2 class="sheet-title">${esc(job.goal)}</h2>
    <p class="lane-state">${pill(word, tone)}<span class="meta">${esc(job.kind)} · ${esc(job.where)} · last word ${clock(job.updated)}, ${ago(job.updated)} ago</span></p>
    <h3 class="sec">Newest progress</h3>
    ${!read?.ok ? notRead(read) : newest ? `<p class="lane-said">${inline(newest.line)}</p><p class="meta">${LINE_FROM[newest.from]}${newest.at ? `, ${clock(newest.at)}` : ''}</p>` : `<p class="meta">${esc(job.id)} has said nothing yet: it sent no line, kept no report, and this Mac holds no transcript of it.</p>`}
    ${facts.length ? `<p class="meta">${esc(facts.join(' · '))}</p>` : ''}
    ${fileButtons(job)}
    <a class="btn ghost" href="#/feature/${esc(job.feature)}">See the task ›</a>
    <p class="rail-note">For: ${esc(featureOf(job.feature).title)}</p>
  </aside>`;
}

function listen(read, tree) {
  if (read === lanesRead) return;
  const routes = new Map([...tree].flatMap(([project, streams]) => [...streams].flatMap(([stream, lanes]) => lanes.map((job) => [job.id, [project, `${project}/${stream}`, `${project}/${stream}/${job.id}`]]))));
  if (lanesRead && shownHash === 'work') {
    for (const job of read.lanes) {
      const was = laneSeen.get(job.id);
      if (was !== job.updated && routes.has(job.id)) packets.push({ lane: job.id, route: routes.get(job.id), up: was !== undefined });
    }
  }
  lanesRead = read;
  laneSeen = new Map(read.lanes.map((job) => [job.id, job.updated]));
}

function sendPackets() {
  const sent = packets.splice(0);
  if (paused || route().screen !== 'work' || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  for (const { lane, route: wires, up } of sent) {
    for (const wire of document.querySelectorAll(wires.map((key) => `#main .wire[data-wire="${CSS.escape(key)}"]`).join(', '))) {
      const packet = wire.cloneNode();
      packet.setAttribute('class', 'packet');
      packet.setAttribute('pathLength', '100');
      wire.parentElement.querySelector('.packets').append(packet);
      packet.animate({ strokeDashoffset: up ? ['-100', '6'] : ['6', '-100'] }, { duration: 1500, easing: 'ease-in-out' }).finished.then(() => packet.remove());
    }
    document.querySelector(`#main .topo-node.lane[data-lane="${CSS.escape(lane)}"]`)?.animate({ boxShadow: ['0 0 0 0 #34685466', '0 0 0 14px #34685400'] }, { duration: 1100, easing: 'ease-out' });
  }
}

function mapContext() {
  const lanes = lanesRead?.lanes ?? [];
  const drawer = $('#drawer');
  drawer.classList.remove('wide');
  drawer.addEventListener('close', () => drawer.classList.add('wide'), { once: true });
  drawer.innerHTML = `<div class="dr-bar"><b>A map of the registered work</b><button class="linkish" data-close>Close</button></div>
    <div class="dr-pane body-pane map-context">
      <p>Every active job in drive.json: ${counted(lanes.length, 'lane')} running, waiting or built, under the projects and workstreams Now lists, so both screens name the same ${counted(index().tree.size, 'project')}.</p>
      <p>A node sits where its order is filed, project then workstream. Positions group the work; they do not show which agent started which.</p>
      <p>A packet moves only for a record between a lane and the coordinator: toward the coordinator when a lane's last word changes, toward the lane when the coordinator starts it. Nothing moves until the drive records something. Pause motion, or the reduced-motion setting, stops packets and orbits.</p>
      <p>${counted(new Set(lanes.map((job) => job.agent).filter(Boolean)).size, 'registered agent')}: the distinct local agents and Orca dispatches behind those lanes.</p>
    </div>`;
  if (!drawer.open) drawer.showModal();
}

function jumpItems() {
  const queued = model.queue.map((item) => ({
    kind: LABEL[item.kind],
    title: item.title,
    href: '#/queue/all',
    item: noticeKey(item),
    keys: [...(item.kind === 'tap' ? (item.card?.prs ?? [{ url: item.url }]).map((row) => prKey(row.url)) : []), ...item.tasks.filter(Boolean)],
  }));
  const onQueue = new Set(queued.flatMap((row) => row.keys));
  return [
    ...SCREENS.map(([, href, title]) => ({ kind: 'Screen', title, href, keys: [] })),
    ...queued,
    ...features().map((entry) => ({ kind: 'Task', title: entry.title, href: `#/feature/${entry.id}`, keys: [entry.id, ...entry.prs.map((pr) => prKey(pr.url))] })),
    ...model.review.filter((entry) => !onQueue.has(entry.key)).map((entry) => ({ kind: LABEL.tap, title: entry.title, pr: entry.key, keys: [entry.key] })),
    ...(slice('/api/lanes')?.lanes ?? []).map((job) => {
      const entry = featureOf(job.feature);
      return { kind: 'Job', title: job.goal, href: zoomHref('work', fileKey(entry.project), fileKey(entry.workstream), job.id), keys: [job.id, job.feature] };
    }),
  ];
}

let jumpAt = 0;

function highlighted(text, words) {
  if (!words.length) return esc(text);
  const found = new RegExp(`(${[...words].sort((a, b) => b.length - a.length).map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'gi');
  return String(text).split(found).map((part, i) => (i % 2 ? `<mark>${esc(part)}</mark>` : esc(part))).join('');
}

function drawJump() {
  const typed = $('#jumpfind').value.trim();
  const words = typed.toLowerCase().split(/\s+/).filter(Boolean);
  const found = jumpItems().filter((item) => words.every((word) => `${item.kind} ${item.title} ${item.keys.join(' ')}`.toLowerCase().includes(word)));
  const shown = found.slice(0, JUMP_ROWS);
  jumpAt = Math.min(jumpAt, Math.max(0, shown.length - 1));
  $('#jumplist').innerHTML = shown.map((item, i) => {
    const key = item.keys.find((each) => words.some((word) => each.toLowerCase().includes(word))) ?? item.keys[0];
    const more = item.keys.length > 1 ? ` +${item.keys.length - 1}` : '';
    return `<li id="jump-${i}" role="option" aria-selected="${i === jumpAt}" class="${i === jumpAt ? 'on' : ''}" data-jump="${i}" ${item.pr ? `data-pr-jump="${esc(item.pr)}"` : `data-href="${esc(item.href)}"`}${item.item ? ` data-item="${esc(item.item)}"` : ''}><em>${esc(item.kind)}</em><span class="jump-title">${highlighted(item.title, words)}</span>${key ? `<code${more ? ` title="${esc(item.keys.join(', '))}"` : ''}>${highlighted(key, words)}${more}</code>` : ''}</li>`;
  }).join('');
  if (shown.length) $('#jumpfind').setAttribute('aria-activedescendant', `jump-${jumpAt}`);
  else $('#jumpfind').removeAttribute('aria-activedescendant');
  $('#jumpnote').textContent = !found.length ? `Nothing matches “${typed}”. Jump finds a screen, a question, a pull request by its title, number or repo, a task by its title or order id, or a job by its id.`
    : found.length > shown.length ? `The first ${shown.length} of ${found.length} matches. Type more to narrow them.`
    : counted(found.length, 'match', 'matches');
}

function openJump() {
  if (!model) return;
  const jump = $('#jump');
  $('#jumpfind').value = '';
  jumpAt = 0;
  drawJump();
  if (!jump.open) jump.showModal();
  $('#jumpfind').focus();
}

function openKeys() {
  const sheet = $('#keys');
  sheet.innerHTML = `<div class="dr-bar"><h2 id="keys-title">Keyboard shortcuts</h2><button class="linkish" data-close autofocus>Close</button></div>
    <div class="key-groups">${KEY_SHEET.map(([group, rows]) => `<section><h3>${esc(group)}</h3><dl>${rows.map(([keys, what]) => `<dt>${keys.map((key) => `<kbd>${esc(key)}</kbd>`).join('<span>or</span>')}</dt><dd>${esc(what)}</dd>`).join('')}</dl></section>`).join('')}</div>`;
  if (!sheet.open) sheet.showModal();
}

function goJump(row) {
  $('#jump').close();
  if (row.dataset.prJump) return openPR(row.dataset.prJump);
  if (row.dataset.item) queueAt = row.dataset.item;
  go(row.dataset.href);
}

function strip() {
  const { picks, taps } = waiting();
  const open = model.incidents.length;
  const reports = model.bug_reports.filter((report) => report.stage !== 'told');
  const stale = (list) => list.filter((each) => isStale(featureOf(each.id))).length;
  const split = (list) => (stale(list) ? `, ${stale(list)} stale` : '');
  const tip = (list) => list.map((each) => `${featureOf(each.id).title}: ${isStale(featureOf(each.id)) ? 'stale' : 'moving'}`).join('\n');
  return [
    open ? `<button type="button" class="al bad" popovertarget="incidents" data-tip="${esc(`${counted(open, 'incident')} open\n${tip(model.incidents)}`)}">${open} ${open === 1 ? 'incident' : 'incidents'} open${split(model.incidents)}</button>` : '',
    reports.length ? `<button type="button" class="al warn" popovertarget="bug-reports" data-tip="${esc(`${counted(reports.length, 'bug report')} open\n${tip(reports)}`)}">${reports.length} ${reports.length === 1 ? 'bug report' : 'bug reports'} open${split(reports)}</button>` : '',
    picks ? `<a class="al" href="#/queue/question">${picks} ${picks === 1 ? 'decision' : 'decisions'} for you</a>` : '',
    taps ? `<a class="al" href="#/queue/tap">${taps} ${taps === 1 ? 'PR' : 'PRs'} to tap</a>` : '',
  ].filter(Boolean).join('');
}

function motion(entry) {
  const running = entry.running;
  const prs = entry.prs.filter((pr) => pr.state === 'open').length;
  const asks = entry.cards.length;
  const facts = [
    asks ? `<b>waits on your answer</b>` : '',
    running ? `${running} ${running === 1 ? 'job' : 'jobs'} working` : '',
    prs ? `${prs} open ${prs === 1 ? 'PR' : 'PRs'}` : '',
    asks || running || prs ? '' : `nothing moving since ${clock(entry.updated)}`,
  ];
  return { asks, moving: running || prs, facts };
}

function isStale(entry) {
  const { asks, moving } = motion(entry);
  return !asks && !moving;
}

function lastMove(entry, orphaned) {
  return [isStale(entry) ? '' : `last move ${clock(entry.updated)}`, orphaned ? `watch said ORPHAN ${clock(orphaned)}` : ''];
}

function popRow(id, title, facts) {
  return `<li><a href="#/feature/${esc(id)}"><span class="inc-title">${esc(title)}</span><span class="meta">${facts.filter(Boolean).join(' · ')}</span></a></li>`;
}

function incidentList() {
  const rows = model.incidents.map((incident) => {
    const entry = featureOf(incident.id);
    const { asks, moving, facts } = motion(entry);
    return { rank: [asks ? 0 : 1, moving ? 0 : 1, -Date.parse(entry.updated)], html: popRow(incident.id, entry.title, [`Since ${clock(incident.created)}`, ...facts, ...lastMove(entry, incident.orphaned)]) };
  });
  rows.sort((a, b) => a.rank.map((value, i) => value - b.rank[i]).find(Boolean) ?? 0);
  const stale = model.incidents.filter((incident) => isStale(featureOf(incident.id))).length;
  return `<div class="inc-head"><b>${rows.length} ${rows.length === 1 ? 'incident' : 'incidents'} open</b><span>${rows.length - stale} moving · ${stale} stale, with no job, card or PR on it. Waiting on you first, then the ones moving</span></div><ul>${rows.map((row) => row.html).join('')}</ul>`;
}

function bugReportList() {
  const reports = BUG_STAGES.flatMap(([stage]) => model.bug_reports.filter((report) => report.stage === stage));
  const open = reports.filter((report) => report.stage !== 'told').length;
  const stages = BUG_STAGES.map(([stage, , short]) => [short, reports.filter((report) => report.stage === stage).length]).filter(([, n]) => n).map(([short, n]) => `${n} ${short}`);
  const rows = reports.map((report) => {
    const said = BUG_STAGES.find(([stage]) => stage === report.stage)[1];
    const facts = report.stage === 'told' ? [] : [...motion(featureOf(report.id)).facts, ...lastMove(featureOf(report.id), report.orphaned)];
    return popRow(report.id, report.title, [said, `reported ${clock(report.created)}`, ...facts]);
  });
  return `<div class="inc-head"><b>${open} ${open === 1 ? 'bug report' : 'bug reports'} open</b><span>${stages.join(' · ')}, each open until its reporter is told the fix is live</span></div><ul>${rows.join('')}</ul>`;
}

function saidRow(entry) {
  return `<li class="happened" data-kind="said" data-moment="said:${esc(entry.at)}">
    <span class="time">${timeOf(entry.at)}</span>
    <span class="dot"></span>
    <div class="body">
      <div><b class="what">${sharedRenderers.HAPPENED.said}</b></div>
      <div class="said-text">${fold(`said:${entry.at}`, prose(entry.text), 4)}</div>
      ${entry.visuals.length ? `<div class="thumbs">${entry.visuals.map((each) => (each.kind === 'image' ? shot(each.picture, each.caption) : visual(each.kind, each.source, each.caption))).join('')}</div>` : ''}
      ${entry.prs.length ? `<ul class="prlist card">${entry.prs.map(prItem).join('')}</ul>` : ''}
      ${entry.prs.some((pr) => pr.tap === 'wanted') ? '<a class="more" href="#/queue/tap">Tap it in your queue ›</a>' : ''}
    </div>
  </li>`;
}

function historyRow(entry) {
  if (entry.kind === 'said') return saidRow(entry);
  return `<li class="happened" data-kind="${esc(entry.kind)}">
    <span class="time">${timeOf(entry.at)}</span>
    <span class="dot"></span>
    <div class="body">
      <div>${momentText(entry)}</div>
      ${entry.detail && !sharedRenderers.DECIDED[entry.kind] ? `<div class="meta">${esc(entry.detail)}</div>` : ''}
    </div>
    ${Date.parse(entry.undo_until) > Date.now() ? `<button class="btn ghost" data-undo="${esc(entry.url)}" data-label="${esc(entry.detail)}" data-title="${esc(entry.text)}">Undo</button>` : ''}
  </li>`;
}

function quietFor(minutes) {
  return minutes < 120 ? `${minutes} min` : `${Math.round(minutes / 60)} hours`;
}

function timeline(list, withDays) {
  return list.flatMap((entry, i) => {
    const before = list[i - 1];
    const day = dayOf(entry.at);
    const head = withDays && (!before || dayOf(before.at) !== day) ? `<li class="day-head"><a href="${historyHref(day)}">${dayName(day, { weekday: 'long', month: 'long', day: 'numeric' })} ›</a></li>` : '';
    const quiet = before && !head ? Math.round((new Date(before.at) - new Date(entry.at)) / 60000) : 0;
    return [head, quiet >= GAP_MINUTES ? `<li class="gap">${quietFor(quiet)} with nothing new</li>` : '', historyRow(entry)];
  }).join('');
}

function hourName(hour) {
  return `${hour % 12 || 12} ${hour < 12 ? 'AM' : 'PM'}`;
}

function dayChart(series, field, title, note, unit) {
  const top = Math.max(1, ...series.map((each) => each[field]));
  const bars = series.map((each) => {
    const label = each.day ? dayName(each.day) : hourName(each.hour);
    const said = `${label}: ${counted(each[field], unit)}`;
    const look = `class="bar" style="--h:${Math.max(2, (100 * each[field]) / top).toFixed(1)}%" aria-label="${esc(said)}"`;
    const drawn = `<span class="col"><i><b>${each[field]}</b></i></span><span class="day">${label}</span>`;
    return each.day ? `<a ${look} href="${historyHref(each.day)}" data-tip="${esc(`${said}. Click to zoom into the day.`)}">${drawn}</a>` : `<span ${look} tabindex="0" data-tip="${esc(said)}">${drawn}</span>`;
  }).join('');
  return `<div class="day-chart ${field}${series[0].day ? '' : ' hours'}"><div class="chart-head"><span>${title}</span><small>${note}</small></div><div class="bars">${bars}</div></div>`;
}

function historyScreen() {
  const filter = shownFilter();
  const read = slice(`/api/history?${new URLSearchParams({ day: route().id ?? '', show: filter, page: historyPage() })}`);
  if (!read?.ok) return notRead(read);
  const { days: series, hours, moments: listed, page, pages } = read;
  const zoomed = series.find((each) => each.day === read.day);
  const scope = zoomed ? [zoomed] : series;
  const total = (field) => scope.reduce((sum, each) => sum + each[field], 0);
  const stats = [[total('merged'), 'PRs merged'], [total('swears'), 'swear words you wrote'], [total('asked'), 'orders from you'], [total('moments'), 'moments']];
  const filters = HISTORY_FILTERS.map(([id, label]) => `<a href="${historyHref(zoomed?.day, id)}" class="${id === filter ? 'on' : ''}"${id === filter ? ' aria-current="true"' : ''}>${label}</a>`).join('');
  const latest = series.at(-1);
  const zoom = zoomed ? `<a class="map-btn" href="${historyHref()}">← All days</a>` : latest ? `<a class="map-btn" href="${historyHref(latest.day)}">Zoom to ${dayName(latest.day, { month: 'long', day: 'numeric' })} ›</a>` : '';
  const newer = page > 1 ? `<a class="map-btn" href="${historyHref(zoomed?.day, filter, page - 1)}">‹ Newer</a>` : '';
  const older = page < pages ? `<a class="map-btn" href="${historyHref(zoomed?.day, filter, page + 1)}">Older ›</a>` : '';
  const where = `${zoomed ? `Every moment of ${dayName(zoomed.day, { month: 'long', day: 'numeric' })}` : page > 1 ? 'Older moments' : `The newest ${counted(listed.length, 'moment')}`}${pages > 1 ? `, page ${page} of ${pages}` : ''}`;
  const [bars, by] = zoomed ? [hours, 'hour'] : [series, 'day'];
  return `<div class="page-head">
      <div><div class="eyebrow">History</div><h1 class="h1">${zoomed ? esc(dayName(zoomed.day, { weekday: 'long', month: 'long', day: 'numeric' })) : 'A little momentum. A little profanity.'}</h1><p class="lead">${zoomed ? 'Every moment of the day, newest first.' : 'Click a day to zoom in.'} Click a moment to follow it back to the work.</p></div>
      <nav class="subtabs" aria-label="Show">${filters}</nav>
    </div>
    <div class="history-layout">
      <section class="history-stage">
        <div class="history-stats">${stats.map(([n, label]) => `<div><b>${n}</b><span>${label}</span></div>`).join('')}</div>
        ${bars.length ? dayChart(bars, 'merged', 'PRs merged', `By the Pacific ${by} each merged`, 'PR') : ''}
        ${bars.length ? dayChart(bars, 'swears', 'A vocabulary of urgency', `Swear words in your orders and answers, by the ${by} you wrote each`, 'swear word') : ''}
        <div class="history-zoom"><span>${where}</span><span>${[newer, older, zoom].filter(Boolean).join(' ')}</span></div>
        <ul class="history">${timeline(listed, !zoomed) || '<li class="empty">Nothing yet.</li>'}</ul>
      </section>
      ${zoomed ? daySide(zoomed, hours) : series.length ? historySide(series) : ''}
    </div>`;
}

function most(series, field) {
  return series.reduce((best, each) => (each[field] > best[field] ? each : best));
}

function historySide(series) {
  const [busiest, saltiest] = [most(series, 'merged'), most(series, 'swears')];
  return `<aside class="history-side">
    <div class="eyebrow">The shape of the drive</div>
    <div class="big-date">${series.length}</div>
    <h3>${series.length === 1 ? 'day' : 'days'} of the drive</h3>
    <p>Busiest day: <a href="${historyHref(busiest.day)}">${dayName(busiest.day)}</a>, ${counted(busiest.merged, 'PR')} merged.</p>
    ${saltiest.swears ? `<p>Saltiest day: <a href="${historyHref(saltiest.day)}">${dayName(saltiest.day)}</a>, ${counted(saltiest.swears, 'swear word')} from you.</p>` : ''}
    <p class="rail-note">PRs count each change the drive merged, on the Pacific day it merged. Swears count words such as ${SWEAR_WORDS} in your own words, your Slack orders and the answers you gave, on the day you wrote each.</p>
  </aside>`;
}

function daySide(day, hours) {
  const [busiest, saltiest] = [most(hours, 'merged'), most(hours, 'swears')];
  return `<aside class="history-side">
    <div class="eyebrow">A day in the drive</div>
    <div class="big-date">${Number(day.day.slice(-2))}</div>
    <h3>${esc(dayName(day.day, { weekday: 'long' }))}</h3>
    <p>${counted(day.merged, 'PR')} merged, ${counted(day.asked, 'order')} from you and ${counted(day.moments, 'moment')} in all.</p>
    ${busiest.merged ? `<p>Busiest hour: ${hourName(busiest.hour)}, ${counted(busiest.merged, 'PR')} merged.</p>` : ''}
    ${saltiest.swears ? `<p>Saltiest hour: ${hourName(saltiest.hour)}, ${counted(saltiest.swears, 'swear word')} from you.</p>` : ''}
    <p class="rail-note">Each bar is one Pacific hour of the day. Swears count words such as ${SWEAR_WORDS} in your own words, your Slack orders and the answers you gave.</p>
  </aside>`;
}

function prItem(pr) {
  const said = pr.state === 'dropped' ? 'dropped from the drive' : `${prSaid(pr)}, ${pr.review === 'none' ? 'no review' : pr.review}`;
  return `<li><a href="${esc(pr.url)}" target="_blank" rel="noopener"><i class="sq ${esc(prTone(pr))}"></i>${esc(pr.title)}</a><em>${esc(said)}</em></li>`;
}

function decisionPiece(card) {
  const item = model.queue.find((each) => each.answer === card.url);
  return `<article class="piece you">
    <div class="eyebrow">Decision for you</div>
    <h3>${esc(card.title)}</h3>
    <p class="meta">Asked ${clock(card.asked)}, waiting ${ago(card.asked)}</p>
    ${item ? `<div class="piece-foot"><button class="linkish" data-queue="${esc(noticeKey(item))}">Answer it in your queue ›</button></div>` : ''}
  </article>`;
}

function prPiece(pr) {
  const key = prKey(pr.url);
  return `<article class="piece">
    <div class="eyebrow" data-tip="${esc(key)}">Pull request #${esc(key.split('#')[1])}</div>
    <h3>${esc(bare(pr.title))}</h3>
    <p class="meta"><i class="sq ${esc(prTone(pr))}"></i> ${esc(prSaid(pr))}, ${esc(pr.review === 'none' ? 'no review' : pr.review)}</p>
    <div class="piece-foot">${pr.state === 'open' ? `<button class="linkish" data-pr="${esc(key)}">Read the change</button>` : '<span></span>'}<a href="${esc(pr.url)}" target="_blank" rel="noopener">GitHub ↗</a></div>
  </article>`;
}

function featureScreen(id) {
  const entry = featureOf(id);
  if (!entry) return '<p class="empty">This feature is no longer on the dashboard. <a href="#/now">Back to Now</a></p>';
  return `<div class="eyebrow">Task</div>
    <h1 class="h1 zoom-title">${esc(bare(entry.title))}</h1>${entry.priorities?.length ? `<p class="prio-chips">${entry.priorities.map(lensOf).filter(Boolean).map((lens) => `<a class="prio-chip" href="#/priority/${esc(lens.id)}">★ ${esc(lens.name)}</a>`).join('')}</p>` : ''}
    <div class="now-grid"><div class="zoom-body">${taskBody(entry)}</div>${story([entry], 'Latest')}</div>`;
}

function taskBody({ id }) {
  const read = slice(`/api/task?${new URLSearchParams({ id })}`);
  if (!read?.ok) return notRead(read);
  const entry = read.task;
  const wrong = entry.jobs.filter((job) => job.state === 'failed' || job.state === 'stopped');
  const next = entry.jobs.filter((job) => job.state === 'running');
  const lined = new Set([...wrong, ...next].map((job) => job.id));
  const facts = [
    entry.where && !lined.has(entry.where.job) ? ['Where it is', `${reportLine(entry.where.text, entry.where.state)}${entry.where.at ? ` <small class="meta">${entry.where.job ? `${esc(entry.where.job)} · ` : ''}${clock(entry.where.at)}</small>` : ''}`] : null,
    ['Asked', clock(entry.created)],
    entry.proof ? ['Proof', inline(entry.proof)] : null,
    entry.note ? ['Note', esc(entry.note)] : null,
  ].filter(Boolean);
  const shipping = entry.parts.filter((part) => !part.standing);
  const shipped = shipping.filter((part) => part.delivered).length;
  const merged = entry.prs.filter((pr) => pr.state === 'merged').length;
  return `<div class="kick">${pill(HEADS[entry.state], STOP_TONES[entry.state])}${entry.holdup ? `<span class="holdup">${holdupLine(entry.holdup)}${entry.holdup.at ? ` <small class="meta">${clock(entry.holdup.at)}</small>` : ''}</span>` : ''}</div>
      <blockquote class="said">${esc(entry.text)}</blockquote>
      <dl class="factlist">${facts.map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`).join('')}</dl>
      <h2 class="sec">What it ships <span class="count">${shipped} of ${shipping.length} delivered${shipping.length < entry.parts.length ? `, ${entry.parts.length - shipping.length} standing` : ''}</span></h2>
      <ul class="lines card">${entry.parts.map(partRow).join('')}</ul>
      <h2 class="sec">What went wrong <span class="count">${wrong.length}</span></h2>
      ${wrong.length ? `<ul class="lines card">${wrong.map(jobLine).join('')}</ul>` : '<p class="empty small">Nothing has failed or stopped.</p>'}
      <h2 class="sec">Next</h2>
      ${next.length ? `<ul class="lines card">${next.map(jobLine).join('')}</ul>` : `<p class="empty small">${entry.state === 'live' ? 'Nothing: it is live.' : 'No job is running on it now.'}</p>`}
      ${entry.cards.length ? `<h2 class="sec">Decisions for you <span class="count">${entry.cards.length}</span></h2><div class="pieces">${entry.cards.map(decisionPiece).join('')}</div>` : ''}
      <h2 class="sec">Pull requests <span class="count">${entry.prs.length}</span>${entry.prs.length ? `<span class="meta">${merged} merged</span>` : ''}</h2>
      ${entry.prs.length ? `<div class="pieces">${entry.prs.map(prPiece).join('')}</div>` : '<p class="empty small">No PR yet.</p>'}
      ${entry.built.length ? `<h2 class="sec">Built, waiting on your design answers</h2><div class="card built">${entry.built.map(builtLine).join('')}</div>` : ''}
      ${entry.jobs.length ? `<h2 class="sec">Jobs <span class="count">${entry.jobs.length}</span></h2><ul class="jobs card">${entry.jobs.map(jobRow).join('')}</ul>` : ''}`;
}

function holdupLine(holdup) {
  const item = holdup.card && model.queue.find((each) => each.answer === holdup.card);
  if (item) return `<button class="linkish" data-queue="${esc(noticeKey(item))}">${esc(holdup.text)}</button>`;
  if (holdup.pr) return `<button class="linkish" data-pr="${esc(holdup.pr)}">${esc(holdup.text)}</button>`;
  return link(holdup.text, holdup.url);
}

function reportLine(text, state) {
  const found = text.match(REPORT_WORD);
  if (!found) return `<span class="report">${inline(text)}</span>`;
  const [word, tone, sets] = REPORTS[found[1]];
  const rest = inline(text.slice(found[0].length));
  if (!state || state === sets) return `<span class="report">${pill(word, tone)} ${rest}</span>`;
  return `<span class="report">${pill(JOB_STATES[state], JOB_TONES[state])} <span class="meta">since it last said ${esc(word)}:</span> ${rest}</span>`;
}

function partRow(part) {
  if (part.standing) return standingRow(part);
  const said = part.delivered ? (part.posted ? 'answered in Slack' : 'delivered') : part.job ? `${part.kind} ${part.job} ${JOB_STATES[part.state].toLowerCase()}` : 'no job carries it yet';
  return `<li class="line${part.delivered ? ' done' : ''}"><span class="tick" aria-hidden="true"></span><span class="line-text">${esc(part.whole ? 'All of it, in one piece' : part.name)}</span><small class="meta">${esc(said)}${part.at ? ` · ${clock(part.at)}` : ''}</small></li>`;
}

function standingRow(part) {
  const { goal, live, last } = part.standing;
  const pass = (held) => `${held.kind} ${held.job} ${JOB_STATES[held.state].toLowerCase()}`;
  const said = `standing: ${live ? `pass ${pass(live)}` : 'no pass at work'}; ${last ? `last pass ${pass(last)}` : 'no pass ended yet'}`;
  return `<li class="line" data-tip="${esc([goal, last?.said].filter(Boolean).join('\n'))}"><span class="tick" aria-hidden="true"></span><span class="line-text"><b>${esc(part.name)}</b> ${esc(said)}</span><small class="meta">${last ? clock(last.at) : ''}</small></li>`;
}

function jobLine(job) {
  return `<li class="line" data-tip="${esc(`${job.kind} ${job.id}\n${job.goal}`)}"><span class="line-text"><b>${esc(job.kind)} ${esc(job.id)}</b> ${job.word ? reportLine(job.word.text, job.state) : esc(job.goal)}</span><small class="meta">${clock(job.word?.at ?? job.updated)}</small></li>`;
}

function measure() {
  for (const el of document.querySelectorAll('.fold')) {
    el.style.removeProperty('--cut');
    el.classList.toggle('long', el.scrollHeight > el.clientHeight + 2 || el.classList.contains('open'));
    if (!el.matches('.long:not(.open)')) continue;
    const top = el.getBoundingClientRect().top;
    const lines = document.createRange();
    lines.selectNodeContents(el);
    const rows = [...el.querySelectorAll('tr')].map((row) => row.getBoundingClientRect()).map((box) => [box.top - top, box.bottom - top]);
    const ends = [...[...lines.getClientRects()].map((box) => box.bottom - top), ...rows.map(([, bottom]) => bottom)];
    const whole = ends.filter((end) => end <= el.clientHeight && !rows.some(([above, below]) => above < end && end < below));
    if (whole.length) el.style.setProperty('--cut', `${Math.ceil(Math.max(...whole))}px`);
  }
}

function render() {
  if (!model) return;
  const { screen, id, query } = route();
  const seated = screen === 'queue' && model.priorities.find((lens) => lens.seat === query.get('project'));
  if (seated) return location.replace(`#/priority/${seated.id}`);
  document.title = `${model.drive}: ${model.counts.queue} waiting on you`;
  const at = { feature: 'now', file: 'work', priority: 'now' }[screen] ?? (SCREENS.some(([name]) => name === screen) ? screen : 'queue');
  const counts = model.lanes
    ? { queue: model.counts.queue, now: model.lanes.now.length, work: model.lanes.work.length, history: model.lanes.merged_today, pages: model.pages.filter((page) => page.live).length }
    : { queue: model.counts.queue, now: model.counts.jobs, work: model.counts.lanes, history: model.counts.merged_today, pages: model.pages.filter((page) => page.live).length };
  const said = model.lanes
    ? { queue: 'waiting on you', now: 'lanes', work: 'working now', history: 'merged today', pages: 'live' }
    : { queue: 'waiting on you', now: 'jobs running', work: 'lanes at work', history: 'merged today', pages: 'live' };
  $('#tabs').innerHTML = SCREENS.map(([name, href, label]) => `<a class="tab${name === at ? ' on' : ''}" href="${href}"${name === at ? ' aria-current="page"' : ''} data-tip="${esc([label, counts[name] ? `${counts[name]} ${said[name]}` : '', name === 'now' && model.incidents.length ? `${counted(model.incidents.length, 'incident')} open` : ''].filter(Boolean).join('\n'))}">${icon(name)}<span>${label}</span>${counts[name] ? `<b class="count${name === 'queue' ? '' : ' soft'}" aria-label="${counts[name]} ${said[name]}">${counts[name]}</b>` : ''}${name === 'now' && model.incidents.length ? `<i class="alarm" aria-label="${counted(model.incidents.length, 'incident')} open"></i>` : ''}</a>`).join('');
  morphChildren($('#crumb'), Object.assign(document.createElement('nav'), { innerHTML: trail() }));
  const raised = strip();
  morphChildren($('#alerts'), Object.assign(document.createElement('div'), { innerHTML: raised }));
  $('#alerts').hidden = !raised;
  morphChildren($('#incidents'), Object.assign(document.createElement('div'), { innerHTML: incidentList() }));
  morphChildren($('#bug-reports'), Object.assign(document.createElement('div'), { innerHTML: bugReportList() }));
  const screens = { queue: () => queueScreen(id), now: model.lanes ? laneNowScreen : nowScreen, work: model.lanes ? laneWorkScreen : workScreen, history: model.lanes ? laneHistoryScreen : historyScreen, pages: () => pagesScreen(id), feature: () => featureScreen(id), priority: () => priorityScreen(id), file: () => fileScreen(route().path) };
  const open = new Set(model.review.map((entry) => entry.key));
  for (const [key, frame] of slotted) {
    if (open.has(key)) continue;
    frame.remove();
    slotted.delete(key);
    reviews.delete(key);
  }
  const html = (screens[screen] ?? queueScreen)();
  const arrived = pageKey() !== shownHash;
  const held = !arrived && screen === 'now' && heldFocus();
  shownHash = pageKey();
  $('#main').classList.toggle('arrived', arrived);
  if (arrived) $('#main').innerHTML = html;
  else morphChildren($('#main'), Object.assign(document.createElement('main'), { innerHTML: html }));
  settleCamera(arrived, held);
  if (reviewing) drawDrawer();
  sendPackets();
  feedPages();
  if ($('#jump').open) drawJump();
  for (const url of [...slices.keys()].slice(0, -SLICES_KEPT)) slices.delete(url);
  measure();
  fill();
  fresh();
  if (placeAfterLoad !== null && document.documentElement.scrollHeight - innerHeight >= placeAfterLoad) {
    window.scrollTo(0, placeAfterLoad);
    placeAfterLoad = null;
  }
  return arrived;
}

function pageKey() {
  const { screen, path } = route();
  return ['now', 'work'].includes(screen) ? screen : screen === 'queue' ? `queue/${filterOf(path[0])}${scopeQuery()}` : location.hash;
}

function morph(old, made) {
  if (old.nodeType !== made.nodeType || old.nodeName !== made.nodeName) return old.replaceWith(made);
  if (old.nodeType !== Node.ELEMENT_NODE) {
    if (old.nodeValue !== made.nodeValue) old.nodeValue = made.nodeValue;
    return;
  }
  if (old.matches('.visual-host.drawn')) return old.dataset.visual === made.dataset.visual && old.dataset.source === made.dataset.source ? undefined : old.replaceWith(made);
  if (old.matches('figure.recording') && old.dataset.recording === made.dataset.recording) return;
  if (old.matches('.story-outcome-media') && old.dataset.option !== made.dataset.option) old.scrollLeft = 0;
  if (old.matches('.packets')) return;
  for (const { name } of [...old.attributes]) if (!made.hasAttribute(name) && !(name === 'open' && old.matches('details'))) old.removeAttribute(name);
  for (const { name, value } of made.attributes) if (old.getAttribute(name) !== value) old.setAttribute(name, value);
  if (old.matches('textarea')) {
    if (old !== document.activeElement && old.value !== made.value) old.value = made.value;
    return;
  }
  morphChildren(old, made);
}

function morphChildren(old, made) {
  const kids = [...made.childNodes];
  const kept = new Map([...old.children].filter((kid) => kid.dataset.moment).map((kid) => [kid.dataset.moment, kid]));
  kids.forEach((kid, i) => {
    const at = old.childNodes[i] ?? null;
    const moment = kid.dataset?.moment;
    const same = moment && kept.get(moment);
    if (same) {
      kept.delete(moment);
      if (same !== at) old.insertBefore(same, at);
      return morph(same, kid);
    }
    if (!at || moment || at.dataset?.moment) return old.insertBefore(kid, at);
    morph(at, kid);
  });
  while (old.childNodes.length > kids.length) old.lastChild.remove();
}

function keepPlace() {
  placeAfterLoad = null;
  history.replaceState({ ...history.state, y: scrollY }, '');
}

function deployed() {
  outdated = true;
  fresh();
  reloadWhenAway();
}

function reloadWhenAway() {
  const busy = [...toasts.values()].some((shown) => ['sending', 'undoing'].includes(shown.state)) || [...sent.values()].some((was) => was.busy) || Boolean(document.activeElement?.closest('textarea, input'));
  if (outdated && document.hidden && !busy) reloadPage();
}

function reloadPage() {
  location.reload();
}

function fresh() {
  const el = $('#fresh');
  el.classList.toggle('late', !connected);
  el.textContent = connected ? 'Live' : model ? 'Stale' : 'Connecting';
  el.title = !model ? '' : connected ? `Live, updated ${clock(model.at)} Pacific` : `Not updated since ${clock(lostAt)}, showing ${clock(model.at)} Pacific`;
  $('#stale').hidden = !outdated && (connected || !model);
  $('#stale').innerHTML = outdated ? 'The dashboard was updated. <button type="button" class="linkish" data-reload>Reload</button>' : model && !connected ? `Not live: this is the drive as of ${esc(clock(model.at))}. Reconnecting…` : '';
}

async function answer(card, label) {
  const tapped = card.startsWith('tap:') ? model.review.find((entry) => `tap:${entry.key}` === card) : null;
  const asked = model.queue.find((candidate) => answerKey(candidate) === card) ?? tapped ?? model.review.find((entry) => entry.answer === card);
  const about = { label, title: asked.title };
  restored.delete(card);
  answers.delete(card);
  answers.set(card, about);
  toast(card, { ...about, state: 'sending' });
  render();
  try {
    const [path, body] = tapped ? ['/api/tap', { pr: tapped.key, label, head: tapped.head }] : ['/api/answer', { card, label }];
    const reply = await fetch(path, posted(body));
    const said = await reply.json();
    if (!reply.ok) throw new Error(said.error);
    answers.set(card, { ...about, label: said.label || label, at: said.at || new Date().toISOString(), done: true, undoUntil: said.undo_until });
    toast(card, { ...about, state: 'sent', undoUntil: said.undo_until });
  } catch (error) {
    answers.set(card, { ...about, error: error.message });
    toast(card, { ...about, state: 'failed', error: error.message });
  }
  render();
}

async function answerBatch(picks) {
  const unique = [...new Map(picks.map((pick) => [pick.card, pick])).values()];
  const group = `group:${crypto.randomUUID()}`;
  const cards = unique.map((pick) => pick.card);
  const label = unique.every((pick) => pick.label === unique[0].label) ? unique[0].label : 'Your choices';
  const about = { label, title: counted(cards.length, 'answer'), cards };
  toast(group, { ...about, state: 'sending' });
  await Promise.all(unique.map((pick) => answer(pick.card, pick.label)));
  const failed = cards.filter((card) => answers.get(card)?.error);
  toast(group, failed.length ? { ...about, state: 'failed', error: `${cards.length - failed.length} sent, ${failed.length} not sent. Each one shows why on the page.` } : { ...about, state: 'sent' });
}

async function undo(card, about = toasts.get(card) ?? answers.get(card)) {
  if (about.cards) {
    toast(card, { ...about, state: 'undoing' });
    await Promise.all(about.cards.filter(takesBack).map((each) => undo(each)));
    const kept = about.cards.filter((each) => answers.get(each)?.done);
    return toast(card, kept.length ? { ...about, state: 'late', error: `${counted(kept.length, 'answer')} could not be taken back` } : { ...about, state: 'restored' });
  }
  toast(card, { ...about, state: 'undoing' });
  const said = await ask('/api/undo', posted({ card }));
  if (!said.ok) return toast(card, { ...about, state: 'late', error: said.error });
  answers.delete(card);
  restored.add(card);
  if (route().screen === 'queue') history.replaceState(history.state, '', queueHref(filterOf(route().id) === 'tap' ? 'all' : filterOf(route().id), { answer: card }));
  toast(card, { ...about, state: 'restored' });
  render();
}

const TOAST_SAID = {
  sending: () => 'Sending…',
  undoing: () => 'Taking it back…',
  sent: () => 'Sent',
  restored: () => 'Restored. This is being asked again.',
  late: (shown) => shown.error,
  failed: (shown) => (shown.cards ? shown.error : `Not sent: ${shown.error}`),
};

function undoable(shown) {
  if (shown.cards) return shown.state === 'sent' && shown.cards.some(takesBack);
  return shown.state === 'sent' && Date.parse(shown.undoUntil) > Date.now();
}

function takesBack(card) {
  return Date.parse(answers.get(card)?.undoUntil) > Date.now();
}

function toast(card, shown) {
  toasts.set(card, shown);
  if (!['sending', 'undoing'].includes(shown.state)) setTimeout(() => toasts.get(card) === shown && toasts.delete(card) && drawToasts(), 5000);
  drawToasts();
}

function drawToasts() {
  const grouped = new Set([...toasts.values()].flatMap((shown) => shown.cards ?? []));
  const strip = $('#toasts');
  const drawn = new Map([...strip.children].map((node) => [node.dataset.toast, node]));
  for (const [card, node] of drawn) if (!toasts.has(card) || grouped.has(card)) node.remove();
  for (const [card, shown] of toasts) {
    if (grouped.has(card)) continue;
    const node = drawn.get(card) ?? strip.appendChild(document.createElement('div'));
    node.dataset.toast = card;
    node.className = `toast ${shown.state}`;
    node.setAttribute('role', 'status');
    node.innerHTML = `<span><b>${esc(shown.label)}</b> · ${esc(shown.title)}</span>
      <span class="meta">${esc(TOAST_SAID[shown.state](shown))}</span>
      ${undoable(shown) ? `<button class="btn" data-undo="${esc(card)}">Undo</button>` : ''}
      ${shown.state === 'restored' && !shown.cards ? `<a class="btn" href="${queueHref('all', { answer: card })}">Open it</a>` : ''}
      <button class="toast-x" data-dismiss="${esc(card)}" aria-label="Dismiss">×</button>`;
  }
}

async function say(key, under, words, about, thread) {
  if (!words.trim() || sent.get(key)?.busy) return;
  sent.set(key, { busy: true });
  render();
  try {
    const reply = await fetch('/api/say', posted({ text: about ? `${about}: ${words.trim()}` : words.trim(), under }));
    const said = await reply.json();
    if (!reply.ok) throw new Error(said.error);
    drafts.delete(key);
    sent.set(key, { at: new Date().toISOString() });
    if (thread) {
      followed.set(thread, Date.now());
      readTalk(thread);
    }
  } catch (error) {
    sent.set(key, { error: error.message });
  }
  render();
}

let heard = null;

function noticeKey(item) {
  return item.answer ?? `${item.kind}:${item.title}`;
}

function notify() {
  const fresh = model.queue.filter((item) => heard && !heard.has(noticeKey(item)));
  heard = new Set(model.queue.map(noticeKey));
  noticeButton();
  if (!fresh.length || !window.isSecureContext || !('Notification' in window) || Notification.permission !== 'granted') return;
  const [first] = fresh;
  const title = fresh.length === 1 ? `${LABEL[first.kind]}: ${first.title}` : `${fresh.length} new things wait on you`;
  const notice = new Notification(title, { body: fresh.length === 1 ? first.detail : fresh.map((item) => item.title).join('\n'), tag: `drive-${model.drive}`, requireInteraction: true });
  notice.onclick = () => {
    window.focus();
    queueAt = noticeKey(first);
    go('#/queue/all');
    notice.close();
  };
}

function noticeButton() {
  $('#notices').hidden = !window.isSecureContext || !('Notification' in window) || Notification.permission !== 'default';
}

function connect() {
  const stream = new EventSource('/api/stream');
  let reconnecting = false;
  stream.onopen = () => {
    reconnecting = false;
    connected = true;
    fresh();
  };
  stream.onmessage = (message) => {
    const next = JSON.parse(message.data);
    if (model && next.site !== model.site) return deployed();
    model = next;
    connected = true;
    render();
    notify();
    followTalk();
  };
  stream.onerror = () => {
    if (connected && !reconnecting && stream.readyState === EventSource.CONNECTING) {
      reconnecting = true;
      return;
    }
    if (connected) lostAt = new Date().toISOString();
    connected = false;
    if (!model) $('#main').innerHTML = '<p class="boot refused" role="status">The dashboard server is not answering. The page keeps trying and fills in as soon as it answers.</p>';
    fresh();
  };
}

document.addEventListener('change', (event) => {
  const picking = event.target.closest('input[data-ap-pick]');
  if (!picking) return;
  const picked = appletPicks.get(picking.dataset.apPick);
  if (picking.checked) picked.add(picking.value);
  else picked.delete(picking.value);
  render();
});
document.addEventListener('input', (event) => {
  const box = event.target.closest('textarea[data-draft]');
  if (box) drafts.set(box.dataset.draft, box.value);
  const filter = event.target.closest('#main [data-lane-filter]');
  if (!filter) return;
  laneFilter = { at: filter.dataset.laneFilter, words: filter.value };
  render();
  flyTo(nowCamera, fitOf($('#main .viewport')), 300);
});
document.addEventListener('submit', (event) => {
  const note = event.target.closest('form[data-note]');
  if (note) {
    event.preventDefault();
    addNote(note.dataset.note, note.dataset.item);
    return reveal(note);
  }
  const form = event.target.closest('form[data-say]');
  if (!form) return;
  event.preventDefault();
  say(form.dataset.say, form.dataset.under, drafts.get(form.dataset.say) ?? '', form.dataset.about, form.dataset.thread);
});
document.addEventListener('click', (event) => {
  const scope = $('#queue-scope');
  if (scope?.open && !scope.contains(event.target)) scope.open = false;
  if (touched) {
    const tipped = !event.target.closest('a, button') && event.target.closest('[data-tip]');
    if (tipped) return $('#srctip').dataset.shows === `tip:${tipped.dataset.tip}` ? hideSource() : showTip(tipped);
    hideSource();
  }
  const undone = event.target.closest('button[data-undo]');
  if (undone) return undo(undone.dataset.undo, toasts.get(undone.dataset.undo) ?? { label: undone.dataset.label, title: undone.dataset.title });
  const dismissed = event.target.closest('button[data-dismiss]');
  if (dismissed) {
    for (const card of [dismissed.dataset.dismiss, ...(toasts.get(dismissed.dataset.dismiss)?.cards ?? [])]) toasts.delete(card);
    return drawToasts();
  }
  const storyPick = event.target.closest('[data-story-pick], [data-story-priority]');
  if (storyPick) {
    const state = decisionStates.get(storyPick.dataset.storyCard);
    state.selected = Number(storyPick.dataset.storyPick ?? storyPick.dataset.storyPriority);
    if (storyPick.hasAttribute('data-story-priority')) state.step = 1;
    picked = null;
    render();
    if (storyPick.hasAttribute('data-story-priority')) { const title = $('#main .decision-story h2'); title.tabIndex = -1; title.focus(); }
    return;
  }
  const compare = event.target.closest('[data-story-compare]');
  if (compare) {
    const state = decisionStates.get(compare.dataset.storyCompare);
    state.compare = !state.compare;
    return render();
  }
  const back = event.target.closest('[data-story-back]');
  if (back) {
    const state = decisionStates.get(back.dataset.storyBack);
    state.step = 0;
    render();
    return document.querySelector(`[data-story-priority="${state.selected}"]`).focus();
  }
  const review = event.target.closest('[data-story-review]');
  if (review) return reviewDecision(review.dataset.storyReview);
  if (event.target.closest('[data-story-close]')) return $('#decision-review').close();
  const pick = event.target.closest('button[data-card]');
  if (pick) {
    $('#decision-review').close();
    return answer(pick.dataset.card, pick.dataset.label);
  }
  const pr = event.target.closest('button[data-pr]');
  if (pr) return openPR(pr.dataset.pr);
  const carding = event.target.closest('button[data-ap-card]');
  if (carding) {
    appletCards.set(carding.dataset.apCard, carding.dataset.item);
    carding.closest('details.ap-card-list')?.removeAttribute('open');
    return render();
  }
  const approving = event.target.closest('button[data-ap-approve]');
  if (approving) {
    const page = model.pages.find((each) => approving.dataset.apApprove.startsWith(`${each.slug}:`));
    const shown = pageSlice(page);
    const picks = [...(appletPicks.get(approving.dataset.apApprove) ?? [])].filter((card) => shown.prs.some((entry) => entry.answer === card)).map((card) => ({ card, label: 'Approve' }));
    return answerBatch(picks).then(render);
  }
  const queued = event.target.closest('[data-queue]');
  if (queued) {
    queueAt = queued.dataset.queue;
    return go(queued.getAttribute('href') ?? '#/queue/all');
  }
  if (event.target.closest('[data-motion]')) {
    paused = !paused;
    localStorage.setItem('motion', paused ? 'paused' : 'moving');
    if (paused) for (const packet of document.querySelectorAll('#main .packet')) packet.remove();
    return render();
  }
  if (event.target.closest('[data-map-context]')) return mapContext();
  const file = event.target.closest('button[data-file]');
  if (file) return openFile(file.dataset.file, file.dataset.title);
  if (event.target.closest('#kbtn')) return openJump();
  if (event.target.closest('[data-keys]')) return openKeys();
  if (event.target.closest('#notices')) return Notification.requestPermission().then(noticeButton);
  const jumped = event.target.closest('#jumplist [data-jump]');
  if (jumped) return goJump(jumped);
  if (event.target.id === 'jump') return $('#jump').close();
  const scoped = event.target.closest('.queue-scope a');
  if (scoped && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && event.button === 0) {
    event.preventDefault();
    history.pushState(null, '', scoped.getAttribute('href'));
    render();
    $('#queue-scope').open = scoped.hasAttribute('data-scope-project');
    return $('#queue-scope').querySelector(scoped.hasAttribute('data-scope-project') ? '.scope-streams a' : 'summary').focus();
  }
  event.target.closest('.incidents a')?.closest('[popover]').hidePopover();
  if (event.target.closest('[data-reload]')) return reloadPage();
  const tool = event.target.closest('[data-cam]');
  if (tool) {
    const view = tool.closest('.canvas, .visual-host').querySelector('.viewport, .frame');
    if (tool.dataset.cam === 'fit') return flyTo(camOf(view), fitOf(view), 420);
    const factor = tool.dataset.cam === 'in' ? 0.7 : 1 / 0.7;
    if (view.closest('.canvas.now') && stepLevel(view, zoomed(view, nowCamera.at.k / factor), view.clientWidth / 2, view.clientHeight / 2)) return;
    return zoomAround(view, factor, view.clientWidth / 2, view.clientHeight / 2, 260);
  }
  const spot = !event.target.closest('a, button, .task-sheet') && event.target.closest('#main .viewport [data-href]');
  if (spot) {
    const zone = spot.closest('.streams[inert]') ? spot.closest('.region') : spot;
    if (!zone.matches('.here')) location.hash = zone.dataset.href;
    return;
  }
  const shown = event.target.closest('#main [data-shot], #drawer [data-shot], #main [data-enlarge], #drawer [data-enlarge], #main .frame, #drawer .frame');
  if (shown) return openLightbox(shown.closest('figure.visual') ?? shown);
  const stepped = event.target.closest('#lightbox [data-step]');
  if (stepped) return stepLightbox(Number(stepped.dataset.step));
  if (event.target.matches('#lightbox .photo img')) return event.target.parentElement.classList.toggle('actual');
  const copied = event.target.closest('[data-copy]');
  if (copied) {
    navigator.clipboard.writeText(copied.closest('.visual-host').dataset.source).then(() => {
      copied.textContent = 'Copied';
    });
    return;
  }
  const closer = event.target.closest('[data-close]') ?? (event.target.matches('#drawer, #reader, #keys, #lightbox, .lb-stage') ? event.target : null);
  if (closer) return closer.closest('dialog').close();
  const more = event.target.closest('[data-more]');
  if (more) {
    if (!expanded.delete(more.dataset.more)) expanded.add(more.dataset.more);
    return render();
  }
  const moved = event.target.closest('[data-move]');
  if (moved) return move(Number(moved.dataset.move));
  const dchapter = event.target.closest('[data-dchapter]');
  if (dchapter) {
    decisionStates.get(dchapter.dataset.dchapterOf).chapter = Number(dchapter.dataset.dchapter);
    return render();
  }
  const chapter = event.target.closest('[data-chapter]');
  if (chapter) {
    prChapters.set(chapter.dataset.chapterOf, Number(chapter.dataset.chapter));
    render();
    return $('#main .ch-nav')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
  const apStep = event.target.closest('[data-ap-step]');
  if (apStep) {
    appletSteps.set(apStep.dataset.apStep, Number(apStep.dataset.index));
    return render();
  }
  const apTab = event.target.closest('[data-ap-tab]');
  if (apTab) {
    appletTabs.set(apTab.dataset.apTab, Number(apTab.dataset.index));
    return render();
  }
  const prtab = event.target.closest('[data-prtab]');
  if (prtab) {
    prTabs.set(prtab.dataset.item, prtab.dataset.prtab);
    return render();
  }
  const row = event.target.closest('[data-row]');
  if (row) {
    prRows.set(row.dataset.item, Number(row.dataset.row));
    if (route().screen === 'queue') history.replaceState(history.state, '', queueHref(filterOf(route().id), model.queue.find((each) => noticeKey(each) === row.dataset.item)));
    return render();
  }
  const retry = event.target.closest('[data-retry]');
  if (retry) {
    reviews.delete(retry.dataset.retry);
    return render();
  }
  const marked = event.target.closest('[data-spot]');
  if (marked) {
    prTabs.set(marked.dataset.item, 'show');
    render();
    reveal(marked);
    const pane = marked.closest('.pr-layout').querySelector('.pr-pane.show');
    const target = pane.querySelectorAll(SPOTS)[Number(marked.dataset.spot)];
    pane.scrollTop += target.getBoundingClientRect().top - pane.getBoundingClientRect().top - 12;
    return (target.matches('button') ? target : target.querySelector('[data-enlarge]'))?.focus({ preventScroll: true });
  }
  const point = event.target.closest('[data-point]');
  if (point) {
    const { point: pr, item, path, line } = point.dataset;
    pointed.set(pr, line ? { path, line: Number(line) } : { path });
    prTabs.set(item, 'change');
    render();
    reveal(point);
    return tell(pr, { type: 'cc-review:show', path, pr: Number(pr.split('#')[1]) });
  }
  const unpoint = event.target.closest('[data-unpoint]');
  if (unpoint) {
    pointed.delete(unpoint.dataset.unpoint);
    return render();
  }
});
document.addEventListener('mouseout', (event) => {
  const choices = event.target.closest('#main .choices, #main .story-options');
  if (!choices || !picked || choices.contains(event.relatedTarget) || choices.contains(document.activeElement)) return;
  picked = null;
  render();
});
document.addEventListener('mouseover', (event) => {
  if (touched) return;
  const choice = event.target.closest('#main .choice[data-preview], #main [data-story-pick]');
  if (choice) preview(choice);
  const cited = event.target.closest('a[data-src]');
  if (cited) return showSource(cited);
  const tipped = event.target.closest('[data-tip]');
  if (tipped) return showTip(tipped);
  hideSource();
});
document.addEventListener('focusin', (event) => {
  const folded = event.target.matches(':focus-visible') && event.target.closest('.fold.long:not(.open)');
  if (folded) {
    expanded.add(folded.dataset.fold);
    render();
  }
  if (event.target.matches('#main .choice[data-preview], #main [data-story-pick]')) preview(event.target);
  if (event.target.matches('a[data-src]')) showSource(event.target);
  else if (!touched && event.target.closest('[data-tip]')) showTip(event.target.closest('[data-tip]'));
});
document.addEventListener('focusout', (event) => {
  if (event.target.closest('a[data-src], [data-tip]')) hideSource();
  const choices = event.target.closest('#main .choices, #main .story-options');
  if (!choices || !picked || choices.contains(event.relatedTarget) || choices.matches(':hover')) return;
  picked = null;
  render();
});
window.addEventListener('message', heardReview);
window.addEventListener('message', heardPage);
document.addEventListener('load', (event) => event.target.matches?.('iframe.page-frame') && feedPages(), true);
document.addEventListener('load', (event) => {
  const frame = event.target;
  const review = frame.matches?.('iframe.review-frame') && reviews.get(frame.dataset.review);
  if (!review || review.url !== frame.getAttribute('src')) return;
  review.loaded = true;
  render();
}, true);
window.addEventListener('hashchange', () => {
  hideSource();
  if (render() || history.state?.y !== undefined) window.scrollTo(0, history.state?.y ?? 0);
});
document.addEventListener('pointerover', (event) => {
  touched = event.pointerType !== 'mouse';
});
document.addEventListener('pointerdown', (event) => {
  const view = event.target.closest('#main .viewport, .frame');
  if (!view || event.button !== 0 || (view.matches('.viewport') && event.target.closest('.task-sheet, .stream.here .stream-body, input'))) return;
  if (!gesture) dragged = false;
  if (gesture?.view !== view) gesture = { view, cam: camOf(view), points: new Map(), moved: false };
  gesture.points.set(event.pointerId, { x: event.clientX, y: event.clientY });
  regrip();
});
document.addEventListener('pointermove', (event) => {
  if (!gesture?.points.has(event.pointerId)) return;
  gesture.points.set(event.pointerId, { x: event.clientX, y: event.clientY });
  const { view, cam, from, camera } = gesture;
  const to = grip(gesture.points, view);
  if (!gesture.moved && gesture.points.size === 1 && Math.hypot(to.x - from.x, to.y - from.y) < 6) return;
  if (!gesture.moved && event.pointerType === 'mouse') view.setPointerCapture(event.pointerId);
  view.classList.add('dragging');
  gesture.moved = dragged = true;
  cancelAnimationFrame(cam.flying);
  const k = zoomed(view, camera.k * (to.spread / from.spread));
  cam.at = { k, x: camera.x + from.x / camera.k - to.x / k, y: camera.y + from.y / camera.k - to.y / k };
  aim(cam);
});
for (const ended of ['pointerup', 'pointercancel']) {
  document.addEventListener(ended, (event) => {
    if (!gesture?.points.delete(event.pointerId)) return;
    if (gesture.points.size) return regrip();
    gesture.view.classList.remove('dragging');
    gesture = null;
  });
}
document.addEventListener('click', (event) => {
  if (!dragged || !event.detail || !event.target.closest('#main .viewport, .frame')) return;
  dragged = false;
  event.preventDefault();
  event.stopImmediatePropagation();
}, true);
document.addEventListener('wheel', (event) => {
  const view = event.target.closest('#main .viewport, .frame');
  if (!view) return;
  const now = view.closest('.canvas.now');
  const pinched = event.ctrlKey || event.metaKey;
  if (!(pinched || now || view.closest('.lb-stage'))) return;
  event.preventDefault();
  if (now && performance.now() < stepped) return;
  if (now && event.shiftKey && !pinched) return panNow(event.deltaX, event.deltaY);
  const r = view.getBoundingClientRect();
  const [px, py] = [event.clientX - r.left, event.clientY - r.top];
  zoomAround(view, Math.exp(event.deltaY * (event.ctrlKey ? 0.01 : 0.0016)), px, py);
  if (now) stepLevel(view, nowCamera.at.k, px, py);
}, { passive: false });
window.addEventListener('scroll', () => {
  if (touched) hideSource();
  clearTimeout(keepPlace.timer);
  keepPlace.timer = setTimeout(keepPlace, 120);
}, { passive: true });
document.addEventListener('click', keepPlace, true);
document.addEventListener('toggle', (event) => {
  const file = event.target.closest?.('.df-file');
  if (file) diffOpen.set(`${file.closest('[data-diff]').dataset.diff}:${file.dataset.dfFile}`, file.open);
}, true);
document.addEventListener('click', (event) => {
  const all = event.target.closest('[data-df-all]');
  if (!all) return;
  for (const file of all.closest('[data-diff]').querySelectorAll('.df-file')) file.open = all.dataset.dfAll === 'open';
});
document.addEventListener('keydown', (event) => {
  touched = false;
  const scope = $('#queue-scope');
  if (scope?.open && event.key === 'Escape') {
    event.preventDefault();
    scope.open = false;
    return scope.querySelector('summary').focus();
  }
  const box = event.target.closest?.('textarea[data-draft]');
  if (box && event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    return box.form.requestSubmit();
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault();
    if (scope) scope.open = false;
    return openJump();
  }
  const tab = event.target.closest?.('[data-prtab]');
  if (tab && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
    const tabs = [...tab.parentElement.querySelectorAll('[data-prtab]')];
    const next = tabs[(tabs.indexOf(tab) + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
    event.preventDefault();
    next.click();
    return next.focus();
  }
  const writing = event.target.closest?.('input, textarea');
  if ((event.metaKey || event.ctrlKey) && !event.shiftKey && event.key.toLowerCase() === 'z' && !writing) {
    const held = [...answers].findLast(([, was]) => Date.parse(was.undoUntil) > Date.now());
    if (!held) return;
    event.preventDefault();
    return undo(held[0]);
  }
  const free = !event.metaKey && !event.ctrlKey && !event.altKey && !writing && !scope?.open && !document.querySelector('dialog[open]');
  const { screen } = route();
  if (free && event.key === 'Escape' && up()) {
    event.preventDefault();
    return go(up());
  }
  if (free && event.key === '?') {
    event.preventDefault();
    return openKeys();
  }
  const zoomKey = { '+': 'in', '=': 'in', '-': 'out' }[event.key];
  if (free && screen === 'now' && zoomKey) {
    event.preventDefault();
    return $(`#main .canvas [data-cam="${zoomKey}"]`).click();
  }
  const pan = { ArrowLeft: [-80, 0], ArrowRight: [80, 0], ArrowUp: [0, -80], ArrowDown: [0, 80] }[event.key];
  if (free && screen === 'now' && pan && !event.target.closest?.('.now-side, .task-sheet')) {
    event.preventDefault();
    return panNow(...pan);
  }
  const decision = $('#main .decision-story');
  const storyChoice = event.target.closest?.('[data-story-pick]');
  if (free && storyChoice && ['ArrowLeft', 'ArrowRight'].includes(event.key)) {
    event.preventDefault();
    const choices = [...decision.querySelectorAll('[data-story-pick]')];
    const next = choices[(choices.indexOf(storyChoice) + (event.key === 'ArrowRight' ? 1 : choices.length - 1)) % choices.length];
    next.click();
    return next.focus();
  }
  if (free && decision && /^[1-9]$/.test(event.key)) {
    event.preventDefault();
    return decision.querySelector(`[data-story-${decision.dataset.format === 'interview' && decisionStates.get(decision.dataset.story).step === 0 ? 'priority' : 'pick'}="${Number(event.key) - 1}"]`)?.click();
  }
  if (free && decision && event.key === 'Enter' && !event.target.closest('button, a, summary')) {
    event.preventDefault();
    return decision.querySelector('[data-story-review]')?.click();
  }
  if (free && decision && event.key === 'e') {
    event.preventDefault();
    const details = $('#main [data-story-evidence]');
    details.open = !details.open;
    return details.querySelector('summary').focus();
  }
  if (free && screen === 'queue') {
    const step = { ArrowLeft: -1, ArrowRight: 1, k: -1, j: 1 }[event.key];
    if (step) {
      event.preventDefault();
      return move(step);
    }
    const filter = filterOf(route().id);
    const target = /^[1-9]$/.test(event.key) ? document.querySelectorAll('#main .focus-card button[data-card]')[Number(event.key) - 1] : {
      a: $('#main .focus-card button[data-card][data-label="Approve"]'),
      h: $('#main .focus-card button[data-card][data-label="Hold"]'),
      o: $('#main .focus-card [data-github]'),
      e: $('#main .focus-card [data-more]'),
      v: $('#main .focus-card .pr-example :is([data-shot], [data-enlarge])'),
      n: $('#main .focus-card [data-chapter-step="1"]'),
      p: $('#main .focus-card [data-chapter-step="-1"]'),
    }[event.key];
    if (target) {
      event.preventDefault();
      return target.click();
    }
    if (event.key === 'q' && $('#main form[data-say] textarea')) {
      event.preventDefault();
      return $('#main form[data-say] textarea').focus();
    }
    if (event.key === 'f') {
      event.preventDefault();
      const at = QUEUE_FILTERS.findIndex(([id]) => id === filter);
      return go(`#/queue/${QUEUE_FILTERS[(at + 1) % QUEUE_FILTERS.length][0]}${scopeQuery()}`);
    }
    if (event.key === '[' || event.key === ']') {
      event.preventDefault();
      const keys = [null, ...scopeKeys(filter)];
      const at = keys.indexOf(route().query.get('project'));
      return go(`#/queue/${filter}${scopeQuery(keys[(at + (event.key === ']' ? 1 : keys.length - 1)) % keys.length], null)}`);
    }
  }
  if ($('#lightbox').open && ['ArrowLeft', 'ArrowRight'].includes(event.key)) {
    event.preventDefault();
    return stepLightbox(event.key === 'ArrowLeft' ? -1 : 1);
  }
  if (!$('#jump').open) return;
  const rows = [...document.querySelectorAll('#jumplist [data-jump]')];
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    jumpAt = Math.max(0, Math.min(rows.length - 1, jumpAt + (event.key === 'ArrowDown' ? 1 : -1)));
    drawJump();
    document.querySelector('#jumplist .on')?.scrollIntoView({ block: 'nearest' });
  } else if (event.key === 'Enter' && rows[jumpAt]) {
    event.preventDefault();
    goJump(rows[jumpAt]);
  }
});
function useRenderers(kit) {
  sharedRenderers = { ...kit, ...kit.createRenderers({ repo: () => model.repo, sourceLink, picture, shown, fenced: visual, featureLink, reportLine, time: clock }) };
}
window.addEventListener('DOMContentLoaded', async () => {
  useRenderers(await import('/kit/renderers.js'));
  history.scrollRestoration = 'manual';
  placeAfterLoad = history.state?.y ?? null;
  paused = localStorage.getItem('motion') === 'paused';
  narrow = matchMedia('(max-width: 760px)');
  narrow.addEventListener('change', render);
  $('#jumpfind').addEventListener('input', () => {
    jumpAt = 0;
    drawJump();
  });
  setInterval(render, 60000);
  window.addEventListener('resize', () => {
    measure();
    place();
  });
  new ResizeObserver(place).observe($('#main'));
  new MutationObserver(() => drawFigures(document)).observe(document.body, { childList: true, subtree: true });
  document.addEventListener('visibilitychange', reloadWhenAway);
  setInterval(() => {
    if (document.hidden) return;
    for (const slot of document.querySelectorAll('[data-slot]')) openReview(slot.dataset.slot, slot.dataset.head);
  }, REVIEW_KEEP_MS);
  document.addEventListener('play', (event) => {
    for (const other of document.querySelectorAll('video')) if (other !== event.target) other.pause();
  }, true);
  document.addEventListener('error', (event) => {
    if (!event.target.matches?.('figure.recording > video')) return;
    event.target.hidden = true;
    event.target.parentElement.dataset.failed = '';
    event.target.parentElement.querySelector('.recording-error').hidden = false;
  }, true);
  $('#lightbox').addEventListener('close', () => {
    handBack();
    viewing.opener?.focus({ preventScroll: true });
    viewing = null;
    $('#lightbox').replaceChildren();
  });
  $('#drawer').addEventListener('close', () => {
    hideSource();
    reviewing = null;
    $('#drawer').replaceChildren();
  });
  connect();
});
