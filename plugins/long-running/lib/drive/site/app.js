const ZONE = 'America/Los_Angeles';
const STALL_MINUTES = 45;
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
const HAPPENED = { order: 'You asked', live: 'Live', merged: 'Merged', failed: 'Failed', refused: 'No card drawn', said: 'Sent you' };
const FEATURE_KINDS = new Set(['order', 'live', 'failed']);
const HISTORY_FILTERS = [
  ['all', 'Everything', () => true],
  ['prs', 'PRs merged', (entry) => entry.kind === 'merged'],
  ['you', 'Your asks and taps', (entry) => ['order', 'tap'].includes(entry.kind)],
  ['said', 'Sent to you', (entry) => entry.kind === 'said'],
];
const NONE = '~';
const UNFILED = 'Unfiled';
const RANK = ['blocked', 'built', 'running', 'waiting', 'not started', 'merged', 'live'];
const STORY_KINDS = new Set(['order', 'live', 'merged', 'tap']);
const STORY_LINES = 6;
const PACKET_SECONDS = 9;
const ORBIT_SECONDS = 100;
const MAP_ZOOMS = [0.7, 0.85, 1, 1.2, 1.45];
const SATELLITES = 14;
const RING_LIMIT = 8;
const RECENT_MOMENTS = 80;
const DAY_MS = 24 * 60 * 60 * 1000;
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
const REVIEW_FACTS = { approved: ['Review approved', 'good'], changes: ['Changes requested', 'bad'] };
const TALK_FOLLOW_MS = 15 * 60 * 1000;
const FRAMES_KEPT = 4;
const STOP_WORDS = { blocked: 'Blocked', running: 'Being built', built: 'Built, waits on your answers', waiting: 'Waiting', merged: 'Merged, not live yet', live: 'Live', 'not started': 'Not started' };
const STOP_TONES = { blocked: 'bad', running: 'doing', built: 'you', waiting: 'waiting', merged: 'merged', live: 'done', 'not started': 'not-started' };
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
let composing = null;
const expanded = new Set();
const answers = new Map();
const drafts = new Map();
const sent = new Map();
const toasts = new Map();
const prTabs = new Map();
const prRows = new Map();
const prReads = new Map();
const reviews = new Map();
const talked = new Map();
const reading = new Set();
const again = new Set();
const slotted = new Map();
const followed = new Map();
const TYPED_MARK = '_You, on the dashboard:_ ';
let reviewing = null;

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

function historyHref(day, filter = shownFilter()) {
  return `#/history${day ? `/${day}` : ''}${filter === 'all' ? '' : `?show=${filter}`}`;
}

function up() {
  const { screen, path } = route();
  if (screen === 'feature') {
    const entry = featureOf(path[0]);
    return entry ? zoomHref('now', fileKey(entry.project), fileKey(entry.workstream)) : '#/now';
  }
  if (screen === 'history') return path.length ? historyHref() : null;
  if (!path.length || !['now', 'work'].includes(screen)) return null;
  return zoomHref(screen, ...path.slice(0, screen === 'work' && path.length > 1 ? 1 : path.length - 1));
}

const VISUAL_KINDS = new Set(['arch', 'mermaid', 'chart', 'compare', 'dot']);
const WORD_FENCES = new Set(['', 'text', 'txt', 'plain']);
const PICTURE_LINE = /^\s*!\[([^\]]*)\]\((\/[^)\s]+)\)\s*$/;

function inline(text, cite = model.repo) {
  const spans = [];
  const keep = (html) => `\u0000${spans.push(html) - 1}\u0000`;
  let out = cite ? String(text).replace(SOURCE_CITE, (_, pre, path, from, to) => `${pre}${keep(sourceLink(cite, path, from, to))}`) : String(text);
  out = out
    .replace(/`([^`]+)`/g, (_, code) => keep(`<code>${esc(code)}</code>`))
    .replace(/<(https?:\/\/[^|>\s]+)\|([^>]+)>/g, (_, url, label) => keep(link(label, url)))
    .replace(/<(https?:\/\/[^|>\s]+)>/g, (_, url) => keep(link(url, url)))
    .replace(/!\[([^\]]*)\]\((https?:\/\/[^)\s]+)\)/g, (_, alt, url) => keep(`<a class="shot" href="${esc(url)}" target="_blank" rel="noopener"><img src="${esc(url)}" alt="${esc(alt)}" loading="lazy"></a>`))
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
        if (!lines.length) continue;
      }
      if (lines.some((line) => PICTURE_LINE.test(line))) {
        blocks.push(...lines.map((line) => {
          const shot = line.match(PICTURE_LINE);
          return shot ? shown(shot[2], shot[1]) : `<p>${inline(line, cite)}</p>`;
        }));
      } else if (lines.every((line) => /^\s*\|.*\|\s*$/.test(line))) {
        blocks.push(table(lines, cite));
      } else if (lines.every((line) => /^\s*(?:[-*•]|\d+[.)])\s/.test(line))) {
        blocks.push(`<ul>${lines.map((line) => `<li>${inline(line.replace(/^\s*(?:[-*•]|\d+[.)])\s+/, ''), cite)}</li>`).join('')}</ul>`);
      } else {
        blocks.push(`<p>${lines.map((line) => inline(line, cite)).join('<br>')}</p>`);
      }
    }
  });
  return blocks.join('');
}

function fenced(language, source) {
  if (WORD_FENCES.has(language)) return `<pre><code>${esc(source.replace(/\n$/, ''))}</code></pre>`;
  return visual(VISUAL_KINDS.has(language) ? language : `code:${language}`, source);
}

function shown(path, caption) {
  const drawn = model.visuals?.[path];
  return drawn && drawn.kind !== 'image' ? visual(drawn.kind, drawn.source, caption) : shot(path, caption);
}

function shot(path, caption) {
  const src = `/api/picture?path=${encodeURIComponent(path)}`;
  return `<a class="shot" href="${src}" target="_blank" rel="noopener"><img src="${src}" alt="${esc(caption)}" loading="lazy"></a>`;
}

function visual(kind, source, caption = '') {
  return `<figure class="visual"><div class="visual-host" data-visual="${esc(kind)}" data-source="${esc(source)}"></div>${caption ? `<figcaption>${esc(caption)}</figcaption>` : ''}</figure>`;
}

async function drawFigures(root) {
  const hosts = [...root.querySelectorAll('.visual-host:not(.drawn)')];
  if (!hosts.length) return;
  for (const host of hosts) host.classList.add('drawn');
  const loaded = import('/visuals.js');
  for (const host of hosts) {
    try {
      await (await loaded).draw(host, host.dataset.visual, host.dataset.source);
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

function table(lines, cite) {
  const cells = (line) => line.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim());
  const rows = lines.filter((line) => !/^\s*\|[\s:|-]+\|\s*$/.test(line)).map(cells);
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
      <span class="arrow" aria-hidden="true">↗</span>
    </button>`;
  });
  return `<div class="${item.kind === 'tap' ? 'pr-picks' : 'choices'}">${buttons.join('')}</div>${failed}`;
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
  return `<div class="fold${open ? ' open' : ''}" data-fold="${esc(id)}" style="--lines:${lines}">${html}</div>`;
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
  let at = list.findIndex((item) => noticeKey(item) === queueAt);
  if (at < 0) at = Math.min(queueSlot, list.length - 1);
  queueSlot = Math.max(at, 0);
  queueAt = list[at] ? noticeKey(list[at]) : null;
  return at;
}

