const ZONE = 'America/Los_Angeles';
const STALL_MINUTES = 45;
const READABLE = 0.42;
const GAP_MINUTES = 30;
const REVIEW_KEEP_MS = 5 * 60 * 1000;
const SLACK = /^https:\/\/[\w-]+\.slack\.com\//;
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
const JOB_STATES = { running: 'Running', waiting: 'Waiting', built: 'Built, no PR until you answer', done: 'Done', failed: 'Failed', stopped: 'Stopped' };
const HAPPENED = { order: 'You asked', answer: 'You answered', live: 'Live', merged: 'Merged', failed: 'Failed', refused: 'No card drawn', said: 'Sent you' };
const LEVEL_IN = 2.2;
const LEVEL_OUT = 2.4;
const DECIDED = { tap: 'You tapped', answered: 'You picked', undone: 'You took back' };
const FEATURE_KINDS = new Set(['order', 'live', 'failed']);
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
const LABEL = { question: 'Decision', tap: 'Pull request', late: 'Late' };
const TAP_LABELS = ['Approve', 'Hold'];
const TAP_KEYS = { Approve: 'a', Hold: 'h' };
const PR_TABS = [['show', 'Show me'], ['change', 'The change'], ['context', 'Context']];
const CHECK_FACTS = { pass: ['Tests pass', 'good'], fail: ['Tests fail', 'bad'], pending: ['Tests running', 'warn'], none: ['No CI', ''] };
const REVIEW_FACTS = { approved: ['Review approved', 'good'], stale: ['Approved on an older head', ''], changes: ['Changes requested', 'bad'] };
const TALK_FOLLOW_MS = 15 * 60 * 1000;
const FRAMES_KEPT = 4;
const GONE_ROWS = { merged: 'Landed', live: 'Landed', closed: 'Closed without merging', dropped: 'Dropped from the drive' };
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
    [['j', '→'], 'Next card'], [['k', '←'], 'Previous card'], [['1–9'], 'Answer with that choice'], [['a'], 'Approve the pull request'], [['h'], 'Hold the pull request'],
    [['o'], 'Open the pull request on GitHub'], [['q'], 'Ask about it, or answer in your own words'], [['e'], 'Explain more, or show less'],
    [['f'], 'Next filter: All, PRs, Decisions'], [['[', ']'], 'Previous or next project'], [['⌘Z'], 'Take back the newest answer that still shows Undo'],
  ]],
  ['Now', [[['scroll', 'pinch'], 'Zoom around the pointer, into a project or workstream and back out'], [['⇧ scroll', '←', '→', '↑', '↓'], 'Pan'], [['+', '−'], 'Zoom in or out']]],
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
const PR_LINK = /https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/(\d+)\S*/g;
const FILE_WORDS = { brief: 'Brief', report: 'Report', question: 'Question', repro: 'Reproduction', transcript: 'Transcript' };
const SOURCE_CITE = /(^|[^\w/.@`-])`?((?:[\w.@-]+\/)+[\w.@-]+\.[A-Za-z][A-Za-z0-9]*):(\d+)(?:-(\d+))?`?(?![\w-]|[./]\w)/g;
const PR_FIELDS = [
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
const answers = new Map();
const drafts = new Map();
const sent = new Map();
const toasts = new Map();
const restored = new Set();
let outdated = false;
const prTabs = new Map();
const prRows = new Map();
const prReads = new Map();
const reviews = new Map();
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
const TYPED_MARK = '_You, on the dashboard:_ ';
let reviewing = null;
let viewing = null;

const $ = (selector) => document.querySelector(selector);
const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const link = (text, url) => (url && !SLACK.test(url) ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text));
const minutesSince = (iso) => Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
const bare = (title) => String(title ?? '').replace(/^[\w./-]+:\s*\p{Extended_Pictographic}\uFE0F?\s*/u, '');
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;
const counted = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const fileKey = (name) => name ?? NONE;
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

const VISUAL_KINDS = new Set(['arch', 'mermaid', 'chart', 'compare', 'dot']);
const VISUAL_WORDS = { arch: 'Map of the parts', mermaid: 'Diagram', dot: 'Diagram', chart: 'Chart', compare: 'Side by side' };
const WORD_FENCES = new Set(['', 'text', 'txt', 'plain']);
const FRAMED = new Set(['mermaid', 'dot', 'chart', 'compare']);
const PICTURE_LINE = /^\s*!\[([^\]]*)\]\((\/[^)\s]+)\)\s*$/;
const QUOTE_LINE = /^\s*>\s?/;
const TABLE_ROW = /^\s*\|/;
const LIST_ITEM = /^\s*(?:[-*•]|\d+[.)])\s+/;
const KEY_LINE = /^([A-Z][A-Za-z0-9 ()'\/-]{0,38}):(?:\s+(.*))?$/;
const HEADING_LINE = /^\s*(?:#{1,4}\s+(.+?)|\*\*([^*\n]+)\*\*|\*([^*\n]+)\*):?\s*$/;

function inline(text, cite = model.repo) {
  const spans = [];
  const keep = (html) => `\u0000${spans.push(html) - 1}\u0000`;
  let out = cite ? String(text).replace(SOURCE_CITE, (_, pre, path, from, to) => `${pre}${keep(sourceLink(cite, path, from, to))}`) : String(text);
  out = out
    .replace(/`([^`]+)`/g, (_, code) => keep(`<code>${esc(code)}</code>`))
    .replace(/<(https?:\/\/[^|>\s]+)\|([^>]+)>/g, (_, url, label) => keep(link(label, url)))
    .replace(/<(https?:\/\/[^|>\s]+)>/g, (_, url) => keep(link(url, url)))
    .replace(/!\[([^\]]*)\]\((https?:\/\/[^)\s]+)\)/g, (_, alt, url) => keep(picture(url, alt)))
    .replace(/\[([^\]]+)\]\((#\/[^)\s]+)\)/g, (_, label, route) => keep(`<a class="ref" href="${esc(route)}">${esc(label)}</a>`))
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_, label, url) => keep(link(label, url)))
    .replace(/https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/(\d+)\S*/g, (url, number) => keep(link(`#${number}`, url)));
  out = esc(out)
    .replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, '$1<b>$2</b>')
    .replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,;:!?]|$)/g, '$1<i>$2</i>');
  return out.replace(/\u0000(\d+)\u0000/g, (_, n) => spans[Number(n)]);
}

function sourceLink(cite, path, from, to) {
  const [repo] = cite.split('#');
  const head = model.review.find((entry) => entry.key === cite)?.head ?? 'HEAD';
  const span = to ? `${from}-${to}` : from;
  return `<a class="src" href="https://github.com/${esc(repo)}/blob/${esc(head)}/${esc(path)}#L${from}${to ? `-L${to}` : ''}" target="_blank" rel="noopener" data-src="${esc(cite)}" data-path="${esc(path)}" data-line="${from}" data-to="${to ?? from}">${esc(path.split('/').pop())}:${span}</a>`;
}

function prose(text, cite) {
  const blocks = [];
  const parts = String(text ?? '').split(/^```[ \t]*([\w+-]*)[^\n]*\n([\s\S]*?)^```\s*$/m);
  parts.forEach((part, i) => {
    if (i % 3 === 1) return;
    if (i % 3 === 2) {
      blocks.push(fenced(parts[i - 1].toLowerCase(), part));
      return;
    }
    for (const para of part.split(/\n\s*\n/)) {
      const lines = para.split('\n').filter((line) => line.trim());
      if (!lines.length) continue;
      while (lines.length) {
        const heading = lines[0].match(HEADING_LINE);
        if (heading) {
          blocks.push(`<h4 class="hd">${inline(heading[1] ?? heading[2] ?? heading[3], cite)}</h4>`);
          lines.shift();
          continue;
        }
        const row = TABLE_ROW.test(lines[0]);
        const end = lines.findIndex((line) => TABLE_ROW.test(line) !== row || HEADING_LINE.test(line));
        const run = lines.splice(0, end < 0 ? lines.length : end);
        if (row) {
          blocks.push(table(run, cite));
        } else if (run.some((line) => PICTURE_LINE.test(line) || QUOTE_LINE.test(line))) {
          blocks.push(...lineBlocks(run, cite));
        } else if (run.every((line) => LIST_ITEM.test(line))) {
          blocks.push(listOf(run, cite));
        } else if (run.filter((line) => KEY_LINE.test(line)).length > 1) {
          const at = run.findIndex((line) => KEY_LINE.test(line));
          if (at) blocks.push(`<p>${run.slice(0, at).map((line) => inline(line, cite)).join('<br>')}</p>`);
          blocks.push(factList(run.slice(at), cite));
        } else {
          blocks.push(`<p>${run.map((line) => inline(line, cite)).join('<br>')}</p>`);
        }
      }
    }
  });
  return blocks.join('');
}

