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
const DECIDED = { tap: 'You tapped', answered: 'You picked', undone: 'You took back' };
const FEATURE_KINDS = new Set(['order', 'live', 'failed']);
const HISTORY_FILTERS = [
  ['all', 'Everything'],
  ['prs', 'PRs merged'],
  ['you', 'Your asks and answers'],
  ['said', 'Sent to you'],
];
const NONE = '~';
const UNFILED = 'Unfiled';
const RANK = ['blocked', 'built', 'running', 'waiting', 'not started', 'merged', 'live'];
const PACKET_SECONDS = 9;
const ORBIT_SECONDS = 100;
const MAP_ZOOMS = [0.7, 0.85, 1, 1.2, 1.45];
const SATELLITES = 14;
const RING_LIMIT = 8;
const SWEAR_WORDS = 'damn, shit and fuck';
const QUEUE_FILTERS = [
  ['all', 'All', () => true],
  ['tap', 'PRs', (item) => item.kind === 'tap'],
  ['question', 'Decisions', (item) => item.kind === 'question'],
];
const NONE_LEFT = { tap: 'No PR waits on your tap.', question: 'No decision waits on you.' };
const LABEL = { question: 'Decision', tap: 'Pull request', late: 'Late' };
const PR_TABS = [['show', 'Show me'], ['change', 'The change'], ['context', 'Context']];
const CHECK_FACTS = { pass: ['Tests pass', 'good'], fail: ['Tests fail', 'bad'], pending: ['Tests running', 'warn'], none: ['No CI', ''] };
const REVIEW_FACTS = { approved: ['Review approved', 'good'], stale: ['Approved on an older head', ''], changes: ['Changes requested', 'bad'] };
const TALK_FOLLOW_MS = 15 * 60 * 1000;
const FRAMES_KEPT = 4;
const GONE_ROWS = { merged: 'Landed', live: 'Landed', closed: 'Closed without merging', dropped: 'Dropped from the drive' };
const SPOTS = 'h4.hd, .shot, figure.visual';
const SLICES_KEPT = 40;
const JUMP_ROWS = 40;
const KEY_SHEET = [
  ['Anywhere', [[['⌘K'], 'Jump to a screen, question, pull request, task or job'], [['?'], 'Show these keys'], [['Esc'], 'Close what is open, or step back up a level']]],
  ['Your queue', [[['j', '→'], 'Next card'], [['k', '←'], 'Previous card'], [['1–9'], 'Answer with that choice once the card has focus; from outside the card, the key only moves focus to that choice'], [['⌘Z'], 'Take back the newest answer that still shows Undo']]],
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
const TILE = { w: 250, h: 170 };
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
];
const ICONS = {
  queue: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  now: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  work: '<path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/>',
  history: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5M12 7v5l4 2"/>',
  enlarge: '<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>',
};