function currentItem() {
  const { screen, id } = route();
  if (screen !== 'queue') return null;
  return shownQueue(filterOf(id)).find((item) => noticeKey(item) === queueAt) ?? null;
}

function move(delta) {
  const list = shownQueue(filterOf(route().id));
  const at = Math.max(0, Math.min(list.length - 1, current(list) + delta));
  if (!list[at] || noticeKey(list[at]) === queueAt) return;
  queueAt = noticeKey(list[at]);
  queueSlot = at;
  picked = null;
  composing = null;
  window.scrollTo(0, 0);
  render();
}

function meaning(item, index) {
  const card = item.card;
  const option = card.options[index];
  const [title, detail] = optionParts(option);
  const label = option === card.recommended ? 'Recommended' : `Choice ${index + 1} of ${card.options.length}`;
  return `<div class="rail-block" data-means><div class="eyebrow">${label}</div><div class="consequence-title">${esc(title)}</div>${detail ? `<p>${esc(detail)}</p>` : ''}</div>`;
}

function featureByTitle(title) {
  const entry = features().find((each) => each.title === title);
  return entry ? `<a href="#/feature/${esc(entry.id)}">${esc(title)}</a>` : esc(title);
}

function decisionRail(item) {
  const card = item.card;
  const index = picked?.card === item.answer ? picked.index : Math.max(0, card.options.indexOf(card.recommended));
  const holds = [
    item.holds ? `<li>${item.holds} finished ${item.holds === 1 ? 'change or built job waits' : 'changes or built jobs wait'} on this answer</li>` : '',
    ...item.blocks.map((title) => `<li>${featureByTitle(title)}</li>`),
  ].join('');
  const links = [...(card.links ?? []), ...(card.refs ?? [])].filter((each) => !SLACK.test(each.url)).map((each) => `<li>${link(each.label, each.url)}</li>`).join('');
  const read = `${links ? `<ul class="rail-list">${links}</ul>` : ''}${pictures(card.pictures)}`;
  return `<aside class="context-rail" aria-label="What each choice means">
    ${meaning(item, index)}
    ${holds ? `<div class="rail-block"><div class="eyebrow">What it holds up</div><ul class="rail-list">${holds}</ul></div>` : ''}
    ${read ? `<div class="rail-block"><div class="eyebrow">Read first</div>${read}</div>` : ''}
    <p class="rail-note">Hover or focus a choice to read it here. An answer waits ${model.undo_seconds} seconds before it is sent, so Undo or ⌘Z takes it back.</p>
  </aside>`;
}

function decisionCard(item, at, total) {
  const card = item.card;
  const key = noticeKey(item);
  const asking = composing === key;
  const rows = (card.rows ?? []).map((row) => `<li>${inline(row.text)}</li>`).join('');
  const body = [card.body ? prose(card.body) : '', rows ? `<ul class="rows">${rows}</ul>` : ''].join('');
  const facts = [
    item.since ? fact(esc(`Asked ${clock(item.since)}`), `Waiting on you for ${ago(item.since)}`) : '',
    item.holds ? fact(esc(`Holds up ${item.holds} finished`), `${item.holds} green, approved ${item.holds === 1 ? 'change or built job waits' : 'changes or built jobs wait'} on this answer`, 'warn') : '',
    ...(card.facts ?? []).map((each) => fact(`${esc(each.label)}: ${inline(each.value)}`)),
  ].join('');
  return `<div class="queue-layout">
    <article class="focus-card" aria-labelledby="item-title">
      <div class="card-content">
        <div class="card-top"><span class="type-label">Decision</span>${counter(at, total)}</div>
        <h2 class="card-title" id="item-title">${inline(card.title || item.title)}</h2>
        ${body ? `<div class="card-summary">${fold(`q:${item.answer}`, body, 5)}</div>` : ''}
        ${facts ? `<div class="facts">${facts}</div>` : ''}
        ${choices(item, true)}
      </div>
      <div class="card-bottom"><button class="ask-open" data-ask="${esc(key)}" aria-expanded="${asking}">${asking ? 'Close' : 'Ask about this, or answer in your own words'}</button></div>
      ${asking ? `<div class="ask-box">${item.thread ? talkPane(item.thread, cardTs(item.answer)) : ''}${sayBox(`q:${item.answer}`, item.answer, 'Ask a question, or answer in your own words', '', item.thread ?? '')}</div>` : ''}
    </article>
    ${decisionRail(item)}
  </div>`;
}

function prFacts(item, row, entry, read) {
  const [checks, checksTone] = CHECK_FACTS[entry?.checks] ?? CHECK_FACTS.none;
  const [review, reviewTone] = REVIEW_FACTS[entry?.review] ?? ['No review yet', ''];
  const pr = read?.pr;
  return [
    fact(esc(checks), '', checksTone),
    fact(esc(review), '', reviewTone),
    pr ? fact(`<span class="add">+${pr.additions}</span> <span class="del">−${pr.deletions}</span> · ${pr.changedFiles} ${pr.changedFiles === 1 ? 'file' : 'files'}`, `${pr.headRefName} into ${pr.baseRefName}`) : '',
    pr?.isDraft ? fact('Draft', '', 'warn') : '',
    row.head ? fact(`<code>${esc(row.head.slice(0, 7))}</code>`, `The commit this card was drawn for, ${row.head}`) : '',
    item.since ? fact(esc(`Asked ${clock(item.since)}`), `Waiting on you for ${ago(item.since)}`) : '',
    row.lands ? fact(esc(`Lands once ${row.lands}`), '', 'good') : '',
    row.blocks ? fact(esc(row.blocks), '', 'bad') : '',
    row.changed ? fact('Changed since your tap', row.changed, 'warn') : '',
  ].join('');
}

function prPane(tab, row, pr, head, read) {
  if (tab === 'change') {
    const review = reviews.get(pr);
    const said = review?.error ? `<p class="refused">cc-review could not open ${esc(pr)}: ${esc(review.error)}</p>` : `<p class="meta">Opening the code review of ${esc(pr)} at ${esc(head.slice(0, 7))}…</p>`;
    return `<div class="review-slot" data-slot="${esc(pr)}" data-head="${esc(head)}">${said}</div>`;
  }
  if (tab === 'context') {
    const fields = PR_FIELDS.filter(([key]) => key !== 'refresher' && key !== 'show_me' && row[key]).map(([key, label]) => `<dt>${label}</dt><dd>${prose(row[key], pr)}</dd>`).join('');
    const whole = read?.pr ? `<details class="whole"><summary>The whole description</summary>${prose(read.pr.body, pr)}</details>` : '';
    return `${fields ? `<dl class="fields">${fields}</dl>` : '<p class="meta">Its description names no surprise, risk or departure from the plan.</p>'}${whole}`;
  }
  const shown = read?.pr?.show_me?.trim();
  if (shown) return prose(shown, pr);
  const drawn = `${row.show_me ? prose(row.show_me, pr) : ''}${pictures(row.pictures)}`;
  if (read?.error) return `${drawn}<p class="refused">${esc(read.error)}</p>`;
  if (!read?.pr) return drawn || '<p class="meta">Reading its Show me…</p>';
  return drawn || '<p class="meta">Its description has no Show me section.</p>';
}