function listOf(lines, cite) {
  return `<ul>${lines.map((line) => `<li>${inline(line.replace(LIST_ITEM, ''), cite)}</li>`).join('')}</ul>`;
}

function factList(lines, cite) {
  const facts = [];
  for (const line of lines) {
    const key = line.match(KEY_LINE);
    if (key) facts.push([key[1], key[2] ? [key[2]] : []]);
    else facts.at(-1)[1].push(line);
  }
  const said = (rest) => {
    const items = rest.filter((line) => LIST_ITEM.test(line));
    return `${rest.filter((line) => !LIST_ITEM.test(line)).map((line) => inline(line, cite)).join('<br>')}${items.length ? listOf(items, cite) : ''}`;
  };
  return `<dl class="keyed">${facts.map(([key, rest]) => `<dt>${esc(key)}</dt><dd>${said(rest)}</dd>`).join('')}</dl>`;
}

function lineBlocks(lines, cite) {
  const blocks = [];
  let quote = [];
  const flush = () => {
    if (quote.length) blocks.push(`<blockquote class="quote">${quote.map((line) => inline(line, cite)).join('<br>')}</blockquote>`);
    quote = [];
  };
  for (const line of lines) {
    if (QUOTE_LINE.test(line)) {
      quote.push(line.replace(QUOTE_LINE, ''));
      continue;
    }
    flush();
    const shot = line.match(PICTURE_LINE);
    blocks.push(shot ? shown(shot[2], shot[1]) : `<p>${inline(line, cite)}</p>`);
  }
  flush();
  return blocks;
}

function fenced(language, source) {
  return visual(VISUAL_KINDS.has(language) ? language : `code:${WORD_FENCES.has(language) ? 'text' : language}`, source);
}

function shown(path, caption) {
  const drawn = sources.get(path);
  return drawn && drawn.kind !== 'image' ? visual(drawn.kind, drawn.source, caption) : shot(path, caption);
}

function shot(path, caption) {
  return picture(`/api/picture?path=${encodeURIComponent(path)}`, caption);
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
  viewing = { items, at: items.indexOf(item), opener: document.activeElement };
  drawLightbox();
  $('#lightbox').showModal();
}

function drawLightbox() {
  const { items, at } = viewing;
  const item = items[at];
  const host = item.querySelector('.visual-host');
  const caption = host ? item.querySelector('figcaption')?.textContent ?? '' : item.dataset.caption;
  const stage = host ? visual(host.dataset.visual, host.dataset.source) : `<img src="${esc(item.dataset.shot)}" alt="${esc(caption)}">`;
  const original = host ? '' : `<a class="ext" href="${esc(item.dataset.shot)}" target="_blank" rel="noopener">Open the original ↗</a>`;
  const steps = items.length > 1 ? `<button class="lb-step" data-step="-1" aria-label="Previous"${at === 0 ? ' disabled' : ''}>‹</button><button class="lb-step next" data-step="1" aria-label="Next"${at === items.length - 1 ? ' disabled' : ''}>›</button>` : '';
  $('#lightbox').innerHTML = `<div class="lb-bar"><span class="meta">${items.length > 1 ? `${at + 1} of ${items.length}` : ''}</span><span class="acts">${original}<button class="linkish" data-close autofocus>Close</button></span></div>
    <div class="lb-stage${host ? '' : ' photo'}">${stage}</div>
    ${caption ? `<p class="lb-caption">${esc(caption)}</p>` : ''}${steps}`;
  drawFigures($('#lightbox'));
}

function stepLightbox(delta) {
  const at = viewing.at + delta;
  if (!viewing.items[at]) return;
  viewing.at = at;
  drawLightbox();
}

async function drawFigures(root) {
  const hosts = [...root.querySelectorAll('.visual-host:not(.drawn)')];
  if (!hosts.length) return;
  for (const host of hosts) host.classList.add('drawn');
  const loaded = import('/visuals.js');
  for (const host of hosts) {
    try {
      await (await loaded).draw(host, host.dataset.visual, host.dataset.source);
      if (FRAMED.has(host.dataset.visual) && !host.closest('.thumbs')) frame(host);
      if (host.dataset.visual.startsWith('code:')) host.insertAdjacentHTML('afterbegin', `<div class="code-bar"><span>${esc(host.dataset.visual.slice(5) || 'code')}</span><span class="acts"><button type="button" class="linkish" data-copy>Copy</button><button type="button" class="linkish" data-enlarge>Enlarge</button></span></div>`);
    } catch (failure) {
      host.innerHTML = `<p class="refused">This ${esc(host.dataset.visual.split(':')[0])} did not draw (${esc(failure.message)}), so here is what it is drawn from.</p><pre><code>${esc(host.dataset.source)}</code></pre>`;
    }
  }
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
  return read?.ok ? { ...item, card: read.card, rail: read.rail } : item;
}

function notRead(read) {
  return read ? `<p class="refused">${esc(read.error)}</p>` : '<p class="empty small">Reading…</p>';
}

function table(lines, cite) {
  const cells = (line) => line.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim());
  const rows = lines.filter((line) => !/^\s*\|[\s:|-]+$/.test(line)).map(cells);
  const [head, ...body] = rows;
  return `<div class="tablewrap"><table><thead><tr>${head.map((cell) => `<th>${inline(cell, cite)}</th>`).join('')}</tr></thead><tbody>${body
    .map((row) => `<tr>${row.map((cell) => `<td>${inline(cell, cite)}</td>`).join('')}</tr>`)
    .join('')}</tbody></table></div>`;
}

function prKey(url) {
  const found = String(url).match(/^https:\/\/github\.com\/([\w.-]+\/[\w.-]+)\/pull\/(\d+)/);
  return found ? `${found[1]}#${found[2]}` : null;
}

function optionParts(option) {
  const cut = option.search(/:\s|\.\s/);
  if (cut < 0) return [option, ''];
  return [option.slice(0, option[cut] === '.' ? cut + 1 : cut), option.slice(cut + 1).trim()];
}

function answerKey(item) {
  return item.answer ?? (item.kind === 'tap' ? `tap:${prKey(item.url)}` : null);
}

function choices(item, keyed = false) {
  const answered = answerKey(item);
  const given = answers.get(answered);
  if (given?.done) return `<div class="answered">✓ <b>${esc(given.label)}</b> · answered ${clock(given.at)}</div>`;
  const held = given && !given.error;
  const failed = given?.error ? `<p class="refused">${esc(given.error)}</p>` : '';
  const shown = picked?.card === answered ? picked.index : -1;
  const buttons = (item.card?.options ?? TAP_LABELS).map((option, i) => {
    const attrs = `data-card="${esc(answered)}" data-label="${esc(option)}"${held ? ' disabled' : ''}`;
    if (item.kind === 'tap') return `<button class="${option === 'Approve' ? 'primary' : 'secondary'}" ${attrs}>${keyed ? `<kbd>${TAP_KEYS[option] ?? i + 1}</kbd>` : ''}${esc(option)}</button>`;
    const [title, detail] = optionParts(option);
    const recommended = option === item.card.recommended;
    return `<button class="choice${recommended ? ' recommended' : ''}${i === shown ? ' on' : ''}" ${attrs} data-preview="${i}">
      <span class="key">${keyed ? i + 1 : ''}</span>
      <span><span class="choice-title">${esc(title)}${recommended ? '<em>Recommended</em>' : ''}</span>${detail ? `<span class="choice-detail">${esc(detail)}</span>` : ''}</span>
      <span class="arrow" aria-hidden="true">›</span>
    </button>`;
  });
  const back = restored.has(answered) ? '<p class="restored" role="status">Restored. This is being asked again.</p>' : '';
  return `${back}<div class="${item.kind === 'tap' ? 'pr-picks' : 'choices'}">${buttons.join('')}</div>${failed}`;
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
  return `<span class="fact${tone ? ` ${tone}` : ''}"${tip ? ` tabindex="0" data-tip="${esc(tip)}"` : ''}>${html}</span>`;
}

function counter(at, total) {
  return `<div class="counter"><span>${at + 1} of ${total}</span><button data-move="-1" aria-label="Previous"${at === 0 ? ' disabled' : ''}>‹</button><button data-move="1" aria-label="Next"${at === total - 1 ? ' disabled' : ''}>›</button></div>`;
}

