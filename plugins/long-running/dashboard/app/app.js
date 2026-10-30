const PACIFIC = 'America/Los_Angeles';
const LATE_MS = 3 * 60 * 1000;
const clockFmt = new Intl.DateTimeFormat('en-US', { timeZone: PACIFIC, hour: 'numeric', minute: '2-digit' });
const dayFmt = new Intl.DateTimeFormat('en-US', { timeZone: PACIFIC, weekday: 'short' });
const KIND_WORD = { owner: 'You', go: 'Approved to land', landed: 'Landed', release: 'Release', incident: 'Incident', mechanism: 'Cause found', 'fix-live': 'Fix live', opened: 'Opened', report: 'Report', hold: 'Held', lift: 'Hold lifted', blocker: 'Blocked', defect: 'Tool problem', correction: 'Correction', decide: 'Decision', ask: 'Question', answer: 'Answer' };

const state = { drive: new Map(), model: null, version: -1, open: new Set(), seen: new Set(), chat: [], asking: false, taps: [] };
const SUGGESTED = ['What is blocking the next release?', 'What did I approve tonight that has not landed yet?', 'Which workers are stuck, and on what?'];
const $ = (sel) => document.querySelector(sel);

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}
function clock(iso) {
  const d = new Date(iso);
  const today = dayFmt.format(new Date()) === dayFmt.format(d);
  return (today ? '' : dayFmt.format(d) + ' ') + clockFmt.format(d);
}
function minutesSince(iso) {
  return Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
}
function sinceClock(label) {
  const found = /^(\d{1,2}):(\d{2}) (AM|PM)$/.exec(label ?? '');
  if (!found) return null;
  const at = new Date();
  at.setHours((Number(found[1]) % 12) + (found[3] === 'PM' ? 12 : 0), Number(found[2]), 0, 0);
  if (at > Date.now()) at.setDate(at.getDate() - 1);
  return at.toISOString();
}
function spanFor(iso) {
  const m = minutesSince(iso);
  return `for ${m < 60 ? `${m} min` : m < 600 ? `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ''}` : `${Math.round(m / 60)} hours`}`;
}
function blockedFor(iso) {
  return `${spanFor(iso)}, since ${clock(iso)}`;
}
function agoWords(iso) {
  const m = minutesSince(iso);
  if (m < 1) return 'just now';
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h >= 12) return `at ${clock(iso)}`;
  return m % 60 ? `${h} h ${m % 60} min ago` : `${h} h ago`;
}
function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}
function prTitle(n) {
  return state.model.prs[n]?.title ?? boardRow(n)?.title ?? `Pull request ${n}`;
}
function boardRow(n) {
  return state.model.board.rows.find((r) => r.number === Number(n));
}
function verdictState(row) {
  return row.state;
}
const ASK_PARTS = new Set(['choice', 'approval', 'input']);
function tapped(tap) {
  state.taps.push(tap);
  settle(state.model);
  render();
}
function unask(model) {
  const open = new Set(model.needs.flatMap((n) => (n.kind === 'card' ? [n.card.id] : n.prs.map((pr) => String(pr.number)))));
  const owners = [...open].map((ask) => model.prs[ask]?.lane).filter(Boolean);
  const asked = (w) => (w.asks ?? []).some((ask) => open.has(String(ask).replace(/^#/, ''))) || w.workers.some((lane) => owners.some((owner) => sameWorker(owner, lane)));
  for (const w of [...(model.brief?.work ?? []), ...(model.pages?.work?.running ?? [])]) {
    if (w.status === 'Ready for you' && !asked(w)) Object.assign(w, { status: 'Stuck', latest: 'Off your board now; its next step is being rewritten.' });
  }
}
function settle(model) {
  state.taps = state.taps.filter((t) => t.seq > (model.board.seq ?? 0));
  const cards = [...model.board.cards, ...model.needs.filter((item) => item.kind === 'card').map((item) => item.card)];
  for (const t of state.taps) {
    if (t.number) {
      const row = model.board.rows.find((r) => r.block === t.block && r.number === t.number && r.head === t.head);
      if (row) Object.assign(row, { verdict: t.verdict, state: t.state });
      continue;
    }
    for (const card of cards) {
      if (t.noted && card.id === t.block) card.noted = true;
      const part = card.parts.find((p) => p.id === t.block);
      if (part) part.answer = t.answer;
      if (card.choice !== t.block) continue;
      const next = card.parts.find((p) => p.kind === 'choice' && !p.answer);
      Object.assign(card, { choice: next?.id ?? null, options: next?.options ?? [], recommended: next?.options.find((o) => o.recommended)?.label ?? null });
    }
  }
  const live = (r) => model.board.rows.find((b) => b.block === r.block && b.number === r.number)?.state ?? r.state;
  const open = (card) => {
    const asks = card.parts.filter((p) => ASK_PARTS.has(p.kind));
    return asks.length ? asks.some((p) => !p.answer) : card.embed || !card.noted;
  };
  model.board.cards = model.board.cards.filter(open);
  model.needs = model.needs.flatMap((item) => {
    if (item.kind === 'card') return open(item.card) ? [item] : [];
    if (item.kind !== 'prs') return [item];
    const rows = [...item.prs, ...item.others].map((r) => ({ ...r, state: live(r) }));
    const prs = rows.filter((r) => r.state === 'you');
    return prs.length ? [{ ...item, prs, others: rows.filter((r) => r.state !== 'you') }] : [];
  });
}
function driveEntry(n) {
  return state.drive.get(Number(n));
}
function prState(n) {
  if (state.model.pr_state[n]?.state === 'Landed') return 'landed';
  return driveEntry(n)?.state ?? (state.model.steps.some((s) => s.untracked.includes(Number(n))) ? 'untracked' : 'closed');
}
const STATE_WORD = { landed: 'Landed', closed: 'Closed', you: 'Waiting on you', failing: 'Tests failing', held: 'Held', queued: 'Approved, waiting to land', doing: 'Being worked on', waiting: 'Waiting its turn', untracked: 'Not tracked by the merge ledger yet' };
const STATE_TONE = { landed: 'ok', closed: '', you: 'bad', failing: 'bad', held: 'warn', queued: 'ok', doing: 'acc', waiting: '', untracked: 'warn' };
function prHref(n) {
  return state.model.prs[n] ? `#/review/${n}` : `https://github.com/${state.model.repo}/pull/${n}`;
}
function prLink(n) {
  const external = !state.model.prs[n];
  const s = prState(n);
  return `<a class="prc ${s}" href="${prHref(n)}"${external ? ' target="_blank" rel="noopener"' : ''} title="${esc(prTitle(n))} · ${STATE_WORD[s]}">${s === 'landed' ? '✓' : s === 'closed' ? '' : `<i class="lt ${s}"></i>`}#${n}</a>`;
}
function words(text) {
  const rels = state.model?.now?.releases ?? [];
  return String(text ?? '').replace(/\bR(\d{1,2})\b ?/g, (all, n, at, whole) => {
    const r = rels.find((x) => x.id === `R${n}`);
    if (!r) return all;
    return whole.startsWith(r.name, at + all.length) ? '' : `the “${r.name}” release${all.endsWith(' ') ? ' ' : ''}`;
  });
}
function inline(md) {
  return esc(words(md))
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/#(\d{5})\b/g, (_, n) => prLink(n))
    .replace(/(\d) (ms|s|min|h|KB|MB|GB)\b/g, '$1&nbsp;$2');
}
function markdown(md) {
  const out = [];
  let list = null;
  let table = null;
  let code = null;
  const cells = (line) => line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  const flushTable = () => {
    if (!table) return;
    const [head, ...rows] = table;
    out.push(`<div class="tablewrap"><table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td data-h="${esc(head[i] ?? '')}">${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
    table = null;
  };
  for (const line of md.split('\n')) {
    if (code !== null) {
      if (/^\s*```/.test(line)) { out.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`); code = null; } else code.push(line);
      continue;
    }
    if (/^\s*```/.test(line)) { flushTable(); code = []; continue; }
    const disclose = /^\s*(<details>)?(?:<summary>(.*?)<\/summary>)?(<\/details>)?\s*$/.exec(line);
    if (disclose && (disclose[1] || disclose[2] !== undefined || disclose[3])) {
      flushTable();
      if (list) { out.push(`</${list}>`); list = null; }
      out.push(`${disclose[1] ? '<details>' : ''}${disclose[2] !== undefined ? `<summary>${inline(disclose[2])}</summary>` : ''}${disclose[3] ? '</details>' : ''}`);
      continue;
    }
    if (/^\s*\|/.test(line)) {
      if (list) { out.push(`</${list}>`); list = null; }
      if (/^\s*\|[\s:|-]+\|\s*$/.test(line)) continue;
      (table ??= []).push(cells(line));
      continue;
    }
    flushTable();
    const item = /^\s*(?:[-*]|\d+\.)\s+(.*)$/.exec(line);
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (!item && list) { out.push(`</${list}>`); list = null; }
    if (item) {
      const kind = /^\s*\d+\./.test(line) ? 'ol' : 'ul';
      if (list !== kind) { if (list) out.push(`</${list}>`); out.push(`<${kind}>`); list = kind; }
      out.push(`<li>${inline(item[1])}</li>`);
    } else if (heading) {
      const id = heading[2].toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
      out.push(`<h${heading[1].length + 1} id="md-${id}">${inline(heading[2])}</h${heading[1].length + 1}>`);
    } else if (line.trim()) {
      out.push(`<p>${inline(line)}</p>`);
    }
  }
  flushTable();
  if (code !== null) out.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);
  if (list) out.push(`</${list}>`);
  return out.join('');
}




function needsTotal() {
  return (state.model?.needs ?? []).reduce((n, item) => n + (item.kind === 'prs' ? item.prs.length : item.kind === 'card' ? 1 : 0), 0);
}
function openQuestion(card) {
  return state.model.needs.find((item) => item.kind === 'card' && item.card.id === card);
}
function boardCard(item) {
  const c = item.card;
  const said = rewritten('cards', c.key);
  const busy = sending(c.choice) ? ' disabled' : '';
  const label = (o, i) => said?.answers?.[i] || o.label.replace(/\s*\(Recommended\)\s*$/i, '');
  const button = (o, i) => `<button class="btn${o.recommended ? ' pri' : ''}" data-choose="${esc(o.id)}" data-choice="${esc(c.choice)}"${busy}>${esc(label(o, i))}</button>`;
  const lead = c.options.findIndex((o) => o.recommended);
  const others = c.options.map((o, i) => (i === lead ? '' : button(o, i))).join('');
  const id = `answers-${c.id}`;
  const answers = c.embed
    ? `<button class="btn" data-embed-card="${esc(c.id)}">${ov.embedCard === c.id ? 'Showing the card' : 'Answer it here'}</button>`
    : c.choice ? `${lead >= 0 ? button(c.options[lead], lead) : ''}<details class="bq-more" data-id="${esc(id)}"${state.open.has(id) || lead < 0 ? ' open' : ''}><summary>${lead >= 0 ? 'Other answers' : 'Answers'}</summary><div class="bq-acts">${others}</div></details>` : '';
  return `<article class="mr bq" id="card-${esc(c.id)}"><header><span class="mr-t">${esc(said?.question ?? words(c.title))}</span><span class="mr-acts"><a class="btn ghost" href="${esc(state.model.board.url)}?block=${encodeURIComponent(c.id)}" target="_blank" rel="noopener">Background on your board</a></span></header>
    ${said ? `<p class="mr-why">${esc(said.means)}</p>` : ''}${answers ? `<div class="bq-acts">${answers}</div>` : ''}${c.choice ? sentNote(c.choice) : ''}</article>`;
}
function syncCardEmbed(r) {
  const host = $('#cardembed');
  const card = r.screen === 'review' && state.model.board.cards.find((c) => c.id === ov.embedCard && c.embed);
  host.hidden = !card;
  if (!card) {
    ov.embedCard = null;
    delete host.dataset.card;
    host.replaceChildren();
    return;
  }
  if (host.dataset.card === card.id) return;
  host.dataset.card = card.id;
  host.innerHTML = `<header><b>${inline(card.title)}</b><button class="btn ghost" data-embed-close>Close</button></header><iframe title="${esc(card.title)}" src="${esc(card.embed)}"></iframe>`;
}
window.addEventListener('message', (e) => {
  const frame = $('#cardembed iframe');
  if (e.data?.type === 'cc-present.height' && frame && e.source === frame.contentWindow) frame.style.height = `${e.data.px}px`;
});


function chatTurns() {
  return state.chat.map((t) => `<div class="turn ${t.role}">${t.role === 'user' ? esc(t.content) : markdown(t.content) || '<span class="dim">Reading the drive…</span>'}</div>`).join('');
}
function askScreen() {
  const turns = chatTurns();
  return `<h2 class="sec">Ask about the drive <span class="dim">answers come only from the drive's records, the plan and the merge board</span></h2>
    <div class="chat">${turns || `<div class="suggest">${SUGGESTED.map((q) => `<button class="btn" data-ask="${esc(q)}">${esc(q)}</button>`).join('')}</div>`}</div>
    <form class="ask" id="ask"><textarea name="q" rows="2" placeholder="Ask anything about the drive…"${state.asking ? ' disabled' : ''}></textarea><button class="btn pri"${state.asking ? ' disabled' : ''}>Ask</button></form>`;
}
async function ask(question) {
  if (!question.trim() || state.asking) return;
  const history = state.chat.map(({ role, content }) => ({ role, content }));
  state.chat.push({ role: 'user', content: question }, { role: 'assistant', content: '' });
  state.asking = true;
  render();
  fetch('/api/asked', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: question }) });
  const answer = state.chat[state.chat.length - 1];
  try {
    answer.content = await assistAsk(question, history, (text) => {
      answer.content = text;
      if (route().screen === 'ask') $('#main .chat').innerHTML = chatTurns();
    });
  } catch (error) {
    answer.content += `\n\nThe answer stopped: ${error.message}`;
  } finally {
    state.asking = false;
    render();
  }
}