function prCard(item, at, total) {
  const key = noticeKey(item);
  const rows = item.card?.prs ?? [{ url: item.url, title: item.title }];
  const chosen = Math.min(prRows.get(key) ?? 0, rows.length - 1);
  const row = rows[chosen];
  const pr = prKey(row.url);
  const entry = model.review.find((each) => each.key === pr);
  const head = entry?.head ?? row.head ?? '';
  const tab = prTabs.get(key) ?? 'show';
  const title = item.card ? bare(item.card.title.replace(/^\w+: approve (.*)\?$/, '$1')).replace(/^./, (c) => c.toUpperCase()) : item.title;
  const stack = rows.length > 1 ? `<div class="stack" role="group" aria-label="The PRs on this card">${rows.map((each, i) => `<button class="chip${i === chosen ? ' on' : ''}" data-row="${i}" data-item="${esc(key)}" aria-pressed="${i === chosen}">${esc(`${prKey(each.url)} · ${bare(each.title)}`)}</button>`).join('')}</div>` : '';
  const tabs = PR_TABS.map(([id, label]) => `<button role="tab" class="${id === tab ? 'on' : ''}" aria-selected="${id === tab}" data-prtab="${id}" data-item="${esc(key)}">${label}</button>`).join('');
  const thread = item.thread ? talkPane(item.thread, cardTs(item.answer)) : '<p class="rail-note">This change has no Slack card yet, so the answer comes back in your Slack DM.</p>';
  return `<div class="pr-layout" data-read="${esc(pr)}" data-head="${esc(head)}">
    <article class="focus-card pr-card" aria-labelledby="item-title">
      <div class="card-content">
        <div class="card-top"><span class="type-label">Pull request <a href="${esc(row.url)}" target="_blank" rel="noopener" data-tip="Open it on GitHub">${esc(pr)}</a></span>${counter(at, total)}</div>
        <h2 class="card-title" id="item-title">${esc(title)}</h2>
        ${stack}
        ${row.refresher ? `<div class="card-summary">${fold(`r:${key}:${pr}`, prose(row.refresher, pr), 3)}</div>` : ''}
        <div class="facts">${prFacts(item, row, entry, prReads.get(pr))}</div>
      </div>
      <div class="pr-tabs" role="tablist" aria-label="About this change">${tabs}</div>
      <div class="pr-pane ${tab}" role="tabpanel">${prPane(tab, row, pr, head, prReads.get(pr))}</div>
      <div class="pr-actions">${item.card ? choices(item, true) : '<span class="meta">No Slack card yet, so there is no tap to give here.</span>'}</div>
    </article>
    <aside class="context-rail talk-rail" aria-label="Ask about this change">
      <div class="eyebrow">Ask about this change</div>
      ${thread}
      ${sayBox(item.card ? `t:${item.answer}` : `r:${pr}`, item.answer ?? '', 'Ask about this change, or tell Claude what to change', item.card ? '' : `About ${row.url}`, item.thread ?? '')}
    </aside>
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
  const filters = QUEUE_FILTERS.map(([each, label]) => `<a href="#/queue/${each}" class="${each === filter ? 'on' : ''}"${each === filter ? ' aria-current="true"' : ''}>${label}<span class="n">${shownQueue(each).length}</span></a>`).join('');
  const unread = model.cards === 'live' ? '' : `<p class="refused">Your questions are not listed here: cc-slack ${esc(model.cards)}</p>`;
  const rest = shownQueue('all').length;
  const done = `<div class="focus-card done"><span class="bigcheck" aria-hidden="true">✓</span><h2>${filter === 'all' ? 'Nothing waits on you.' : NONE_LEFT[filter]}</h2>${filter !== 'all' && rest ? `<p><a href="#/queue/all">${rest} other ${rest === 1 ? 'thing waits' : 'things wait'} in All ›</a></p>` : ''}</div>`;
  const shown = item ? { question: decisionCard, tap: prCard, late: lateCard }[item.kind](item, at, list.length) : done;
  return `<div class="page-head">
      <h1 class="h1">${list.length ? `${list.length} ${list.length === 1 ? 'thing waits' : 'things wait'} on you` : 'Your queue'}</h1>
      <nav class="subtabs" aria-label="Show">${filters}</nav>
    </div>
    ${unread}
    ${shown}
    <footer class="qfoot"><span>As of ${clock(model.at)}</span><span><kbd>←</kbd><kbd>→</kbd> move <kbd>1–9</kbd> answer <kbd>⌘Z</kbd> undo</span></footer>`;
}

function nowScreen() {
  const [project, stream] = route().path;
  const { tree } = index();
  if (project === undefined) return nowPage('All projects', [], projectsLevel(tree), null);
  const streams = tree.get(project);
  if (!streams) return gone('This project');
  if (stream === undefined) return nowPage(fileName(project), [project], streamsLevel(project, streams), [...streams.values()].flat());
  const list = streams.get(stream);
  if (!list) return gone('This workstream');
  return nowPage(fileName(stream), [project, stream], tasksLevel(list), list);
}

function gone(what) {
  return `<p class="empty">${what} has no open work on the dashboard now. <a href="#/now">All projects</a></p>`;
}

function nowPage(title, keys, body, scope) {
  const counts = model.counts;
  const oldest = model.queue.find((item) => item.since);
  const headline = `${counted(counts.live, 'feature')} went live in the last day, ${counts.running} ${counts.running === 1 ? 'is' : 'are'} moving and ${counts.blocked} ${counts.blocked === 1 ? 'is' : 'are'} blocked${counts.queue ? `; ${counts.queue} ${counts.queue === 1 ? 'thing waits' : 'things wait'} on you${oldest ? `, the oldest for ${ago(oldest.since)}` : ''}` : ''}.`;
  return `<div class="eyebrow">Now</div>
    <h1 class="h1 zoom-title">${esc(title)}</h1>
    ${keys.length ? crumbs(keys) : `<p class="lead">${esc(headline)}</p>`}
    <div class="now-grid"><div class="zoom-body">${body}</div>${story(scope)}</div>`;
}

function crumbs(keys, last = '') {
  const steps = [['Projects', '#/now'], ...keys.map((key, i) => [fileName(key), zoomHref('now', ...keys.slice(0, i + 1))])];
  const here = last ? steps : steps.slice(0, -1);
  return `<nav class="crumbs" aria-label="Zoom out">${[...here.map(([name, href]) => `<a href="${href}">${esc(name)}</a>`), `<b>${esc(last || steps.at(-1)[0])}</b>`].join('<span aria-hidden="true">/</span>')}</nav>`;
}

function projectsLevel(tree) {
  const cards = [...tree].map(([key, streams]) => {
    const sub = key === NONE ? 'Orders the coordinator has not filed under a project yet' : counted(streams.size, 'workstream');
    return zoomCard(zoomHref('now', key), fileName(key), sub, [...streams.values()].flat(), key === NONE);
  });
  return `<h2 class="sec">Projects <span class="count">${tree.size}</span></h2>
    <div class="zoom-list">${cards.join('') || '<p class="empty small">No open orders.</p>'}</div>`;
}

function streamsLevel(project, streams) {
  const cards = [...streams].map(([key, list]) => zoomCard(zoomHref('now', project, key), fileName(key), key === NONE ? 'Orders filed under no workstream yet' : counted(list.length, 'task'), list, key === NONE));
  return `<h2 class="sec">Workstreams <span class="count">${streams.size}</span></h2><div class="zoom-list">${cards.join('')}</div>`;
}

function zoomCard(href, name, sub, list, unfiled) {
  const open = list.filter((entry) => entry.state !== 'live').length;
  const asks = new Set(list.flatMap((entry) => entry.cards.map((card) => card.url))).size;
  const blocked = list.filter((entry) => entry.state === 'blocked').length;
  const flags = `${asks ? `<span class="need">${counted(asks, 'question')} for you</span>` : ''}${blocked ? `<span class="need bad">${blocked} blocked</span>` : ''}`;
  return `<a class="zoom-card${unfiled ? ' unfiled' : ''}" href="${href}">
    <span class="zoom-mark" aria-hidden="true">${unfiled ? '?' : esc([...name][0].toUpperCase())}</span>
    <span class="zoom-name"><b>${esc(name)}</b><small>${esc(sub)}</small>${flags ? `<span class="flags">${flags}</span>` : ''}</span>
    <span class="zoom-stat"><b>${open}</b><small>open of ${list.length}</small>${stateBar(list)}</span>
    <span class="arrow" aria-hidden="true">↗</span>
  </a>`;
}

function stateBar(list) {
  const counts = RANK.map((state) => [state, list.filter((entry) => entry.state === state).length]).filter(([, n]) => n);
  return `<span class="statebar" data-tip="${esc(counts.map(([state, n]) => `${n} ${STOP_WORDS[state].toLowerCase()}`).join(', '))}">${counts.map(([state, n]) => `<i class="t-${STOP_TONES[state]}" style="flex:${n}"></i>`).join('')}</span>`;
}

function tasksLevel(list) {
  const asks = [...new Map(list.flatMap((entry) => entry.cards.map((card) => [card.url, card]))).values()].sort((a, b) => new Date(a.asked) - new Date(b.asked));
  const tasks = list.toSorted((a, b) => RANK.indexOf(a.state) - RANK.indexOf(b.state) || new Date(b.updated) - new Date(a.updated));
  return `${asks.length ? `<h2 class="sec">Your asks <span class="count">${asks.length}</span></h2><div class="task-list">${asks.map(askRow).join('')}</div>` : ''}
    <h2 class="sec">Tasks <span class="count">${tasks.length}</span></h2><div class="task-list">${tasks.map(taskRow).join('')}</div>`;
}

function askRow(card) {
  const item = model.queue.find((each) => each.answer === card.url);
  const body = `<span class="task-mark ask" aria-hidden="true"></span><span class="task-body"><b>${esc(card.title)}</b><small>Asked ${clock(card.asked)}, waiting ${ago(card.asked)}</small></span>`;
  return item ? `<button class="task-row" data-queue="${esc(noticeKey(item))}">${body}<span class="task-go">Answer it ↗</span></button>` : `<div class="task-row">${body}</div>`;
}

function taskRow(entry) {
  const merged = entry.prs.filter((pr) => pr.state === 'merged').length;
  const facts = [entry.prs.length ? `${merged} of ${counted(entry.prs.length, 'PR')} merged` : '', entry.cards.length ? `${counted(entry.cards.length, 'decision')} for you` : ''].filter(Boolean).join(' · ');
  return `<a class="task-row" href="#/feature/${esc(entry.id)}">
    <span class="task-mark t-${STOP_TONES[entry.state]}" aria-hidden="true"></span>
    <span class="task-body"><b>${esc(entry.title)}</b>${entry.now ? `<small>${esc(entry.now)}</small>` : ''}</span>
    <span class="task-facts">${pill(STOP_WORDS[entry.state], STOP_TONES[entry.state])}${facts ? `<small>${esc(facts)}</small>` : ''}</span>
    <span class="arrow" aria-hidden="true">↗</span>
  </a>`;
}

function momentText(entry) {
  const text = FEATURE_KINDS.has(entry.kind) ? featureLink(entry.feature, entry.text) : link(entry.text, entry.url);
  return `<b class="what">${entry.kind === 'tap' ? `You tapped ${esc(entry.detail)} on` : HAPPENED[entry.kind]}</b> ${text}`;
}

function story(scope) {
  const ids = scope && new Set(scope.map((entry) => entry.id));
  const told = model.history.filter((entry) => STORY_KINDS.has(entry.kind) && (!ids || ids.has(entry.feature))).slice(0, STORY_LINES);
  return `<aside class="story" aria-label="The live story">
    <div class="eyebrow">The live story</div>
    ${told.length ? `<ol>${told.map((entry) => `<li><time>${clock(entry.at)}</time><p>${momentText(entry)}</p></li>`).join('')}</ol>` : '<p class="meta">Nothing has happened here yet.</p>'}
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

async function openPR(key) {
  const item = model.queue.find((each) => each.kind === 'tap' && (each.card?.prs ?? [{ url: each.url }]).some((row) => prKey(row.url) === key));
  if (item) {
    queueAt = noticeKey(item);
    prRows.set(queueAt, Math.max(0, (item.card?.prs ?? []).findIndex((row) => prKey(row.url) === key)));
    return go('#/queue/all');
  }
  reviewing = key;
  const drawer = $('#drawer');
  const entry = model.review.find((candidate) => candidate.key === key);
  drawer.classList.add('wide');
  drawer.innerHTML = `<div class="dr-bar"><b>${esc(entry?.title ?? key)}</b><button class="linkish" data-close>Close</button></div><p class="empty small">Reading the change…</p>`;
  if (!drawer.open) drawer.showModal();
  const [repo, number] = key.split('#');
  const review = startReview(key);
  const pr = await ask(`/api/pr/${repo}/${number}`);
  if (reviewing !== key) return;
  if (!pr.ok) {
    drawer.querySelector('.empty').outerHTML = `<p class="refused">${esc(pr.error)}</p>`;
    return;
  }
  const showMe = pr.show_me.trim();
  drawer.innerHTML = `<div class="dr-bar"><b>${esc(bare(pr.title))}</b><span class="acts"><a class="ext" href="${esc(pr.url)}" target="_blank" rel="noopener">GitHub ↗</a><button class="linkish" data-close>Close</button></span></div>
    <div class="pr-split">
      <div class="pr-main">
        <div class="dr-tabs">${showMe ? '<button class="on" data-drtab="show">Show me</button>' : ''}<button${showMe ? '' : ' class="on"'} data-drtab="review">Code and review</button><button data-drtab="body">Description</button></div>
        ${showMe ? `<div class="dr-pane body-pane" data-pane="show">${prose(showMe, key)}</div>` : ''}
        <div class="dr-pane review-pane" data-pane="review"${showMe ? ' hidden' : ''}><p class="empty small">Opening the review of ${esc(key)}…</p></div>
        <div class="dr-pane body-pane" data-pane="body" hidden>${prose(pr.body, key)}</div>
      </div>
      <aside class="pr-side">
        <p class="meta">${esc(pr.headRefName)} → ${esc(pr.baseRefName)} · ${pr.changedFiles} files · <span class="add">+${pr.additions}</span> <span class="del">−${pr.deletions}</span>${pr.isDraft ? ' · draft' : ''}</p>
        ${entry ? talkBox(entry) : ''}
      </aside>
    </div>`;
  fill();
  showReview(key, await review);
}

async function startReview(key) {
  const [repo, number] = key.split('#');
  return ask(`/api/review/${repo}/${number}`, { method: 'POST' });
}

function showReview(key, review) {
  const pane = $('#drawer [data-pane="review"]');
  if (reviewing !== key || !pane) return;
  if (!review.ok) {
    pane.innerHTML = `<p class="refused">cc-review could not open ${esc(key)}: ${esc(review.error)}</p>`;
    return;
  }
  if (pane.querySelector('iframe')?.getAttribute('src') === review.url) return;
  pane.innerHTML = `<iframe class="review-frame" title="Code and review of ${esc(key)}" src="${esc(review.url)}" allow="clipboard-write"></iframe>`;
}

async function openReview(key, head) {
  reviews.set(key, { ...reviews.get(key), head, error: null });
  const review = await startReview(key);
  if (!review.ok) {
    reviews.set(key, { head, error: review.error });
    return render();
  }
  reviews.set(key, { head, url: review.url });
  let frame = slotted.get(key);
  slotted.delete(key);
  if (!frame) {
    frame = Object.assign(document.createElement('iframe'), { className: 'review-frame', title: `Code and review of ${key}`, allow: 'clipboard-write' });
    $('#frames').append(frame);
  }
  slotted.set(key, frame);
  if (frame.getAttribute('src') !== review.url) frame.src = review.url;
  for (const [old, kept] of slotted) {
    if (slotted.size <= FRAMES_KEPT) break;
    kept.remove();
    slotted.delete(old);
    reviews.delete(old);
  }
  place();
}

function place() {
  for (const [key, frame] of slotted) {
    const slot = document.querySelector(`#main [data-slot="${CSS.escape(key)}"]`);
    frame.hidden = !slot || Boolean(reviews.get(key)?.error);
    if (frame.hidden) continue;
    const box = slot.getBoundingClientRect();
    Object.assign(frame.style, { top: `${box.top + scrollY}px`, left: `${box.left + scrollX}px`, width: `${box.width}px`, height: `${box.height}px` });
  }
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
  for (const card of document.querySelectorAll('#main [data-read]')) if (prReads.get(card.dataset.read)?.head !== card.dataset.head) readPR(card.dataset.read, card.dataset.head);
  for (const slot of document.querySelectorAll('#main [data-slot]')) if (reviews.get(slot.dataset.slot)?.head !== slot.dataset.head) openReview(slot.dataset.slot, slot.dataset.head);
  place();
}

function talkBox(entry) {
  const said = entry.thread ? talkPane(entry.thread, cardTs(entry.answer)) : '<p class="meta">This change has no Slack card yet, so the answer comes back in your Slack DM.</p>';
  return `<section class="talk"><h4 class="hd">Ask about this change</h4>${said}${sayBox(`r:${entry.key}`, entry.answer ?? '', 'Ask about this change, or tell Claude what to change', entry.answer ? '' : `About ${entry.url}`, entry.thread ?? '')}</section>`;
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
  const projects = [...Map.groupBy(list, (entry) => fileKey(entry.project))].sort(byName);
  const tree = new Map(projects.map(([project, entries]) => [project, new Map([...Map.groupBy(entries, (entry) => fileKey(entry.workstream))].sort(byName))]));
  return { list, byId: new Map(list.map((entry) => [entry.id, entry])), tree };
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
  const held = pr.tap === 'Hold' ? ', held by you' : pr.tap === 'wanted' ? ', wants your tap' : '';
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
      ${job.said ? `<div class="now">${esc(job.said)}</div>` : ''}
      <div class="meta">${facts.filter(Boolean).map(esc).join(' · ')}</div>
      ${fileButtons(job)}
    </div>
  </li>`;
}

function openFile(file, title) {
  const reader = $('#reader');
  const url = `/artifact/${file}`;
  reader.innerHTML = `<div class="dr-bar"><b>${esc(title)}</b><span class="acts"><a class="ext" href="${esc(url)}" target="_blank" rel="noopener">New tab ↗</a><button class="linkish" data-close>Close</button></span></div><iframe title="${esc(title)}" src="${esc(url)}"></iframe>`;
  if (!reader.open) reader.showModal();
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

const laneTree = perModel(() => {
  const filed = (job, field) => fileKey(featureOf(job.feature)[field]);
  const projects = [...Map.groupBy(model.work, (job) => filed(job, 'project'))].sort(byName);
  return new Map(projects.map(([project, lanes]) => [project, new Map([...Map.groupBy(lanes, (job) => filed(job, 'workstream'))].sort(byName))]));
});

function workScreen() {
  const [project, stream, laneId] = route().path;
  const streams = laneTree().get(project);
  const lanes = streams?.get(stream);
  const running = model.work.filter((job) => job.state === 'running').length;
  const keys = [project, stream].filter((key) => key !== undefined);
  const where = [[model.drive, '#/work'], ...keys.map((key, i) => [fileName(key), zoomHref('work', ...keys.slice(0, i + 1))])];
  const zooms = stream === undefined ? `<button class="map-btn" data-zoom="-1" aria-label="Zoom out"${mapZoom === 0 ? ' disabled' : ''}>−</button><button class="map-btn" data-zoom="1" aria-label="Zoom in"${mapZoom === MAP_ZOOMS.length - 1 ? ' disabled' : ''}>+</button>` : '';
  const shown = project === undefined ? projectMap()
    : !streams ? '<p class="empty topo-empty">This project has no lane running or waiting now. <a href="#/work">All projects</a></p>'
    : stream === undefined ? streamMap(project, streams)
    : lanes ? laneZoom(zoomHref('work', project, stream), lanes, laneId)
    : `<p class="empty topo-empty">This workstream has no lane running or waiting now. <a href="${zoomHref('work', project)}">${esc(fileName(project))}</a></p>`;
  return `<div class="page-head">
      <div><div class="eyebrow">Work</div><h1 class="h1">Inside the machine.</h1><p class="lead">${counted(model.work.length, 'lane')}, ${running} working. Click a project to zoom into its workstreams, then a workstream to see each lane.</p></div>
      <button class="secondary" data-motion aria-pressed="${Boolean(paused)}">${paused ? '▷ Resume motion' : 'Ⅱ Pause motion'}</button>
    </div>
    <section class="topology${paused ? ' paused' : ''}" style="--zoom:${MAP_ZOOMS[mapZoom]}" aria-label="Agent topology">
      <div class="topology-bar"><nav class="crumbs" aria-label="Zoom out">${where.map(([name, href], i) => (i === where.length - 1 ? `<b>${esc(name)}</b>` : `<a href="${href}">${esc(name)}</a>`)).join('<span aria-hidden="true">/</span>')}</nav><div>${zooms}</div></div>
      ${shown}
      <div class="map-legend"><span class="t-doing">Working</span><span class="t-you">Waiting or built</span><span class="t-bad">Quiet ${STALL_MINUTES} minutes or more</span><span>Moving packets are decoration, not traffic</span></div>
    </section>`;
}

function projectMap() {
  const nodes = [...laneTree()].map(([key, streams]) => ({ key, href: zoomHref('work', key), sub: key === NONE ? 'Orders not filed yet' : counted(streams.size, 'workstream'), lanes: [...streams.values()].flat() }));
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
      <strong>${esc(name)} ↗</strong>
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
    <a class="btn ghost" href="#/feature/${esc(job.feature)}">See the task ↗</a>
    <p class="rail-note">For: ${esc(entry.title)}</p>
  </aside>`;
}

function jumpItems() {
  return [
    ...SCREENS.map(([, href, title]) => ({ kind: 'Screen', title, href })),
    ...model.queue.map((item) => ({ kind: LABEL[item.kind], title: item.title, href: '#/queue/all', item: noticeKey(item) })),
    ...features().map((entry) => ({ kind: 'Feature', title: entry.title, href: `#/feature/${entry.id}` })),
    ...model.review.map((entry) => ({ kind: 'Change', title: entry.title, pr: entry.key })),
    ...model.work.map((job) => ({ kind: 'Job', title: job.goal, href: `#/feature/${job.feature}` })),
  ];
}

let jumpAt = 0;

function drawJump() {
  const words = $('#jumpfind').value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const found = jumpItems().filter((item) => words.every((word) => `${item.kind} ${item.title}`.toLowerCase().includes(word))).slice(0, 40);
  jumpAt = Math.min(jumpAt, Math.max(0, found.length - 1));
  $('#jumplist').innerHTML = found.map((item, i) => `<li class="${i === jumpAt ? 'on' : ''}" data-jump="${i}" ${item.pr ? `data-pr-jump="${esc(item.pr)}"` : `data-href="${esc(item.href)}"`}${item.item ? ` data-item="${esc(item.item)}"` : ''}><em>${esc(item.kind)}</em>${esc(item.title)}</li>`).join('') || '<li class="none">Nothing matches.</li>';
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

function goJump(row) {
  $('#jump').close();
  if (row.dataset.prJump) return openPR(row.dataset.prJump);
  if (row.dataset.item) queueAt = row.dataset.item;
  go(row.dataset.href);
}

function strip() {
  const picks = model.queue.filter((item) => item.kind === 'question').length;
  const taps = model.queue.filter((item) => item.kind === 'tap').length;
  return [
    ...model.incidents.map((incident) => {
      const said = `Incident since ${clock(incident.created)}: ${incident.title}`;
      return `<a class="al bad" href="#/feature/${esc(incident.id)}" title="${esc(said)}">${esc(said)}</a>`;
    }),
    picks ? `<a class="al warn" href="#/queue/question">${picks} ${picks === 1 ? 'question waits' : 'questions wait'} on your answer</a>` : '',
    taps ? `<a class="al warn" href="#/queue/tap">${taps} ${taps === 1 ? 'change waits' : 'changes wait'} on your tap</a>` : '',
  ].filter(Boolean).join('');
}

function saidRow(entry) {
  return `<li class="happened said">
    <span class="time">${timeOf(entry.at)}</span>
    <span class="dot"></span>
    <div class="body">
      <div><b class="what">${HAPPENED.said}</b></div>
      <div class="said-text">${prose(entry.text)}</div>
      ${entry.visuals.map((each) => (each.kind === 'image' ? shot(each.picture, each.caption) : visual(each.kind, each.source, each.caption))).join('')}
      ${entry.prs.length ? `<ul class="prlist card">${entry.prs.map(prItem).join('')}</ul>` : ''}
      ${entry.prs.some((pr) => pr.tap === 'wanted') ? '<a class="more" href="#/queue/tap">Tap it in your queue ›</a>' : ''}
    </div>
  </li>`;
}

function historyRow(entry) {
  if (entry.kind === 'said') return saidRow(entry);
  return `<li class="happened ${esc(entry.kind)}">
    <span class="time">${timeOf(entry.at)}</span>
    <span class="dot"></span>
    <div class="body">
      <div>${momentText(entry)}</div>
      ${entry.detail && entry.kind !== 'tap' ? `<div class="meta">${esc(entry.detail)}</div>` : ''}
    </div>
  </li>`;
}

const days = perModel(() => {
  if (!model.history.length) return [];
  const byDay = Map.groupBy(model.history, (entry) => dayOf(entry.at));
  const series = [];
  const last = dayOf(model.at);
  for (let day = dayOf(model.history.at(-1).at); day <= last; day = new Date(Date.parse(`${day}T00:00:00Z`) + DAY_MS).toISOString().slice(0, 10)) {
    const moments = byDay.get(day) ?? [];
    const asked = moments.filter((entry) => entry.kind === 'order');
    series.push({ day, moments, merged: moments.filter((entry) => entry.kind === 'merged').length, swears: asked.reduce((sum, entry) => sum + entry.swears, 0), asked: asked.length });
  }
  return series;
});

function quietFor(minutes) {
  return minutes < 120 ? `${minutes} min` : `${Math.round(minutes / 60)} hours`;
}

function timeline(list, withDays) {
  return list.flatMap((entry, i) => {
    const before = list[i - 1];
    const day = dayOf(entry.at);
    const head = withDays && (!before || dayOf(before.at) !== day) ? `<li class="day-head"><a href="${historyHref(day)}">${dayName(day, { weekday: 'long', month: 'long', day: 'numeric' })} ↗</a></li>` : '';
    const quiet = before && !head ? Math.round((new Date(before.at) - new Date(entry.at)) / 60000) : 0;
    return [head, quiet >= GAP_MINUTES ? `<li class="gap">${quietFor(quiet)} with nothing new</li>` : '', historyRow(entry)];
  }).join('');
}

function dayChart(series, field, title, note, unit, zoomed) {
  const top = Math.max(1, ...series.map((each) => each[field]));
  const bars = series.map((each) => {
    const label = dayName(each.day);
    const tone = zoomed ? (zoomed === each ? ' on' : ' dim') : '';
    return `<a class="bar${tone}" href="${historyHref(each.day)}" style="--h:${Math.max(2, (100 * each[field]) / top).toFixed(1)}%" data-tip="${esc(`${label}: ${counted(each[field], unit)}. Click to zoom into the day.`)}" aria-label="${esc(`${label}: ${counted(each[field], unit)}`)}"><span class="col"><i><b>${each[field]}</b></i></span><span class="day">${label}</span></a>`;
  }).join('');
  return `<div class="day-chart ${field}"><div class="chart-head"><span>${title}</span><small>${note}</small></div><div class="bars">${bars}</div></div>`;
}

function historyScreen() {
  const series = days();
  const zoomed = series.find((each) => each.day === route().id);
  const filter = shownFilter();
  const [, , keep] = HISTORY_FILTERS.find(([id]) => id === filter);
  const scope = zoomed ? [zoomed] : series;
  const moments = zoomed ? zoomed.moments : model.history;
  const listed = moments.filter(keep).slice(0, zoomed ? undefined : RECENT_MOMENTS);
  const total = (field) => scope.reduce((sum, each) => sum + each[field], 0);
  const stats = [[total('merged'), 'PRs merged'], [total('swears'), 'swear words in your orders'], [total('asked'), 'orders from you'], [moments.length, 'moments']];
  const filters = HISTORY_FILTERS.map(([id, label]) => `<a href="${historyHref(zoomed?.day, id)}" class="${id === filter ? 'on' : ''}"${id === filter ? ' aria-current="true"' : ''}>${label}</a>`).join('');
  const latest = series.at(-1);
  const zoom = zoomed ? `<a class="map-btn" href="${historyHref()}">← All days</a>` : latest ? `<a class="map-btn" href="${historyHref(latest.day)}">Zoom to ${dayName(latest.day, { month: 'long', day: 'numeric' })} ↗</a>` : '';
  return `<div class="page-head">
      <div><div class="eyebrow">History</div><h1 class="h1">${zoomed ? esc(dayName(zoomed.day, { weekday: 'long', month: 'long', day: 'numeric' })) : 'A little momentum. A little profanity.'}</h1><p class="lead">Click a day to zoom in. Click a moment to follow it back to the work.</p></div>
      <nav class="subtabs" aria-label="Show">${filters}</nav>
    </div>
    <div class="history-layout">
      <section class="history-stage">
        <div class="history-stats">${stats.map(([n, label]) => `<div><b>${n}</b><span>${label}</span></div>`).join('')}</div>
        ${series.length ? dayChart(series, 'merged', 'PRs merged', 'By the Pacific day each merged', 'PR', zoomed) : ''}
        ${series.length ? dayChart(series, 'swears', 'A vocabulary of urgency', 'Swear words in your orders, by the day you sent each', 'swear word', zoomed) : ''}
        <div class="history-zoom"><span>${zoomed ? `Every moment of ${dayName(zoomed.day, { month: 'long', day: 'numeric' })}` : `The newest ${counted(listed.length, 'moment')}`}</span>${zoom}</div>
        <ul class="history">${timeline(listed, !zoomed) || '<li class="empty">Nothing yet.</li>'}</ul>
      </section>
      ${series.length ? historySide(series, zoomed) : ''}
    </div>`;
}

function historySide(series, zoomed) {
  const most = (field) => series.reduce((best, each) => (each[field] > best[field] ? each : best));
  const [busiest, saltiest] = [most('merged'), most('swears')];
  return `<aside class="history-side">
    <div class="eyebrow">${zoomed ? 'A day in the drive' : 'The shape of the drive'}</div>
    <div class="big-date">${zoomed ? Number(zoomed.day.slice(-2)) : series.length}</div>
    <h3>${zoomed ? esc(dayName(zoomed.day, { weekday: 'long' })) : `${series.length === 1 ? 'day' : 'days'} of the drive`}</h3>
    <p>Busiest day: <a href="${historyHref(busiest.day)}">${dayName(busiest.day)}</a>, ${counted(busiest.merged, 'PR')} merged.</p>
    ${saltiest.swears ? `<p>Saltiest day: <a href="${historyHref(saltiest.day)}">${dayName(saltiest.day)}</a>, ${counted(saltiest.swears, 'swear word')} in your orders.</p>` : ''}
    <p class="rail-note">PRs count each change the drive merged, on the Pacific day it merged. Swears count words such as ${SWEAR_WORDS} in your orders' own words, on the day you sent each.</p>
  </aside>`;
}

function prItem(pr) {
  return `<li><a href="${esc(pr.url)}" target="_blank" rel="noopener"><i class="sq ${esc(prTone(pr))}"></i>${esc(pr.title)}</a><em>${esc(prSaid(pr))}, ${esc(pr.review === 'none' ? 'no review' : pr.review)}</em></li>`;
}

function decisionPiece(card) {
  const item = model.queue.find((each) => each.answer === card.url);
  return `<article class="piece you">
    <div class="eyebrow">Decision · needs you</div>
    <h3>${esc(card.title)}</h3>
    <p class="meta">Asked ${clock(card.asked)}, waiting ${ago(card.asked)}</p>
    ${item ? `<div class="piece-foot"><button class="linkish" data-queue="${esc(noticeKey(item))}">Answer it in your queue ↗</button></div>` : ''}
  </article>`;
}

function prPiece(pr) {
  const key = prKey(pr.url);
  return `<article class="piece">
    <div class="eyebrow">Pull request ${esc(key)}</div>
    <h3>${esc(bare(pr.title))}</h3>
    <p class="meta"><i class="sq ${esc(prTone(pr))}"></i> ${esc(prSaid(pr))}, ${esc(pr.review === 'none' ? 'no review' : pr.review)}</p>
    <div class="piece-foot">${pr.state === 'open' ? `<button class="linkish" data-pr="${esc(key)}">Read the change</button>` : '<span></span>'}<a href="${esc(pr.url)}" target="_blank" rel="noopener">GitHub ↗</a></div>
  </article>`;
}

function featureScreen(id) {
  const entry = featureOf(id);
  if (!entry) return '<p class="empty">This feature is no longer on the dashboard. <a href="#/now">Back to Now</a></p>';
  const facts = [
    entry.now ? ['Where it is', esc(entry.now)] : null,
    ['Asked', clock(entry.created)],
    entry.due ? ['Promised', `${clock(entry.due)}${entry.late ? ' <b class="red">late</b>' : ''}`] : null,
    entry.proof ? ['Proof', esc(entry.proof)] : null,
    entry.note ? ['Note', esc(entry.note)] : null,
  ].filter(Boolean);
  const pieces = [...entry.cards.map(decisionPiece), ...entry.prs.map(prPiece)];
  return `<div class="eyebrow">Task</div>
    <h1 class="h1 zoom-title">${esc(entry.title)}</h1>
    ${crumbs([fileKey(entry.project), fileKey(entry.workstream)], 'This task')}
    <div class="now-grid"><div class="zoom-body">
      <div class="kick"><span class="dot ${entry.state.replace(' ', '-')}"></span>${GROUPS[entry.state]}${entry.prs.length ? ` · ${entry.prs.filter((pr) => pr.state === 'merged').length} of ${entry.prs.length} merged` : ''}</div>
      <blockquote class="said">${esc(entry.text)}</blockquote>
      <h2 class="sec">PRs and decisions <span class="count">${pieces.length}</span></h2>
      ${pieces.length ? `<div class="pieces">${pieces.join('')}</div>` : '<p class="empty small">No PR or decision yet.</p>'}
      ${entry.built.length ? `<h2 class="sec">Built, waiting on your design answers</h2><div class="card built">${entry.built.map(builtLine).join('')}</div>` : ''}
      <dl class="factlist">${facts.map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`).join('')}</dl>
      ${entry.jobs.length ? `<h2 class="sec">Jobs</h2><ul class="jobs card">${entry.jobs.map(jobRow).join('')}</ul>` : ''}
    </div>${story([entry])}</div>`;
}

function measure() {
  for (const el of document.querySelectorAll('.fold')) el.classList.toggle('long', el.scrollHeight > el.clientHeight + 2 || el.classList.contains('open'));
}

function render() {
  if (!model) return;
  const { screen, id } = route();
  document.title = `${model.drive}: ${model.counts.queue} waiting on you`;
  $('#brand').textContent = model.drive;
  const at = screen === 'feature' ? 'now' : SCREENS.some(([name]) => name === screen) ? screen : 'queue';
  const counts = { queue: model.counts.queue };
  $('#tabs').innerHTML = SCREENS.map(([name, href, label]) => `<a class="tab${name === at ? ' on' : ''}" href="${href}"${name === at ? ' aria-current="page"' : ''}>${icon(name)}<span>${label}</span>${counts[name] ? `<b class="count" aria-label="${counts[name]} waiting on you">${counts[name]}</b>` : ''}</a>`).join('');
  $('#crumb').textContent = SCREENS.find(([name]) => name === at)[2];
  const raised = strip();
  $('#alerts').innerHTML = raised;
  $('#alerts').hidden = !raised;
  const screens = { queue: () => queueScreen(id), now: nowScreen, work: workScreen, history: historyScreen, feature: () => featureScreen(id) };
  const open = new Set(model.review.map((entry) => entry.key));
  for (const [key, frame] of slotted) {
    if (open.has(key)) continue;
    frame.remove();
    slotted.delete(key);
    reviews.delete(key);
  }
  const html = (screens[screen] ?? queueScreen)();
  const arrived = location.hash !== shownHash;
  shownHash = location.hash;
  $('#main').classList.toggle('arrived', arrived);
  if (arrived) $('#main').innerHTML = html;
  else morphChildren($('#main'), Object.assign(document.createElement('main'), { innerHTML: html }));
  if ($('#jump').open) drawJump();
  measure();
  fill();
  fresh();
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

function fresh() {
  const el = $('#fresh');
  el.classList.toggle('late', !connected);
  el.textContent = connected ? 'Live' : model ? 'Stale' : 'Connecting';
  el.title = !model ? '' : connected ? `Live, updated ${clock(model.at)} Pacific` : `Not updated since ${clock(lostAt)}, showing ${clock(model.at)} Pacific`;
  $('#stale').hidden = connected || !model;
  $('#stale').textContent = model && !connected ? `Not live: this is the drive as of ${clock(model.at)}. Reconnecting…` : '';
}

async function answer(card, label) {
  const asked = [...model.queue, ...model.review].find((candidate) => candidate.answer === card);
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
      toast(card, { ...toasts.get(card), state: 'sent' });
    }
  } catch (error) {
    answers.set(card, { label, error: error.message });
    toast(card, { ...toasts.get(card), state: 'failed', error: error.message });
  }
  render();
  const shown = $(`#drawer[open] [data-card="${CSS.escape(card)}"]`)?.closest('.choices, .pr-picks');
  shown?.nextElementSibling?.classList.contains('refused') && shown.nextElementSibling.remove();
  if (shown) shown.outerHTML = choices({ ...asked, kind: asked.kind ?? 'tap' });
}