function filterOf(id) {
  return QUEUE_FILTERS.some(([filter]) => filter === id) ? id : 'all';
}

function shownQueue(filter, project = route().query.get('project'), stream = route().query.get('stream')) {
  const [, , keep] = QUEUE_FILTERS.find(([id]) => id === filter);
  const inScope = (item) => (project === null || fileKey(item.project) === project) && (stream === null || fileKey(item.workstream) === stream);
  return model.queue.filter((item) => keep(item) && inScope(item) && !(answers.has(answerKey(item)) && !answers.get(answerKey(item)).error));
}

function scopeQuery(project = route().query.get('project'), stream = route().query.get('stream')) {
  const query = new URLSearchParams([...(project === null ? [] : [['project', project]]), ...(stream === null ? [] : [['stream', stream]])]);
  return query.size ? `?${query}` : '';
}

function scopeKeys(filter, project = null) {
  const chosen = route().query.get(project === null ? 'project' : 'stream');
  const keys = shownQueue(filter, project, null).map((item) => fileKey(project === null ? item.project : item.workstream));
  return [...new Set([...(chosen === null ? [] : [chosen]), ...keys])].sort((a, b) => byName([a], [b]));
}

function scopeChips(filter) {
  const project = route().query.get('project');
  const stream = route().query.get('stream');
  const chip = (label, href, on, n) => `<a href="${esc(href)}" class="${on ? 'on' : ''}"${on ? ' aria-current="true"' : ''}>${esc(label)}<span class="n">${n}</span></a>`;
  const projects = scopeKeys(filter);
  if (projects.length < 2 && project === null) return '';
  const streams = project === null ? [] : scopeKeys(filter, project);
  const rows = [chip('All projects', `#/queue/${filter}`, project === null, shownQueue(filter, null, null).length), ...projects.map((key) => chip(fileName(key), `#/queue/${filter}${scopeQuery(key, null)}`, key === project && stream === null, shownQueue(filter, key, null).length))];
  const inner = streams.length > 1 ? streams.map((key) => chip(fileName(key), `#/queue/${filter}${scopeQuery(project, key)}`, key === stream, shownQueue(filter, project, key).length)) : [];
  return `<nav class="subtabs scopes" aria-label="Project">${rows.join('')}</nav>${inner.length ? `<nav class="subtabs scopes" aria-label="Workstream">${inner.join('')}</nav>` : ''}`;
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

function queueHref(filter, item) {
  if (!item?.answer) return `#/queue/${filter}${scopeQuery()}`;
  const rows = item.card?.prs ?? [];
  return `#/queue/${filter}/${cardTs(item.answer)}${rows.length > 1 ? `/${prKey(rows[chosenRow(item)].url).split('#')[1]}` : ''}${scopeQuery()}`;
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

function meaning(item, index) {
  const card = item.card;
  const option = card.options[index];
  const [title, detail] = optionParts(option);
  const label = option === card.recommended ? 'Recommended' : `Choice ${index + 1} of ${card.options.length}`;
  const explained = (card.hints?.[option] ?? '').split('\n').filter(Boolean).map((line) => `<p>${inline(line)}</p>`).join('');
  return `<div class="rail-block" data-means><div class="eyebrow">${label}</div><div class="consequence-title">${esc(title)}</div>${detail ? `<p>${esc(detail)}</p>` : ''}${explained}</div>`;
}

function heldRow(item, entry, held) {
  const project = fileKey(entry.project);
  const stream = fileKey(entry.workstream);
  const finished = held.filter((each) => each.feature === entry.id).map((each) => (each.kind === 'pr' ? `${prKey(each.url)} green and approved` : `built on ${each.branch}`));
  const others = entry.cards.filter((each) => each.url !== item.answer).map((each) => `“${each.title}”`);
  const tip = [`${entry.title}.`, `${STOP_WORDS[entry.state]}.`, finished.length ? `Finished and held: ${finished.join(', ')}.` : '', `Waits on this answer${others.length ? ` and on ${others.join(', ')}` : ''}.`].filter(Boolean).join(' ');
  return `<li class="held" data-tip="${esc(tip)}">
    <nav class="crumbs" aria-label="Where it sits"><a href="${zoomHref('now', project)}">${esc(fileName(project))}</a><span aria-hidden="true">/</span><a href="${zoomHref('now', project, stream)}">${esc(fileName(stream))}</a></nav>
    <a class="held-title" href="#/feature/${esc(entry.id)}">${esc(entry.title)}</a>
    ${pill(STOP_WORDS[entry.state], STOP_TONES[entry.state])}
    ${finished.length ? `<small>${esc(finished.join(' · '))}</small>` : ''}
  </li>`;
}

function earlierWords(answer) {
  const read = answer.mark === 'stands' ? 'The drive read these words against this question and found it still open.' : 'The drive has not read these words against this question yet.';
  return `<li tabindex="0" data-tip="${esc(`“${answer.words}” ${read}`)}"><q>${esc(answer.words)}</q><span class="meta">${clock(answer.at)}</span></li>`;
}

function decisionRail(item) {
  const card = item.card;
  const rail = item.rail ?? { held: [], asker: null, answers: [] };
  const index = picked?.card === item.answer ? picked.index : Math.max(0, card.options.indexOf(card.recommended));
  const tasks = item.blocks.map(({ id }) => featureOf(id)).sort((a, b) => (b.id === rail.asker?.feature) - (a.id === rail.asker?.feature));
  const links = [...(card.links ?? []), ...(card.refs ?? [])].filter((each) => !SLACK.test(each.url)).map((each) => `<li>${link(each.label, each.url)}</li>`).join('');
  const read = `${links ? `<ul class="rail-list">${links}</ul>` : ''}${pictures(card.pictures)}`;
  return `<aside class="context-rail" aria-label="About this decision">
    ${meaning(item, index)}
    <p class="rail-note"><span class="pointer">Hover or focus a choice to read it here.</span><span class="touch">Tap a choice to answer it.</span> An answer is sent ${model.undo_seconds} seconds after you pick it. While its Undo shows, Undo<span class="pointer"> or ⌘Z</span> takes it back and asks it again.</p>
    ${tasks.length ? `<div class="rail-block"><div class="eyebrow">What it holds up</div><ul class="held-list">${tasks.map((entry) => heldRow(item, entry, rail.held)).join('')}</ul></div>` : ''}
    ${rail.asker ? `<div class="rail-block"><div class="eyebrow">Asked by</div><ul class="jobs">${jobRow(rail.asker)}</ul></div>` : ''}
    ${rail.answers.length ? `<div class="rail-block"><div class="eyebrow">Your earlier words on it</div><ul class="said-list">${rail.answers.map(earlierWords).join('')}</ul></div>` : ''}
    ${read ? `<div class="rail-block"><div class="eyebrow">Read first</div>${read}</div>` : ''}
    <div class="rail-block talk-block">
      <div class="eyebrow" data-key="q">Ask about this, or answer in your own words</div>
      ${item.thread ? talkPane(item.thread, cardTs(item.answer)) : ''}
      ${sayBox(`q:${item.answer}`, item.answer, 'Ask a question, or answer in your own words', '', item.thread ?? '')}
    </div>
  </aside>`;
}

function decisionCard(item, at, total) {
  const card = item.card;
  const rows = (card.rows ?? []).map((row) => `<li>${inline(row.text)}</li>`).join('');
  const body = [card.body ? prose(card.body) : '', rows ? `<ul class="rows">${rows}</ul>` : ''].join('');
  const facts = [
    item.since ? fact(esc(`Asked ${clock(item.since)}`), `Waiting on you for ${ago(item.since)}`) : '',
    item.holds ? fact(esc(`Holds up ${item.holds} finished`), `${item.holds} green, approved ${item.holds === 1 ? 'change or built job waits' : 'changes or built jobs wait'} on this answer`, 'warn') : '',
    item.rank_why ? fact('Why it ranks here', item.rank_why) : '',
    ...(card.facts ?? []).map((each) => fact(`${esc(each.label)}: ${inline(each.value)}`)),
  ].join('');
  return `<div class="queue-layout">
    <article class="focus-card" aria-labelledby="item-title" tabindex="-1">
      <div class="card-content">
        <div class="card-top"><span class="type-label">Decision</span>${counter(at, total)}</div>
        <h2 class="card-title" id="item-title">${inline(card.title || item.title)}</h2>
        ${body ? `<div class="card-summary">${fold(`q:${item.answer}`, body, 8, 'e')}</div>` : ''}
        ${facts ? `<div class="facts">${facts}</div>` : ''}
        ${choices(item, true)}
      </div>
    </article>
    ${decisionRail(item)}
  </div>`;
}

function stateFacts(row) {
  const [checks, checksTone] = CHECK_FACTS[row.checks] ?? CHECK_FACTS.none;
  const [review, reviewTone] = REVIEW_FACTS[row.review] ?? ['No review yet', ''];
  return fact(esc(checks), '', checksTone) + fact(esc(review), '', reviewTone);
}

function prFacts(item, row, read) {
  const pr = read?.pr;
  return [
    stateFacts(row),
    pr ? fact(`<span class="add">+${pr.additions}</span> <span class="del">−${pr.deletions}</span> · ${pr.changedFiles} ${pr.changedFiles === 1 ? 'file' : 'files'}`, `${pr.headRefName} into ${pr.baseRefName}`) : '',
    pr?.isDraft ? fact('Draft', '', 'warn') : '',
    row.head ? fact(`<code>${esc(row.head.slice(0, 7))}</code>`, `The commit this card was drawn for, ${row.head}`) : '',
    item.since ? fact(esc(`Asked ${clock(item.since)}`), `Waiting on you for ${ago(item.since)}`) : '',
    item.rank_why ? fact('Why it ranks here', item.rank_why) : '',
    row.lands ? fact(esc(`Lands once ${row.lands}`), '', 'good') : '',
    row.blocks ? fact(esc(row.blocks), '', 'bad') : '',
    row.changed ? fact('Changed since your tap', row.changed, 'warn') : '',
  ].join('');
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
    const fields = PR_FIELDS.filter(([key]) => key !== 'refresher' && key !== 'show_me' && row[key]).map(([key, label]) => `<dt>${label}</dt><dd>${prose(row[key], pr)}</dd>`).join('');
    const whole = read?.pr ? `<details class="whole"><summary>The whole description</summary>${prose(read.pr.body, pr)}</details>` : '';
    return `${fields ? `<dl class="fields">${fields}</dl>` : '<p class="meta">Its description names no surprise, risk or departure from the plan.</p>'}${whole}`;
  }
  return showMe(row, pr, read);
}

function stackRow(row, i, on, key) {
  const name = `<span class="n">${i + 1}</span><span class="name">${esc(`${prKey(row.url)} · ${bare(row.title)}`)}</span>`;
  if (row.state !== 'open') return `<li class="gone">${name}${fact(esc(GONE_ROWS[row.state] ?? row.state), '', GONE_ROWS[row.state] === 'Landed' ? 'good' : '')}</li>`;
  const lands = row.lands ? fact('Approve lands it', `It lands once ${row.lands}`, 'good') : row.blocks ? fact(esc(row.blocks), '', 'bad') : '';
  return `<li><button class="row${on ? ' on' : ''}" data-row="${i}" data-item="${esc(key)}" aria-current="${on}">${name}<span class="row-facts">${stateFacts(row)}${lands}</span></button></li>`;
}

function approveWords(entry, row) {
  if (entry?.approve_waits === '') return 'Approve queues it for merge at once';
  if (entry?.approve_waits) return `After your Approve it still waits: ${entry.approve_waits}`;
  return `Lands once ${row?.lands || 'you approve'}`;
}

function landing(item, rows, entry) {
  if (!item.card) return `<li><b>${esc(prKey(item.url))}</b> ${esc(approveWords(entry).replace(/^./, (c) => c.toLowerCase()))}</li>`;
  return rows.map((row) => {
    const said = row.state !== 'open' ? GONE_ROWS[row.state] ?? row.state : row.lands ? `lands once ${row.lands}` : row.blocks ? `does not land on Approve: ${row.blocks}` : 'Reading its card…';
    return `<li><b>${esc(prKey(row.url))}</b> ${esc(said)}</li>`;
  }).join('');
}

function spots(html) {
  const drawn = Object.assign(document.createElement('template'), { innerHTML: html }).content;
  return [...drawn.querySelectorAll(SPOTS)].map((el, i) => {
    const lead = el.previousElementSibling?.matches('p') ? el.previousElementSibling.textContent.trim() : '';
    const kind = el.querySelector('[data-visual]')?.dataset.visual;
    return el.textContent.trim() || el.dataset.caption || lead || (kind && (VISUAL_WORDS[kind] ?? `${kind.replace(/^code:/, '')} code`)) || `Picture ${i + 1}`;
  });
}

function jumps(key, row, pr, read) {
  const shown = spots(showMe(row, pr, read)).map((label, i) => `<li><button class="linkish" data-spot="${i}" data-item="${esc(key)}">${esc(label)}</button></li>`).join('');
  const words = [row.summary, read?.pr?.show_me, ...PR_FIELDS.map(([field]) => row[field])].join('\n');
  const cites = [...new Map([...words.matchAll(SOURCE_CITE)].map(([, , path, from]) => [`${path}:${from}`, { path, line: from }])).values()];
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

function prState(entry) {
  const facts = [
    entry.conflict ? fact(esc(`Conflicts with ${entry.base}`), `GitHub cannot merge ${entry.branch} until it is rebased onto ${entry.base}`, 'bad') : '',
    entry.draft ? fact('Draft', '', 'warn') : '',
    entry.landing ? fact(esc(`Lander: ${entry.landing}`)) : '',
  ].join('');
  const failed = entry.failed.map((name) => `<li>${link(name, `${entry.url}/checks`)}</li>`).join('');
  const stack = entry.stack.length > 1 ? entry.stack.map((key) => `<li>${key === entry.key ? `<b>${esc(key)}</b>, this one` : `<button class="linkish" data-pr="${esc(key)}">${esc(key)}</button>`}</li>`).join('') : '';
  return `${facts ? `<div class="facts">${facts}</div>` : ''}${failed ? `<p class="spot-head">Failing checks</p><ul class="rail-list">${failed}</ul>` : ''}${stack ? `<p class="spot-head">Its stack, bottom first</p><ol class="rail-list">${stack}</ol>` : ''}`;
}

function serves(entry) {
  const task = featureOf(entry.feature);
  const html = [
    task ? `<p><a href="#/feature/${esc(task.id)}">${esc(task.title)}</a></p>` : entry.feature_title ? `<p>${esc(entry.feature_title)}</p>` : '',
    entry.parts.length ? `<ul class="rail-list">${entry.parts.map((part) => `<li>${esc(part)}</li>`).join('')}</ul>` : '',
    entry.job_said ? `<p class="spot-head">Its builder, ${ago(entry.job_at)} ago</p><p>${esc(entry.job_said)}</p>` : '',
  ].join('');
  return html ? `<div class="rail-block"><div class="eyebrow">What it serves</div>${html}</div>` : '';
}

function prRail(item, key, rows, row, pr, head, read, inline) {
  const entry = model.review.find((each) => each.key === pr);
  return `<aside class="context-rail pr-rail" aria-label="About this change">
    <div class="rail-block"><div class="eyebrow">Attached to this change <code>${esc(head.slice(0, 7))}</code></div><div class="facts">${stateFacts(row)}</div>${entry ? prState(entry) : ''}<p class="spot-head">What Approve lands</p><ul class="rail-list">${landing(item, rows, entry)}</ul></div>
    ${entry ? serves(entry) : ''}
    <div class="rail-block"><div class="eyebrow">Jump to a point in the change</div>${jumps(key, row, pr, read)}</div>
    <div class="rail-block"><div class="eyebrow">Question or line comment</div>${noteBox(key, pr)}</div>
    <div class="rail-block talk"><div class="eyebrow"${inline ? '' : ' data-key="q"'}>Tell the drive what to change</div>${item.thread ? talkPane(item.thread, cardTs(item.answer)) : ''}
      ${sayBox(item.answer ? `t:${item.answer}` : `r:${pr}`, item.answer ?? '', 'Ask the drive, or tell Claude what to change', item.answer ? '' : `About ${row.url}`, item.thread ?? '')}
    </div>
  </aside>`;
}

function prCard(item, at, total, inline = false) {
  const key = noticeKey(item);
  const rows = (item.card?.prs ?? [{ url: item.url, title: item.title }]).map((row, i) => ({ ...prReads.get(prKey(row.url))?.pr?.row, ...item.prs?.[i], ...row }));
  const chosen = chosenRow(item);
  const row = rows[chosen];
  const pr = prKey(row.url);
  const head = model.review.find((each) => each.key === pr)?.head ?? row.head ?? '';
  const read = prReads.get(pr);
  const tab = prTabs.get(key) ?? 'show';
  const title = item.card ? bare(item.card.title.replace(/^\w+: approve (.*)\?$/, '$1')).replace(/^./, (c) => c.toUpperCase()) : item.title;
  const stack = rows.length > 1 ? `<ol class="stack" aria-label="The PRs on this card, in the order they land">${rows.map((each, i) => stackRow(each, i, i === chosen, key)).join('')}</ol>` : '';
  const tabs = PR_TABS.map(([id, label]) => `<button role="tab" class="${id === tab ? 'on' : ''}" aria-selected="${id === tab}" data-prtab="${id}" data-item="${esc(key)}">${label}</button>`).join('');
  const panes = PR_TABS.map(([id]) => `<div class="pr-pane ${id}" role="tabpanel"${id === tab ? '' : ' hidden'}>${prPane(id, row, pr, head, read, inline)}</div>`).join('');
  return `<div class="pr-layout" data-read="${esc(pr)}" data-head="${esc(head)}">
    <article class="focus-card pr-card" aria-labelledby="item-title" tabindex="-1">
      <div class="card-content">
        <div class="card-top"><span class="type-label">Pull request <a href="${esc(row.url)}" target="_blank" rel="noopener" data-github data-tip="Open it on GitHub"${inline ? '' : ' data-key="o"'}>${esc(pr)}</a></span>${inline ? '' : counter(at, total)}</div>
        <h2 class="card-title" id="item-title">${esc(title)}</h2>
        ${stack}
        ${row.summary ? `<div class="card-summary">${prose(row.summary, pr)}</div>` : ''}
        ${row.refresher ? `<div class="card-summary">${fold(`r:${key}:${pr}`, prose(row.refresher, pr), 3, inline ? '' : 'e')}</div>` : ''}
        <div class="facts">${prFacts(item, row, read)}</div>
      </div>
      <div class="pr-tabs" role="tablist" aria-label="About this change">${tabs}</div>
      ${panes}
      <div class="pr-actions">${choices(item, !inline)}<span class="meta">${esc(item.untapped ?? approveWords(model.review.find((each) => each.key === pr), row))}</span></div>
    </article>
    ${prRail(item, key, rows, row, pr, head, read, inline)}
  </div>`;
}

function lateCard(item, at, total) {
  return `<article class="focus-card late" aria-labelledby="item-title">
    <div class="card-content">
      <div class="card-top"><span class="type-label">Late</span>${counter(at, total)}</div>
      <h2 class="card-title" id="item-title">${esc(item.title)}</h2>
      <div class="facts">${fact(esc(`Promised ${clock(item.due)}`), `${ago(item.due)} late`, 'bad')}${item.since ? fact(esc(`Asked ${clock(item.since)}`)) : ''}</div>
      ${item.detail ? `<p class="card-summary">${esc(item.detail)}</p>` : ''}
    </div>
    <div class="ask-box">${sayBox(`l:${noticeKey(item)}`, '', 'Ask where it is, or change it', `About "${item.title}"`)}</div>
  </article>`;
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
  const shown = item ? { question: decisionCard, tap: prCard, late: lateCard }[item.kind](opened(item), at, list.length) : done;
  return `<div class="page-head">
      <h1 class="h1">${list.length ? `${list.length} ${list.length === 1 ? 'thing waits' : 'things wait'} on you` : 'Your queue'}</h1>
      <nav class="subtabs" aria-label="Show">${filters}</nav>
      ${scopeChips(filter)}
    </div>
    ${unread}
    <div class="queue-shell${list.length > 1 ? '' : ' alone'}">${list.length > 1 ? queueList(list, at, filter) : ''}${shown}</div>
    <footer class="qfoot"><span>As of ${clock(model.at)}</span><span><kbd>j</kbd><kbd>k</kbd> move <kbd>1–9</kbd> answer <kbd>a</kbd> approve <kbd>h</kbd> hold <kbd>o</kbd> GitHub <kbd>q</kbd> ask <kbd>e</kbd> explain <kbd>f</kbd> filter <kbd>[</kbd><kbd>]</kbd> project <kbd>⌘Z</kbd> undo <button type="button" class="linkish" data-keys><kbd>?</kbd> every key</button></span></footer>`;
}

function queueList(list, at, filter) {
  return `<ol class="queue-list" aria-label="Everything here, most urgent first">${list.map((item, i) => {
    const tip = [item.title, item.rank_why, [fileName(fileKey(item.project)), fileName(fileKey(item.workstream))].join(' / '), item.since ? `Waiting ${ago(item.since)}` : ''].filter(Boolean).join('\n');
    return `<li class="qrow${i === at ? ' on' : ''}">
      <a class="qrow-main" href="${esc(queueHref(filter, item))}" data-queue="${esc(noticeKey(item))}" data-tip="${esc(tip)}"${i === at ? ' aria-current="true"' : ''}><span class="type-label">${LABEL[item.kind]}</span><span class="qrow-title">${esc(bare(item.title))}</span><small>${esc(item.rank_why)}</small></a>
      <span class="qrow-acts">${rowActions(item, filter)}</span>
    </li>`;
  }).join('')}</ol>`;
}

function rowActions(item, filter) {
  const answered = answerKey(item);
  const options = item.kind === 'late' ? [] : item.card?.options ?? TAP_LABELS;
  const picks = options.map((option, i) => `<button class="mini${option === 'Approve' ? ' go' : ''}" data-card="${esc(answered)}" data-label="${esc(option)}" data-tip="${esc(option)}">${item.kind === 'tap' ? esc(option) : i + 1}</button>`);
  const task = item.tasks.find(Boolean);
  return [
    ...picks,
    `<button class="mini" data-ask-about="${esc(noticeKey(item))}" data-href="${esc(queueHref(filter, item))}">Ask</button>`,
    task ? `<a class="mini" href="#/feature/${esc(task)}">Open task</a>` : '',
    item.pr ? `<a class="mini" href="${esc(item.pr)}" target="_blank" rel="noopener">GitHub ↗</a>` : '',
  ].join('');
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
  const side = task ? '' : `<aside class="now-side" aria-label="What is happening here">${keys.length ? scopeWork(scope, project, stream) : ''}${story(scope)}</aside>`;
  return `<div class="now-head"><div class="eyebrow">Now</div>
    <h1 class="h1 zoom-title">${esc(keys.length ? fileName(keys.at(-1)) : 'All projects')}</h1>
    <p class="lead"${keys.length ? ' hidden' : ''}>${keys.length ? '' : esc(nowHeadline())}</p></div>
    ${nowCanvas(tree, project, stream, task, side)}`;
}

function scopeWork(scope, project, stream) {
  const asks = shownQueue('all', project, stream ?? null);
  const queued = new Set(asks.map((item) => item.pr).filter(Boolean));
  const open = new Map(scope.flatMap((entry) => entry.prs.filter((pr) => pr.state === 'open' && !queued.has(pr.url)).map((pr) => [pr.url, pr])));
  const prs = model.review.filter((entry) => open.has(entry.url));
  const rows = prs.map((entry) => `<li class="qrow"><button class="qrow-main linkish" data-pr="${esc(entry.key)}" data-tip="${esc([entry.title, prSaid(open.get(entry.url)), entry.job_said].filter(Boolean).join('\n'))}"><span class="type-label">${LABEL.tap}</span><span class="qrow-title">${esc(bare(entry.title))}</span><small>${esc(prSaid(open.get(entry.url)))}</small></button></li>`);
  return `<section class="scope-work">
    <div class="eyebrow">Here <span class="count">${counted(asks.length, 'thing waits', 'things wait')} on you · ${counted(prs.length, 'other open PR')}</span></div>
    ${asks.length ? queueList(asks, -1, 'all') : ''}${rows.length ? `<ol class="queue-list">${rows.join('')}</ol>` : ''}
    <a class="more" href="#/queue/all${scopeQuery(project, stream ?? null)}">Queue filtered to this ›</a>
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
      <div><div class="eyebrow">Pages</div><h1 class="h1">Live pages built for your orders</h1><p class="lead">Each page shows its tasks as they move, and anything you answer or approve on one waits ${model.undo_seconds}s for your Undo.</p></div>
      <nav class="subtabs" aria-label="Show">${PAGE_FILTERS.map(([id, label]) => chip(label, href({ show: id }), id === show)).join('')}</nav>
    </div>
    ${scopes}${subs}
    ${rows.length ? `<ol class="page-list">${rows.join('')}</ol>` : `<p class="empty">${model.pages.length ? 'No page here.' : 'No page yet. A page job puts one here with <code>drive page add</code>.'}</p>`}`;
}

function pageScreen(page) {
  if (!page) return gone('This page', 'pages');
  return `<div class="page-head">
      <div><div class="eyebrow">Page · ${esc([fileName(fileKey(page.project)), fileName(fileKey(page.workstream))].join(' / '))}</div><h1 class="h1">${esc(page.title)}</h1><p class="lead">${esc(page.goal)}</p></div>
      <a class="map-btn" href="#/feature/${esc(page.orders[0])}">Open its task ›</a>
    </div>
    <iframe class="page-frame" data-page="${esc(page.slug)}" sandbox="allow-scripts" src="/pages/${encodeURIComponent(page.slug)}/" title="${esc(page.title)}"></iframe>`;
}

function pageSlice(page) {
  const orders = new Set(page.orders);
  const prs = new Set(page.prs);
  const actable = (item, card, options) => ({ ...item, answer: card, options, answered: answers.get(card) ?? null });
  return {
    page,
    at: model.at,
    undo_seconds: model.undo_seconds,
    features: model.status.flatMap((group) => group.features).filter((entry) => orders.has(entry.id)),
    prs: model.review.filter((entry) => orders.has(entry.feature) || prs.has(entry.key)).map((entry) => actable(entry, entry.answer ?? `tap:${entry.key}`, entry.card?.options ?? ['Approve', 'Hold'])),
    questions: model.queue.filter((item) => item.kind === 'question' && item.tasks.some((id) => orders.has(id))).map((item) => actable(item, item.answer, item.card.options)),
    story: slice(`/api/story?${new URLSearchParams({ ids: page.orders.join(',') })}`)?.moments ?? [],
    asked: sent.get(`page:${page.slug}`) ?? null,
  };
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
  if (!page || !navigator.userActivation.isActive) return;
  const { type, answer: card, label, text } = event.data;
  const shown = pageSlice(page);
  if (type === 'drive:answer' && [...shown.prs, ...shown.questions].some((item) => item.answer === card && item.options.includes(label))) return answer(card, label);
  if (type === 'drive:ask' && typeof text === 'string') say(`page:${page.slug}`, null, text, page.title);
}

function nowHeadline() {
  const { counts } = model;
  const { picks, taps, late, oldest } = waiting();
  const owed = [picks && counted(picks, 'decision'), taps && `${counted(taps, 'PR')} to tap`, late && `${counted(late, 'promised line')} past due`].filter(Boolean);
  const loose = model.queue.filter((item) => item.kind === 'question' && !index().owner.has(item.answer)).length;
  const moving = `${counted(counts.live, 'feature')} went live in the last day, ${counts.running} ${counts.running === 1 ? 'is' : 'are'} moving and ${counts.blocked} ${counts.blocked === 1 ? 'is' : 'are'} blocked`;
  if (!owed.length) return `${moving}; nothing waits on you.`;
  const list = owed.length > 1 ? `${owed.slice(0, -1).join(', ')} and ${owed.at(-1)}` : owed[0];
  return `${moving}; ${list} ${picks + taps + late === 1 ? 'waits' : 'wait'} on you${oldest ? `, the oldest since ${clock(oldest)} (${ago(oldest)})` : ''}${loose ? `. ${loose} of the decisions ${loose === 1 ? 'holds' : 'hold'} no open task` : ''}.`;
}

function waiting() {
  const kinds = Map.groupBy(model.queue, (item) => item.kind);
  const since = model.queue.map((item) => (item.kind === 'late' ? item.due : item.since)).filter(Boolean).sort((a, b) => new Date(a) - new Date(b));
  return { picks: kinds.get('question')?.length ?? 0, taps: kinds.get('tap')?.length ?? 0, late: kinds.get('late')?.length ?? 0, oldest: since[0] };
}

function gone(what, screen = 'now') {
  return `<p class="empty">${what} has no open work on the dashboard now. <a href="#/${screen}">All projects</a></p>`;
}

function trail() {
  const { screen, path } = route();
  const at = { feature: 'now', file: 'work' }[screen] ?? (SCREENS.some(([name]) => name === screen) ? screen : 'queue');
  const [, href, label] = SCREENS.find(([name]) => name === at);
  const zooms = (where, keys) => keys.map((key, i) => [fileName(key), zoomHref(where, ...keys.slice(0, i + 1))]);
  const entry = screen === 'feature' ? featureOf(path[0]) : screen === 'now' && path[2] ? featureOf(path[2]) : null;
  const steps = [[model.drive, '#/'], [label, href]];
  if (screen === 'now' || screen === 'work') steps.push(...zooms(screen, path.slice(0, 2)));
  if (screen === 'feature' && entry) steps.push(...zooms('now', [fileKey(entry.project), fileKey(entry.workstream)]));
  if (entry) steps.push([entry.title, location.hash]);
  if (screen === 'work' && path[2]) steps.push([path[2], location.hash]);
  if (screen === 'history' && path[0]) steps.push([dayName(path[0]), historyHref(path[0])]);
  if (screen === 'pages' && path[0]) steps.push([model.pages.find((page) => page.slug === path[0])?.title ?? path[0], location.hash]);
  if (screen === 'file') steps.push([`${path[0]} ${(FILE_WORDS[path[1]] ?? 'file').toLowerCase()}`, location.hash]);
  return steps.map(([name, to], i) => (i === steps.length - 1 ? `<strong aria-current="page">${esc(name)}</strong>` : `<a href="${esc(to)}">${esc(name)}</a>`)).join('<span aria-hidden="true">/</span>');
}

function nowCanvas(tree, project, stream, task, side) {
  const level = stream !== undefined ? 'stream' : project !== undefined ? 'project' : 'all';
  const regions = [...tree].map(([key, streams]) => region(key, streams, project, stream)).join('');
  const live = model.counts.live;
  return `<section class="canvas now" data-level="${level}" style="--cols:${tree.size}" aria-label="Projects, workstreams and tasks">
    <div class="canvas-bar"><span class="meta">${level === 'all' ? 'Click a project to zoom in' : level === 'project' ? 'Click a workstream to see its tasks' : 'Esc zooms out'}</span>
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
      <span class="zoom-mark" aria-hidden="true">${key === NONE ? '?' : esc([...fileName(key)][0].toUpperCase())}</span>
      <span class="region-name"><b>${esc(fileName(key))}</b><small>${esc(`${counted(streams.size, 'workstream')}: ${[...streams.keys()].map(fileName).join(', ')}`)}</small></span>
      ${needs(list)}
      <span class="region-open"><b>${open}</b><small>open of ${list.length}</small>${stateBar(list)}</span>
    </a>
    <div class="streams" style="--n:${Math.min(streams.size, 2)}"${here ? '' : ' inert'}>${[...streams].map(([skey, slist]) => streamBox(key, skey, slist, here && skey === stream)).join('')}</div>
  </div>`;
}

function streamBox(project, key, list, here) {
  const asks = asksOf(list).sort((a, b) => new Date(a.asked) - new Date(b.asked));
  const tasks = list.toSorted((a, b) => RANK.indexOf(a.state) - RANK.indexOf(b.state) || new Date(b.updated) - new Date(a.updated));
  return `<div class="stream${key === NONE ? ' unfiled' : ''}${here ? ' here' : ''}" data-cam-key="${esc(project)}/${esc(key)}" data-href="${zoomHref('now', project, key)}">
    <a class="stream-head" href="${zoomHref('now', project, key)}" data-tip="${esc(groupTip(fileName(key), list))}"${here ? ' tabindex="-1"' : ''}><b>${esc(fileName(key))}</b><small>${esc(counted(list.length, 'task'))} · ${openOf(list)}</small>${needs(list)}${stateBar(list)}</a>
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

function openOf(list) {
  return `${list.filter((entry) => entry.state !== 'live').length} open of ${list.length}`;
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
    <h2 class="sheet-title">${esc(entry.title)}</h2>
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
  const sheet = view.querySelector('.task-sheet');
  const side = sheet ?? (narrow.matches ? null : view.parentElement.querySelector(':scope > .now-side'));
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
  } else if (`${viewport.dataset.at}#${viewport.dataset.view}` !== nowAt) {
    const region = held && regionOf(held.key);
    if (region) {
      const box = boxOf(region, nowCamera.world);
      const k = Math.min(held.rect.width / box.w, held.rect.height / box.h);
      const origin = viewport.getBoundingClientRect();
      nowCamera.at = { k, x: box.x + box.w / 2 - (held.rect.left + held.rect.width / 2 - origin.left) / k, y: box.y - (held.rect.top - origin.top) / k };
      aim(nowCamera);
    }
    flyTo(nowCamera, fitOf(viewport));
  }
  nowAt = `${viewport.dataset.at}#${viewport.dataset.view}`;
}

function regionOf(key) {
  return key && $(`#main .canvas.now .region[data-cam-key="${CSS.escape(key)}"]`);
}

function heldRegion() {
  const key = route().path[0] ?? $('#main .canvas.now .viewport')?.dataset.at.split('/')[0];
  const region = regionOf(key);
  return region && { key, rect: region.getBoundingClientRect() };
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
    <span class="task-body"><b>${esc(entry.title)}</b>${entry.now ? `<small>${esc(entry.now.replace(REPORT_WORD, '').replace(PR_LINK, '#$1'))}</small>` : ''}</span>
    <span class="task-facts">${pill(STOP_WORDS[entry.state], STOP_TONES[entry.state])}${facts ? `<small>${esc(facts)}</small>` : ''}</span>
    <span class="arrow" aria-hidden="true">›</span>
  </a>`;
}

function momentText(entry) {
  if (entry.kind === 'heard') return `<b class="what">${esc(entry.detail)}</b> ${reportLine(entry.text)}`;
  const text = FEATURE_KINDS.has(entry.kind) ? featureLink(entry.feature, entry.text) : link(entry.text, entry.url);
  return `<b class="what">${DECIDED[entry.kind] ? `${DECIDED[entry.kind]} ${esc(entry.detail)} on` : HAPPENED[entry.kind]}</b> ${text}`;
}

function story(scope, title = 'The live story') {
  const read = slice(`/api/story${scope ? `?${new URLSearchParams({ ids: scope.map((entry) => entry.id).join(',') })}` : ''}`);
  const told = read?.ok ? read.moments : [];
  const tip = (entry) => (featureOf(entry.feature) ? ` data-tip="${esc(taskTip(featureOf(entry.feature)))}"` : '');
  return `<aside class="story" aria-label="${title}">
    <div class="eyebrow">${title}</div>
    ${!read?.ok ? notRead(read) : told.length ? `<ol>${told.map((entry) => `<li${tip(entry)}><time>${clock(entry.at)}</time><p>${momentText(entry)}</p></li>`).join('')}</ol>` : '<p class="meta">Nothing has happened here yet.</p>'}
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
  const item = entry && {
    kind: 'tap', url: entry.url, title: entry.title, since: entry.since, answer: entry.answer, thread: entry.thread, card: null,
    untapped: entry.group === 'decided' ? `You tapped ${entry.tap}; ${entry.detail}.` : entry.group === 'you' ? null : `It does not wait on your tap yet: ${entry.detail}.`,
    prs: [{ state: 'open', checks: entry.checks, review: entry.review }],
  };
  const html = `<div class="dr-bar"><b>${esc(reviewing)}</b><span class="acts"><a class="ext" href="https://github.com/${esc(repo)}/pull/${esc(number)}" target="_blank" rel="noopener">GitHub ↗</a><button class="linkish" data-close>Close</button></span></div>
    ${item ? prCard(item, 0, 1, true) : `<p class="meta dr-gone">${esc(reviewing)} is no longer an open PR of this drive.</p>`}`;
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
  const typed = said.from_claude && said.text.startsWith(TYPED_MARK);
  const who = typed ? 'You, on the dashboard' : said.from_claude ? esc(said.user_name) : 'You';
  return `<div class="msg${said.from_claude && !typed ? '' : ' you'}">
    <div class="who">${who} · ${slackTime(said.ts)}</div>
    <div class="said-text">${prose(typed ? said.text.slice(TYPED_MARK.length) : said.text)}</div>
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
  return featureOf(id) ? `<a href="#/feature/${esc(id)}">${esc(text)}</a>` : esc(text);
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
      <div class="title">${esc(job.goal)} ${stall}</div>
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
  tip.innerHTML = lines.length ? `<header>${esc(head)}</header>${lines.map((line) => `<p>${esc(line)}</p>`).join('')}` : `<p>${esc(head)}</p>`;
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
  const index = Number(choice.dataset.preview);
  if (picked?.card === choice.dataset.card && picked.index === index) return;
  picked = { card: choice.dataset.card, index };
  const item = model.queue.find((each) => each.answer === choice.dataset.card);
  const rail = document.querySelector('#main [data-means]');
  if (item && rail) rail.outerHTML = meaning(opened(item), index);
  for (const each of choice.parentElement.querySelectorAll('.choice')) each.classList.toggle('on', each === choice);
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
  return `<li class="happened" data-kind="said">
    <span class="time">${timeOf(entry.at)}</span>
    <span class="dot"></span>
    <div class="body">
      <div><b class="what">${HAPPENED.said}</b></div>
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
      ${entry.detail && !DECIDED[entry.kind] ? `<div class="meta">${esc(entry.detail)}</div>` : ''}
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
    <h1 class="h1 zoom-title">${esc(entry.title)}</h1>
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
    entry.due ? ['Promised', `${clock(entry.due)}${entry.late ? ' <b class="red">late</b>' : ''}`] : null,
    entry.proof ? ['Proof', inline(entry.proof)] : null,
    entry.note ? ['Note', esc(entry.note)] : null,
  ].filter(Boolean);
  const shipped = entry.parts.filter((part) => part.delivered).length;
  const merged = entry.prs.filter((pr) => pr.state === 'merged').length;
  return `<div class="kick">${pill(HEADS[entry.state], STOP_TONES[entry.state])}${entry.holdup ? `<span class="holdup">${holdupLine(entry.holdup)}${entry.holdup.at ? ` <small class="meta">${clock(entry.holdup.at)}</small>` : ''}</span>` : ''}</div>
      <blockquote class="said">${esc(entry.text)}</blockquote>
      <dl class="factlist">${facts.map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`).join('')}</dl>
      <h2 class="sec">What it ships <span class="count">${shipped} of ${entry.parts.length} delivered</span></h2>
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
  const said = part.delivered ? (part.posted ? 'answered in Slack' : 'delivered') : part.job ? `${part.kind} ${part.job} ${JOB_STATES[part.state].toLowerCase()}` : 'no job carries it yet';
  return `<li class="line${part.delivered ? ' done' : ''}"><span class="tick" aria-hidden="true"></span><span class="line-text">${esc(part.whole ? 'All of it, in one piece' : part.name)}</span><small class="meta">${esc(said)}${part.at ? ` · ${clock(part.at)}` : ''}</small></li>`;
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
  const { screen, id } = route();
  document.title = `${model.drive}: ${model.counts.queue} waiting on you`;
  const at = { feature: 'now', file: 'work' }[screen] ?? (SCREENS.some(([name]) => name === screen) ? screen : 'queue');
  const counts = { queue: model.counts.queue, now: model.counts.jobs, work: model.counts.lanes, history: model.counts.merged_today, pages: model.pages.filter((page) => page.live).length };
  const said = { queue: 'waiting on you', now: 'jobs running', work: 'lanes at work', history: 'merged today', pages: 'live' };
  $('#tabs').innerHTML = SCREENS.map(([name, href, label]) => `<a class="tab${name === at ? ' on' : ''}" href="${href}"${name === at ? ' aria-current="page"' : ''} data-tip="${esc([label, counts[name] ? `${counts[name]} ${said[name]}` : '', name === 'now' && model.incidents.length ? `${counted(model.incidents.length, 'incident')} open` : ''].filter(Boolean).join('\n'))}">${icon(name)}<span>${label}</span>${counts[name] ? `<b class="count${name === 'queue' ? '' : ' soft'}" aria-label="${counts[name]} ${said[name]}">${counts[name]}</b>` : ''}${name === 'now' && model.incidents.length ? `<i class="alarm" aria-label="${counted(model.incidents.length, 'incident')} open"></i>` : ''}</a>`).join('');
  morphChildren($('#crumb'), Object.assign(document.createElement('nav'), { innerHTML: trail() }));
  const raised = strip();
  morphChildren($('#alerts'), Object.assign(document.createElement('div'), { innerHTML: raised }));
  $('#alerts').hidden = !raised;
  morphChildren($('#incidents'), Object.assign(document.createElement('div'), { innerHTML: incidentList() }));
  morphChildren($('#bug-reports'), Object.assign(document.createElement('div'), { innerHTML: bugReportList() }));
  const screens = { queue: () => queueScreen(id), now: nowScreen, work: workScreen, history: historyScreen, pages: () => pagesScreen(id), feature: () => featureScreen(id), file: () => fileScreen(route().path) };
  const open = new Set(model.review.map((entry) => entry.key));
  for (const [key, frame] of slotted) {
    if (open.has(key)) continue;
    frame.remove();
    slotted.delete(key);
    reviews.delete(key);
  }
  const html = (screens[screen] ?? queueScreen)();
  const arrived = pageKey() !== shownHash;
  const held = !arrived && screen === 'now' && heldRegion();
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
  if (old.matches('.visual-host.drawn') && old.dataset.visual === made.dataset.visual && old.dataset.source === made.dataset.source) return;
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
  kids.forEach((kid, i) => (old.childNodes[i] ? morph(old.childNodes[i], kid) : old.append(kid)));
  while (old.childNodes.length > kids.length) old.lastChild.remove();
}