let model = null;
let connected = false;
let lostAt = null;
let paused = null;
let mapZoom = MAP_ZOOMS.indexOf(1);
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
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)}h`;
  return `${Math.round(minutes / 1440)}d`;
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
const WORD_FENCES = new Set(['', 'text', 'txt', 'plain']);
const PICTURE_LINE = /^\s*!\[([^\]]*)\]\((\/[^)\s]+)\)\s*$/;
const QUOTE_LINE = /^\s*>\s?/;
const TABLE_ROW = /^\s*\|/;

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
      const heading = lines[0].match(/^(#{1,4})\s+(.*)$/);
      if (heading) {
        blocks.push(`<h4 class="hd">${inline(heading[2], cite)}</h4>`);
        lines.shift();
      }
      while (lines.length) {
        const row = TABLE_ROW.test(lines[0]);
        const end = lines.findIndex((line) => TABLE_ROW.test(line) !== row);
        const run = lines.splice(0, end < 0 ? lines.length : end);
        if (row) {
          blocks.push(table(run, cite));
        } else if (run.some((line) => PICTURE_LINE.test(line) || QUOTE_LINE.test(line))) {
          blocks.push(...lineBlocks(run, cite));
        } else if (run.every((line) => /^\s*(?:[-*•]|\d+[.)])\s/.test(line))) {
          blocks.push(`<ul>${run.map((line) => `<li>${inline(line.replace(/^\s*(?:[-*•]|\d+[.)])\s+/, ''), cite)}</li>`).join('')}</ul>`);
        } else {
          blocks.push(`<p>${run.map((line) => inline(line, cite)).join('<br>')}</p>`);
        }
      }
    }
  });
  return blocks.join('');
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
  if (WORD_FENCES.has(language)) return `<pre><code>${esc(source.replace(/\n$/, ''))}</code></pre>`;
  return visual(VISUAL_KINDS.has(language) ? language : `code:${language}`, source);
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

function choices(item, keyed = false) {
  const card = item.card;
  const given = answers.get(item.answer);
  if (given?.done) return `<div class="answered">✓ <b>${esc(given.label)}</b> · answered ${clock(given.at)}</div>`;
  const held = given && !given.error;
  const failed = given?.error ? `<p class="refused">${esc(given.error)}</p>` : '';
  const shown = picked?.card === item.answer ? picked.index : -1;
  const buttons = card.options.map((option, i) => {
    const attrs = `data-card="${esc(item.answer)}" data-label="${esc(option)}"${held ? ' disabled' : ''}`;
    const key = keyed ? `<kbd>${i + 1}</kbd>` : '';
    if (item.kind === 'tap') return `<button class="${option === 'Approve' ? 'primary' : 'secondary'}" ${attrs}>${key}${esc(option)}</button>`;
    const [title, detail] = optionParts(option);
    const recommended = option === card.recommended;
    return `<button class="choice${recommended ? ' recommended' : ''}${i === shown ? ' on' : ''}" ${attrs} data-preview="${i}">
      <span class="key">${keyed ? i + 1 : ''}</span>
      <span><span class="choice-title">${esc(title)}${recommended ? '<em>Recommended</em>' : ''}</span>${detail ? `<span class="choice-detail">${esc(detail)}</span>` : ''}</span>
      <span class="arrow" aria-hidden="true">›</span>
    </button>`;
  });
  const back = restored.has(item.answer) ? '<p class="restored" role="status">Restored. This is being asked again.</p>' : '';
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

function fold(id, html, lines) {
  const open = expanded.has(id);
  return `<div class="fold${open ? ' open' : ''}" id="fold:${esc(id)}" data-fold="${esc(id)}" style="--lines:${lines}">${html}</div><button type="button" class="linkish fold-more" data-more="${esc(id)}" aria-controls="fold:${esc(id)}" aria-expanded="${open}">${open ? 'Show less' : 'Explain more'}</button>`;
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

function shownQueue(filter) {
  const [, , keep] = QUEUE_FILTERS.find(([id]) => id === filter);
  return model.queue.filter((item) => keep(item) && !(answers.has(item.answer) && !answers.get(item.answer).error));
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
  if (!item?.answer) return `#/queue/${filter}`;
  const rows = item.card?.prs ?? [];
  return `#/queue/${filter}/${cardTs(item.answer)}${rows.length > 1 ? `/${prKey(rows[chosenRow(item)].url).split('#')[1]}` : ''}`;
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
      <div class="eyebrow">Ask about this, or answer in your own words</div>
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
    ...(card.facts ?? []).map((each) => fact(`${esc(each.label)}: ${inline(each.value)}`)),
  ].join('');
  return `<div class="queue-layout">
    <article class="focus-card" aria-labelledby="item-title" tabindex="-1">
      <div class="card-content">
        <div class="card-top"><span class="type-label">Decision</span>${counter(at, total)}</div>
        <h2 class="card-title" id="item-title">${inline(card.title || item.title)}</h2>
        ${body ? `<div class="card-summary">${fold(`q:${item.answer}`, body, 8)}</div>` : ''}
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

function landing(item, rows) {
  if (!item.card) return `<li>${esc(item.untapped ?? 'No Slack card yet, so no tap of yours lands it.')}</li>`;
  return rows.map((row) => {
    const said = row.state !== 'open' ? GONE_ROWS[row.state] ?? row.state : row.lands ? `lands once ${row.lands}` : row.blocks ? `does not land on Approve: ${row.blocks}` : 'Reading its card…';
    return `<li><b>${esc(prKey(row.url))}</b> ${esc(said)}</li>`;
  }).join('');
}

function spots(html) {
  const drawn = Object.assign(document.createElement('template'), { innerHTML: html }).content;
  return [...drawn.querySelectorAll(SPOTS)].map((el, i) => el.textContent.trim() || el.dataset.caption || `Picture ${i + 1}`);
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

function prRail(item, key, rows, row, pr, head, read) {
  const thread = item.thread ? talkPane(item.thread, cardTs(item.answer)) : '<p class="rail-note">This change has no Slack card yet, so the answer comes back in your Slack DM.</p>';
  return `<aside class="context-rail pr-rail" aria-label="About this change">
    <div class="rail-block"><div class="eyebrow">Attached to this change <code>${esc(head.slice(0, 7))}</code></div><div class="facts">${stateFacts(row)}</div><p class="spot-head">What Approve lands</p><ul class="rail-list">${landing(item, rows)}</ul></div>
    <div class="rail-block"><div class="eyebrow">Jump to a point in the change</div>${jumps(key, row, pr, read)}</div>
    <div class="rail-block"><div class="eyebrow">Question or line comment</div>${noteBox(key, pr)}</div>
    <div class="rail-block talk"><div class="eyebrow">Tell the drive what to change</div>${thread}
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
        <div class="card-top"><span class="type-label">Pull request <a href="${esc(row.url)}" target="_blank" rel="noopener" data-tip="Open it on GitHub">${esc(pr)}</a></span>${inline ? '' : counter(at, total)}</div>
        <h2 class="card-title" id="item-title">${esc(title)}</h2>
        ${stack}
        ${row.summary ? `<div class="card-summary">${prose(row.summary, pr)}</div>` : ''}
        ${row.refresher ? `<div class="card-summary">${fold(`r:${key}:${pr}`, prose(row.refresher, pr), 3)}</div>` : ''}
        <div class="facts">${prFacts(item, row, read)}</div>
      </div>
      <div class="pr-tabs" role="tablist" aria-label="About this change">${tabs}</div>
      ${panes}
      <div class="pr-actions">${item.card ? choices(item, true) : `<span class="meta">${esc(item.untapped ?? 'No Slack card yet, so there is no tap to give here.')}</span>`}</div>
    </article>
    ${prRail(item, key, rows, row, pr, head, read)}
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
  const read = item?.answer && slice(`/api/card?${new URLSearchParams({ answer: item.answer })}`);
  const filters = QUEUE_FILTERS.map(([each, label]) => `<a href="#/queue/${each}" class="${each === filter ? 'on' : ''}"${each === filter ? ' aria-current="true"' : ''}>${label}<span class="n">${shownQueue(each).length}</span></a>`).join('');
  const unread = model.cards === 'live' ? '' : `<p class="refused">Your questions are not listed here: cc-slack ${esc(model.cards)}</p>`;
  const rest = shownQueue('all').length;
  const done = `<div class="focus-card done"><span class="bigcheck" aria-hidden="true">✓</span><h2>${filter === 'all' ? 'Nothing waits on you.' : NONE_LEFT[filter]}</h2>${filter !== 'all' && rest ? `<p><a href="#/queue/all">${rest} other ${rest === 1 ? 'thing waits' : 'things wait'} in All ›</a></p>` : ''}</div>`;
  const shown = item ? { question: decisionCard, tap: prCard, late: lateCard }[item.kind](read?.ok ? { ...item, card: read.card, rail: read.rail } : item, at, list.length) : done;
  return `<div class="page-head">
      <h1 class="h1">${list.length ? `${list.length} ${list.length === 1 ? 'thing waits' : 'things wait'} on you` : 'Your queue'}</h1>
      <nav class="subtabs" aria-label="Show">${filters}</nav>
    </div>
    ${unread}
    ${shown}
    <footer class="qfoot"><span>As of ${clock(model.at)}</span><span><kbd>j</kbd><kbd>k</kbd> move <kbd>1–9</kbd> answer, with the card focused <kbd>⌘Z</kbd> undo <button type="button" class="linkish" data-keys><kbd>?</kbd> every key</button></span></footer>`;
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
  return `<div class="eyebrow">Now</div>
    <h1 class="h1 zoom-title">${esc(keys.length ? fileName(keys.at(-1)) : 'All projects')}</h1>
    <p class="lead"${keys.length ? ' hidden' : ''}>${keys.length ? '' : esc(nowHeadline())}</p>
    <div class="now-grid">${nowCanvas(tree, project, stream, task)}${story(scope)}</div>`;
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