async function undo(card) {
  toast(card, { ...toasts.get(card), state: 'undoing' });
  const said = await ask('/api/undo', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ card }) });
  if (!said.ok) toast(card, { ...toasts.get(card), state: 'late', error: said.error });
}

const TOAST_SAID = {
  held: (shown) => `Sending in ${Math.max(0, Math.ceil((shown.until - Date.now()) / 1000))}s`,
  undoing: () => 'Taking it back…',
  undone: () => 'Undone. It waits on you again.',
  sent: () => 'Sent.',
  late: (shown) => shown.error,
  failed: (shown) => `Not sent: ${shown.error}`,
};

function toast(card, shown) {
  toasts.set(card, shown);
  if (!['held', 'undoing'].includes(shown.state)) setTimeout(() => toasts.get(card) === shown && toasts.delete(card) && drawToasts(), 5000);
  drawToasts();
}

function drawToasts() {
  $('#toasts').innerHTML = [...toasts].map(([card, shown]) => `<div class="toast ${shown.state}" role="status">
    <span><b>${esc(shown.label)}</b> · ${esc(shown.title)}</span>
    <span class="meta">${esc(TOAST_SAID[shown.state](shown))}</span>
    ${shown.state === 'held' ? `<button class="btn" data-undo="${esc(card)}">Undo</button>` : ''}
  </div>`).join('');
}