function keepPlace() {
  history.replaceState({ ...history.state, y: scrollY }, '');
}

function deployed() {
  const busy = [...toasts.values()].some((shown) => shown.state === 'held') || [...sent.values()].some((was) => was.busy) || Boolean(document.activeElement?.closest('textarea, input'));
  if (!busy) return reloadPage();
  outdated = true;
  fresh();
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
  restored.delete(card);
  answers.set(card, { label });
  toast(card, { label, title: asked.title, until: Date.now() + model.undo_seconds * 1000, state: 'held' });
  render();
  try {
    const [path, body] = tapped ? ['/api/tap', { pr: tapped.key, label, head: tapped.head }] : ['/api/answer', { card, label }];
    const reply = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const said = await reply.json();
    if (!reply.ok) throw new Error(said.error);
    if (said.undone) {
      answers.delete(card);
      queueAt = noticeKey(asked);
      toast(card, { ...toasts.get(card), state: 'undone' });
    } else {
      answers.set(card, { label: said.label || label, at: said.at || new Date().toISOString(), done: true });
      toast(card, { ...toasts.get(card), state: 'sent', undoUntil: said.undo_until });
    }
  } catch (error) {
    answers.set(card, { label, error: error.message });
    toast(card, { ...toasts.get(card), state: 'failed', error: error.message });
  }
  render();
}