function gone(what) {
  return `<p class="empty">${what} has no open work on the dashboard now. <a href="#/now">All projects</a></p>`;
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
  if (screen === 'file') steps.push([`${path[0]} ${(FILE_WORDS[path[1]] ?? 'file').toLowerCase()}`, location.hash]);
  return steps.map(([name, to], i) => (i === steps.length - 1 ? `<strong aria-current="page">${esc(name)}</strong>` : `<a href="${esc(to)}">${esc(name)}</a>`)).join('<span aria-hidden="true">/</span>');
}

function nowCanvas(tree, project, stream, task) {
  const level = stream !== undefined ? 'stream' : project !== undefined ? 'project' : 'all';
  const regions = [...tree].map(([key, streams]) => region(key, streams, project, stream)).join('');
  const live = model.counts.live;
  return `<section class="canvas" data-level="${level}" style="--cols:${Math.min(tree.size, Number($('#main .canvas')?.style.getPropertyValue('--cols')) || tree.size)}" aria-label="Projects, workstreams and tasks">
    <div class="canvas-bar"><span class="meta">${level === 'all' ? 'Click a project to zoom in' : level === 'project' ? 'Click a workstream to see its tasks' : 'Esc zooms out'}</span>
      <div class="canvas-tools"><button class="map-btn" data-cam="out" aria-label="Zoom out">−</button><button class="map-btn" data-cam="in" aria-label="Zoom in">+</button><button class="map-btn" data-cam="fit" aria-label="Fit to this level" title="Fit">⤢</button></div></div>
    <div class="viewport" data-at="${esc([project, stream].filter((key) => key !== undefined).join('/'))}" data-view="${esc(task?.id ?? '')}">
      <div class="world" style="${cameraStyle()}">${regions || `<p class="empty small">Nothing is open now${live ? `; ${counted(live, 'feature')} went live in the last day, in History` : ''}.</p>`}</div>
      ${task ? taskSheet(task, project, stream) : ''}
    </div>
  </section>`;
}

function region(key, streams, project, stream) {
  const list = [...streams.values()].flat();
  const here = key === project;
  return `<div class="region${key === NONE ? ' unfiled' : ''}${project !== undefined && !here ? ' aside' : ''}" data-cam-key="${esc(key)}" data-href="${zoomHref('now', key)}">
    <a class="region-head" href="${zoomHref('now', key)}"${project === undefined || !here ? '' : ' tabindex="-1"'}>
      <span class="zoom-mark" aria-hidden="true">${key === NONE ? '?' : esc([...fileName(key)][0].toUpperCase())}</span>
      <span class="region-name"><b>${esc(fileName(key))}</b><small>${esc(counted(streams.size, 'workstream'))} · ${openOf(list)}</small></span>
      ${needs(list)}${stateBar(list)}
    </a>
    <div class="region-glance" aria-hidden="true">
      <span class="glance-name"><span class="zoom-mark">${key === NONE ? '?' : esc([...fileName(key)][0].toUpperCase())}</span><b>${esc(fileName(key))}</b></span>
      <span class="glance-streams">${esc(`${counted(streams.size, 'workstream')}: ${[...streams.keys()].map(fileName).join(', ')}`)}</span>
      <span class="glance-open">${esc(openOf(list))}</span>
      ${needs(list)}${stateBar(list)}
    </div>
    <div class="streams" style="--n:${Math.min(streams.size, 2)}"${here ? '' : ' inert'}>${[...streams].map(([skey, slist]) => streamBox(key, skey, slist, here && skey === stream)).join('')}</div>
  </div>`;
}