function redrawBoxes(key) {
  render();
  for (const box of document.querySelectorAll(`#drawer form[data-say="${CSS.escape(key)}"]`)) box.outerHTML = sayBox(key, box.dataset.under, box.dataset.placeholder, box.dataset.about);
}

async function say(key, under, words, about, thread) {
  if (!words.trim() || sent.get(key)?.busy) return;
  sent.set(key, { busy: true });
  redrawBoxes(key);
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
  redrawBoxes(key);
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
  stream.onopen = () => {
    connected = true;
    fresh();
  };
  stream.onmessage = (message) => {
    model = JSON.parse(message.data);
    connected = true;
    render();
    notify();
    followTalk();
  };
  stream.onerror = () => {
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
  const form = event.target.closest('form[data-say]');
  if (!form) return;
  event.preventDefault();
  say(form.dataset.say, form.dataset.under, drafts.get(form.dataset.say) ?? '', form.dataset.about, form.dataset.thread);
});
document.addEventListener('click', (event) => {
  const undone = event.target.closest('button[data-undo]');
  if (undone) return undo(undone.dataset.undo);
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
  if (event.target.closest('#notices')) return Notification.requestPermission().then(noticeButton);
  const jumped = event.target.closest('#jumplist [data-jump]');
  if (jumped) return goJump(jumped);
  if (event.target.id === 'jump') return $('#jump').close();
  const closer = event.target.closest('[data-close]') ?? (event.target.matches('#drawer, #reader') ? event.target : null);
  if (closer) return closer.closest('dialog').close();
  const drtab = event.target.closest('[data-drtab]');
  if (drtab) {
    for (const button of document.querySelectorAll('[data-drtab]')) button.classList.toggle('on', button === drtab);
    for (const pane of document.querySelectorAll('[data-pane]')) pane.hidden = pane.dataset.pane !== drtab.dataset.drtab;
    return;
  }
  const folded = event.target.closest('.fold.long');
  if (folded && !event.target.closest('a')) {
    const id = folded.dataset.fold;
    if (expanded.has(id)) expanded.delete(id);
    else expanded.add(id);
    return render();
  }
  const moved = event.target.closest('[data-move]');
  if (moved) return move(Number(moved.dataset.move));
  const prtab = event.target.closest('[data-prtab]');
  if (prtab) {
    prTabs.set(prtab.dataset.item, prtab.dataset.prtab);
    for (const [key, review] of reviews) if (review.error) reviews.delete(key);
    return render();
  }
  const row = event.target.closest('[data-row]');
  if (row) {
    prRows.set(row.dataset.item, Number(row.dataset.row));
    return render();
  }
  const ask = event.target.closest('[data-ask]');
  if (ask) {
    composing = composing === ask.dataset.ask ? null : ask.dataset.ask;
    render();
    return composing && $('#main .ask-box textarea')?.focus();
  }
});
document.addEventListener('mouseover', (event) => {
  const choice = event.target.closest('#main .choice[data-preview]');
  if (choice) preview(choice);
  const cited = event.target.closest('a[data-src]');
  if (cited) return showSource(cited);
  const tipped = event.target.closest('[data-tip]');
  if (tipped) return showTip(tipped);
  hideSource();
});
document.addEventListener('focusin', (event) => {
  if (event.target.matches('#main .choice[data-preview]')) preview(event.target);
  if (event.target.matches('a[data-src]')) showSource(event.target);
  else if (event.target.matches('[data-tip]')) showTip(event.target);
});
document.addEventListener('focusout', (event) => {
  if (event.target.matches('a[data-src], [data-tip]')) hideSource();
});
window.addEventListener('hashchange', () => {
  render();
  window.scrollTo(0, history.state?.y ?? 0);
});
window.addEventListener('scroll', () => {
  clearTimeout(keepPlace.timer);
  keepPlace.timer = setTimeout(keepPlace, 120);
}, { passive: true });
document.addEventListener('click', keepPlace, true);
document.addEventListener('keydown', (event) => {
  const box = event.target.closest?.('textarea[data-draft]');
  if (box && event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    return box.form.requestSubmit();
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault();
    return openJump();
  }
  const writing = event.target.closest?.('input, textarea');
  if ((event.metaKey || event.ctrlKey) && !event.shiftKey && event.key.toLowerCase() === 'z' && !writing) {
    const held = [...toasts].findLast(([, shown]) => shown.state === 'held');
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
  if (free && screen === 'queue') {
    const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
    if (step) {
      event.preventDefault();
      return move(step);
    }
    const option = currentItem()?.card?.options[Number(event.key) - 1];
    if (/^[1-9]$/.test(event.key) && option) {
      event.preventDefault();
      return answer(currentItem().answer, option);
    }
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
  setInterval(async () => {
    if (document.hidden) return;
    for (const slot of document.querySelectorAll('#main [data-slot]')) openReview(slot.dataset.slot, slot.dataset.head);
    const key = reviewing;
    if (key) showReview(key, await startReview(key));
  }, REVIEW_KEEP_MS);
  $('#drawer').addEventListener('close', () => {
    hideSource();
    reviewing = null;
    $('#drawer').classList.remove('wide');
  });
  connect();
});
