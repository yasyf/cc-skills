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
const STOP_WORDS = { blocked: 'Blocked', running: 'Being built', built: 'Built, waits on your answers', waiting: 'Waiting', merged: 'Merged, not live yet', live: 'Live', 'not started': 'Not started' };
const STOP_TONES = { blocked: 'bad', running: 'doing', built: 'you', waiting: 'waiting', merged: 'merged', live: 'done', 'not started': 'not-started' };
const PART_WORDS = { bad: 'Blocked', you: 'Waits on you', doing: 'Being built', waiting: 'Waiting', 'not started': 'Not started', done: 'Done' };
const PART_COUNTS = { bad: 'blocked', you: 'waiting on you', doing: 'being built', waiting: 'waiting', 'not started': 'not started' };
const NODE_KINDS = { part: 'Part', job: 'Job', pr: 'Change', card: 'Your question' };
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
  ['plan', '#/plan', 'Plan'],
  ['review', '#/review', 'Review'],
  ['work', '#/work', 'Work'],
  ['history', '#/history', 'History'],
];
const ICONS = {
  queue: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  now: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  plan: '<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="15" y="15" width="6" height="6" rx="1"/><path d="M6 9v3a3 3 0 0 0 3 3h6"/>',
  review: '<circle cx="6" cy="6" r="3"/><circle cx="18" cy="18" r="3"/><path d="M6 9v12M13 6h3a2 2 0 0 1 2 2v7"/>',
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
const drafts = new Map();
const sent = new Map();
const toasts = new Map();
const TYPED_MARK = '_You, on the dashboard:_ ';
let talk = null;
let reviewing = null;

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

function inline(text, cite = model.repo) {
  const spans = [];
  const keep = (html) => `\u0000${spans.push(html) - 1}\u0000`;
  let out = cite ? String(text).replace(SOURCE_CITE, (_, pre, path, from, to) => `${pre}${keep(sourceLink(cite, path, from, to))}`) : String(text);
  out = out
    .replace(/`([^`]+)`/g, (_, code) => keep(`<code>${esc(code)}</code>`))
    .replace(/<(https?:\/\/[^|>\s]+)\|([^>]+)>/g, (_, url, label) => keep(link(label, url)))
    .replace(/<(https?:\/\/[^|>\s]+)>/g, (_, url) => keep(link(url, url)))
    .replace(/!\[([^\]]*)\]\((https?:\/\/[^)\s]+)\)/g, (_, alt, url) => keep(`<a class="shot" href="${esc(url)}" target="_blank" rel="noopener"><img src="${esc(url)}" alt="${esc(alt)}" loading="lazy"></a>`))
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
      blocks.push(parts[i - 1].toLowerCase() === 'arch' ? `<div class="arch-fig" data-arch="${esc(part)}"></div>` : `<pre><code>${esc(part.replace(/\n$/, ''))}</code></pre>`);
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
      if (lines.every((line) => /^\s*\|.*\|\s*$/.test(line))) {
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

async function drawFigures(root) {
  const figures = [...root.querySelectorAll('.arch-fig:not(.drawn)')];
  if (!figures.length) return;
  for (const figure of figures) figure.classList.add('drawn');
  const { mount } = await import(model.arch_lib);
  for (const figure of figures) {
    try {
      mount(figure, JSON.parse(figure.dataset.arch), { key: false });
    } catch (error) {
      figure.innerHTML = `<p class="refused">This diagram does not draw: ${esc(error.message)}</p>`;
    }
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

function sayBox(key, under, placeholder, about = '') {
  const was = sent.get(key);
  const note = was?.busy ? '<span class="meta">Sending…</span>'
    : was?.error ? `<span class="refused">Not sent: ${esc(was.error)}</span>`
    : was?.at ? `<span class="meta">✓ Sent ${clock(was.at)}. The drive has it, and your DM shows it.</span>` : '';
  return `<form class="say" data-say="${esc(key)}" data-under="${esc(under)}" data-about="${esc(about)}" data-placeholder="${esc(placeholder)}">
    <textarea data-draft="${esc(key)}" rows="1" placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}"${was?.busy ? ' disabled' : ''}>${esc(drafts.get(key) ?? '')}</textarea>
    <button class="btn"${was?.busy ? ' disabled' : ''}>Send</button>
    ${note}
  </form>`;
}

function pictures(paths) {
  return paths?.length ? `<div class="pics">${paths.map((path) => `<a href="/api/picture?path=${encodeURIComponent(path)}" target="_blank" rel="noopener"><img loading="lazy" src="/api/picture?path=${encodeURIComponent(path)}" alt=""></a>`).join('')}</div>` : '';
}

function ranked(item) {
  return `<span class="label"><b class="rank">${item.rank}</b>${LABEL[item.kind]}</span>`;
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
  const figures = pictures(card.pictures);
  const links = [...(card.links ?? []), ...(card.refs ?? [])].filter((each) => !SLACK.test(each.url)).map((each) => link(each.label, each.url)).join(' · ');
  return `<article class="item question">
    ${ranked(item)}
    <div class="body">
      <h3>${inline(card.title || item.title)}</h3>
      <div class="meta">${esc([waited(item), item.holds ? `holds up ${item.holds} finished` : '', item.blocks.length ? `blocks ${item.blocks.join('; ')}` : ''].filter(Boolean).join(' · '))}</div>
      ${body ? fold(`q:${item.answer}`, body, 4) : ''}
      ${figures}
      ${links ? `<div class="links">${links}</div>` : ''}
      ${choices(item)}
      ${sayBox(`q:${item.answer}`, item.answer, 'Reply, or tell Claude something else')}
    </div>
  </article>`;
}

function prRow(pr, cardId, alone) {
  const fields = PR_FIELDS.filter(([key]) => pr[key])
    .map(([key, label]) => `<dt>${label}</dt><dd>${fold(`${cardId}:${pr.url}:${key}`, prose(pr[key], prKey(pr.url)), 2)}</dd>`)
    .join('');
  const waits = [pr.blocks, pr.lands ? `Lands once ${pr.lands}` : ''].filter(Boolean).map(esc).join(' · ');
  return `<section class="pr">
    <div class="pr-head"><b>${alone ? '' : esc(bare(pr.title))}</b><span class="acts"><button class="linkish" data-pr="${esc(prKey(pr.url))}">View change</button><a class="ext" href="${esc(pr.url)}" target="_blank" rel="noopener">GitHub ↗</a></span></div>
    ${fields ? `<dl class="fields">${fields}</dl>` : ''}
    ${pictures(pr.pictures)}
    ${waits ? `<div class="meta">${waits}</div>` : ''}
  </section>`;
}

function tapCard(item) {
  if (!item.card) {
    return `<article class="item tap bare">
      ${ranked(item)}
      <div class="body"><h3>${link(item.title, item.url)}</h3><div class="meta">${esc(item.detail)}</div>${sayBox(`r:${prKey(item.url)}`, '', 'Tell Claude what to change', `About ${item.url}`)}</div>
    </article>`;
  }
  const card = item.card;
  return `<article class="item tap">
    ${ranked(item)}
    <div class="body">
      <h3>${esc(bare(card.title.replace(/^\w+: approve (.*)\?$/, '$1')))}</h3>
      <div class="meta">${esc(waited(item))}${item.detail ? ` · ${esc(item.detail)}` : ''}</div>
      ${card.body ? fold(`b:${item.answer}`, prose(card.body), 3) : ''}
      ${(card.prs ?? []).map((pr, _, all) => prRow(pr, item.answer, all.length === 1)).join('')}
      ${choices(item)}
      ${sayBox(`t:${item.answer}`, item.answer, 'Tell Claude what to change')}
    </div>
  </article>`;
}

function lateCard(item) {
  return `<article class="item late">
    ${ranked(item)}
    <div class="body"><h3>${esc(item.title)}</h3><div class="meta">${esc(waited(item))}</div>${item.detail ? `<p class="said">${esc(item.detail)}</p>` : ''}${item.url ? sayBox(`l:${item.url}`, item.url, 'Ask where it is, or change it') : ''}</div>
  </article>`;
}

function queueScreen(filter) {
  if (filter) queueFilter = filter;
  const [, , keep] = QUEUE_FILTERS.find(([id]) => id === queueFilter);
  const shown = model.queue.map((item, at) => ({ ...item, rank: at + 1 })).filter(keep);
  const count = model.queue.length;
  const head = count ? `${count} ${count === 1 ? 'thing waits' : 'things wait'} on you` : 'Nothing waits on you';
  const filters = QUEUE_FILTERS.map(([id, label, test]) => {
    const n = model.queue.filter(test).length;
    return `<button data-queue="${id}" class="${id === queueFilter ? 'on' : ''}">${label}<span class="n">${n}</span></button>`;
  }).join('');
  const cards = shown.map((item) => ({ question: questionCard, tap: tapCard, late: lateCard })[item.kind](item)).join('');
  const unread = model.cards === 'live' ? '' : `<p class="refused">Your questions are not listed here: cc-slack ${esc(model.cards)}</p>`;
  return `<h1 class="h1">${head}</h1>
    ${unread}
    <div class="subtabs">${filters}</div>
    <div class="queue">${cards || '<p class="empty">Nothing here.</p>'}</div>`;
}

const REVIEW_TABS = [
  ['you', 'Waiting on you'],
  ['not ready', 'Not ready yet'],
  ['decided', 'You approved or held'],
];

function band(entry) {
  if (entry.tap === 'Approve') return ['ok', 'Approved, landing'];
  if (entry.tap === 'Hold') return ['held', 'You held it'];
  if (entry.group === 'you') return ['you', 'Waiting on you'];
  if (entry.checks === 'fail') return ['bad', 'Tests failing'];
  if (entry.checks === 'pending') return ['doing', 'Tests running'];
  if (entry.stuck) return ['bad', `Stuck: ${entry.stuck.as}`];
  return ['', entry.review === 'approved' ? 'Approved by review, waiting its turn' : 'Waiting on a review'];
}

function reviewCard(entry) {
  const [tone, word] = band(entry);
  const card = entry.card;
  const rows = card ? (card.prs ?? []).map((pr, _, all) => prRow(pr, entry.answer, all.length === 1)).join('') : '';
  const acts = `<span class="acts"><button class="linkish" data-pr="${esc(entry.key)}">Details</button><a class="ext" href="${esc(entry.url)}" target="_blank" rel="noopener">GitHub ↗</a></span>`;
  return `<article class="mr ${tone}">
    <div class="band">${esc(word)}</div>
    <div class="mr-body">
      <div class="pr-head"><h3>${esc(card ? bare(card.title.replace(/^\w+: approve (.*)\?$/, '$1')) : entry.title)}</h3>${card ? '' : acts}</div>
      <div class="meta">${esc(entry.detail)}${entry.since ? ` · waiting ${ago(entry.since)}` : ''}</div>
      ${rows}
      ${card && entry.group === 'you' ? choices({ ...entry, kind: 'tap' }) : ''}
      ${sayBox(`r:${entry.key}`, entry.answer ?? '', 'Tell Claude what to change', entry.answer ? '' : `About ${entry.url}`)}
    </div>
  </article>`;
}

function reviewScreen(tab) {
  const current = REVIEW_TABS.some(([id]) => id === tab) ? tab : 'you';
  const tabs = REVIEW_TABS.map(([id, label]) => `<a class="${id === current ? 'on' : ''}" href="#/review/${encodeURIComponent(id)}">${label}<span class="n">${model.review.filter((entry) => entry.group === id).length}</span></a>`).join('');
  const seen = new Set();
  const shown = model.review.filter((entry) => entry.group === current && !(entry.answer && seen.has(entry.answer)) && (seen.add(entry.answer) || true));
  const byFeature = Map.groupBy(shown, (entry) => entry.feature_title || 'No feature');
  const groups = [...byFeature].map(([title, entries]) => `<h2 class="lane">${esc(title)}</h2>${entries.map(reviewCard).join('')}`).join('');
  return `<h1 class="h1">Review <span class="count">every open change of this drive, as of ${clock(model.at)}</span></h1>
    <div class="subtabs">${tabs}</div>
    ${groups || '<p class="empty">Nothing here.</p>'}`;
}

function nowScreen() {
  const counts = model.counts;
  const oldest = model.queue.find((item) => item.since);
  const headline = `${counts.live} ${counts.live === 1 ? 'feature' : 'features'} went live in the last day, ${counts.running} ${counts.running === 1 ? 'is' : 'are'} moving and ${counts.blocked} ${counts.blocked === 1 ? 'is' : 'are'} blocked${counts.queue ? `; ${counts.queue} ${counts.queue === 1 ? 'thing waits' : 'things wait'} on you${oldest ? `, the oldest for ${ago(oldest.since)}` : ''}` : ''}.`;
  const blocked = (model.status.find((group) => group.state === 'blocked')?.features ?? []).map((entry) => `<a class="hc bad" href="#/feature/${esc(entry.id)}">
      <span class="bar-label">Blocked</span><b>${esc(entry.title)}</b>${entry.now ? `<span>${esc(entry.now)}</span>` : ''}
      <em>since ${clock(entry.updated)}${entry.late ? ', late' : ''}</em></a>`).join('');
  const running = model.work.filter((job) => job.state === 'running').slice(0, 12).map((job) => {
    const quiet = isStalled(job);
    return `<a class="hc ${quiet ? 'you' : 'doing'}" href="#/feature/${esc(job.feature)}">
      <span class="bar-label">${quiet ? `Quiet ${ago(job.updated)}` : 'Running'}</span><b>${esc(job.goal)}</b>${job.said ? `<span>${esc(job.said)}</span>` : ''}
      <em>last word ${ago(job.updated)} ago</em></a>`;
  }).join('');
  const stops = model.milestones;
  const live = stops.filter((stop) => stop.state === 'live').length;
  const map = stops.length ? `<section class="panel">${journey(currentStop())}<p class="meta">${live} of ${stops.length} milestones are live. <a class="more" href="#/plan">Plan ›</a></p></section>` : '';
  return `<h1 class="headline">${esc(headline)}</h1>
    <p class="asof">As of ${clock(model.at)} · ${model.progress.merged} of ${model.progress.prs} pull requests merged since the drive began</p>
    ${map}
    <div class="cols">
      <section><h2 class="sec">What blocks it, nearest first</h2>${blocked || '<p class="empty small">Nothing is blocked.</p>'}</section>
      <section><h2 class="sec">Work on it</h2>${running || '<p class="empty small">No job is running.</p>'}${model.work.length > 12 ? '<a class="more" href="#/work">All work ›</a>' : ''}</section>
    </div>`;
}

function currentStop() {
  return model.milestones.findIndex((stop) => stop.state !== 'live');
}

function needsYou(stop) {
  return stop.parts.some((part) => part.state === 'you');
}

function pill(word, tone) {
  return `<span class="pill t-${tone}">${esc(word)}</span>`;
}

function stopWaits(stop) {
  const open = stop.parts.filter((part) => part.state !== 'done');
  if (!open.length) return stop.state === 'live' ? 'Nothing: it is live' : 'Nothing open';
  const counts = Object.entries(PART_COUNTS).map(([state, word]) => [open.filter((part) => part.state === state).length, word]).filter(([n]) => n);
  return `${open.length} of ${stop.parts.length} parts open: ${counts.map(([n, word]) => `${n} ${word}`).join(', ')}`;
}

function journey(current) {
  const stops = model.milestones;
  return `<div class="journey" style="--n:${stops.length}">${stops.map((stop, i) => `<a class="ms t-${STOP_TONES[stop.state]}${i === current ? ' cur' : ''}" href="#/plan/${i}">
      <span class="here">${i === current ? 'Drive is here' : ''}</span><span class="pin"></span>
      <span class="nm">${esc(stop.name)}</span><span class="st">${STOP_WORDS[stop.state]}</span>${needsYou(stop) ? '<span class="need">Needs you</span>' : ''}
    </a>`).join('')}</div>`;
}

function planRow(href, cells) {
  return `<a class="prow" href="${href}">${cells.map((cell) => `<span>${cell}</span>`).join('')}</a>`;
}

function planHead(...labels) {
  return `<div class="phead">${labels.map((label) => `<span>${label}</span>`).join('')}</div>`;
}

function planFold(id, label, rows) {
  if (!rows.length) return '';
  return `<details class="pfold" data-group="${id}"${opened.get(id) ? ' open' : ''}><summary>${label}</summary>${rows.join('')}</details>`;
}

function partsTable(parts, group) {
  const partRow = (part) => planRow(`#/feature/${esc(part.order)}`, [`<b>${esc(part.title)}</b>`, pill(PART_WORDS[part.state], part.state.replace(' ', '-')), esc(part.blocks), esc(part.doing)]);
  const open = parts.filter((part) => !['done', 'not started'].includes(part.state));
  const waiting = parts.filter((part) => part.state === 'not started');
  const done = parts.filter((part) => part.state === 'done');
  return `<div class="ptable parts">${planHead('Part', 'State', 'What blocks it', 'What is being done')}${open.map(partRow).join('') || '<p class="empty small">Every started part is done.</p>'}
      ${planFold(`${group}-waiting`, `<b>Not started</b> <span class="count">${waiting.length}</span>`, waiting.map(partRow))}
      ${planFold(`${group}-done`, `<b>Done</b> <span class="count">${done.length}</span>`, done.map(partRow))}
    </div>`;
}