function streamBox(project, key, list, here) {
  const asks = asksOf(list).sort((a, b) => new Date(a.asked) - new Date(b.asked));
  const tasks = list.toSorted((a, b) => RANK.indexOf(a.state) - RANK.indexOf(b.state) || new Date(b.updated) - new Date(a.updated));
  return `<div class="stream${key === NONE ? ' unfiled' : ''}${here ? ' here' : ''}" data-cam-key="${esc(project)}/${esc(key)}" data-href="${zoomHref('now', project, key)}">
    <a class="stream-head" href="${zoomHref('now', project, key)}"${here ? ' tabindex="-1"' : ''}><b>${esc(fileName(key))}</b><small>${esc(counted(list.length, 'task'))} · ${openOf(list)}</small>${needs(list)}${stateBar(list)}</a>
    <div class="stream-body"${here ? '' : ' inert'}>
      ${asks.length ? `<h3 class="sec">Your asks <span class="count">${asks.length}</span></h3><div class="task-list">${asks.map(askRow).join('')}</div>` : ''}
      <h3 class="sec">Tasks <span class="count">${tasks.length}</span></h3><div class="task-list">${tasks.map((entry) => taskRow(entry, zoomHref('now', project, key, entry.id))).join('')}</div>
    </div>
  </div>`;
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
  </aside>`;
}

let camera = null;
let cameraAt = null;
let flying = 0;
let dragged = false;
let fitter = null;
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

function cameraStyle() {
  return camera ? `transform:scale(${camera.k}) translate(${-camera.x}px, ${-camera.y}px);--k:${camera.k}` : 'visibility:hidden';
}

function aim() {
  $('#main .world').setAttribute('style', cameraStyle());
}

function boxOf(el, world) {
  let x = 0;
  let y = 0;
  for (let at = el; at !== world; at = at.offsetParent) {
    x += at.offsetLeft;
    y += at.offsetTop;
  }
  return { x, y, w: el.offsetWidth, h: el.offsetHeight };
}

function fitTo(box, viewport, level) {
  const pad = level === 'all' ? 0 : 0.025;
  const w = viewport.clientWidth;
  const h = viewport.clientHeight;
  const room = Math.min(w / (box.w * (1 + 2 * pad)), h / (box.h * (1 + 2 * pad)), 1.15);
  const k = level === 'all' ? Math.min(w / box.w, 1.15) : Math.max(room, Math.min(w / (box.w * (1 + 2 * pad)), READABLE));
  const top = box.h * k > h ? box.y - box.h * pad : box.y + box.h / 2 - h / k / 2;
  return { k, x: box.x + box.w / 2 - w / k / 2, y: top };
}

function cameraTarget() {
  const viewport = $('#main .viewport');
  const world = viewport.querySelector('.world');
  const at = viewport.dataset.at;
  const target = at ? viewport.querySelector(`[data-cam-key="${CSS.escape(at)}"]`) : world.firstElementChild;
  const box = at ? boxOf(target, world) : { x: 0, y: 0, w: world.scrollWidth, h: world.scrollHeight };
  const sheet = viewport.querySelector('.task-sheet');
  const room = { clientWidth: viewport.clientWidth - (sheet && viewport.clientWidth - sheet.offsetWidth >= 420 ? sheet.offsetWidth : 0), clientHeight: viewport.clientHeight };
  return fitTo(box, room, $('#main .canvas').dataset.level);
}

function settleCamera(arrived) {
  const viewport = $('#main .viewport');
  if (!viewport) {
    fitter?.disconnect();
    camera = null;
    cameraAt = null;
    return;
  }
  if (arrived || !camera) {
    fitColumns();
    camera = cameraTarget();
    aim();
    fitter?.disconnect();
    fitter = new ResizeObserver(() => {
      fitColumns();
      camera = cameraTarget();
      aim();
    });
    fitter.observe(viewport);
  } else if (`${viewport.dataset.at}#${viewport.dataset.view}` !== cameraAt) flyTo(cameraTarget());
  cameraAt = `${viewport.dataset.at}#${viewport.dataset.view}`;
}

function fitColumns() {
  const canvas = $('#main .canvas');
  const tile = canvas.querySelector('.region');
  if (!tile) return;
  const shown = (cols) => canvas.clientWidth / (cols * tile.offsetWidth + (cols - 1) * 160 + 240);
  let cols = index().tree.size;
  while (cols > 1 && (tile.offsetWidth * shown(cols) < TILE.w || tile.offsetHeight * shown(cols) < TILE.h)) cols -= 1;
  canvas.style.setProperty('--cols', cols);
}

function flyTo(target, ms = 560) {
  cancelAnimationFrame(flying);
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
    camera = target;
    return aim();
  }
  const from = { ...camera, w: 1 / camera.k };
  const to = { ...target, w: 1 / target.k };
  const t0 = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - t0) / ms);
    const e = ease(t);
    const w = from.w + (to.w - from.w) * e;
    camera = { k: 1 / w, x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e };
    aim();
    if (t < 1) flying = requestAnimationFrame(step);
  };
  flying = requestAnimationFrame(step);
}

function zoomAround(factor, px, py, ms = 0) {
  const viewport = $('#main .viewport');
  const ux = camera.x + px / camera.k;
  const uy = camera.y + py / camera.k;
  const k = Math.min(3, Math.max(0.03, camera.k / factor));
  const target = { k, x: ux - px / k, y: uy - py / k };
  if (ms) return flyTo(target, ms);
  cancelAnimationFrame(flying);
  camera = target;
  aim();
  return viewport;
}

function askRow(card) {
  const item = model.queue.find((each) => each.answer === card.url);
  const body = `<span class="task-mark ask" aria-hidden="true"></span><span class="task-body"><b>${esc(card.title)}</b><small>Asked ${clock(card.asked)}, waiting ${ago(card.asked)}</small></span>`;
  return item ? `<button class="task-row" data-queue="${esc(noticeKey(item))}">${body}<span class="task-go">Answer it ›</span></button>` : `<div class="task-row">${body}</div>`;
}

function taskRow(entry, href = `#/feature/${entry.id}`) {
  const merged = entry.prs.filter((pr) => pr.state === 'merged').length;
  const facts = [entry.prs.length ? `${merged} of ${counted(entry.prs.length, 'PR')} merged` : '', entry.cards.length ? `${counted(entry.cards.length, 'decision')} for you` : ''].filter(Boolean).join(' · ');
  return `<a class="task-row" href="${esc(href)}">
    <span class="task-mark t-${STOP_TONES[entry.state]}" aria-hidden="true"></span>
    <span class="task-body"><b>${esc(entry.title)}</b>${entry.now ? `<small>${esc(entry.now.replace(REPORT_WORD, '').replace(PR_LINK, '#$1'))}</small>` : ''}</span>
    <span class="task-facts">${pill(STOP_WORDS[entry.state], STOP_TONES[entry.state])}${facts ? `<small>${esc(facts)}</small>` : ''}</span>
    <span class="arrow" aria-hidden="true">›</span>
  </a>`;
}

function momentText(entry) {
  const text = FEATURE_KINDS.has(entry.kind) ? featureLink(entry.feature, entry.text) : link(entry.text, entry.url);
  return `<b class="what">${DECIDED[entry.kind] ? `${DECIDED[entry.kind]} ${esc(entry.detail)} on` : HAPPENED[entry.kind]}</b> ${text}`;
}

