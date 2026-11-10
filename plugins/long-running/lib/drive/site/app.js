const ZONE = 'America/Los_Angeles';
const STALL_MINUTES = 45;
const GAP_MINUTES = 30;
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
const FOLDED = new Set(['waiting', 'not started']);
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
  now: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  status: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  review: '<circle cx="6" cy="6" r="3"/><circle cx="18" cy="18" r="3"/><path d="M6 9v12M13 6h3a2 2 0 0 1 2 2v7"/>',
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
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
const TYPED_MARK = '_You, on the dashboard:_ ';

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
      const heading = lines[0].match(/^(#{1,4})\s+(.*)$/);
      if (heading) {
        blocks.push(`<h4 class="hd">${inline(heading[2])}</h4>`);
        lines.shift();
        if (!lines.length) continue;
      }
      if (lines.every((line) => /^\s*\|.*\|\s*$/.test(line))) {
        blocks.push(table(lines));
      } else if (lines.every((line) => /^\s*(?:[-*•]|\d+[.)])\s/.test(line))) {
        blocks.push(`<ul>${lines.map((line) => `<li>${inline(line.replace(/^\s*(?:[-*•]|\d+[.)])\s+/, ''))}</li>`).join('')}</ul>`);
      } else {
        blocks.push(`<p>${lines.map(inline).join('<br>')}</p>`);
      }
    }
  });
  return blocks.join('');
}

