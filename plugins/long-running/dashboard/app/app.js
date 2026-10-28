const PACIFIC = 'America/Los_Angeles';
const LATE_MS = 3 * 60 * 1000;
const clockFmt = new Intl.DateTimeFormat('en-US', { timeZone: PACIFIC, hour: 'numeric', minute: '2-digit' });
const dayFmt = new Intl.DateTimeFormat('en-US', { timeZone: PACIFIC, weekday: 'short' });
const KIND_WORD = { owner: 'You', go: 'Approved to land', landed: 'Landed', release: 'Release', incident: 'Incident', mechanism: 'Cause found', 'fix-live': 'Fix live', opened: 'Opened', report: 'Report', hold: 'Held', lift: 'Hold lifted', blocker: 'Blocked', defect: 'Tool problem', correction: 'Correction', decide: 'Decision', ask: 'Question', answer: 'Answer' };

const state = { drive: new Map(), model: null, version: -1, open: new Set(), seen: new Set(), chat: [], asking: false, overlay: null, taps: [] };
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
function blockedFor(iso) {
  const m = minutesSince(iso);
  const span = m < 60 ? `${m} min` : m < 600 ? `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ''}` : `${Math.round(m / 60)} hours`;
  return `for ${span}, since ${clock(iso)}`;
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
function plainTitle(n) {
  const row = boardRow(n);
  if (row) return row.title;
  const title = state.model.prs[n]?.title ?? `Pull request ${n}`;
  return title.replace(/^[\w./-]+:\s*/, '').replace(/^[^\w#]+/, '').replace(/^./, (c) => c.toUpperCase());
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
  return `<a class="prc ${s}" href="${prHref(n)}"${external ? ' target="_blank" rel="noopener"' : ''} title="${esc(plainTitle(n))} · ${STATE_WORD[s]}">${s === 'landed' ? '✓' : s === 'closed' ? '' : `<i class="lt ${s}"></i>`}#${n}</a>`;
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

function route() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  return { screen: parts[0] ?? 'overview', id: parts[1], tab: parts[2] };
}
function render() {
  const m = state.model;
  if (!m || !m.prs) return;
  const r = route();
  if (state.overlay && !history.state?.sheet) history.pushState({ sheet: true }, '');
  document.title = m.title;
  $('.brand').textContent = m.title;
  const tabs = [['overview', 'Now'], ['plan', 'Plan'], ['status', 'Status'], ['work', 'Work'], ['review', 'Review', needsTotal()], ['history', 'History']];
  $('#kbtn').classList.toggle('on', r.screen === 'jump');
  $('#tabs').innerHTML = tabs.map(([id, label, count]) => `<a class="tab${r.screen === id || (id === 'overview' && r.screen === 'story') ? ' on' : ''}" href="#/${id === 'overview' ? '' : id}">${label}${count ? `<span class="count">${count}</span>` : ''}</a>`).join('');
  const screens = { overview: nowScreen, story: nowScreen, plan: () => planScreen(r.id), work: () => workScreen(r.id && decodeURIComponent(r.id)), review: () => (/^\d+$/.test(r.id ?? '') ? reviewPR(r.id, r.tab) : reviewScreen(r.id ?? '')), history: () => historyScreen(r.id), ask: askScreen, status: statusScreen, jump: () => `<div class="pg">${pageHead('Jump to anything')}<p class="pg-lead">Find a change, a part of the plan, a piece of work or a page, or ask the assistant a question.</p></div>`, worker: () => workerScreen(decodeURIComponent(r.id ?? '')) };
  const y = window.scrollY;
  const kept = [...document.querySelectorAll('[data-keep-scroll]')].map((el) => [el.dataset.keepScroll, el.scrollLeft, el.scrollTop]);
  const typing = document.activeElement?.matches('#main input, #main textarea') ? document.activeElement : null;
  const caret = typing && [typing.name, typing.dataset.draft ?? '', typing.selectionStart, typing.selectionEnd];
  const spot = !typing && state.shown === location.hash && focusSpot();
  $('#main').innerHTML = (screens[r.screen] ?? nowScreen)() + `<div class="foot">${m.errors && Object.keys(m.errors).length ? `Some sources failed: ${esc(Object.values(m.errors).join('; '))}. ` : ''}All times Pacific.</div>`;
  decorate(r);
  paintModal();
  if (state.shown !== location.hash) {
    state.shown = location.hash;
    $('#main').classList.add('enter');
    setTimeout(() => $('#main').classList.remove('enter'), 220);
  }
  const again = caret && [...$('#main').querySelectorAll(`[name="${caret[0]}"]`)].find((el) => (el.dataset.draft ?? '') === caret[1]);
  if (again) { again.focus(); again.setSelectionRange(caret[2], caret[3]); }
  if (spot) refocus(spot);
  window.scrollTo(0, y);
  for (const [id, left, top] of kept) { const el = document.querySelector(`[data-keep-scroll="${id}"]`); if (el) [el.scrollLeft, el.scrollTop] = [left, top]; }
  syncReviewEmbed(r);
  syncCardEmbed(r);
  if (r.screen === 'review' && r.id === 'q' && r.tab && state.focused !== location.hash) {
    state.focused = location.hash;
    document.getElementById(`card-${decodeURIComponent(r.tab)}`)?.scrollIntoView({ block: 'center' });
  }
  if ((screens[r.screen] ?? nowScreen) === nowScreen) mountNow();
  else if (r.screen === 'work' && r.id) follow(decodeURIComponent(r.id));
  else unfollow();
  tickFresh();
}
const FOCUSABLE = 'a[href], button, summary, input, select, textarea, [tabindex]';
function signature(el) {
  return [...el.attributes].filter((a) => a.name !== 'class').map((a) => `${a.name}=${a.value}`).join(' ');
}
function focusSpot() {
  const el = document.activeElement;
  if (!el || !$('#main').contains(el)) return null;
  const stops = [...$('#main').querySelectorAll(FOCUSABLE)];
  const sig = signature(el);
  return { sig, nth: stops.filter((x) => signature(x) === sig).indexOf(el), at: stops.indexOf(el) };
}
function refocus(spot) {
  const stops = [...$('#main').querySelectorAll(FOCUSABLE)];
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

async function load() {
  const res = await fetch('/api/model', { cache: 'no-store' });
  const model = await res.json();
  if (!model.prs) return setTimeout(load, 1000);
  if ((model.board.seq ?? 0) < (state.model?.board.seq ?? 0)) return;
  settle(model);
  unask(model);
  state.model = model;
  state.drive = new Map(model.drive.prs.map((e) => [e.number, e]));
  state.version = model.version;
  if (state.pressing) state.owed = true;
  else render();
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
  es.addEventListener('version', (e) => { if (Number(e.data) !== state.version) load(); });
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
  const close = e.target.closest('[data-close]');
  if (close && (e.target === close || close.matches('button'))) {
    if (state.overlay) closeSheet();
    return;
  }
  const suggestion = e.target.closest('[data-ask]');
  if (suggestion) ask(suggestion.dataset.ask);
});
document.addEventListener('submit', (e) => {
  if (e.target.id !== 'ask') return;
  e.preventDefault();
  ask(e.target.q.value);
});
function leave(to) {
  if (history.state?.inApp) history.back();
  else location.replace(to);
}
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && state.overlay) closeSheet();
  else if (e.key === 'Escape' && ov.embedCard) { ov.embedCard = null; render(); }
  else if (e.key === 'Escape' && route().screen === 'story') leave('#/');
  else if (e.key === 'Escape' && route().screen === 'work' && route().id) leave('#/work');
  if (e.key === 'Enter' && !e.shiftKey && e.target.matches('#ask textarea')) { e.preventDefault(); ask(e.target.value); }
});
function paintModal() {
  const root = document.getElementById('modal-root') ?? document.body.appendChild(Object.assign(document.createElement('div'), { id: 'modal-root' }));
  const [, kind, at] = /^([ms])(\d+)$/.exec(state.overlay ?? '') ?? [];
  const p = kind === 'm' && curated('plan');
  const step = kind === 's' && statusRows().rows[at];
  const held = root.contains(document.activeElement) ? document.activeElement.getAttribute('href') : null;
  const sheet = (label, body) => `<div class="overlay" data-close="1"><div class="sheet ms-sheet" role="dialog" aria-modal="true" aria-label="${esc(label)}"><button class="btn close" data-close="1">Close <kbd>Esc</kbd></button>${body}</div></div>`;
  const html = p ? sheet(state.model.milestones[at] ?? 'Milestone', `${milestonePage(p, Number(at))}<p class="acts ms-out"><a class="btn" data-out href="#/plan/m${at}">Open as its own page ›</a></p>`)
    : step ? sheet(step[0], statusStep(step)) : '';
  if (state.modalHtml !== html) {
    const opening = !state.modalHtml;
    root.innerHTML = html;
    state.modalHtml = html;
    if (opening && html) root.firstElementChild.classList.add('enter');
  }
  document.body.classList.toggle('modal-open', Boolean(html));
  if (held) root.querySelector(`a[href="${CSS.escape(held)}"]`)?.focus();
}
document.addEventListener('click', (e) => {
  const step = !e.target.closest('a') && e.target.closest('.st-table tr[data-step]');
  if (step) return step.querySelector('a.st-step').click();
}, true);
document.addEventListener('click', (e) => {
  const link = e.target.closest('a[href^="#/plan/m"], a[href^="#/status/s"]');
  if (!link || e.metaKey || e.ctrlKey || e.shiftKey) return;
  if (link.hasAttribute('data-out')) { state.overlay = null; return; }
  const ms = /^#\/(?:plan|status)\/([ms]\d+)$/.exec(link.getAttribute('href'));
  if (!ms || (link.closest('.pg-page') && !link.closest('#modal-root'))) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  if (!state.overlay) state.opener = link;
  state.overlay = ms[1];
  render();
  document.querySelector('#modal-root .close')?.focus();
}, true);
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Tab' || !state.overlay) return;
  const stops = [...document.querySelectorAll('#modal-root a[href], #modal-root button')];
  const at = stops.indexOf(document.activeElement);
  if (at === -1 || (e.shiftKey && at === 0) || (!e.shiftKey && at === stops.length - 1)) {
    e.preventDefault();
    stops.at(e.shiftKey ? -1 : 0)?.focus();
  }
});
function closeSheet() {
  if (history.state?.sheet) return history.back();
  state.overlay = null;
  render();
}
window.addEventListener('popstate', (e) => {
  if (e.state?.sheet || !state.overlay) return;
  state.overlay = null;
  render();
  const href = state.opener?.getAttribute('href');
  (state.opener?.isConnected ? state.opener : href && document.querySelector(`#main a[href="${CSS.escape(href)}"]`))?.focus();
});
window.addEventListener('hashchange', () => {
  if (!history.state) history.replaceState({ inApp: true }, '');
  state.overlay = null;
  render();
  window.scrollTo(0, history.state?.y ?? 0);
});
function keepScroll() {
  history.replaceState({ ...history.state, y: window.scrollY }, '');
}
document.addEventListener('click', (e) => {
  if (e.target.closest('a[href^="#"]') && !e.defaultPrevented) keepScroll();
}, true);
window.addEventListener('DOMContentLoaded', () => {
  setInterval(tickFresh, 1000);
  load();
  connect();
});

