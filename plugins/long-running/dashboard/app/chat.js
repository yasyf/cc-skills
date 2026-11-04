const CHAT_ICONS = { chat: 'M4 4h16v12H8l-4 4V4Z', grid: 'M3 3h7v7H3V3Zm11 0h7v7h-7V3ZM3 14h7v7H3v-7Zm11 0h7v7h-7v-7Z', check: 'm5 12 4 4L19 6', chevron: 'm9 5 7 7-7 7', arrow: 'M5 12h14m-5-5 5 5-5 5', search: 'M21 21l-5-5M18 10.5a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0Z', edit: 'm14 4 6 6M4 20l4-1L21 6l-3-3L5 16l-1 4Z', doc: 'M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 12h8M8 16h6', diagram: 'M3 3h6v5H3V3Zm12 13h6v5h-6v-5ZM3 16h6v5H3v-5Zm3-8v5h12v3', pr: 'M6 3v12M18 19V9c0-3-3-3-6-3m2-3-3 3 3 3M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm12 3a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z', link: 'm9 15 6-6m-5-3 2-2a5 5 0 0 1 7 7l-2 2M7 11l-2 2a5 5 0 0 0 7 7l2-2', expand: 'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5', send: 'm3 3 18 9-18 9 4-9-4-9Zm4 9h14', list: 'M8 5h13M8 12h13M8 19h13M3 5h1M3 12h1M3 19h1', history: 'M3 10a9 9 0 1 1 1 7M3 4v6h6m3-3v5l3 2', reply: 'm9 5-6 6 6 6m-6-6h10c5 0 8 3 8 8', bell: 'M5 17h14l-2-4V8a5 5 0 0 0-10 0v5l-2 4Zm5 4h4', code: 'm8 6-6 6 6 6m8-12 6 6-6 6', spark: 'm12 3 2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2L12 3Z', alert: 'M12 3 2 21h20L12 3Zm0 7v5m0 3v.5' };
const NAV_ICONS = { overview: 'grid', needs: 'bell', plan: 'diagram', status: 'list', work: 'code', review: 'pr', replies: 'chat', history: 'history' };
const STAGE_WORD = { you: 'Needs you', received: 'Received', routed: 'Routed', acked: 'ACKed', progress: 'In progress', completed: 'Completed' };
const STAGE_STEP = { received: 0, routed: 1, acked: 1, progress: 2, you: 2, completed: 3 };
const STEP_WORDS = ['Received', 'ACKed', 'In progress', 'Completed'];
const REPLY_KIND = {
  explanation: { label: 'Explanation', icon: 'doc', tone: '' },
  update: { label: 'Update', icon: 'spark', tone: 'acc' },
  for_you: { label: 'For you', icon: 'bell', tone: 'you' },
  question: { label: 'Question', icon: 'bell', tone: 'you' },
  incident: { label: 'Incident', icon: 'alert', tone: 'bad' },
  done: { label: 'Done', icon: 'check', tone: 'ok' },
};
const CHAT_SHOWN = 30;
const ATTENTION_SHOWN = 5;
const RAIL_SHOWN = 6;
const SIMPLE_PICK = 50;
const BARE_ASK_MINUTES = 60;
const CLAMP_CHARS = 340;
const PR_CARDS_SHOWN = 3;
const PR_CHIPS_SHOWN = 6;
const DONE_LEAD = /^Done, nothing for you[.:]?\s*/i;
const SCREENSHOT = /\[Image #\d+\]/g;
const chatDraft = (on) => `draft:chat:${on}:`;
const chatIcon = (name, cls = '') => `<svg class="ico ${cls}" viewBox="0 0 24 24" aria-hidden="true"><path d="${CHAT_ICONS[name]}"></path></svg>`;
const chatSigs = new WeakMap();
const cw = {
  filter: 'all',
  query: '',
  limit: CHAT_SHOWN,
  lead: ATTENTION_SHOWN,
  open: new Set(),
  read: new Set(),
  replying: new Set(Object.keys(localStorage).flatMap((key) => /^draft:chat:(.+):$/.exec(key)?.[1] ?? [])),
  pinned: null,
  reader: null,
  focus: null,
  seen: new Set(),
};

function cardAnswered(id) {
  return state.model.board.answered[id]?.picked ?? keptWords(id);
}
function questionOpen(r) {
  const card = replyCard(r);
  return Boolean(card?.decides && !cardAnswered(card.id));
}
function bareAskOpen(entry, answeredInThread) {
  return entry.reply.type === 'for_you' && !DONE_LEAD.test(entry.reply.title) && !entry.reply.picks?.length && !answeredInThread && minutesSince(entry.at) < BARE_ASK_MINUTES;
}
function openReplies() {
  return state.model?.replies ? state.model.replies.filter((entry) => questionOpen(entry.reply) || bareAskOpen(entry, false)).length : 0;
}
function askKeyOf(r) {
  const head = /^m(\d+)/.exec(r.order ?? '') ?? /^line:m(\d+)/.exec(r.on ?? '');
  return head && `line:m${head[1]}`;
}
function typedRow(at, text) {
  return { m: '', iso: at, text, state: 'asked', ack: 'received', lanes: [], prs: [] };
}
function chatThreads() {
  const m = state.model;
  const threads = new Map();
  const askKey = (row) => `line:m${String(row.m).split('.')[0]}`;
  for (const row of m.owner_asks.messages) {
    const key = askKey(row);
    if (threads.has(key)) threads.get(key).rows.push(row);
    else threads.set(key, { key, at: row.iso, rows: [row], replies: [], said: [] });
  }
  const home = new Map();
  for (const line of m.chat_lines.filter((row) => !row.on)) {
    const key = `seq:${line.seq}`;
    const row = m.owner_asks.messages.find((candidate) => candidate.text.includes(line.text.trim()));
    if (row) {
      const ask = threads.get(askKey(row));
      if (!ask.key.startsWith('seq:')) ask.key = key;
      home.set(key, askKey(row));
    } else {
      threads.set(key, { key, at: line.at, rows: [typedRow(line.at, line.text)], replies: [], said: [] });
      home.set(key, key);
    }
  }
  for (const entry of [...m.replies].reverse()) {
    const r = entry.reply;
    const key = `reply:${r.id}`;
    const parent = threads.get(askKeyOf(r)) ?? threads.get(home.get(r.on));
    if (parent) {
      parent.replies.push(entry);
      home.set(key, parent.key);
    } else {
      threads.set(key, { key, at: entry.at, rows: [], replies: [entry], said: [] });
      home.set(key, key);
    }
  }
  const place = (at, text, on, key, sending) => {
    const parent = threads.get(home.get(on) ?? on);
    if (parent) parent.said.push({ at, text, sending });
    else threads.set(key, { key, at, rows: [typedRow(at, text)], replies: [], said: [], sending });
  };
  const landed = new Set(m.chat_lines.map((line) => line.seq));
  for (const line of m.chat_lines.filter((row) => row.on)) place(line.at, line.text, line.on, `seq:${line.seq}`, false);
  for (const sent of rp.sent.filter((row) => !landed.has(row.seq))) place(sent.at, sent.text, sent.on, `sent:${sent.at}`, true);
  const replied = new Set(m.replies.map((entry) => `card-${entry.reply.id}`));
  for (const card of m.board.cards.filter((c) => c.decides && !replied.has(c.id))) {
    threads.set(`card:${card.id}`, { key: `card:${card.id}`, at: null, rows: [], replies: [], said: [], card });
  }
  return [...threads.values()];
}
function askStage(t) {
  if (t.replies.some((entry) => questionOpen(entry.reply) || bareAskOpen(entry, t.said.length > 0))) return 'you';
  if (t.rows.some((row) => row.owed)) return 'progress';
  const waiting = t.rows.find((row) => row.state === 'asked' && row.ack !== 'done');
  if (!waiting) return 'completed';
  const type = t.replies.at(-1)?.reply.type;
  if (type) return ['done', 'explanation'].includes(type) ? 'completed' : 'progress';
  if (waiting.ack === 'received') return 'received';
  if (waiting.lanes.length) return 'progress';
  return waiting.due ? 'acked' : 'routed';
}
function threadStage(t) {
  if (t.card) return 'you';
  if (t.rows.length) return askStage(t);
  const entry = t.replies[0];
  if (questionOpen(entry.reply) || bareAskOpen(entry, t.said.length > 0)) return 'you';
  return entry.reply.type === 'done' ? 'completed' : '';
}
function threadFrees(t) {
  const ids = new Set([t.card?.id, ...t.replies.map((entry) => `card-${entry.reply.id}`)]);
  return Math.max(0, ...(state.model.taps ?? []).filter((tap) => ids.has(tap.card)).map((tap) => tap.frees ?? 0));
}
function threadTitle(t) {
  const row = t.rows[0];
  const words = row.title || askTitle(t.rows.map((part) => part.text).join(' ').replace(SCREENSHOT, ' ').trim());
  return words.charAt(0).toUpperCase() + words.slice(1);
}
function threadWords(t) {
  return [...t.rows.flatMap((row) => [row.title, row.text]), ...t.replies.flatMap((entry) => [entry.reply.title, entry.reply.md]), ...t.said.map((line) => line.text), t.card?.title].join(' ').toLowerCase();
}
function chatVisible(t, stage) {
  if (t.card && cw.filter !== 'needs') return false;
  if (cw.filter === 'needs' && stage !== 'you') return false;
  if (['progress', 'completed'].includes(cw.filter) && stage !== cw.filter) return false;
  return !cw.query || threadWords(t).includes(cw.query.toLowerCase());
}
function chatDayWord(iso) {
  const dayOf = new Intl.DateTimeFormat('en-US', { timeZone: PACIFIC, weekday: 'long', month: 'long', day: 'numeric' });
  const day = dayOf.format(new Date(iso));
  if (day === dayOf.format(new Date())) return 'Today';
  return day === dayOf.format(new Date(Date.now() - 86400000)) ? 'Yesterday' : day;
}
function chatRows() {
  const all = chatThreads().map((t) => ({ t, stage: threadStage(t) }));
  const shown = all.filter(({ t, stage }) => chatVisible(t, stage));
  const newest = (a, b) => new Date(b.t.at ?? 0) - new Date(a.t.at ?? 0);
  const attention = shown.filter(({ stage }) => stage === 'you').sort((a, b) => threadFrees(b.t) - threadFrees(a.t) || newest(a, b));
  const rest = shown.filter(({ stage }) => stage !== 'you').sort(newest);
  const focused = cw.focus && shown.find(({ t }) => t.replies.some((entry) => entry.reply.id === cw.focus));
  cw.lead = Math.max(cw.lead, attention.indexOf(focused) + 1);
  const leading = cw.filter === 'needs' || cw.query ? attention : attention.slice(0, cw.lead);
  if (focused) cw.limit = Math.max(cw.limit, [...leading, ...rest].indexOf(focused) + 1);
  const lines = [...leading, ...rest].slice(0, cw.limit);
  return { all, lines, count: shown.length, hidden: attention.length - leading.length, attention: leading.length };
}

function replyTitle(r) {
  const words = plain(r.title).replace(DONE_LEAD, '').replace(/#\d{4,5}\b,? ?/g, '').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
function bodyAfterTitle(words, title) {
  const lead = /^[^.\n]{12,}[.\n]\s*/.exec(words);
  return lead && title.toLowerCase().startsWith(lead[0].replace(/[.\s]+$/, '').toLowerCase()) ? words.slice(lead[0].length) : words;
}
function chatBody(id, md, title = '') {
  const words = bodyAfterTitle(plain(namedChanges(md)).replace(DONE_LEAD, ''), title);
  const body = `<div class="rp-md prose obj-text" data-clamp="${esc(id)}">${replyMarkdown(id, words)}</div>`;
  return words.length > CLAMP_CHARS ? `${body}<button class="text-button" data-read="${esc(id)}">Read all</button>` : body;
}
function linkTile(l) {
  const label = plain(l.label).replace(/\s*\(doc [0-9a-f]+\)/i, '').replace(/#(\d{5})\b/g, (_, n) => changeWords(n)).replace(/\s*\(your tap\)/, '');
  return `<a class="embedded-ref" href="${esc(l.url)}" target="_blank" rel="noopener">${chatIcon('link')}${esc(label)}</a>`;
}
const isOnePager = (url) => /\/one-pagers\//.test(url);
function docCard(l, deck, kind = 'One-pager', icon = 'doc') {
  const label = plain(l.label).replace(/\s*\(doc [0-9a-f]+\)/i, '');
  const framed = /\.ts\.net/.test(l.url) ? framedUrl(l.url) : null;
  const preview = framed ? `<div class="diagram-trigger" role="button" tabindex="0" aria-label="Read ${esc(label)}" data-doc-open="${esc(l.url)}" data-doc-title="${esc(label)}"><div class="mini-viewport"><iframe title="${esc(label)}" src="${esc(framed)}" loading="lazy" tabindex="-1"></iframe></div><span class="diagram-label">${chatIcon('expand')} Hover to read</span><div class="diagram-popover"><div class="popover-title">${esc(label)}</div><iframe title="${esc(label)}" data-src="${esc(framed)}"></iframe></div></div>` : '';
  return `<div class="artifact document-card"><div class="artifact-top">${chatIcon(icon)} ${kind}</div><div class="document-body${preview ? '' : ' plain'}"><div><h3 class="document-title">${esc(label)}</h3><p class="document-deck">${esc(deck)}</p></div>${preview}</div><div class="doc-footer"><span></span>${framed ? `<button data-doc-open="${esc(l.url)}" data-doc-title="${esc(label)}">Read it ${chatIcon('arrow')}</button>` : `<a href="${esc(l.url)}" target="_blank" rel="noopener">Read it ${chatIcon('arrow')}</a>`}</div></div>`;
}
function replyLinks(r) {
  const links = r.links ?? [];
  const deck = firstClause(plain(r.md).replace(DONE_LEAD, '').replace(/[*`#]+/g, ''));
  const chips = links.filter((l) => !isOnePager(l.url)).map(linkTile).join('');
  const diagram = r.diagram ? docCard({ label: r.diagram.title, url: `${location.origin}/diagram/${r.id}` }, deck, 'Diagram', 'diagram') : '';
  return `${diagram}${links.filter((l) => isOnePager(l.url)).map((l) => docCard(l, deck)).join('')}${chips ? `<div class="decision-tail">${chips}</div>` : ''}`;
}
function prNumbers(r) {
  const named = (r.prs ?? []).flatMap((url) => {
    const found = /github\.com\/([\w.-]+\/[\w.-]+)\/pull\/(\d+)/.exec(url);
    return found?.[1] === state.model.repo ? [Number(found[2])] : [];
  });
  return [...new Set([...named, ...[...r.md.matchAll(/#(\d{5})\b/g)].map((found) => Number(found[1]))])];
}
function prCard(n) {
  const row = boardRow(n);
  const pr = state.model.prs[n] ?? {};
  const words = rowWords(row);
  const stage = prState(n);
  const checks = [pr.test_state && `Checks ${pr.test_state}`, pr.ai_review && `Review ${pr.ai_review}`, pr.changed_files && plural(Number(pr.changed_files), 'file')].filter(Boolean);
  const failed = pending.number === Number(n) && pending.error ? `<em class="nsent bad">Not recorded: ${esc(pending.error)}. Try again.</em>` : '';
  return `<div class="artifact pr-card" id="pr-${n}"><div class="artifact-top">${chatIcon('pr')} Pull request<span class="right">${esc(STATE_WORD[stage] ?? '')}</span></div><div class="pr-heading"><h3>${esc(plainTitle(n))}</h3></div>${words.change ? `<p class="pr-desc">${inline(plain(words.change))}</p>` : ''}${words.blocker && verdictState(row) === 'you' ? `<p class="pr-desc pr-blocks">${inline(plain(words.blocker))}</p>` : ''}<div class="pr-checks">${checks.map((c) => `<span><i class="dot"></i>${esc(c)}</span>`).join('')}</div><div class="pr-bottom">${rowActs(row)}${failed}<a class="source-link" href="#/replies~review/${n}">View change ${chatIcon('arrow')}</a></div></div>`;
}
function prChip(n) {
  return `<a class="embedded-ref" href="#/replies~review/${n}">${chatIcon('pr')}${esc(changeWords(n))}${state.model.prs[n] ? ` · ${esc(STATE_WORD[prState(n)] ?? '')}` : ''}</a>`;
}
function prObjects(numbers) {
  const fresh = numbers.filter((n) => !cw.seen.has(n));
  for (const n of fresh) cw.seen.add(n);
  const open = (n) => boardRow(n) && !['landed', 'closed'].includes(prState(n));
  const cards = fresh.filter(open).slice(0, PR_CARDS_SHOWN);
  const chips = fresh.filter((n) => !cards.includes(n) && (state.model.prs[n] || boardRow(n)));
  const shown = chips.slice(0, PR_CHIPS_SHOWN).map(prChip).join('');
  return `${cards.map(prCard).join('')}${shown ? `<div class="decision-tail">${shown}${chips.length > PR_CHIPS_SHOWN ? `<span class="embedded-ref">${chips.length - PR_CHIPS_SHOWN} more</span>` : ''}</div>` : ''}`;
}
function replyOthers(r) {
  const others = (r.prs ?? []).filter((url) => !url.includes(`github.com/${state.model.repo}/`));
  return others.length ? `<div class="decision-tail">${others.map((url) => `<a class="embedded-ref" href="${esc(url)}" target="_blank" rel="noopener">${chatIcon('pr')}A tooling change</a>`).join('')}</div>` : '';
}
function chatReplyBox(on, about, placeholder) {
  const failed = rp.failed.get(on);
  return `${failed ? `<p class="ct-failed">Not sent: ${esc(failed)}. Try again.</p>` : ''}<form class="reply-editor" data-chat-send="${esc(on)}" data-about="${esc(about)}"><textarea name="chat" data-draft="${esc(on)}" rows="2" placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}"></textarea><button class="action primary">Send</button></form>`;
}
function objectReplyButton(key) {
  return `<button class="obj-reply" data-chat-thread="${esc(key)}">${chatIcon('reply')} Reply</button>`;
}
function objectReplyBox(key, about) {
  return cw.replying.has(key) ? chatReplyBox(key, about, 'Reply to this') : '';
}
function choiceButton(c, o) {
  const label = o.title || o.label.replace(RECOMMENDED_LABEL, '');
  const sent = ov.sent.get(c.choice);
  const on = sending(c.choice) && sent.option === o.id;
  return `<button class="choice-button${o.recommended ? ' recommended' : ''}${on ? ' on' : ''}" data-choose="${esc(o.id)}" data-choice="${esc(c.choice)}"${sending(c.choice) ? ' disabled' : ''}><span class="choice-copy"><span>${esc(plain(label).replace(/\(?#\d{4,5}\)?/g, 'this change'))}</span>${o.hint ? `<em>${inline(plain(o.hint))}</em>` : ''}</span>${o.recommended ? '<small>Recommended</small>' : chatIcon('arrow')}</button>`;
}
function cardChoices(c) {
  if (!c.choice || c.embed) return `<div class="choice-list"><button class="choice-button recommended" data-chat-modal="${esc(c.id)}"><span class="choice-copy"><span>Open the pick</span></span>${chatIcon('arrow')}</button></div>`;
  const part = c.parts?.find((p) => p.id === c.choice);
  const options = part?.options ?? c.options;
  return `${part?.prompt ? `<p class="choice-context choice-prompt">${inline(part.prompt)}</p>` : ''}<div class="choice-list">${options.map((o) => choiceButton(c, o)).join('')}</div><form class="write-in" data-pick-other="${esc(c.choice)}"><textarea name="pick-other" data-draft="${esc(c.choice)}" rows="1" placeholder="Or write your answer" aria-label="Write your own answer"></textarea><button>Send answer</button></form>${sentNote(c.choice)}`;
}
function isSimple(c) {
  if (!c.choice || c.embed) return false;
  const options = c.parts?.find((p) => p.id === c.choice)?.options ?? c.options;
  return options.length <= 2 && options.every((o) => !o.hint && (o.title || o.label).length <= SIMPLE_PICK);
}
function questionObject(c, id) {
  const title = rewritten('cards', c.key)?.question ?? c.title;
  const summary = c.summary || rewritten('cards', c.key)?.means || '';
  const simple = isSimple(c);
  const options = c.parts?.find((p) => p.id === c.choice)?.options ?? c.options ?? [];
  const inlineButtons = simple ? `<span class="inline-picks">${options.map((o) => `<button class="action${o.recommended ? ' primary' : ''}" data-choose="${esc(o.id)}" data-choice="${esc(c.choice)}"${sending(c.choice) ? ' disabled' : ''}>${esc(o.label.replace(RECOMMENDED_LABEL, ''))}</button>`).join('')}</span>` : '';
  const summaryRow = `<span class="question-mark">?</span><span class="question-copy"><small>Open question</small><span class="question-title">${esc(tapWords(title))}</span></span>`;
  const head = simple ? `<div class="question-summary">${summaryRow}${inlineButtons}</div>${sentNote(c.choice)}` : `<button class="question-summary" aria-expanded="false" data-q-pin="${esc(c.id)}">${summaryRow}<span class="question-hint">Hover to decide ${chatIcon('arrow')}</span></button>`;
  const more = explainMore(c, cardAsk(c));
  return `<div class="artifact question${simple ? ' simple' : ''}" data-question="${esc(c.id)}"${id ? ` id="rp-${esc(id)}"` : ''}>${head}${simple ? (more ? `<div class="simple-more">${more}</div>` : '') : `<div class="choices">${summary ? `<p class="choice-context">${inline(tapWords(summary))}</p>` : ''}${cardChoices(c)}${more}</div>`}</div>`;
}
function replyObject(entry, t) {
  const r = entry.reply;
  const key = `reply:${r.id}`;
  const about = `reply ${r.id} "${r.title}"`;
  const card = replyCard(r);
  const links = `${replyLinks(r)}${replyOthers(r)}${prObjects(prNumbers(r))}`;
  const picked = cardAnswered(`card-${r.id}`);
  if (card?.decides && !picked) return `${questionObject(card, r.id)}${links}`;
  const kind = REPLY_KIND[r.type];
  const failed = entry.surfaces.filter((s) => s.error).map((s) => `<p class="ct-failed">${esc(SURFACE_WORD[s.surface])} failed: ${esc(s.error)}</p>`).join('');
  if (t.rows.length && r.order && !r.picks?.length && r.type === 'update') return `<div class="update-row" id="rp-${esc(r.id)}">${chatIcon('spark')}<div><span class="update-title">${esc(replyTitle(r))} <time>${clock(entry.at)}</time>${objectReplyButton(key)}</span>${chatBody(r.id, r.md, replyTitle(r))}${failed}${objectReplyBox(key, about)}</div></div>${links}`;
  const label = picked ? 'Decision' : kind.label;
  const pickLine = picked ? `<p class="obj-picked">${chatIcon('check')}<span>You picked <b>${esc(picked)}</b></span></p>` : r.picks?.length ? '<p class="ct-closed">This pick is no longer open.</p>' : '';
  const done = !picked && bareAskOpen(entry, t.said.length > 0) ? `<div class="decision-tail"><button class="action primary" data-chat-done="${esc(key)}" data-about="${esc(about)}">Done</button></div>` : '';
  return `<div class="artifact decision-card ${picked ? 'ok' : kind.tone}" id="rp-${esc(r.id)}"><div class="artifact-top">${chatIcon(picked ? 'check' : kind.icon)} ${label}<span class="right">${clock(entry.at)}${objectReplyButton(key)}</span></div><h3>${esc(replyTitle(r))}</h3>${pickLine}${chatBody(r.id, r.md, replyTitle(r))}${done}${failed}${objectReplyBox(key, about)}</div>${links}`;
}
function whoHasIt(t) {
  const owed = t.rows.map((part) => part.owed).find(Boolean);
  if (owed?.status === 'working') return `${workerName(owed.lane)} is on it`;
  if (owed?.status === 'parked') return `Parked with ${workerName(owed.lane)}: ${owed.hold}`;
  if (owed?.lane) return `Last with ${workerName(owed.lane)}, no proof it reached you`;
  if (owed) return 'Nobody has it';
  const workers = new Set(t.rows.flatMap((part) => part.lanes)).size;
  return workers ? `${plural(workers, 'worker')} on it` : 'The communicator';
}
function askDetail(t, stage) {
  const row = t.rows.find((part) => part.due) ?? t.rows[0];
  const step = STAGE_STEP[stage];
  const facts = [['Received', clock(t.rows[0].iso)], ['Who has it', whoHasIt(t)], ...(row.due ? [['Answer due', clock(row.due)]] : [])];
  const said = t.rows.map((part) => {
    const shot = part.text.match(SCREENSHOT)?.length;
    return `<p class="original">${esc(part.text.replace(SCREENSHOT, '').trim())}${shot ? ` <span class="embedded-ref">${plural(shot, 'screenshot')}</span>` : ''}</p>`;
  }).join('');
  return `<div class="ask-detail"><div class="detail-grid"><div><h4>Your whole message</h4>${said}</div><div><h4>Routing and clock</h4><dl>${facts.map(([k, v]) => `<div class="fact-row"><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl></div></div><div class="steps">${STEP_WORDS.map((word, at) => `<span class="step ${at < step || step === 3 ? 'passed' : at === step ? 'current' : ''}"><em>${at < step || step === 3 ? '✓' : at + 1}</em>${word}</span>`).join('')}</div></div>`;
}
function askPill(stage, row) {
  if (row?.due && new Date(row.due) <= Date.now() && ['received', 'routed', 'acked'].includes(stage)) return '<span class="pill hold">Past due</span>';
  return `<span class="pill ${stage}">${STAGE_WORD[stage]}</span>`;
}
function saidLines(t) {
  return t.said.map((line) => `<p class="reply-receipt">${chatIcon('reply')}<span>${esc(line.text)}<small>${line.sending ? 'Sending…' : clock(line.at)}</small></span></p>`).join('');
}
function threadBody({ t, stage }) {
  if (t.card) return `<div class="responses loose">${questionObject(t.card)}</div>`;
  const objects = t.replies.map((entry) => replyObject(entry, t)).join('');
  const said = saidLines(t);
  if (!t.rows.length) return `<div class="responses loose">${objects}${said}</div>`;
  const row = t.rows.find((part) => part.due) ?? t.rows[0];
  const title = threadTitle(t);
  const number = String(t.rows[0].m).split('.')[0];
  const about = `his line ${number ? `m${number} ` : ''}"${title.replaceAll('"', "'")}"`;
  const answer = !t.replies.length && t.rows.map((part) => part.answer).find(Boolean);
  const answered = answer ? `<div class="artifact decision-card"><div class="artifact-top">${chatIcon('doc')} Answer</div>${chatBody(t.key, answer)}</div>` : '';
  const named = prObjects(t.rows.flatMap((part) => part.prs).map(Number));
  const waiting = ['received', 'routed', 'acked'].includes(stage) && !t.sending ? '<span class="ct-wait">Waiting for the communicator<i></i><i></i><i></i></span>' : '<span></span>';
  const count = t.said.length;
  return `<button class="ask-head" aria-expanded="false" data-ask-open="${esc(t.key)}"><span class="ask-icon">${chatIcon('list')}</span><span class="ask-title">${esc(title)}</span><span class="ask-meta">${t.sending ? '<span class="pill received">Sending</span>' : askPill(stage, row)}<time datetime="${esc(t.at)}">${clock(t.at)}</time>${chatIcon('chevron', 'chevron')}</span></button>${askDetail(t, stage)}${objects || answered || named ? `<div class="responses">${objects}${answered}${named}</div>` : ''}${said ? `<div class="thread-said">${said}</div>` : ''}${t.sending ? '' : `<div class="thread-foot">${waiting}<button data-chat-thread="${esc(t.key)}">${chatIcon('reply')} Reply</button></div>${cw.replying.has(t.key) ? chatReplyBox(t.key, about, 'Reply in this ask') : ''}`}`;
}
function chatFilters(all) {
  const count = (stage) => all.filter((row) => row.stage === stage).length;
  const counts = { all: all.filter(({ t }) => !t.card).length, needs: count('you'), progress: count('progress'), completed: count('completed') };
  return [['all', 'All asks'], ['needs', 'Needs me'], ['progress', 'In progress'], ['completed', 'Completed']].map(([id, label]) => `<button class="filter${cw.filter === id ? ' active' : ''}" aria-pressed="${cw.filter === id}" data-cw-filter="${id}">${label}<small>${counts[id]}</small></button>`).join('');
}
function railTap(t) {
  const card = t.kind !== 'land' && !t.subject && tapCard(t);
  const title = esc(tapWords(t.kind === 'land' ? plainTitle(t.number) : t.title));
  const open = card ? `<button class="attention-title" data-chat-modal="${esc(card.id)}">${title}</button>` : t.kind === 'land' ? `<a class="attention-title" href="#/replies~review/${t.number}">${title}</a>` : `<a class="attention-title" href="#/needs">${title}</a>`;
  return `<div class="attention-item"><div class="attention-kind">${chatIcon(t.kind === 'land' ? 'pr' : 'spark')} ${t.kind === 'land' ? 'Landing approval' : 'Decision'}${t.frees ? `<span class="attention-frees">Frees ${t.frees}</span>` : ''}</div>${open}</div>`;
}
function chatRail(all) {
  const taps = openTaps();
  const asks = all.filter(({ t }) => t.rows.length);
  const done = asks.filter(({ stage }) => stage === 'completed').length;
  const working = asks.filter(({ stage }) => stage === 'progress' || stage === 'you').length;
  const waiting = asks.length - done - working;
  const width = (n) => `${asks.length ? (n / asks.length) * 100 : 0}%`;
  return `<div class="rail-section"><div class="rail-heading"><h2>Needs your decision</h2><small>${taps.length}</small></div>${taps.slice(0, RAIL_SHOWN).map(railTap).join('') || '<p class="rail-none">Nothing waits on you.</p>'}${taps.length > RAIL_SHOWN ? `<a class="rail-all" href="#/needs">All ${taps.length} on Needs me ${chatIcon('arrow')}</a>` : ''}</div><div class="rail-section"><div class="rail-heading"><h2>Your asks</h2></div><div class="mini-progress"><i style="width:${width(done)}"></i><i style="width:${width(working)}"></i><i style="width:${width(waiting)}"></i></div><div class="legend"><span><i></i>${done} done</span><span><i></i>${working} working</span><span><i></i>${waiting} waiting</span></div></div>`;
}
function repliesScreen() {
  return `<div class="pg cw"><header class="page-heading"><div><h1>Chat</h1><p>Your asks, and what came back.</p></div><button class="quiet-button" data-cw-new>${chatIcon('edit')} New ask</button></header><div class="cw-layout"><div class="conversation"><div id="cw-owed" class="ct-owed-slot"></div><div class="filters" role="group" aria-label="Filter asks"><div id="cw-filters" style="display:contents"></div><label class="search-wrap">${chatIcon('search')}<input id="cw-search" aria-label="Search asks" placeholder="Search asks" value="${esc(cw.query)}"></label></div><section id="cw-list" aria-label="Your asks and responses"></section><div id="cw-more"></div><p class="ct-failed" id="cw-failed" hidden></p><form class="composer" data-chat-send="" data-about=""><div class="compose-context"><span>New ask</span><span>To the communicator</span></div><textarea name="chat" data-draft="" aria-label="New ask" placeholder="What would you like done?" rows="2"></textarea><div class="compose-bottom"><span>Enter to send · Shift + Enter for a new line</span><button type="submit">Send ask ${chatIcon('send')}</button></div></form></div><aside class="context-rail" id="cw-rail" aria-label="Decisions"></aside></div></div>`;
}
function chatSet(el, html) {
  if (chatSigs.get(el) === html) return;
  const held = [...el.querySelectorAll('textarea[data-draft]')].filter((box) => box === document.activeElement || box.value);
  const active = document.activeElement;
  const caret = held.includes(active) && [active.selectionStart, active.selectionEnd];
  el.innerHTML = html;
  chatSigs.set(el, html);
  for (const box of held) el.querySelector(`textarea[name="${box.name}"][data-draft="${CSS.escape(box.dataset.draft)}"]`)?.closest('form').replaceWith(box.closest('form'));
  if (caret && el.contains(active)) {
    active.focus();
    active.setSelectionRange(...caret);
  }
}
function chatReapply(el) {
  el.classList.toggle('open', cw.open.has(el.dataset.k));
  el.querySelector(':scope .ask-head')?.setAttribute('aria-expanded', String(cw.open.has(el.dataset.k)));
  for (const body of el.querySelectorAll('[data-clamp]')) {
    const long = Boolean(body.nextElementSibling?.matches('[data-read]'));
    const read = cw.read.has(body.dataset.clamp);
    body.classList.toggle('clamped', long && !read);
    if (long) body.nextElementSibling.textContent = read ? 'Show less' : 'Read all';
  }
  for (const q of el.querySelectorAll('[data-question]')) q.classList.toggle('pinned', q.dataset.question === cw.pinned);
}
function chatKeyed(parent, items) {
  const had = new Map([...parent.children].map((el) => [el.dataset.k, el]));
  let cursor = parent.firstElementChild;
  for (const item of items) {
    let el = had.get(item.key);
    if (!el) {
      el = document.createElement(item.tag);
      el.className = item.cls;
      el.dataset.k = item.key;
      if (item.fill) el.innerHTML = '<div class="t-body"></div>';
    }
    if (el === cursor) cursor = cursor.nextElementSibling;
    else parent.insertBefore(el, cursor);
    if (item.fill) {
      chatSet(el.firstElementChild, item.fill);
      chatReapply(el);
    } else {
      chatSet(el, item.html);
    }
    had.delete(item.key);
  }
  for (const el of had.values()) el.remove();
}
function patchChat() {
  if (!state.model.owner_asks) return;
  cw.seen = new Set();
  const { all, lines, count, hidden, attention } = chatRows();
  const list = $('#cw-list');
  if (!list) return;
  const items = [];
  let lastDay = null;
  lines.forEach((row, at) => {
    if (at === 0 && attention && cw.filter !== 'needs') items.push({ key: 'head:you', tag: 'div', cls: 'dayline', html: `<span class="eyebrow">Needs you first</span><span>${hidden ? `<button class="text-button" data-cw-filter="needs">${hidden} more</button>` : ''}</span>` });
    if (at >= attention && row.t.at) {
      const day = chatDayWord(row.t.at);
      if (day !== lastDay) items.push({ key: `day:${day}`, tag: 'div', cls: 'dayline', html: `<span class="eyebrow">${esc(day)}</span><span></span>` });
      lastDay = day;
    }
    items.push({ key: row.t.key, tag: 'article', cls: `thread${row.t.rows.length ? '' : ' loose'}`, fill: threadBody(row) });
  });
  chatKeyed(list, items);
  if (!lines.length) list.innerHTML = `<div class="empty">${state.model.owner_asks.messages.length || cw.query ? 'No asks match this view.' : '<b>Ask the communicator</b> It answers here, and you can reply to any of it.'}${cw.query || cw.filter !== 'all' ? '<button class="text-button" data-cw-clear>Clear filters and search</button>' : ''}</div>`;
  chatSet($('#cw-filters'), chatFilters(all));
  chatSet($('#cw-rail'), chatRail(all));
  chatSet($('#cw-more'), count > lines.length + hidden ? `<button class="archive-toggle" data-cw-more>${chatIcon('history')} Show earlier <span>${count - lines.length - hidden}</span></button>` : '');
  const overdue = [...overdueRows(), ...picksRow()];
  chatSet($('#cw-owed'), overdue.length ? `<details class="ct-owed" data-id="cw-owed"${state.open.has('cw-owed') ? ' open' : ''}><summary>${plural(overdue.length, 'thing')} owed to you</summary>${eventList(overdue)}</details>` : '');
  const failed = rp.failed.get('');
  $('#cw-failed').hidden = !failed;
  $('#cw-failed').textContent = failed ? `Not sent: ${failed}. Try again.` : '';
  restoreBoxes();
}
function chatScroll(arrived) {
  const spot = cw.focus && document.getElementById(`rp-${cw.focus}`);
  if (spot) {
    cw.focus = null;
    spot.scrollIntoView({ block: 'center' });
  } else if (arrived) {
    window.scrollTo(0, history.state?.y ?? 0);
  }
}
function chatAim(id) {
  Object.assign(cw, { focus: id, filter: 'all', query: '' });
  if ($('#cw-search')) $('#cw-search').value = '';
}
function chatInPlace(p, stack) {
  if (p.screen !== 'replies' || stack.length || !$('#main > .cw') || state.depth) return false;
  patchChat();
  chatScroll(false);
  syncChatModal();
  syncReviewEmbed(route());
  placeEmbed();
  syncCardEmbed();
  unfollow();
  tickFresh();
  return true;
}
async function chatSend(on, about, text) {
  if (!text.trim()) return;
  const sent = { on, text: text.trim(), at: new Date().toISOString() };
  rp.sent.push(sent);
  localStorage.removeItem(chatDraft(on));
  const box = liveChatBox(on);
  if (box) box.value = '';
  if (on) {
    box?.blur();
    cw.replying.delete(on);
  }
  rp.failed.delete(on);
  render();
  const out = await fetch('/api/reply', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, on, about }) })
    .then((res) => res.json())
    .catch((failure) => ({ error: `could not reach the dashboard server (${failure.message})` }));
  if (out.error) {
    rp.sent.splice(rp.sent.indexOf(sent), 1);
    localStorage.setItem(chatDraft(on), text);
    if (on) cw.replying.add(on);
    rp.failed.set(on, out.error);
  } else {
    sent.seq = out.seq;
  }
  render();
  if (out.error && liveChatBox(on)) liveChatBox(on).value = text;
}
function liveChatBox(on) {
  return $('#main').querySelector(`[name="chat"][data-draft="${CSS.escape(on)}"]`);
}
function liveChatBoxes() {
  return new Map([...$('#main').querySelectorAll('.composer')].filter((form) => form.chat.value || form.contains(document.activeElement)).map((form) => [form.dataset.chatSend, form]));
}
function keepChatBoxes(live) {
  for (const form of $('#main').querySelectorAll('.composer')) {
    const held = live.get(form.dataset.chatSend);
    if (held) form.replaceWith(held);
  }
}
function syncChatModal() {
  const box = $('#chatmodal');
  if (cw.reader) {
    if (!box.open) box.showModal();
    if (box.dataset.card === cw.reader.url) return;
    box.dataset.card = cw.reader.url;
    box.innerHTML = `<header><b>${esc(cw.reader.title)}</b><a class="btn ghost" href="${esc(cw.reader.url)}" target="_blank" rel="noopener">New tab</a><button class="btn ghost" data-chat-close>Close</button></header><iframe title="${esc(cw.reader.title)}" src="${esc(framedUrl(cw.reader.url))}"></iframe>`;
    return;
  }
  const card = rp.modal && state.model.board.cards.find((c) => c.id === rp.modal);
  if (!card || state.model.board.answered[card.id]) {
    rp.modal = null;
    delete box.dataset.card;
    if (box.open) box.close();
    return;
  }
  if (!box.open) box.showModal();
  if (box.dataset.card === card.id && (card.embed || box.contains(document.activeElement))) return;
  box.dataset.card = card.id;
  const body = card.embed
    ? `<iframe title="${esc(card.title)}" src="${esc(framedUrl(card.embed))}"></iframe>`
    : boardCard({ card });
  box.innerHTML = `<header><b>${inline(plain(card.title))}</b><button class="btn ghost" data-chat-close>Close</button></header>${body}`;
  restoreBoxes();
}
document.addEventListener('close', (e) => {
  if (e.target.id !== 'chatmodal') return;
  rp.modal = null;
  cw.reader = null;
  delete e.target.dataset.card;
}, true);
document.addEventListener('keydown', (e) => {
  if (e.target.name === 'chat' && e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    e.target.form.requestSubmit();
  }
  if (e.key === 'Escape' && cw.pinned) {
    cw.pinned = null;
    patchChat();
  }
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('.diagram-trigger[data-doc-open]')) {
    e.preventDefault();
    e.target.click();
  }
});
document.addEventListener('mouseover', (e) => {
  const frame = e.target.closest('.diagram-trigger')?.querySelector('.diagram-popover iframe[data-src]');
  if (!frame) return;
  frame.src = frame.dataset.src;
  delete frame.dataset.src;
});
document.addEventListener('input', (e) => {
  if (e.target.id !== 'cw-search') return;
  cw.query = e.target.value;
  cw.limit = CHAT_SHOWN;
  patchChat();
});
document.addEventListener('click', (e) => {
  const opener = e.target.closest('[data-chat-modal]');
  if (opener) {
    rp.modal = opener.dataset.chatModal;
    return render();
  }
  if (e.target.closest('[data-chat-close]') || e.target.id === 'chatmodal') {
    rp.modal = null;
    cw.reader = null;
    return render();
  }
  if (!e.target.closest('.cw')) return;
  const written = e.target.closest('[data-choose]')?.closest('.question')?.querySelector('[name="pick-other"]');
  if (written) {
    written.value = '';
    localStorage.removeItem(`draft:pick-other:${written.dataset.draft}:`);
  }
  const read = e.target.closest('[data-read]');
  if (read) {
    if (!cw.read.delete(read.dataset.read)) cw.read.add(read.dataset.read);
    return chatReapply(read.closest('.thread'));
  }
  const ask = e.target.closest('[data-ask-open]');
  if (ask) {
    if (!cw.open.delete(ask.dataset.askOpen)) cw.open.add(ask.dataset.askOpen);
    return chatReapply(ask.closest('.thread'));
  }
  const doc = e.target.closest('[data-doc-open]');
  if (doc) {
    cw.reader = { url: doc.dataset.docOpen, title: doc.dataset.docTitle };
    return syncChatModal();
  }
  const thread = e.target.closest('[data-chat-thread]');
  if (thread) {
    const key = thread.dataset.chatThread;
    if (!cw.replying.delete(key)) cw.replying.add(key);
    else liveChatBox(key)?.closest('form').remove();
    patchChat();
    return liveChatBox(key)?.focus();
  }
  const done = e.target.closest('[data-chat-done]');
  if (done) return chatSend(done.dataset.chatDone, done.dataset.about, 'Done');
  const pin = e.target.closest('[data-q-pin]');
  if (pin) {
    cw.pinned = cw.pinned === pin.dataset.qPin ? null : pin.dataset.qPin;
    return document.querySelectorAll('.cw .thread').forEach(chatReapply);
  }
  const filter = e.target.closest('[data-cw-filter]');
  if (filter) {
    cw.filter = filter.dataset.cwFilter;
    cw.limit = CHAT_SHOWN;
    return patchChat();
  }
  if (e.target.closest('[data-cw-more]')) {
    cw.limit += CHAT_SHOWN;
    return patchChat();
  }
  if (e.target.closest('[data-cw-clear]')) {
    cw.query = '';
    cw.filter = 'all';
    $('#cw-search').value = '';
    return patchChat();
  }
  if (e.target.closest('[data-cw-new]')) return liveChatBox('')?.focus();
  if (cw.pinned && !e.target.closest('.question')) {
    cw.pinned = null;
    document.querySelectorAll('.cw .thread').forEach(chatReapply);
  }
});
document.addEventListener('submit', (e) => {
  const form = e.target.closest('[data-chat-send]');
  if (!form) return;
  e.preventDefault();
  chatSend(form.dataset.chatSend, form.dataset.about, form.chat.value);
});
