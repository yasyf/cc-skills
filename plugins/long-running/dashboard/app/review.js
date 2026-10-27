const VERDICT_TONE = { you: 'bad', approved: 'ok', held: 'warn' };
const VERDICT_WORD = { you: 'Waiting on you', approved: 'Approved', held: 'Held' };
const embed = { number: null, head: null };
const pending = { number: null, holding: false, error: '', note: '' };

function reviewList() {
  const groups = new Map();
  for (const row of state.model.board.rows) {
    if (!groups.has(row.group)) groups.set(row.group, []);
    groups.get(row.group).push(row);
  }
  const order = { you: 0, held: 1, approved: 2 };
  const html = [...groups.entries()].map(([group, rows]) => `<div class="group-h">${esc(group)}</div><div class="rows">${rows
    .sort((a, b) => order[verdictState(a)] - order[verdictState(b)])
    .map((r) => {
      const v = verdictState(r);
      return `<a class="row ${v}" href="#/review/${r.number}"><span class="title">${esc(r.title)}</span>
        <span class="chips"><span class="pill ${VERDICT_TONE[v]}">${VERDICT_WORD[v]}</span>${testPill(state.model.prs[r.number] ?? {})}<span class="pill">#${r.number}</span></span></a>`;
    }).join('')}</div>`).join('');
  return `<h2 class="sec">Pull requests for your review <span class="pill bad">${state.model.board.rows.filter((r) => verdictState(r) === 'you').length} waiting on you</span></h2>${html || '<p class="dim">Nothing is waiting for review.</p>'}`;
}
function testPill(pr) {
  if (pr.test_state === 'success') return '<span class="pill ok">Tests pass</span>';
  if (pr.test_state === 'failure') return '<span class="pill bad">Tests failing</span>';
  if (pr.test_state === 'pending') return '<span class="pill warn">Tests running</span>';
  return '';
}
function reviewPR(n, tab) {
  const row = boardRow(n);
  if (!row) return `<p>#${n} is not on the merge board. <a href="https://github.com/${state.model.repo}/pull/${n}" target="_blank" rel="noopener">Open it on GitHub</a>.</p>`;
  const v = verdictState(row);
  const rows = state.model.board.rows.filter((r) => r.group === row.group);
  const index = rows.indexOf(row);
  const next = state.model.board.rows.find((r) => r.number !== row.number && verdictState(r) === 'you');
  const step = (r, label, key) => (r ? `<a class="btn" href="#/review/${r.number}" title="${key}">${label}</a>` : '');
  return `<div class="pr-head"><div class="kick">${esc(row.group)} · ${index + 1} of ${rows.length}</div><h1>${esc(row.title)}</h1>
    <a class="pill" href="https://github.com/${state.model.repo}/pull/${n}" target="_blank" rel="noopener">#${n} at ${esc(row.head.slice(0, 7))}</a>${testPill(state.model.prs[n] ?? {})}
    <span class="actions">${step(rows[index - 1], '‹', 'K')}${step(rows[index + 1], '›', 'J')}${next ? step(next, 'Next waiting on you ›', 'N') : ''}</span></div>
    <div class="decide">${decideBar(row, v)}</div><div class="bar-bottom decide">${decideBar(row, v)}</div>
    <nav class="subtabs review-tabs"><a href="#/review/${n}" class="${tab === 'code' ? '' : 'on'}">Verdict</a><a href="#/review/${n}/code" class="${tab === 'code' ? 'on' : ''}">Code and review</a></nav>`;
}
function decideBar(row, v) {
  const head = esc(row.head.slice(0, 7));
  const busy = pending.number === row.number && !pending.holding && !pending.error;
  if (pending.number === row.number && pending.holding) {
    return `<form class="hold-form" data-hold="${row.number}"><input name="note" placeholder="Why hold? (optional)" value="${esc(pending.note)}"><button class="btn hold">Hold at ${head}</button><button type="button" class="btn" data-decide="cancel">Cancel</button></form>`;
  }
  const error = pending.number === row.number && pending.error ? `<span class="pill bad">${esc(pending.error)}</span>` : '';
  if (v === 'approved' || v === 'held') {
    return `<span class="decided ${v}">${v === 'approved' ? '✓ You approved' : '‖ You held'} ${head}${row.verdict.note ? `: ${esc(row.verdict.note)}` : ''}</span><button class="btn" data-decide="cleared" ${busy ? 'disabled' : ''}>Undo</button>${error}`;
  }
  return `<button class="btn pri" data-decide="approved" ${busy ? 'disabled' : ''} title="A">✓ Approve ${head}</button><button class="btn hold" data-decide="hold" ${busy ? 'disabled' : ''} title="H">‖ Hold</button>${error}`;
}
async function decide(row, verdict, note) {
  Object.assign(pending, { number: row.number, holding: false, error: '', note: '' });
  render();
  const res = await fetch('/api/verdict', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ block: row.block, number: row.number, verdict, head: row.head, ...(note ? { note } : {}) }) });
  const { error } = await res.json();
  if (error) {
    pending.error = error;
    return render();
  }
  row.verdict = verdict === 'cleared' ? null : { verdict, head: row.head, note };
  pending.number = null;
  const next = verdict === 'cleared' ? null : state.model.board.rows.find((r) => r.number !== row.number && verdictState(r) === 'you');
  if (next) setTimeout(() => { location.hash = `#/review/${next.number}`; }, 700);
  render();
}
document.addEventListener('click', (e) => {
  const button = e.target.closest('[data-decide]');
  const r = route();
  const row = r.screen === 'review' && r.id ? boardRow(r.id) : null;
  if (!button || !row) return;
  const verdict = button.dataset.decide;
  if (verdict === 'hold' || verdict === 'cancel') {
    Object.assign(pending, { number: verdict === 'hold' ? row.number : null, holding: verdict === 'hold', error: '' });
    return render();
  }
  decide(row, verdict);
});
document.addEventListener('submit', (e) => {
  const form = e.target.closest('[data-hold]');
  if (!form) return;
  e.preventDefault();
  decide(boardRow(form.dataset.hold), 'rejected', form.note.value.trim());
});
document.addEventListener('input', (e) => {
  if (e.target.closest('[data-hold]')) pending.note = e.target.value;
});
document.addEventListener('keydown', (e) => {
  const r = route();
  const row = r.screen === 'review' && r.id ? boardRow(r.id) : null;
  if (!row || e.metaKey || e.ctrlKey || e.altKey || e.target.closest('input, textarea')) return;
  const key = e.key.toLowerCase();
  const rows = state.model.board.rows.filter((x) => x.group === row.group);
  const index = rows.indexOf(row);
  const go = (to) => to && (location.hash = `#/review/${to.number}`);
  if (key === 'a' && verdictState(row) === 'you') decide(row, 'approved');
  else if (key === 'h' && verdictState(row) === 'you') { Object.assign(pending, { number: row.number, holding: true, error: '' }); render(); }
  else if (key === 'j') go(rows[index + 1]);
  else if (key === 'k') go(rows[index - 1]);
  else if (key === 'n') go(state.model.board.rows.find((x) => x.number !== row.number && verdictState(x) === 'you'));
  else if (key === 'c') location.hash = `#/review/${row.number}${r.tab === 'code' ? '' : '/code'}`;
  else return;
  e.preventDefault();
});
function syncReviewEmbed(r) {
  const note = [...document.querySelectorAll('.hold-form input')].find((input) => input.offsetParent);
  if (note) {
    note.focus();
    note.setSelectionRange(note.value.length, note.value.length);
  }
  const el = $('#embed');
  const row = r.screen === 'review' && r.id ? boardRow(r.id) : null;
  el.hidden = !row;
  if (!row) {
    embed.number = null;
    el.replaceChildren();
    return;
  }
  el.dataset.tab = r.tab === 'code' ? 'code' : 'verdict';
  if (embed.number === row.number && embed.head === row.head) return;
  const theme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  const verdict = `${state.model.board.url}?block=${encodeURIComponent(row.block)}&row=${row.number}&theme=${theme}`;
  if (embed.number !== row.number) {
    el.innerHTML = `<div class="pane verdict"><iframe title="Verdict on #${row.number}" src="${esc(verdict)}"></iframe></div><div class="pane code"></div>`;
  }
  embed.number = row.number;
  embed.head = row.head;
  const code = el.querySelector('.pane.code');
  code.innerHTML = `<p class="dim pane-wait">Opening the review of #${row.number} at ${esc(row.head.slice(0, 7))}…</p>`;
  fetch(`/api/review/${row.number}`, { cache: 'no-store' })
    .then((res) => res.json())
    .then(({ url, error }) => {
      if (embed.number !== row.number) return;
      code.innerHTML = url
        ? `<iframe title="Code and review of #${row.number}" src="${esc(url)}" allow="clipboard-write"></iframe>`
        : `<p class="pane-wait bad">cc-review could not open #${row.number}: ${esc(error)}</p>`;
    });
}
window.addEventListener('message', (e) => {
  if (e.data?.type !== 'cc-present.height') return;
  const frame = $('#embed .verdict iframe');
  if (frame && e.source === frame.contentWindow) frame.style.height = `${e.data.px}px`;
});