function planScreen() {
  const stops = model.milestones;
  const loose = model.unplanned ? `<h2 class="sec">${esc(model.unplanned.name)}</h2>${partsTable(model.unplanned.parts, 'plan-loose')}` : '';
  if (!stops.length) return `<h1 class="h1">Plan</h1><p class="empty">No milestones yet. Once the coordinator names the milestone each of your orders moves, their map shows here.</p>${loose}`;
  const parts = stops.flatMap((stop) => stop.parts);
  const done = parts.filter((part) => part.state === 'done');
  const share = parts.length ? Math.round((100 * done.length) / parts.length) : 0;
  const current = currentStop();
  const live = stops.filter((stop) => stop.state === 'live').length;
  const lead = current < 0 ? `All ${stops.length} milestones are live.` : `${live} of ${stops.length} milestones are live. ${stops[current].name}: ${STOP_WORDS[stops[current].state].toLowerCase()}, ${stopWaits(stops[current]).replace(/^./, (c) => c.toLowerCase())}.`;
  const stopRow = (stop) => planRow(`#/plan/${stops.indexOf(stop)}`, [`<b>${esc(stop.name)}</b>`, `${pill(STOP_WORDS[stop.state], STOP_TONES[stop.state])}${needsYou(stop) ? ' <span class="need">Needs you</span>' : ''}`, esc(stopWaits(stop))]);
  const later = stops.filter((stop, i) => stop.state === 'not started' && i > current);
  const middle = stops.filter((stop) => stop.state !== 'live' && !later.includes(stop));
  return `<div class="plan-head">
      <div><h1 class="h1">Plan</h1><p class="lead">${esc(lead)}</p></div>
      <div class="ringbox"><span class="ring" style="--p:${share}%"><span>${share}<small>%</small></span></span><span>${done.length} of ${parts.length} parts done</span></div>
    </div>
    <section class="panel">${journey(current)}</section>
    <h2 class="sec">Milestones</h2>
    <div class="ptable stops">${planHead('Milestone', 'State', 'What it waits on')}
      ${planFold('plan-live', `<b>${live} ${live === 1 ? 'milestone' : 'milestones'} live</b>`, stops.filter((stop) => stop.state === 'live').map(stopRow))}
      ${middle.map(stopRow).join('')}
      ${planFold('plan-later', `<b>${later.length} ${later.length === 1 ? 'milestone' : 'milestones'} later</b>`, later.map(stopRow))}
    </div>
    <h2 class="sec">Parts still open</h2>
    ${partsTable(parts, 'plan')}
    ${loose}`;
}