function story(scope) {
  const read = slice(`/api/story${scope ? `?${new URLSearchParams({ ids: scope.map((entry) => entry.id).join(',') })}` : ''}`);
  const told = read?.ok ? read.moments : [];
  return `<aside class="story" aria-label="The live story">
    <div class="eyebrow">The live story</div>
    ${!read?.ok ? notRead(read) : told.length ? `<ol>${told.map((entry) => `<li><time>${clock(entry.at)}</time><p>${momentText(entry)}</p></li>`).join('')}</ol>` : '<p class="meta">Nothing has happened here yet.</p>'}
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
    untapped: entry.group === 'decided' ? `You answered ${entry.tap} on its card; ${entry.detail}.` : `It does not wait on your tap yet: ${entry.detail}.`,
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
  return `<li class="job ${esc(job.state)}">
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
  return `${head}<pre>${found.lines.map((line) => `<span class="${line.n >= Number(a.dataset.line) && line.n <= Number(a.dataset.to) ? 'hl' : ''}"><i>${line.n}</i>${esc(line.text)}</span>`).join('')}</pre>`;
}

function hideSource() {
  const tip = $('#srctip');
  delete tip.dataset.key;
  if (tip.matches(':popover-open')) tip.hidePopover();
}

function showTip(el) {
  const tip = $('#srctip');
  if (tip.dataset.key === `tip:${el.dataset.tip}` && tip.matches(':popover-open')) return;
  tip.dataset.key = `tip:${el.dataset.tip}`;
  tip.innerHTML = `<p>${esc(el.dataset.tip)}</p>`;
  if (!tip.matches(':popover-open')) tip.showPopover();
  const box = el.getBoundingClientRect();
  tip.style.left = `${Math.max(8, Math.min(innerWidth - tip.offsetWidth - 8, box.left))}px`;
  tip.style.top = `${box.bottom + 6 + tip.offsetHeight > innerHeight ? Math.max(8, box.top - tip.offsetHeight - 6) : box.bottom + 6}px`;
}

function preview(choice) {
  const index = Number(choice.dataset.preview);
  if (picked?.card === choice.dataset.card && picked.index === index) return;
  picked = { card: choice.dataset.card, index };
  const item = model.queue.find((each) => each.answer === choice.dataset.card);
  const rail = document.querySelector('#main [data-means]');
  if (item && rail) rail.outerHTML = meaning(item, index);
  for (const each of choice.parentElement.querySelectorAll('.choice')) each.classList.toggle('on', each === choice);
}

function showSource(a) {
  const tip = $('#srctip');
  tip.dataset.key = a.href;
  tip.innerHTML = sourceRows(a, sourceSeen.get(a.href));
  if (!tip.matches(':popover-open')) tip.showPopover();
  const box = a.getBoundingClientRect();
  tip.style.left = `${Math.max(8, Math.min(innerWidth - tip.offsetWidth - 8, box.left))}px`;
  tip.style.top = `${box.bottom + 6 + tip.offsetHeight > innerHeight ? Math.max(8, box.top - tip.offsetHeight - 6) : box.bottom + 6}px`;
  if (sourceSeen.has(a.href)) return;
  const key = a.href;
  fetch(`/api/source?${new URLSearchParams({ cite: a.dataset.src, path: a.dataset.path, line: a.dataset.line, to: a.dataset.to })}`)
    .then((reply) => reply.json())
    .catch((failure) => ({ error: `Could not reach the dashboard server (${failure.message}).` }))
    .then((found) => {
      sourceSeen.set(key, found);
      if (tip.dataset.key === key && a.isConnected && tip.matches(':popover-open')) showSource(a);
    });
}

function isStalled(job) {
  return job.state === 'running' && minutesSince(job.updated) >= STALL_MINUTES;
}

function phase(seconds, offset = 0) {
  return `${-(((paused ?? Date.now()) / 1000 + offset) % seconds).toFixed(2)}s`;
}

function laneTone(job) {
  if (isStalled(job)) return 't-bad';
  return job.state === 'running' ? 't-doing' : 't-you';
}

function laneState(job) {
  return isStalled(job) ? `Quiet ${ago(job.updated)}` : JOB_STATES[job.state];
}

function laneTree(work) {
  const filed = (job, field) => fileKey(featureOf(job.feature)[field]);
  const projects = [...Map.groupBy(work, (job) => filed(job, 'project'))].sort(byName);
  return new Map(projects.map(([project, lanes]) => [project, new Map([...Map.groupBy(lanes, (job) => filed(job, 'workstream'))].sort(byName))]));
}

function workScreen() {
  const read = slice('/api/lanes');
  if (!read?.ok) return notRead(read);
  const [project, stream, laneId] = route().path;
  const tree = laneTree(read.lanes);
  const streams = tree.get(project);
  const lanes = streams?.get(stream);
  const running = read.lanes.filter((job) => job.state === 'running').length;
  const keys = [project, stream].filter((key) => key !== undefined);
  const zooms = stream === undefined ? `<button class="map-btn" data-zoom="-1" aria-label="Zoom out"${mapZoom === 0 ? ' disabled' : ''}>−</button><button class="map-btn" data-zoom="1" aria-label="Zoom in"${mapZoom === MAP_ZOOMS.length - 1 ? ' disabled' : ''}>+</button>` : '';
  const shown = project === undefined ? projectMap(tree)
    : !streams ? '<p class="empty topo-empty">This project has no lane running or waiting now. <a href="#/work">All projects</a></p>'
    : stream === undefined ? streamMap(project, streams)
    : lanes ? laneZoom(zoomHref('work', project, stream), lanes, laneId)
    : `<p class="empty topo-empty">This workstream has no lane running or waiting now. <a href="${zoomHref('work', project)}">${esc(fileName(project))}</a></p>`;
  return `<div class="page-head">
      <div><div class="eyebrow">Work</div><h1 class="h1">Inside the machine.</h1><p class="lead">${counted(read.lanes.length, 'lane')}, ${running} working. Click a project to zoom into its workstreams, then a workstream to see each lane.</p></div>
      <button class="secondary" data-motion aria-pressed="${Boolean(paused)}">${paused ? '▷ Resume motion' : 'Ⅱ Pause motion'}</button>
    </div>
    <section class="topology${paused ? ' paused' : ''}" style="--zoom:${MAP_ZOOMS[mapZoom]}" aria-label="Agent topology">
      <div class="topology-bar"><div>${zooms}</div></div>
      ${shown}
      <div class="map-legend"><span class="t-doing">Working</span><span class="t-you">Waiting or built</span><span class="t-bad">Quiet ${STALL_MINUTES} minutes or more</span><span>Moving packets are decoration, not traffic</span></div>
    </section>`;
}