async function undo(card, about = toasts.get(card)) {
  toast(card, { ...about, state: 'undoing' });
  const said = await ask('/api/undo', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ card }) });
  if (!said.ok) return toast(card, { ...about, state: 'late', error: said.error });
  if (!said.restored) return;
  answers.delete(card);
  restored.add(card);
  if (route().screen === 'queue') history.replaceState(history.state, '', queueHref(filterOf(route().id) === 'tap' ? 'all' : filterOf(route().id), { answer: card }));
  toast(card, { ...about, state: 'restored' });
  render();
}

const TOAST_SAID = {
  held: (shown) => `Sending in ${Math.max(0, Math.ceil((shown.until - Date.now()) / 1000))}s`,
  undoing: () => 'Taking it back…',
  undone: () => 'Undone. It waits on you again.',
  sent: (shown) => (shown.undoUntil ? `Sent. Undo until ${timeOf(shown.undoUntil)}` : 'Sent.'),
  restored: () => 'Restored. This is being asked again.',
  late: (shown) => shown.error,
  failed: (shown) => `Not sent: ${shown.error}`,
};

function undoable(shown) {
  return shown.state === 'held' || (shown.state === 'sent' && Date.parse(shown.undoUntil) > Date.now());
}

function toast(card, shown) {
  toasts.set(card, shown);
  const lasts = shown.state === 'sent' && shown.undoUntil ? Date.parse(shown.undoUntil) - Date.now() : 5000;
  if (!['held', 'undoing'].includes(shown.state)) setTimeout(() => toasts.get(card) === shown && toasts.delete(card) && drawToasts(), lasts);
  drawToasts();
}