function milestoneScreen(at) {
  const stops = model.milestones;
  const i = Number(at);
  const stop = stops[i];
  if (!stop) return '<p class="empty">This milestone is no longer on the plan. <a href="#/plan">Back to Plan</a></p>';
  const step = (j, label, key) => (stops[j] ? `<a class="btn ghost" href="#/plan/${j}">${label} <kbd>${key}</kbd></a>` : '');
  const prs = stop.features.flatMap((entry) => entry.prs);
  const done = stop.parts.filter((part) => part.state === 'done');
  return `<a class="back" href="#/plan">‹ Plan</a>
    <div class="kick">Milestone ${i + 1} of ${stops.length} · ${pill(STOP_WORDS[stop.state], STOP_TONES[stop.state])}${needsYou(stop) ? ' <span class="need">Needs you</span>' : ''}</div>
    <h1 class="h1">${esc(stop.name)}</h1>
    <dl class="factlist">
      <dt>What it waits on</dt><dd>${esc(stopWaits(stop))}</dd>
      <dt>Your orders in it</dt><dd>${stop.features.map((entry) => `<a href="#/feature/${esc(entry.id)}">${esc(entry.title)}</a> ${pill(STOP_WORDS[entry.state], STOP_TONES[entry.state])}`).join('<br>')}</dd>
    </dl>
    ${stop.nodes.length ? `<h3 class="sec">What stands in its way, each arrow pointing at what it holds up</h3>${chart(stop)}` : '<p class="empty small">Nothing stands in its way.</p>'}
    ${planFold(`plan-done-${i}`, `<b>Done so far</b> <span class="count">${done.length}</span>`, done.map((part) => planRow(`#/feature/${esc(part.order)}`, [`<b>${esc(part.title)}</b>`])))}
    ${prs.length ? `<h3 class="sec">Its pull requests <span class="count">${prs.length}</span></h3><ul class="prlist card">${prs.map(prItem).join('')}</ul>` : ''}
    <p class="steps">${step(i - 1, '‹ Previous milestone', 'K')}${step(i + 1, 'Next milestone ›', 'J')}</p>`;
}

function chart(stop) {
  const into = Map.groupBy(stop.edges, (edge) => edge.to);
  const depth = new Map();
  const visit = (id, d, path) => {
    if (path.has(id) || (depth.get(id) ?? -1) >= d) return;
    depth.set(id, d);
    for (const edge of into.get(id) ?? []) visit(edge.from, d + 1, new Set([...path, id]));
  };
  visit('milestone', 0, new Set());
  const deepest = Math.max(...depth.values());
  const place = new Map([['milestone', 0]]);
  const columns = [];
  for (let d = 1; d <= deepest; d++) {
    const column = stop.nodes.filter((node) => depth.get(node.id) === d);
    const heldUp = (node) => stop.edges.filter((edge) => edge.from === node.id && place.has(edge.to)).map((edge) => place.get(edge.to));
    const score = new Map(column.map((node, n) => [node.id, heldUp(node).length ? Math.min(...heldUp(node)) : n]));
    column.sort((a, b) => score.get(a.id) - score.get(b.id));
    column.forEach((node, n) => place.set(node.id, n));
    columns.unshift(column);
  }
  const goal = `<div class="stage goal"><div class="node t-${STOP_TONES[stop.state]}" data-node="milestone"><span class="nb">Then</span><b>${esc(stop.name)}</b></div></div>`;
  return `<div class="chart"><div class="flow" data-edges="${esc(JSON.stringify(stop.edges))}"><svg class="arrows" aria-hidden="true"></svg>${columns.map((column) => `<div class="stage">${column.map(nodeBox).join('')}</div>`).join('')}${goal}</div></div>`;
}

function nodeBox(node) {
  const tone = node.state.replace(' ', '-');
  const inner = `<span class="nb">${NODE_KINDS[node.kind]} · ${PART_WORDS[node.state]}</span><b title="${esc(node.text)}">${esc(node.text)}</b>${node.how ? `<span class="how" title="${esc(node.how)}">${esc(node.how)}</span>` : ''}${node.since ? `<span class="clk">since ${clock(node.since)}</span>` : ''}`;
  if (node.kind === 'pr') return `<button class="node t-${tone}" data-node="${esc(node.id)}" data-pr="${esc(prKey(node.url))}">${inner}</button>`;
  return `<a class="node t-${tone}" data-node="${esc(node.id)}" href="${node.kind === 'card' ? '#/queue/question' : `#/feature/${esc(node.order)}`}">${inner}</a>`;
}

function drawArrows() {
  for (const flow of document.querySelectorAll('#main .flow[data-edges]')) {
    const frame = flow.getBoundingClientRect();
    const box = (id) => flow.querySelector(`[data-node="${CSS.escape(id)}"]`).getBoundingClientRect();
    const tone = (id) => flow.querySelector(`[data-node="${CSS.escape(id)}"]`).className.match(/\bt-([\w-]+)/)[1];
    const paths = JSON.parse(flow.dataset.edges).map(({ from, to }) => {
      const [a, b] = [box(from), box(to)];
      const [x1, y1, x2, y2] = [a.right - frame.left, a.top + a.height / 2 - frame.top, b.left - frame.left - 2, b.top + b.height / 2 - frame.top];
      const bend = Math.max(24, (x2 - x1) / 2);
      return `<path class="t-${tone(from)}" d="M${x1} ${y1} C${x1 + bend} ${y1} ${x2 - bend} ${y2} ${x2} ${y2}" marker-end="url(#head)"/>`;
    });
    const svg = flow.querySelector('svg');
    svg.setAttribute('viewBox', `0 0 ${frame.width} ${frame.height}`);
    svg.innerHTML = `<defs><marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 10 5 0 10z"/></marker></defs>${paths.join('')}`;
  }
}