function projectMap(tree) {
  const nodes = [...tree].map(([key, streams]) => ({ key, href: zoomHref('work', key), sub: key === NONE ? 'Orders not filed yet' : counted(streams.size, 'workstream'), lanes: [...streams.values()].flat() }));
  return topoMap(model.drive, 'Coordinator ↔ communicator', nodes);
}

function streamMap(project, streams) {
  const nodes = [...streams].map(([key, lanes]) => ({ key, href: zoomHref('work', project, key), sub: key === NONE ? 'No workstream filed yet' : lanes[0].said || lanes[0].goal, lanes }));
  return topoMap(fileName(project), counted(nodes.reduce((sum, node) => sum + node.lanes.length, 0), 'lane'), nodes);
}

function topoMap(root, under, nodes) {
  if (!nodes.length) return '<p class="empty topo-empty">No lane is running or waiting.</p>';
  const spots = nodes.map((node, i) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / nodes.length;
    const reach = nodes.length > RING_LIMIT && i % 2 ? 0.7 : 1;
    return { node, i, x: 50 + 37 * reach * Math.cos(angle), y: 50 + 35 * reach * Math.sin(angle) };
  });
  const paths = spots.map(({ x, y, i }) => {
    const d = `M 550 305 C 550 ${(y * 6.1).toFixed(1)} ${(x * 11).toFixed(1)} 305 ${(x * 11).toFixed(1)} ${(y * 6.1).toFixed(1)}`;
    return `<path class="flow-path" d="${d}"/><path class="flow-packet" d="${d}" style="animation-delay:${phase(PACKET_SECONDS, i * 1.3)}"/>`;
  }).join('');
  const cards = spots.map(({ node, x, y, i }) => {
    const name = fileName(node.key);
    const working = node.lanes.filter((job) => job.state === 'running').length;
    const quiet = node.lanes.filter(isStalled).length;
    const dots = node.lanes.slice(0, SATELLITES).map((job) => `<i class="${laneTone(job)}"></i>`).join('');
    const more = node.lanes.length > SATELLITES ? `<em>+${node.lanes.length - SATELLITES}</em>` : '';
    const tip = `${name}: ${counted(node.lanes.length, 'lane')}, ${working} working${quiet ? `, ${quiet} quiet` : ''}. Click to zoom in.`;
    return `<a class="topo-node${node.key === NONE ? ' unfiled' : ''}" href="${node.href}" style="left:${x.toFixed(2)}%;top:${y.toFixed(2)}%;--i:${i}" data-tip="${esc(tip)}">
      <strong>${esc(name)} ›</strong>
      <small class="sub">${esc(node.sub)}</small>
      <small>${counted(node.lanes.length, 'lane')} · ${working} working</small>
      <span class="satellites">${dots}${more}</span>
    </a>`;
  }).join('');
  return `<div class="map">
    <svg class="topo-svg" viewBox="0 0 1100 610" preserveAspectRatio="none" aria-hidden="true"><ellipse class="orbital" cx="550" cy="305" rx="410" ry="220" style="animation-delay:${phase(ORBIT_SECONDS)}"/><ellipse class="orbital" cx="550" cy="305" rx="300" ry="150" style="animation-delay:${phase(ORBIT_SECONDS, 50)}"/>${paths}</svg>
    <div class="topo-root"><b>${esc(root)}</b><small>${esc(under)}</small></div>
    ${cards}
  </div>`;
}

function laneZoom(href, lanes, laneId) {
  const chosen = lanes.find((job) => job.id === laneId) ?? lanes[0];
  const cards = lanes.map((job, i) => `<a class="lane-card${job === chosen ? ' on' : ''}" href="${href}/${encodeURIComponent(job.id)}" style="--i:${i}"${job === chosen ? ' aria-current="true"' : ''}>
      <small><i class="${laneTone(job)}"></i>${esc(laneState(job))} · ${esc(job.kind)}</small>
      <strong>${esc(job.goal)}</strong>
      <span class="lane-id">${esc(job.id)} · ${esc(job.where)} · last word ${ago(job.updated)} ago</span>
    </a>`).join('');
  return `<div class="lane-zoom"><div class="lane-cards">${cards}</div>${laneInspector(chosen)}</div>`;
}

function laneInspector(job) {
  const entry = featureOf(job.feature);
  const facts = [job.where, job.branch ? `on ${job.branch}` : '', job.prs ? counted(job.prs, 'PR') : '', job.waits ? `waits on ${counted(job.waits, 'card or job', 'cards or jobs')}` : ''].filter(Boolean);
  return `<aside class="lane-inspector" aria-label="Inside this lane">
    <div class="eyebrow">Inside this lane</div>
    <h3>${esc(job.goal)}</h3>
    <span class="mono">${esc(job.id)} · ${esc(laneState(job).toLowerCase())} · ${esc(job.kind)}</span>
    ${job.said ? `<p class="lane-said">${esc(job.said)}</p>` : '<p class="meta">No progress line yet.</p>'}
    <p class="meta">Last word ${clock(job.updated)}, ${ago(job.updated)} ago · ${esc(facts.join(' · '))}</p>
    ${fileButtons(job)}
    <a class="btn ghost" href="#/feature/${esc(job.feature)}">See the task ›</a>
    <p class="rail-note">For: ${esc(entry.title)}</p>
  </aside>`;
}