function table(lines) {
  const cells = (line) => line.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim());
  const rows = lines.filter((line) => !/^\s*\|[\s:|-]+\|\s*$/.test(line)).map(cells);
  const [head, ...body] = rows;
  return `<div class="tablewrap"><table><thead><tr>${head.map((cell) => `<th>${inline(cell)}</th>`).join('')}</tr></thead><tbody>${body
    .map((row) => `<tr>${row.map((cell) => `<td>${inline(cell)}</td>`).join('')}</tr>`)
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
    .map(([key, label]) => `<dt>${label}</dt><dd>${fold(`${cardId}:${pr.url}:${key}`, prose(pr[key]), 2)}</dd>`)
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
  return `<h1 class="h1">${head}</h1>
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
      ${changeBox(entry)}
    </div>
  </article>`;
}

function changeBox(entry) {
  return sayBox(`r:${entry.key}`, entry.answer ?? '', 'Tell Claude what to change', entry.answer ? '' : `About ${entry.url}`);
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
  const taps = model.queue.filter((item) => item.kind === 'tap').length;
  const picks = model.queue.filter((item) => item.kind === 'question').length;
  const late = model.queue.filter((item) => item.kind === 'late').length;
  const needs = [
    taps && `<div class="nl"><span>${taps} ${taps === 1 ? 'change waits' : 'changes wait'} on your approval</span><a class="btn" href="#/queue/tap">Review them</a></div>`,
    picks && `<div class="nl"><span>${picks} ${picks === 1 ? 'question waits' : 'questions wait'} on your answer</span><a class="btn" href="#/queue/question">Answer them</a></div>`,
    late && `<div class="nl"><span>${late} promised ${late === 1 ? 'time has' : 'times have'} passed</span><a class="btn" href="#/queue/late">See them</a></div>`,
  ].filter(Boolean);
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
  return `${needs.length ? `<section class="needs"><span class="tag">Needs you</span><div>${needs.join('')}</div></section>` : ''}
    <h1 class="headline">${esc(headline)}</h1>
    <p class="asof">As of ${clock(model.at)} · ${model.progress.merged} of ${model.progress.prs} pull requests merged since the drive began</p>
    <div class="cols">
      <section><h2 class="sec">What blocks it, nearest first</h2>${blocked || '<p class="empty small">Nothing is blocked.</p>'}</section>
      <section><h2 class="sec">Work on it</h2>${running || '<p class="empty small">No job is running.</p>'}${model.work.length > 12 ? '<a class="more" href="#/work">All work ›</a>' : ''}</section>
    </div>`;
}

function diffHtml(diff) {
  return diff.split(/^(?=diff --git )/m).filter(Boolean).map((file) => {
    const name = file.match(/^diff --git a\/(\S+)/)?.[1] ?? 'file';
    const lines = file.split('\n').slice(1).filter((line) => !/^(?:index |--- |\+\+\+ |new file|deleted file|similarity|rename )/.test(line));
    const body = lines.map((line) => `<span class="${line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : line.startsWith('@@') ? 'hunk' : ''}">${esc(line)}</span>`).join('\n');
    return `<details class="file" open><summary>${esc(name)}</summary><pre class="diff">${body}</pre></details>`;
  }).join('');
}

async function openPR(key) {
  const drawer = $('#drawer');
  const entry = model.review.find((candidate) => candidate.key === key);
  drawer.innerHTML = `<div class="dr-bar"><b>${esc(entry?.title ?? key)}</b><button class="linkish" data-close>Close</button></div><p class="empty small">Reading the change…</p>`;
  if (!drawer.open) drawer.showModal();
  const [repo, number] = key.split('#');
  const reply = await fetch(`/api/pr/${repo}/${number}`);
  const pr = await reply.json();
  if (!reply.ok) {
    drawer.querySelector('.empty').textContent = pr.error;
    return;
  }
  const more = pr.diff_lines > pr.diff.split('\n').length ? `<p class="meta">The diff runs ${pr.diff_lines} lines; the rest is on <a href="${esc(pr.url)}/files" target="_blank" rel="noopener">GitHub ↗</a>.</p>` : '';
  const answerable = entry?.card && entry.group === 'you' ? choices({ ...entry, kind: 'tap' }) : '';
  drawer.innerHTML = `<div class="dr-bar"><b>${esc(bare(pr.title))}</b><span class="acts"><a class="ext" href="${esc(pr.url)}" target="_blank" rel="noopener">GitHub ↗</a><button class="linkish" data-close>Close</button></span></div>
    <div class="dr-body">
      <p class="meta">${esc(pr.headRefName)} → ${esc(pr.baseRefName)} · ${pr.changedFiles} files · <span class="add">+${pr.additions}</span> <span class="del">−${pr.deletions}</span>${pr.isDraft ? ' · draft' : ''}</p>
      ${answerable}
      ${entry ? changeBox(entry) : ''}
      <div class="dr-tabs"><button class="on" data-drtab="body">Description</button><button data-drtab="diff">Changes</button></div>
      <div class="dr-pane" data-pane="body">${prose(pr.body)}</div>
      <div class="dr-pane" data-pane="diff" hidden>${diffHtml(pr.diff)}${more}</div>
    </div>`;
}

const ASK_TABS = [
  ['all', 'All asks', () => true],
  ['open', 'In progress', (ask) => ['open', 'working'].includes(ask.state)],
  ['waiting', 'Waiting', (ask) => ask.state === 'waiting'],
  ['done', 'Completed', (ask) => ask.state === 'done'],
];
const ASK_WORD = { open: 'In progress', working: 'In progress', waiting: 'Waiting', done: 'Done', dropped: 'Dropped' };

function askRow(ask) {
  const due = ask.due ? `<span class="due${ask.late ? ' late' : ''}">${ask.late ? 'late, was due' : 'due'} ${clock(ask.due)}</span>` : '';
  return `<li class="ask ${esc(ask.state)}" data-find="${esc(ask.text.toLowerCase())}">
    <button class="ask-open" data-ask="${esc(ask.id)}">
      <span class="dot"></span>
      <span class="ask-body"><b>${esc(ask.title)}</b><span class="meta">${ASK_WORD[ask.state] ?? esc(ask.state)} · asked ${clock(ask.created)}${ask.jobs ? ` · ${ask.jobs} ${ask.jobs === 1 ? 'job' : 'jobs'}` : ''}${ask.proof ? ` · ${esc(ask.proof)}` : ''}</span></span>
      ${due}
    </button>
  </li>`;
}

function chatScreen(tab) {
  const current = ASK_TABS.some(([id]) => id === tab) ? tab : 'all';
  const [, , keep] = ASK_TABS.find(([id]) => id === current);
  const tabs = ASK_TABS.map(([id, label, test]) => `<a class="${id === current ? 'on' : ''}" href="#/chat/${id}">${label}<span class="n">${model.asks.filter(test).length}</span></a>`).join('');
  const owed = model.queue.filter((item) => item.kind === 'late').length;
  const rail = model.queue.filter((item) => item.kind !== 'late').slice(0, 6).map((item) => `<a class="rl" href="#/queue/${item.kind}"><em>${item.kind === 'tap' ? 'Landing approval' : 'Decision'}</em>${esc(item.title)}</a>`).join('');
  return `<div class="chat-cols">
    <div>
      <h1 class="h1">Chat <span class="count">your asks, and what came back</span></h1>
      ${owed ? `<a class="owed" href="#/queue/late">${owed} ${owed === 1 ? 'thing' : 'things'} owed to you</a>` : ''}
      ${sayBox('new', '', 'What would you like done?')}
      <div class="subtabs">${tabs}</div>
      <ul class="asks card">${model.asks.filter(keep).map(askRow).join('') || '<li class="empty">Nothing here.</li>'}</ul>
    </div>
    <aside class="rail"><h2 class="sec">Needs your decision <span class="count">${model.queue.length}</span></h2>${rail}<a class="more" href="#/">All ${model.queue.length} in Your queue ›</a></aside>
  </div>`;
}

function slackTime(ts) {
  return clock(new Date(parseFloat(ts) * 1000).toISOString());
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

async function openAsk(id) {
  const ask = model.asks.find((candidate) => candidate.id === id);
  const drawer = $('#drawer');
  const head = `<div class="dr-bar"><b>${esc(ask.title)}</b><button class="linkish" data-close>Close</button></div>`;
  const words = `<blockquote class="said">${esc(ask.text)}</blockquote>`;
  drawer.innerHTML = `${head}<div class="dr-body">${words}${ask.thread ? '<p class="empty small">Reading the thread…</p>' : '<p class="meta">This ask came from the terminal, so it has no thread.</p>'}</div>`;
  if (!drawer.open) drawer.showModal();
  if (!ask.thread) return;
  const reply = await fetch(`/api/thread/${ask.thread}`);
  const thread = await reply.json();
  if (!reply.ok) {
    drawer.querySelector('.empty').textContent = thread.error;
    return;
  }
  const waiting = new Map(model.queue.filter((item) => item.answer).map((item) => [item.answer.match(/\/p(\d+)(\d{6})(?:\?|$)/).slice(1).join('.'), item]));
  const messages = thread.messages.filter((said) => said.ts !== ask.thread);
  const open = !['done', 'dropped'].includes(ask.state);
  const gotIt = open && messages.some((said) => said.from_claude) ? `<button class="btn" data-gotit="a:${esc(ask.id)}" data-under="${esc(ask.url)}">Got it</button>` : '';
  drawer.innerHTML = `${head}<div class="dr-body">${words}<div class="thread">${messages.map((said) => message(said, waiting)).join('') || '<p class="meta">Nothing has come back in this thread yet.</p>'}</div>${gotIt}${sayBox(`a:${ask.id}`, ask.url, 'Reply')}</div>`;
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
    ${entry.built.map(builtLine).join('')}
    ${cardsLine(entry.cards)}
  </li>`;
}