async function openPR(key) {
  talk = null;
  reviewing = key;
  const drawer = $('#drawer');
  const entry = model.review.find((candidate) => candidate.key === key);
  drawer.classList.add('wide');
  drawer.innerHTML = `<div class="dr-bar"><b>${esc(entry?.title ?? key)}</b><button class="linkish" data-close>Close</button></div><p class="empty small">Reading the change…</p>`;
  if (!drawer.open) drawer.showModal();
  const [repo, number] = key.split('#');
  const review = startReview(key);
  const reply = await fetch(`/api/pr/${repo}/${number}`);
  const pr = await reply.json();
  if (reviewing !== key) return;
  if (!reply.ok) {
    drawer.querySelector('.empty').textContent = pr.error;
    return;
  }
  const answerable = entry?.card && entry.group === 'you' ? choices({ ...entry, kind: 'tap' }) : '';
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
        ${answerable}
        ${entry ? talkBox(entry) : ''}
      </aside>
    </div>`;
  if (entry?.thread) drawTalk(entry);
  showReview(key, await review);
}

async function startReview(key) {
  const [repo, number] = key.split('#');
  const reply = await fetch(`/api/review/${repo}/${number}`, { method: 'POST' });
  return { ok: reply.ok, ...(await reply.json()) };
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

function talkBox(entry) {
  const said = entry.thread ? '<div class="thread" data-talk><p class="meta">Reading what was said about it…</p></div>' : '<p class="meta">This change has no Slack card yet, so the answer comes back in your Slack DM.</p>';
  return `<section class="talk"><h4 class="hd">Ask about this change</h4>${said}${sayBox(`r:${entry.key}`, entry.answer ?? '', 'Ask about this change, or tell Claude what to change', entry.answer ? '' : `About ${entry.url}`)}</section>`;
}

async function drawTalk(entry) {
  if (talk?.key === entry.key && talk.reading) {
    talk.again = true;
    return;
  }
  const reading = { key: entry.key, thread: entry.thread, reading: true, again: false };
  talk = reading;
  let said;
  try {
    said = (await readThread(entry.thread)).map((each) => message(each, waitingCards())).join('') || '<p class="meta">Nothing has been asked about it yet. What you ask, and the answer, show here.</p>';
  } catch (error) {
    said = `<p class="refused">${esc(error.message)}</p>`;
  }
  if (talk !== reading) return;
  reading.reading = false;
  const pane = $('#drawer [data-talk]');
  if (pane) pane.innerHTML = said;
  if (reading.again) drawTalk(reading);
}

function followTalk() {
  if (talk && $('#drawer').open) drawTalk(talk);
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

async function readThread(ts) {
  const reply = await fetch(`/api/thread/${ts}`);
  const thread = await reply.json();
  if (!reply.ok) throw new Error(thread.error);
  return thread.messages.filter((said) => said.ts !== ts);
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

let indexed = null;

function features() {
  if (indexed?.model !== model) {
    const all = [...model.status.flatMap((group) => group.features), ...model.milestones.flatMap((stop) => stop.features)];
    indexed = { model, list: [...new Map(all.map((entry) => [entry.id, entry])).values()] };
  }
  return indexed.list;
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

function builtLine(built) {
  return `<div class="now">Built on <code>${esc(built.branch)}</code>, ${built.waits.length ? 'no PR yet' : 'its PR opens next'}</div>`;
}

function cardsLine(cards) {
  if (!cards.length) return '';
  const asks = cards.map((card) => `<a href="${esc(card.url)}" target="_blank" rel="noopener">${esc(card.title)}</a>, asked ${esc(clock(card.asked))}`);
  return `<div class="now">Waits on your answer to ${asks.join('; ')}</div>`;
}

function jobRow(job, withFeature) {
  const quiet = minutesSince(job.updated);
  const stall = isStalled(job) ? `<span class="badge red">quiet ${ago(job.updated)}</span>` : '';
  const owner = withFeature ? features().find((entry) => entry.id === job.feature) : null;
  const facts = [JOB_STATES[job.state] ?? job.state, job.kind, job.where, `last word ${quiet < 1 ? 'just now' : `${ago(job.updated)} ago`}`, job.state === 'built' ? `built on ${job.branch}` : '', job.waits ? `waits on ${job.waits} ${job.waits === 1 ? 'card or job' : 'cards or jobs'}` : '', job.prs ? `${job.prs} PRs` : ''];
  return `<li class="job ${esc(job.state)}">
    <span class="dot"></span>
    <div class="body">
      <div class="title">${esc(job.goal)} ${stall}</div>
      ${owner ? `<div class="meta">for ${featureLink(owner.id, owner.title)}</div>` : ''}
      ${job.said ? `<div class="now">${esc(job.said)}</div>` : ''}
      <div class="meta">${facts.filter(Boolean).map(esc).join(' · ')}</div>
      ${job.files.length ? `<div class="files">${job.files.map((kind) => `<button class="linkish" data-file="${esc(job.id)}/${kind}" data-title="${esc(`${job.id} ${FILE_WORDS[kind].toLowerCase()}: ${job.goal}`)}">${FILE_WORDS[kind]}</button>`).join('')}</div>` : ''}
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
  if (tip.matches(':popover-open')) tip.hidePopover();
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

function workScreen() {
  const running = model.work.filter((job) => job.state === 'running');
  const stalled = running.filter(isStalled);
  const head = `<h1 class="h1">Work <span class="count">${running.length} running, ${model.work.length - running.length} waiting</span>${stalled.length ? `<span class="badge red">${stalled.length} quiet ${STALL_MINUTES} minutes or more</span>` : ''}</h1>`;
  const groups = [
    ['stalled', `Quiet ${STALL_MINUTES} minutes or more`, stalled, true],
    ['running', 'Running', running.filter((job) => !isStalled(job)), true],
    ['built', 'Built, no PR until you answer', model.work.filter((job) => job.state === 'built'), true],
    ['waiting', 'Waiting', model.work.filter((job) => job.state === 'waiting'), false],
    ['finished', 'Finished in the last day', model.finished, false],
  ].filter(([, , jobs]) => jobs.length);
  if (!groups.length) return `${head}<p class="empty">No job is running, waiting or finished today.</p>`;
  return head + groups.map(([id, label, jobs, open]) => {
    const isOpen = opened.get(`work-${id}`) ?? open;
    const newest = jobs.reduce((a, b) => (new Date(a.updated) > new Date(b.updated) ? a : b));
    return `<details class="group wk ${id}" data-group="work-${id}"${isOpen ? ' open' : ''}>
      <summary><span class="dot"></span>${label} <span class="count">${jobs.length}</span><span class="newest">newest: ${esc(newest.goal)}</span></summary>
      <ul class="jobs">${jobs.map((job) => jobRow(job, true)).join('')}</ul>
    </details>`;
  }).join('');
}

function jumpItems() {
  return [
    ...SCREENS.map(([, href, title]) => ({ kind: 'Screen', title, href })),
    ...model.queue.map((item) => ({ kind: LABEL[item.kind], title: item.title, href: `#/queue/${item.kind}` })),
    ...model.milestones.map((stop, i) => ({ kind: 'Milestone', title: stop.name, href: `#/plan/${i}` })),
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
  $('#jumplist').innerHTML = found.map((item, i) => `<li class="${i === jumpAt ? 'on' : ''}" data-jump="${i}" ${item.pr ? `data-pr-jump="${esc(item.pr)}"` : `data-href="${esc(item.href)}"`}><em>${esc(item.kind)}</em>${esc(item.title)}</li>`).join('') || '<li class="none">Nothing matches.</li>';
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
  location.hash = row.dataset.href;
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

function prItem(pr) {
  return `<li><a href="${esc(pr.url)}" target="_blank" rel="noopener"><i class="sq ${esc(prTone(pr))}"></i>${esc(pr.title)}</a><em>${esc(prSaid(pr))}, ${esc(pr.review === 'none' ? 'no review' : pr.review)}</em></li>`;
}

function featureScreen(id) {
  const entry = features().find((candidate) => candidate.id === id);
  if (!entry) return '<p class="empty">This feature is no longer on the dashboard. <a href="#/now">Back to Now</a></p>';
  const facts = [
    ['Where it is', `${GROUPS[entry.state]}${entry.now ? `: ${esc(entry.now)}` : ''}`],
    ['Asked', clock(entry.created)],
    entry.milestone ? ['Milestone', `<a href="#/plan/${model.milestones.findIndex((stop) => stop.name === entry.milestone)}">${esc(entry.milestone)}</a>`] : null,
    entry.due ? ['Promised', `${clock(entry.due)}${entry.late ? ' <b class="red">late</b>' : ''}`] : null,
    entry.proof ? ['Proof', esc(entry.proof)] : null,
    entry.note ? ['Note', esc(entry.note)] : null,
  ].filter(Boolean);
  const prs = entry.prs.map(prItem).join('');
  return `<a class="back" href="#/now">‹ Now</a>
    <div class="kick"><span class="dot ${entry.state.replace(' ', '-')}"></span>${GROUPS[entry.state]}${entry.prs.length ? ` · ${entry.prs.filter((pr) => pr.state === 'merged').length} of ${entry.prs.length} merged` : ''}</div>
    <h1 class="h1">${esc(entry.title)}</h1>
    <blockquote class="said">${esc(entry.text)}</blockquote>
    <dl class="factlist">${facts.map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`).join('')}</dl>
    ${prs ? `<h3 class="sec">Pull requests</h3><ul class="prlist card">${prs}</ul>` : ''}
    ${entry.built.length ? `<h3 class="sec">Built, waiting on your design answers</h3><div class="card built">${entry.built.map(builtLine).join('')}</div>` : ''}
    ${entry.cards.length ? `<h3 class="sec">Waiting on your answer</h3><div class="card built">${cardsLine(entry.cards)}</div>` : ''}
    ${entry.jobs.length ? `<h3 class="sec">Jobs</h3><ul class="jobs card">${entry.jobs.map((job) => jobRow(job, false)).join('')}</ul>` : ''}`;
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
  const counts = { queue: model.counts.queue, review: model.review.filter((entry) => entry.group === 'you').length };
  $('#tabs').innerHTML = SCREENS.map(([name, href, label]) => `<a class="tab${name === at ? ' on' : ''}" href="${href}"${name === at ? ' aria-current="page"' : ''}>${icon(name)}<span>${label}</span>${counts[name] ? `<b class="count" aria-label="${counts[name]} waiting on you">${counts[name]}</b>` : ''}</a>`).join('');
  $('#crumb').textContent = SCREENS.find(([name]) => name === at)[2];
  const raised = strip();
  $('#alerts').innerHTML = raised;
  $('#alerts').hidden = !raised;
  const screens = { queue: () => queueScreen(id), now: nowScreen, plan: () => (id === undefined ? planScreen() : milestoneScreen(id)), review: () => reviewScreen(decodeURIComponent(id ?? '')), work: workScreen, history: historyScreen, feature: () => featureScreen(id) };
  const y = window.scrollY;
  const typing = document.activeElement?.matches?.('#main textarea[data-draft]') ? document.activeElement : null;
  const caret = typing && [typing.dataset.draft, typing.selectionStart, typing.selectionEnd];
  $('#main').innerHTML = (screens[screen] ?? queueScreen)();
  const box = caret && document.querySelector(`#main textarea[data-draft="${CSS.escape(caret[0])}"]`);
  if (box && !box.disabled) {
    box.focus({ preventScroll: true });
    box.setSelectionRange(caret[1], caret[2]);
  }
  if ($('#jump').open) drawJump();
  window.scrollTo(0, y);
  measure();
  drawArrows();
  fresh();
}

function fresh() {
  const el = $('#fresh');
  el.classList.toggle('late', !connected);
  el.textContent = connected ? 'Live' : model ? 'Stale' : 'Connecting';
  el.title = !model ? '' : connected ? `Live, updated ${clock(model.at)} Pacific` : `Not updated since ${clock(lostAt)}, showing ${clock(model.at)} Pacific`;
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
  const shown = $(`#drawer[open] [data-card="${CSS.escape(card)}"]`)?.closest('.picks');
  shown?.nextElementSibling?.classList.contains('refused') && shown.nextElementSibling.remove();
  if (shown) shown.outerHTML = choices({ ...asked, kind: asked.kind ?? 'tap' });
}

async function undo(card) {
  toast(card, { ...toasts.get(card), state: 'undoing' });
  const reply = await fetch('/api/undo', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ card }) });
  if (!reply.ok) toast(card, { ...toasts.get(card), state: 'late', error: (await reply.json()).error });
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