function jumpItems() {
  const queued = model.queue.map((item) => ({
    kind: LABEL[item.kind],
    title: item.title,
    href: '#/queue/all',
    item: noticeKey(item),
    keys: item.kind === 'tap' ? (item.card?.prs ?? [{ url: item.url }]).map((row) => prKey(row.url)) : [],
  }));
  const onQueue = new Set(queued.flatMap((row) => row.keys));
  return [
    ...SCREENS.map(([, href, title]) => ({ kind: 'Screen', title, href, keys: [] })),
    ...queued,
    ...features().map((entry) => ({ kind: 'Task', title: entry.title, href: `#/feature/${entry.id}`, keys: [] })),
    ...model.review.filter((entry) => !onQueue.has(entry.key)).map((entry) => ({ kind: LABEL.tap, title: entry.title, pr: entry.key, keys: [entry.key] })),
    ...(slice('/api/lanes')?.lanes ?? []).map((job) => {
      const entry = featureOf(job.feature);
      return { kind: 'Job', title: job.goal, href: zoomHref('work', fileKey(entry.project), fileKey(entry.workstream), job.id), keys: [job.id] };
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
  $('#jumpnote').textContent = !found.length ? `Nothing matches “${typed}”. Jump finds a screen, a question, a pull request by its title, number or repo, a task, or a job.`
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
  return [
    open ? `<button type="button" class="al bad" popovertarget="incidents">${open} ${open === 1 ? 'incident' : 'incidents'} open</button>` : '',
    picks ? `<a class="al" href="#/queue/question">${picks} ${picks === 1 ? 'decision' : 'decisions'} for you</a>` : '',
    taps ? `<a class="al" href="#/queue/tap">${taps} ${taps === 1 ? 'PR' : 'PRs'} to tap</a>` : '',
  ].filter(Boolean).join('');
}

function incidentList() {
  const rows = model.incidents.map((incident) => {
    const entry = featureOf(incident.id);
    const running = entry.running;
    const prs = entry.prs.filter((pr) => pr.state === 'open').length;
    const asks = entry.cards.length;
    const facts = [
      `Since ${clock(incident.created)}`,
      asks ? `<b>waits on your answer</b>` : '',
      running ? `${running} ${running === 1 ? 'job' : 'jobs'} working` : '',
      prs ? `${prs} open ${prs === 1 ? 'PR' : 'PRs'}` : '',
      asks || running || prs ? '' : `nothing moving since ${clock(entry.updated)}`,
    ].filter(Boolean).join(' · ');
    return { rank: [asks ? 0 : 1, running || prs ? 0 : 1, -Date.parse(entry.updated)], html: `<li><a href="#/feature/${esc(incident.id)}"><span class="inc-title">${esc(entry.title)}</span><span class="meta">${facts}</span></a></li>` };
  });
  rows.sort((a, b) => a.rank.map((value, i) => value - b.rank[i]).find(Boolean) ?? 0);
  return `<div class="inc-head"><b>${rows.length} ${rows.length === 1 ? 'incident' : 'incidents'} open</b><span>Waiting on you first, then the ones moving</span></div><ul>${rows.map((row) => row.html).join('')}</ul>`;
}

function saidRow(entry) {
  return `<li class="happened" data-kind="said">
    <span class="time">${timeOf(entry.at)}</span>
    <span class="dot"></span>
    <div class="body">
      <div><b class="what">${HAPPENED.said}</b></div>
      <div class="said-text">${prose(entry.text)}</div>
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
    <div class="now-grid"><div class="zoom-body">${taskBody(entry)}</div>${story([entry])}</div>`;
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
  return `<div class="kick">${pill(HEADS[entry.state], STOP_TONES[entry.state])}${entry.holdup ? `<span class="holdup">${holdupLine(entry.holdup)}</span>` : ''}</div>
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
  return `<li class="line"><span class="line-text"><b>${esc(job.kind)} ${esc(job.id)}</b> ${esc(job.goal)}${job.said ? `<br>${reportLine(job.said, job.state)}` : ''}</span><small class="meta">${clock(job.updated)}</small></li>`;
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
  const counts = { queue: model.counts.queue };
  $('#tabs').innerHTML = SCREENS.map(([name, href, label]) => `<a class="tab${name === at ? ' on' : ''}" href="${href}"${name === at ? ' aria-current="page"' : ''}>${icon(name)}<span>${label}</span>${counts[name] ? `<b class="count" aria-label="${counts[name]} waiting on you">${counts[name]}</b>` : ''}</a>`).join('');
  morphChildren($('#crumb'), Object.assign(document.createElement('nav'), { innerHTML: trail() }));
  const raised = strip();
  morphChildren($('#alerts'), Object.assign(document.createElement('div'), { innerHTML: raised }));
  $('#alerts').hidden = !raised;
  morphChildren($('#incidents'), Object.assign(document.createElement('div'), { innerHTML: incidentList() }));
  const screens = { queue: () => queueScreen(id), now: nowScreen, work: workScreen, history: historyScreen, feature: () => featureScreen(id), file: () => fileScreen(route().path) };
  const open = new Set(model.review.map((entry) => entry.key));
  for (const [key, frame] of slotted) {
    if (open.has(key)) continue;
    frame.remove();
    slotted.delete(key);
    reviews.delete(key);
  }
  const html = (screens[screen] ?? queueScreen)();
  const arrived = pageKey() !== shownHash;
  shownHash = pageKey();
  $('#main').classList.toggle('arrived', arrived);
  if (arrived) $('#main').innerHTML = html;
  else morphChildren($('#main'), Object.assign(document.createElement('main'), { innerHTML: html }));
  settleCamera(arrived);
  if (reviewing) drawDrawer();
  if ($('#jump').open) drawJump();
  for (const url of [...slices.keys()].slice(0, -SLICES_KEPT)) slices.delete(url);
  measure();
  fill();
  fresh();
  return arrived;
}

function pageKey() {
  const { screen, path } = route();
  if (screen === 'work') return `work/${path.slice(0, 2).join('/')}`;
  return screen === 'now' ? 'now' : screen === 'queue' ? `queue/${filterOf(path[0])}` : location.hash;
}

function morph(old, made) {
  if (old.nodeType !== made.nodeType || old.nodeName !== made.nodeName) return old.replaceWith(made);
  if (old.nodeType !== Node.ELEMENT_NODE) {
    if (old.nodeValue !== made.nodeValue) old.nodeValue = made.nodeValue;
    return;
  }
  if (old.matches('.visual-host.drawn') && old.dataset.visual === made.dataset.visual && old.dataset.source === made.dataset.source) return;
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
  const asked = [...model.queue, ...model.review].find((candidate) => candidate.answer === card);
  restored.delete(card);
  answers.set(card, { label });
  toast(card, { label, title: asked.title, until: Date.now() + model.undo_seconds * 1000, state: 'held' });
  render();
  try {
    const reply = await fetch('/api/answer', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ card, label }) });
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
    if (tipped) return $('#srctip').dataset.key === `tip:${tipped.dataset.tip}` ? hideSource() : showTip(tipped);
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
    return go('#/queue/all');
  }
  if (event.target.closest('[data-motion]')) {
    paused = paused ? null : Date.now();
    localStorage.setItem('motion', paused ? 'paused' : 'moving');
    return render();
  }
  const zoomed = event.target.closest('[data-zoom]');
  if (zoomed) {
    mapZoom = Math.max(0, Math.min(MAP_ZOOMS.length - 1, mapZoom + Number(zoomed.dataset.zoom)));
    return render();
  }
  const file = event.target.closest('button[data-file]');
  if (file) return openFile(file.dataset.file, file.dataset.title);
  if (event.target.closest('#kbtn')) return openJump();
  if (event.target.closest('[data-keys]')) return openKeys();
  if (event.target.closest('#notices')) return Notification.requestPermission().then(noticeButton);
  const jumped = event.target.closest('#jumplist [data-jump]');
  if (jumped) return goJump(jumped);
  if (event.target.id === 'jump') return $('#jump').close();
  if (event.target.closest('#incidents a')) $('#incidents').hidePopover();
  if (event.target.closest('[data-reload]')) return reloadPage();
  const cam = event.target.closest('#main [data-cam]');
  if (cam) {
    const viewport = $('#main .viewport');
    if (cam.dataset.cam === 'fit') return flyTo(cameraTarget(), 420);
    return zoomAround(cam.dataset.cam === 'in' ? 0.7 : 1 / 0.7, viewport.clientWidth / 2, viewport.clientHeight / 2, 260);
  }
  const spot = !event.target.closest('a, button, .task-sheet') && event.target.closest('#main .viewport [data-href]');
  if (spot) {
    const zone = spot.closest('.streams[inert]') ? spot.closest('.region') : spot;
    if (!zone.matches('.here')) location.hash = zone.dataset.href;
    return;
  }
  const shown = event.target.closest('#main [data-shot], #drawer [data-shot], #main [data-enlarge], #drawer [data-enlarge]');
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
  const viewport = event.target.closest('#main .viewport');
  if (!viewport || event.button !== 0 || event.target.closest('.task-sheet, .stream.here .stream-body')) return;
  const start = { x: event.clientX, y: event.clientY, camera: { ...camera } };
  dragged = false;
  const move = (moved) => {
    const dx = moved.clientX - start.x;
    const dy = moved.clientY - start.y;
    if (!dragged && Math.hypot(dx, dy) < 6) return;
    if (!dragged) viewport.setPointerCapture(event.pointerId);
    dragged = true;
    viewport.classList.add('dragging');
    cancelAnimationFrame(flying);
    camera = { ...start.camera, x: start.camera.x - dx / start.camera.k, y: start.camera.y - dy / start.camera.k };
    aim();
  };
  const end = () => {
    viewport.classList.remove('dragging');
    viewport.removeEventListener('pointermove', move);
  };
  viewport.addEventListener('pointermove', move);
  viewport.addEventListener('pointerup', end, { once: true });
  viewport.addEventListener('pointercancel', end, { once: true });
});
document.addEventListener('click', (event) => {
  if (!dragged || !event.target.closest('#main .viewport')) return;
  dragged = false;
  event.preventDefault();
  event.stopImmediatePropagation();
}, true);
document.addEventListener('wheel', (event) => {
  const viewport = event.target.closest('#main .viewport');
  if (!viewport) return;
  if (!(event.ctrlKey || event.metaKey)) {
    const tall = viewport.querySelector('.world').offsetHeight - viewport.clientHeight / camera.k;
    if ($('#main .canvas').dataset.level !== 'all' || tall <= 0) return;
    event.preventDefault();
    cancelAnimationFrame(flying);
    camera = { ...camera, y: Math.min(tall, Math.max(0, camera.y + event.deltaY / camera.k)) };
    return aim();
  }
  event.preventDefault();
  const r = viewport.getBoundingClientRect();
  zoomAround(Math.exp(event.deltaY * (event.ctrlKey ? 0.01 : 0.0016)), event.clientX - r.left, event.clientY - r.top);
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
  if (free && screen === 'queue') {
    const step = { ArrowLeft: -1, ArrowRight: 1, k: -1, j: 1 }[event.key];
    if (step) {
      event.preventDefault();
      return move(step);
    }
    const choice = /^[1-9]$/.test(event.key) && document.querySelectorAll('#main .focus-card button[data-card]')[Number(event.key) - 1];
    if (choice) {
      event.preventDefault();
      return event.target.closest('#main .focus-card') ? choice.click() : choice.focus();
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
  paused = localStorage.getItem('motion') === 'paused' ? Date.now() : null;
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