function builtLine(built) {
  return `<div class="now">Built on <code>${esc(built.branch)}</code>, ${built.waits.length ? 'no PR yet' : 'its PR opens next'}</div>`;
}

function cardsLine(cards) {
  if (!cards.length) return '';
  const asks = cards.map((card) => `<a href="${esc(card.url)}" target="_blank" rel="noopener">${esc(card.title)}</a>, asked ${esc(clock(card.asked))}`);
  return `<div class="now">Waits on your answer to ${asks.join('; ')}</div>`;
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
  const facts = [JOB_STATES[job.state] ?? job.state, job.kind, job.where, `last word ${quiet < 1 ? 'just now' : `${ago(job.updated)} ago`}`, job.state === 'built' ? `built on ${job.branch}` : '', job.waits ? `waits on ${job.waits} ${job.waits === 1 ? 'card or job' : 'cards or jobs'}` : '', job.prs ? `${job.prs} PRs` : ''];
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
  const screens = [['Your queue', '#/'], ['Now', '#/now'], ['Status', '#/status'], ['Review', '#/review'], ['Chat', '#/chat'], ['Work', '#/work'], ['History', '#/history']];
  return [
    ...screens.map(([title, href]) => ({ kind: 'Screen', title, href })),
    ...model.queue.map((item) => ({ kind: LABEL[item.kind], title: item.title, href: `#/queue/${item.kind}` })),
    ...features().map((entry) => ({ kind: 'Feature', title: entry.title, href: `#/feature/${entry.id}` })),
    ...model.review.map((entry) => ({ kind: 'Change', title: entry.title, pr: entry.key })),
    ...model.work.map((job) => ({ kind: 'Job', title: job.goal, href: `#/feature/${job.feature}` })),
    ...model.asks.map((ask) => ({ kind: 'Ask', title: ask.title, ask: ask.id })),
  ];
}

let jumpAt = 0;

function drawJump() {
  const words = $('#jumpfind').value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const found = jumpItems().filter((item) => words.every((word) => `${item.kind} ${item.title}`.toLowerCase().includes(word))).slice(0, 40);
  jumpAt = Math.min(jumpAt, Math.max(0, found.length - 1));
  $('#jumplist').innerHTML = found.map((item, i) => `<li class="${i === jumpAt ? 'on' : ''}" data-jump="${i}" ${item.pr ? `data-pr-jump="${esc(item.pr)}"` : item.ask ? `data-ask-jump="${esc(item.ask)}"` : `data-href="${esc(item.href)}"`}><em>${esc(item.kind)}</em>${esc(item.title)}</li>`).join('') || '<li class="none">Nothing matches.</li>';
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
  if (row.dataset.askJump) return openAsk(row.dataset.askJump);
  location.hash = row.dataset.href;
}