const DRAWER = /^(?:review\/\d+(?:\/code)?|plan\/[^/~]+|status\/s\d+|(?:work|story|worker|blocker|question)\/[^/~]+)$/;
const NATURAL = { review: '#/review', plan: '#/plan', status: '#/status', work: '#/work', worker: '#/work' };
const BASE_WORD = { overview: 'Now', plan: 'Plan', status: 'Status', work: 'Work', review: 'Review', history: 'History', ask: 'Ask' };
function drawerSeg(href) {
  const path = String(href ?? '').replace(/^#\/?/, '');
  return DRAWER.test(path) ? path.replace(/^story\//, 'work/') : null;
}
function baseOf(hash = location.hash) {
  return hash.split('~')[0] || '#/';
}
function stackOf(hash = location.hash) {
  return hash.split('~').slice(1);
}
function stackHash(base, stack) {
  return [base, ...stack].join('~');
}
function route(hash = location.hash) {
  const parts = (stackOf(hash).at(-1) ?? baseOf(hash)).replace(/^#\/?/, '').split('/').filter(Boolean);
  return { screen: parts[0] ?? 'overview', id: parts[1], tab: parts[2] };
}
function prOpen(r = route()) {
  return stackOf().length > 0 && r.screen === 'review' && /^\d+$/.test(r.id ?? '');
}
function pageRoute() {
  return route(baseOf());
}
function render() {
  const m = state.model;
  if (!m || !m.prs) return;
  const p = pageRoute();
  const stack = stackOf();
  document.title = m.title;
  $('.brand').textContent = m.title;
  const tabs = [['overview', 'Now'], ['plan', 'Plan'], ['status', 'Status'], ['work', 'Work'], ['review', 'Review', needsTotal()], ['history', 'History']];
  $('#kbtn').classList.toggle('on', p.screen === 'jump');
  const typing = document.activeElement?.matches('#main input, #main textarea') ? document.activeElement : null;
  const caret = typing && [typing.name, typing.dataset.draft ?? '', typing.selectionStart, typing.selectionEnd];
  const spotRoot = [$('#main'), $('#tabs')].find((el) => el.contains(document.activeElement));
  const spot = !typing && state.shown === location.hash && spotRoot && focusSpot(spotRoot);
  $('#tabs').innerHTML = tabs.map(([id, label, count]) => `<a class="tab${(p.screen === 'milestone' ? 'plan' : p.screen) === id ? ' on' : ''}" href="#/${id === 'overview' ? '' : id}">${label}${count ? `<span class="count">${count}</span>` : ''}</a>`).join('');
  const screens = { overview: nowScreen, plan: planScreen, work: workScreen, review: () => reviewScreen(p.id ?? ''), milestone: () => (curated('plan') ? milestonePage(curated('plan'), Number(p.id)) : writing(state.model.milestones[p.id])), history: () => historyScreen(p.id), ask: askScreen, status: statusScreen, jump: () => `<div class="pg">${pageHead('Jump to anything')}<p class="pg-lead">Find a change, a part of the plan, a piece of work or a page, or ask the assistant a question.</p></div>` };
  const y = window.scrollY;
  const kept = [...document.querySelectorAll('[data-keep-scroll]')].map((el) => [el.dataset.keepScroll, el.scrollLeft, el.scrollTop]);
  $('#main').innerHTML = (screens[p.screen] ?? nowScreen)() + drawers(stack) + `<div class="foot">${m.errors && Object.keys(m.errors).length ? `Some sources could not be read just now (${esc(Object.keys(m.errors).join(', '))}). ` : ''}All times Pacific.</div>`;
  if (stack.length > (state.depth ?? 0)) $('#main .pr-drawer.front')?.classList.add('in');
  state.depth = stack.length;
  decorate(p);
  composeLinks(stack);
  holdPeek();
  if (state.shown !== location.hash) {
    if (baseOf(state.shown ?? '') !== baseOf()) {
      $('#main').classList.add('enter');
      setTimeout(() => $('#main').classList.remove('enter'), 220);
    }
    state.shown = location.hash;
  }
  const again = caret && [...$('#main').querySelectorAll(`[name="${caret[0]}"]`)].find((el) => (el.dataset.draft ?? '') === caret[1]);
  if (again) { again.focus(); again.setSelectionRange(caret[2], caret[3]); }
  if (spot) refocus(spot, spotRoot);
  window.scrollTo(0, y);
  for (const [id, left, top] of kept) { const el = document.querySelector(`[data-keep-scroll="${id}"]`); if (el) [el.scrollLeft, el.scrollTop] = [left, top]; }
  syncReviewEmbed(route());
  placeEmbed();
  syncCardEmbed(p);
  if (p.screen === 'review' && p.id === 'q' && state.focused !== location.hash) {
    state.focused = location.hash;
    (p.tab ? document.getElementById(`card-${decodeURIComponent(p.tab)}`) : document.getElementById('review-questions'))?.scrollIntoView({ block: p.tab ? 'center' : 'start' });
  }
  if (p.screen === 'overview') mountNow();
  const top = route();
  const followed = stack.length && top.screen === 'work' ? decodeURIComponent(top.id) : p.screen === 'overview' ? feed.item : null;
  if (followed) follow(followed);
  else unfollow();
  tickFresh();
}
function drawerOf(seg) {
  const [kind, raw, tab] = seg.split('/');
  const id = decodeURIComponent(raw);
  const gone = (text) => () => `<p class="pg-none">${text}</p>`;
  if (kind === 'review') return { kick: `Pull request #${id}`, title: prTitle(id), tone: verdictState(boardRow(id) ?? {}) === 'you' ? 'you' : '', body: () => reviewPR(id, tab) };
  if (kind === 'work') return { kick: 'Work', title: workItem(id)?.title ?? id, body: () => workDrawer(id) };
  if (kind === 'worker') return { kick: 'Worker', title: workerName(id), body: () => workerScreen(id) };
  if (kind === 'question') {
    const item = openQuestion(id);
    return { kick: 'Needs you', title: 'A question for you', tone: 'you', body: item ? () => boardCard(item) : gone('This question is answered or no longer open.') };
  }
  if (kind === 'blocker') return { kick: 'Blocker', title: blockerById(id)?.node.text ?? id, tone: blockerById(id)?.node.state ?? '', body: () => blockerPage(id) };
  if (kind === 'status') {
    const row = statusRows().rows[id.slice(1)];
    return { kick: 'Plan step', title: row ? words(row[0]) : 'Plan step', body: row ? () => statusStep(row) : gone('This step is no longer in the plan.') };
  }
  const plan = curated('plan');
  const ms = /^m(\d+)$/.exec(id);
  if (ms) return { kick: 'Milestone', title: state.model.milestones[ms[1]] ?? 'Milestone', tone: 'ms', own: `#/milestone/${ms[1]}`, body: () => (plan ? milestonePage(plan, Number(ms[1])) : writing('Milestone')) };
  const part = plan?.parts.find((x) => x.id === id);
  return { kick: 'Part of the plan', title: part?.name ?? id, body: part ? () => partPage(part) : gone('This part is no longer in the plan.') };
}
function drawers(stack) {
  if (!stack.length) return '';
  const base = baseOf();
  const heads = stack.map(drawerOf);
  const top = heads.at(-1);
  const list = history.state?.list ?? [];
  const at = list.indexOf(stack.at(-1).replace(/\/code$/, ''));
  const tone = (d) => (d.tone ? ` t-${d.tone}` : '');
  const crumbs = [`<a href="${base}" data-pop="0">${BASE_WORD[route(base).screen] ?? 'Now'}</a>`, ...heads.slice(0, -1).map((d, i) => `<a href="${stackHash(base, stack.slice(0, i + 1))}" data-pop="${i + 1}">${esc(d.title)}</a>`), `<b>${esc(top.title)}</b>`].join('<i>›</i>');
  const step = (to, label, key) => (to ? `<a class="btn" href="#/${to}" data-key="${key}" title="${key === 'j' ? 'Next' : 'Previous'} in the list you opened this from">${label}<kbd>${key.toUpperCase()}</kbd></a>` : `<span class="btn off">${label}<kbd>${key.toUpperCase()}</kbd></span>`);
  const stepper = at >= 0 && list.length > 1 ? `<span class="dr-step">${step(list[at - 1], '‹', 'k')}<span class="dr-at">${at + 1} of ${list.length}</span>${step(list[at + 1], '›', 'j')}</span>` : '';
  const under = heads.slice(0, -1).map((d, i) => `<a class="pr-drawer under${tone(d)}" style="--back:${Math.min(3, stack.length - 1 - i)}" href="${stackHash(base, stack.slice(0, i + 1))}" data-pop="${i + 1}" title="Back to ${esc(d.title)}"><span class="dr-spine"><span>${esc(d.kick)}</span>${esc(d.title)}</span></a>`).join('');
  return `<div class="pr-scrim" data-drawer-close></div>${under}<aside class="pr-drawer front${tone(top)}" role="dialog" aria-label="${esc(top.title)}" data-keep-scroll="drawer-${esc(stack.at(-1))}"><div class="dr-bar"><nav class="dr-crumbs" aria-label="Opened from">${crumbs}</nav>${stepper}${top.own ? `<a class="btn ghost" href="${top.own}">Open as a page</a>` : ''}<button class="btn pr-x" data-drawer-close>Close <kbd>Esc</kbd></button></div><div class="dr-body">${top.body()}</div></aside>`;
}
function composeLinks(stack) {
  const base = baseOf();
  const top = $('#main .pr-drawer.front');
  const steps = top?.querySelector('.dr-step');
  if (steps) for (const a of top.querySelectorAll('.dr-body [data-key="j"], .dr-body [data-key="k"]')) a.remove();
  for (const a of $('#main').querySelectorAll('a[href^="#/"]')) {
    const seg = drawerSeg(a.getAttribute('href'));
    if (!seg) continue;
    const inside = Boolean(top?.contains(a));
    const swap = inside && a.matches('[data-key], .review-tabs a');
    const open = inside && !swap ? stack.indexOf(seg) : -1;
    if (open >= 0) {
      a.dataset.pop = open + 1;
      a.setAttribute('href', stackHash(base, stack.slice(0, open + 1)));
      continue;
    }
    a.dataset.seg = seg;
    if (swap) a.dataset.swap = '';
    a.setAttribute('href', stackHash(base, [...(inside ? stack.slice(0, swap ? -1 : undefined) : []), seg]));
  }
}
function siblingsOf(link) {
  const plain = (s) => s.replace(/\/code$/, '');
  const own = plain(link.dataset.seg);
  const kind = own.split('/')[0];
  const stop = link.closest('.pr-drawer') ?? $('#main');
  for (let el = link.parentElement; el && stop.contains(el); el = el.parentElement) {
    const segs = [...new Set([...el.querySelectorAll('a[data-seg]:not([data-swap])')].filter((l) => l.checkVisibility()).map((a) => plain(a.dataset.seg)).filter((s) => s.split('/')[0] === kind))];
    if (segs.length > 1) return segs;
  }
  return [own];
}
function remember() {
  if (pageRoute().screen !== 'jump') Object.assign(state, { lastHash: location.hash, lastUps: history.state?.ups ?? [] });
}
function arrive(from) {
  const seg = drawerSeg(location.hash);
  const prior = state.lastHash;
  const known = (hash) => stackOf(hash).map((_, i) => state.lastUps?.[i] ?? 0);
  if (seg) {
    if (prior === undefined) return history.replaceState({ ups: [], list: [seg] }, '', stackHash(NATURAL[seg.split('/')[0]] ?? '#/', [seg]));
    return history.replaceState({ ups: [...known(prior), from === '#/jump' ? 2 : 1], list: [seg] }, '', `${prior || '#/'}~${seg}`);
  }
  const was = stackOf(prior ?? '');
  const now = stackOf();
  const pushed = prior !== undefined && baseOf(prior) === baseOf() && now.length === was.length + 1 && was.every((s, i) => s === now[i]);
  history.replaceState({ ups: pushed ? [...known(prior), 1] : [], ...(state.nextList?.hash === location.hash && { list: state.nextList.list }) }, '');
}
function swapDrawer(to) {
  history.replaceState(history.state, '', stackHash(baseOf(), [...stackOf().slice(0, -1), drawerSeg(to)]));
  remember();
  render();
  if (document.activeElement === document.body) $('#main .pr-drawer.front .pr-x')?.focus({ preventScroll: true });
}
function popTo(len) {
  const stack = stackOf();
  const ups = (history.state?.ups ?? []).slice(len);
  if (len >= stack.length) return;
  if (ups.length === stack.length - len && ups.every(Boolean)) history.go(-ups.reduce((a, b) => a + b, 0));
  else location.replace(stackHash(baseOf(), stack.slice(0, len)));
}
function placeEmbed() {
  const tabs = $('#main .pr-drawer .review-tabs');
  if (tabs) $('#embed').style.setProperty('--code-top', `${Math.max(64, tabs.getBoundingClientRect().bottom + 10)}px`);
}
document.addEventListener('scroll', (e) => { if (e.target.classList?.contains('pr-drawer')) placeEmbed(); }, true);
window.addEventListener('resize', placeEmbed);
document.addEventListener('click', (e) => {
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const pop = e.target.closest('[data-pop], [data-drawer-close]');
  if (pop) {
    e.preventDefault();
    return popTo(pop.dataset.pop === undefined ? stackOf().length - 1 : Number(pop.dataset.pop));
  }
  const link = e.target.closest('#main a[data-seg]');
  if (!link) return;
  if (link.hasAttribute('data-swap')) {
    e.preventDefault();
    return swapDrawer(link.dataset.seg);
  }
  state.nextList = { hash: link.getAttribute('href'), list: siblingsOf(link) };
});
const FOCUSABLE = 'a[href], button, summary, input, select, textarea, [tabindex]';
function signature(el) {
  return [...el.attributes].filter((a) => a.name !== 'class').map((a) => `${a.name}=${a.value}`).join(' ');
}
function focusSpot(root = $('#main'), el = document.activeElement) {
  if (!el || !root.contains(el)) return null;
  const stops = [...root.querySelectorAll(FOCUSABLE)];
  const sig = signature(el);
  return { sig, nth: stops.filter((x) => signature(x) === sig).indexOf(el), at: stops.indexOf(el) };
}
function refocus(spot, root = $('#main')) {
  const stops = [...root.querySelectorAll(FOCUSABLE)];
  (stops.filter((x) => signature(x) === spot.sig)[spot.nth] ?? stops[spot.at])?.focus({ preventScroll: true });
}
function tickFresh() {
  const m = state.model;
  if (!m?.built_at) return;
  const age = Date.now() - new Date(m.built_at);
  const el = $('#fresh');
  el.classList.toggle('late', age > LATE_MS);
  el.textContent = age > LATE_MS ? `Not updated since ${clock(m.built_at)}` : `Updated ${Math.max(0, Math.round(age / 1000))} s ago · Pacific time`;
}

let loading = null;
function load() {
  loading ??= fetchModel().finally(() => {
    loading = null;
    if (state.wanted !== undefined && state.wanted !== state.fetched) load();
  });
}
async function fetchModel() {
  const model = await (window.firstModel ?? fetch('/api/model', { cache: 'no-store' }).then((res) => res.json()));
  window.firstModel = null;
  state.fetched = model.version;
  if (!model.prs) return setTimeout(load, 1000);
  if ((model.board.seq ?? 0) < (state.model?.board.seq ?? 0)) return;
  settle(model);
  unask(model);
  state.model = model;
  state.drive = new Map(model.drive.prs.map((e) => [e.number, e]));
  state.version = model.version;
  if (state.pressing) state.owed = true;
  else render();
  if (!state.painted) {
    state.painted = true;
    if (history.state?.spot) refocus(history.state.spot);
  }
  if (model.words_at !== state.wordsAt) {
    state.wordsAt = model.words_at;
    fetch('/api/words', { cache: 'no-store' }).then((res) => res.json()).then((words) => { state.words = words; });
  }
}
function reloadWhenIdle() {
  const typing = document.activeElement?.matches('input, textarea') && document.activeElement.value;
  if (typing || state.pressing || state.asking || (pending.number && !pending.error)) setTimeout(reloadWhenIdle, 2000);
  else location.reload();
}
document.addEventListener('pointerdown', () => { state.pressing = true; }, true);
for (const type of ['pointerup', 'pointercancel']) {
  document.addEventListener(type, () => {
    state.pressing = false;
    if (state.owed) { state.owed = false; setTimeout(render); }
  }, true);
}
function connect() {
  const es = new EventSource('/api/stream');
  es.addEventListener('build', (e) => { if (e.data !== $('meta[name="build"]').content) reloadWhenIdle(); });
  es.addEventListener('version', (e) => {
    state.wanted = Number(e.data);
    if (state.wanted !== state.version) load();
  });
  es.onerror = () => { es.close(); setTimeout(connect, 3000); };
}
document.addEventListener('toggle', (e) => {
  const id = e.target.dataset?.id;
  if (!id) return;
  if (e.target.open) state.open.add(id);
  else state.open.delete(id);
}, true);
document.addEventListener('click', (e) => {
  const toggle = e.target.closest('[data-toggle]');
  if (toggle && !e.target.closest('a')) {
    const id = toggle.dataset.toggle;
    state.open.has(id) ? state.open.delete(id) : state.open.add(id);
    render();
  }
  const suggestion = e.target.closest('[data-ask]');
  if (suggestion) ask(suggestion.dataset.ask);
});
document.addEventListener('submit', (e) => {
  if (e.target.id !== 'ask') return;
  e.preventDefault();
  ask(e.target.q.value);
});
window.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || !e.target.matches?.('#main input, #main textarea') || e.target.closest('#pal')) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  if (!e.target.value) return e.target.blur();
  e.target.value = '';
  e.target.dispatchEvent(new Event('input', { bubbles: true }));
}, true);
document.addEventListener('keydown', (e) => {
  const depth = stackOf().length;
  const key = e.key.toLowerCase();
  if (e.key === 'Escape' && ov.embedCard) { ov.embedCard = null; render(); }
  else if (e.key === 'Escape' && depth) popTo(depth - 1);
  else if (e.key === 'Tab' && depth) {
    const stops = [...$('#main .pr-drawer.front').querySelectorAll(FOCUSABLE)].filter((el) => el.checkVisibility());
    const at = stops.indexOf(document.activeElement);
    const wrap = at < 0 || (e.shiftKey ? at === 0 : at === stops.length - 1);
    if (wrap) { e.preventDefault(); (e.shiftKey ? stops.at(-1) : stops[0]).focus(); }
  }
  else if (depth && (key === 'j' || key === 'k') && !e.metaKey && !e.ctrlKey && !e.altKey && !e.target.closest('input, textarea')) {
    e.preventDefault();
    $(`#main .pr-drawer.front [data-key="${key}"]`)?.click();
  }
  if (e.key === 'Enter' && !e.shiftKey && e.target.matches('#ask textarea')) { e.preventDefault(); ask(e.target.value); }
});
document.addEventListener('click', (e) => {
  const step = !e.target.closest('a') && e.target.closest('.st-table tr[data-step]');
  if (step) return step.querySelector('a.st-step').click();
}, true);
window.addEventListener('hashchange', (e) => {
  const from = new URL(e.oldURL).hash;
  const before = baseOf(from === '#/jump' ? state.lastHash ?? '' : from);
  if (!history.state || drawerSeg(location.hash)) arrive(from);
  const deeper = before === baseOf() && stackOf().length > stackOf(from === '#/jump' ? state.lastHash ?? '' : from).length;
  remember();
  render();
  if (before !== baseOf() && !/^#\/review\/q(\/|$)/.test(location.hash)) window.scrollTo(0, history.state?.y ?? 0);
  if (history.state?.spot && !deeper) refocus(history.state.spot);
  else if (stackOf().length) $('#main .pr-drawer.front .pr-x')?.focus({ preventScroll: true });
  else if (history.state?.y !== undefined) focusInView();
});
document.addEventListener('click', (e) => {
  const control = e.target.closest(FOCUSABLE);
  if (control) history.replaceState({ ...history.state, y: window.scrollY, spot: focusSpot($('#main'), control) }, '');
}, true);
function focusInView() {
  for (const el of $('#main').querySelectorAll(FOCUSABLE)) {
    const box = el.getBoundingClientRect();
    if (box.height === 0 || box.top < 0) continue;
    el.focus({ preventScroll: true });
    if (document.activeElement === el) return;
  }
}
window.addEventListener('DOMContentLoaded', () => {
  if (!history.state || drawerSeg(location.hash)) arrive('');
  remember();
  setInterval(tickFresh, 1000);
  load();
  connect();
});