function drawToasts() {
  $('#toasts').innerHTML = [...toasts].map(([card, shown]) => `<div class="toast ${shown.state}" role="status">
    <span><b>${esc(shown.label)}</b> · ${esc(shown.title)}</span>
    <span class="meta">${esc(TOAST_SAID[shown.state](shown))}</span>
    ${undoable(shown) ? `<button class="btn" data-undo="${esc(card)}">Undo</button>` : ''}
    ${shown.state === 'restored' ? `<a class="btn" href="${queueHref('all', { answer: card })}">Open it</a>` : ''}
    <button class="toast-x" data-dismiss="${esc(card)}" aria-label="Dismiss">×</button>
  </div>`).join('');
}

async function say(key, under, words, about, thread) {
  if (!words.trim() || sent.get(key)?.busy) return;
  sent.set(key, { busy: true });
  render();
  try {
    const reply = await fetch('/api/say', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: about ? `${about}: ${words.trim()}` : words.trim(), under }) });
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
  return item.answer ?? `${item.kind}:${item.title}:${item.due ?? ''}`;
}

function notify() {
  const fresh = model.queue.filter((item) => item.kind !== 'late' && heard && !heard.has(noticeKey(item)));
  heard = new Set(model.queue.map(noticeKey));
  noticeButton();
  if (!fresh.length || !window.isSecureContext || !('Notification' in window) || Notification.permission !== 'granted') return;
  const [first] = fresh;
  const title = fresh.length === 1 ? `${LABEL[first.kind]}: ${first.title}` : `${fresh.length} new things wait on you`;
  const notice = new Notification(title, { body: fresh.length === 1 ? first.detail : fresh.map((item) => item.title).join('\n'), tag: `drive-${model.drive}`, requireInteraction: fresh.some((item) => item.kind !== 'late') });
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
    if (model && next.code !== model.code) return deployed();
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
  if (touched) {
    const tipped = !event.target.closest('a, button') && event.target.closest('[data-tip]');
    if (tipped) return $('#srctip').dataset.shows === `tip:${tipped.dataset.tip}` ? hideSource() : showTip(tipped);
    hideSource();
  }
  const undone = event.target.closest('button[data-undo]');
  if (undone) return undo(undone.dataset.undo, toasts.get(undone.dataset.undo) ?? { label: undone.dataset.label, title: undone.dataset.title });
  const dismissed = event.target.closest('button[data-dismiss]');
  if (dismissed) {
    toasts.delete(dismissed.dataset.dismiss);
    return drawToasts();
  }
  const pick = event.target.closest('button[data-card]');
  if (pick) return answer(pick.dataset.card, pick.dataset.label);
  const pr = event.target.closest('button[data-pr]');
  if (pr) return openPR(pr.dataset.pr);
  const queued = event.target.closest('[data-queue]');
  if (queued) {
    queueAt = queued.dataset.queue;
    return go(queued.getAttribute('href') ?? '#/queue/all');
  }
  const asking = event.target.closest('[data-ask-about]');
  if (asking) {
    queueAt = asking.dataset.askAbout;
    history.replaceState(history.state, '', asking.dataset.href);
    render();
    return $('#main form[data-say] textarea')?.focus();
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
    return;
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
  const choices = event.target.closest('#main .choices');
  if (!choices || !picked || choices.contains(event.relatedTarget) || choices.contains(document.activeElement)) return;
  picked = null;
  render();
});
document.addEventListener('mouseover', (event) => {
  if (touched) return;
  const choice = event.target.closest('#main .choice[data-preview]');
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
  if (event.target.matches('#main .choice[data-preview]')) preview(event.target);
  if (event.target.matches('a[data-src]')) showSource(event.target);
  else if (!touched && event.target.closest('[data-tip]')) showTip(event.target.closest('[data-tip]'));
});
document.addEventListener('focusout', (event) => {
  if (event.target.closest('a[data-src], [data-tip]')) hideSource();
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
document.addEventListener('keydown', (event) => {
  touched = false;
  const box = event.target.closest?.('textarea[data-draft]');
  if (box && event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    return box.form.requestSubmit();
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault();
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
    const held = [...toasts].findLast(([, shown]) => undoable(shown));
    if (!held) return;
    event.preventDefault();
    return undo(held[0]);
  }
  const free = !event.metaKey && !event.ctrlKey && !event.altKey && !writing && !document.querySelector('dialog[open]');
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
window.addEventListener('DOMContentLoaded', () => {
  history.scrollRestoration = 'manual';
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
  setInterval(() => [...toasts.values()].some((shown) => shown.state === 'held') && drawToasts(), 1000);
  setInterval(() => {
    if (document.hidden) return;
    for (const slot of document.querySelectorAll('[data-slot]')) openReview(slot.dataset.slot, slot.dataset.head);
  }, REVIEW_KEEP_MS);
  $('#lightbox').addEventListener('close', () => {
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