function alerts() {
  const stalled = model.work.filter(isStalled).length;
  const late = model.queue.filter((item) => item.kind === 'late').length;
  const red = model.progress.red;
  return [
    model.cards === 'live' ? '' : `<span class="al warn">Your questions are not listed here: cc-slack ${esc(model.cards)}</span>`,
    stalled ? `<a class="al bad" href="#/work">${stalled} ${stalled === 1 ? 'job has' : 'jobs have'} been quiet ${STALL_MINUTES} minutes or more</a>` : '',
    late ? `<a class="al bad" href="#/queue/late">${late} promised ${late === 1 ? 'time has' : 'times have'} passed</a>` : '',
    red ? `<a class="al bad" href="#/review/not%20ready">${red} open ${red === 1 ? 'PR is' : 'PRs are'} red</a>` : '',
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
    ${entry.built.length ? `<h3 class="sec">Built, waiting on your design answers</h3><div class="card built">${entry.built.map(builtLine).join('')}</div>` : ''}
    ${entry.cards.length ? `<h3 class="sec">Waiting on your answer</h3><div class="card built">${cardsLine(entry.cards)}</div>` : ''}
    ${entry.jobs.length ? `<h3 class="sec">Jobs</h3><ul class="jobs card">${entry.jobs.map((job) => jobRow(job, false)).join('')}</ul>` : ''}`;
}

function find() {
  const words = $('#find').value.trim().toLowerCase();
  for (const row of document.querySelectorAll('.feature[data-find], .ask[data-find]')) row.hidden = Boolean(words) && !row.dataset.find.includes(words);
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
  const waiting = model.review.filter((entry) => entry.group === 'you').length;
  $('#tabs').innerHTML = tab('queue', '#/', 'Your queue', model.counts.queue) + tab('now', '#/now', 'Now') + tab('status', '#/status', 'Status') + tab('review', '#/review', 'Review', waiting) + tab('chat', '#/chat', 'Chat', model.asks.filter((ask) => ask.state === 'waiting').length) + tab('work', '#/work', 'Work') + tab('history', '#/history', 'History');
  const raised = screen === 'now' ? '' : alerts();
  $('#alerts').innerHTML = raised;
  $('#alerts').hidden = !raised;
  const screens = { queue: () => queueScreen(id), now: nowScreen, status: statusScreen, review: () => reviewScreen(decodeURIComponent(id ?? '')), chat: () => chatScreen(id), work: workScreen, history: historyScreen, feature: () => featureScreen(id) };
  $('#findbar').hidden = !['status', 'chat'].includes(screen);
  $('#find').placeholder = screen === 'chat' ? 'Search your asks' : 'Find a feature or pull request';
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
  if (['status', 'chat'].includes(screen)) find();
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
  const asked = [...model.queue, ...model.review].find((candidate) => candidate.answer === card);
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
  const shown = $(`#drawer[open] [data-card="${CSS.escape(card)}"]`)?.closest('.picks');
  shown?.nextElementSibling?.classList.contains('refused') && shown.nextElementSibling.remove();
  if (shown) shown.outerHTML = choices({ ...asked, kind: asked.kind ?? 'tap' });
}

function redrawBoxes(key) {
  render();
  for (const box of document.querySelectorAll(`#drawer form[data-say="${CSS.escape(key)}"]`)) box.outerHTML = sayBox(key, box.dataset.under, box.dataset.placeholder, box.dataset.about);
  for (const button of document.querySelectorAll(`#drawer button[data-gotit="${CSS.escape(key)}"]`)) button.hidden = Boolean(sent.get(key)?.at);
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
  const fresh = model.queue.filter((item) => heard && !heard.has(noticeKey(item)));
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
  const gotIt = event.target.closest('button[data-gotit]');
  if (gotIt) return say(gotIt.dataset.gotit, gotIt.dataset.under, 'Got it', '');
  const pick = event.target.closest('button[data-card]');
  if (pick) return answer(pick.dataset.card, pick.dataset.label);
  const pr = event.target.closest('button[data-pr]');
  if (pr) return openPR(pr.dataset.pr);
  const asked = event.target.closest('button[data-ask]');
  if (asked) return openAsk(asked.dataset.ask);
  if (event.target.closest('#kbtn')) return openJump();
  if (event.target.closest('#notices')) return Notification.requestPermission().then(noticeButton);
  const jumped = event.target.closest('#jumplist [data-jump]');
  if (jumped) return goJump(jumped);
  if (event.target.id === 'jump') return $('#jump').close();
  if (event.target.closest('[data-close]') || event.target.id === 'drawer') return $('#drawer').close();
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
window.addEventListener('hashchange', () => {
  if (location.hash.startsWith('#group-')) return;
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
  $('#find').addEventListener('input', find);
  $('#jumpfind').addEventListener('input', () => {
    jumpAt = 0;
    drawJump();
  });
  setInterval(render, 60000);
  connect();
});