async function say(key, under, words, about) {
  if (!words.trim() || sent.get(key)?.busy) return;
  sent.set(key, { busy: true });
  redrawBoxes(key);
  try {
    const reply = await fetch('/api/say', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: about ? `${about}: ${words.trim()}` : words.trim(), under }) });
    const said = await reply.json();
    if (!reply.ok) throw new Error(said.error);
    drafts.delete(key);
    sent.set(key, { at: new Date().toISOString() });
    if (talk && key === `r:${talk.key}` && $('#drawer').open) drawTalk(talk);
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
    location.hash = `#/queue/${first.kind}`;
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
    fresh();
  };
}

document.addEventListener('toggle', (event) => {
  const id = event.target.dataset?.group;
  if (id) opened.set(id, event.target.open);
}, true);
document.addEventListener('input', (event) => {
  const box = event.target.closest('textarea[data-draft]');
  if (box) drafts.set(box.dataset.draft, box.value);
});
document.addEventListener('submit', (event) => {
  const form = event.target.closest('form[data-say]');
  if (!form) return;
  event.preventDefault();
  say(form.dataset.say, form.dataset.under, drafts.get(form.dataset.say) ?? '', form.dataset.about);
});
document.addEventListener('click', (event) => {
  const undone = event.target.closest('button[data-undo]');
  if (undone) return undo(undone.dataset.undo);
  const pick = event.target.closest('button[data-card]');
  if (pick) return answer(pick.dataset.card, pick.dataset.label);
  const pr = event.target.closest('button[data-pr]');
  if (pr) return openPR(pr.dataset.pr);
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
document.addEventListener('mouseover', (event) => {
  const cited = event.target.closest('a[data-src]');
  if (cited) showSource(cited);
  else hideSource();
});
document.addEventListener('focusin', (event) => {
  if (event.target.matches('a[data-src]')) showSource(event.target);
});
document.addEventListener('focusout', (event) => {
  if (event.target.matches('a[data-src]')) hideSource();
});
window.addEventListener('hashchange', () => {
  window.scrollTo(0, 0);
  render();
});
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
  const { screen, id } = route();
  const stepped = { j: 1, k: -1 }[event.key];
  if (stepped && screen === 'plan' && id !== undefined && !event.metaKey && !event.ctrlKey && !event.altKey && !event.target.closest?.('input, textarea') && !document.querySelector('dialog[open]')) {
    const next = Number(id) + stepped;
    if (model.milestones[next]) location.hash = `#/plan/${next}`;
    return;
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
  $('#jumpfind').addEventListener('input', () => {
    jumpAt = 0;
    drawJump();
  });
  setInterval(render, 60000);
  window.addEventListener('resize', drawArrows);
  new MutationObserver(() => drawFigures(document)).observe(document.body, { childList: true, subtree: true });
  setInterval(() => [...toasts.values()].some((shown) => shown.state === 'held') && drawToasts(), 1000);
  setInterval(async () => {
    const key = reviewing;
    if (key && !document.hidden) showReview(key, await startReview(key));
  }, REVIEW_KEEP_MS);
  $('#drawer').addEventListener('close', () => {
    hideSource();
    reviewing = null;
    $('#drawer').classList.remove('wide');
  });
  connect();
});
