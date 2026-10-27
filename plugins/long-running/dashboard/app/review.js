const VERDICT_TONE = { you: 'bad', approved: 'ok', held: 'warn' };
const VERDICT_WORD = { you: 'Waiting on you', approved: 'Approved', held: 'Held' };
const embed = { number: null, head: null, row: false };
const pending = { number: null, holding: false, error: '', note: '' };

const REVIEW_LISTS = [['', 'Needs you'], ['flight', 'In flight'], ['landed', 'Landed']];
function reviewList(list = '') {
  const prs = Object.entries(state.model.prs).map(([n, pr]) => ({ n: Number(n), ...pr }));
  const flight = prs.filter((pr) => pr.state === 'open' && !boardRow(pr.n));
  const landed = prs.filter((pr) => pr.state === 'landed').sort((a, b) => String(b.landed_at).localeCompare(String(a.landed_at)));
  const counts = { '': state.model.board.rows.filter((r) => verdictState(r) === 'you').length, flight: flight.length, landed: landed.length };
  const chips = `<div class="filters review-lists">${REVIEW_LISTS.map(([id, label]) => `<a href="#/review${id ? `/${id}` : ''}" class="${id === list ? 'on' : ''}">${label} <b>${counts[id]}</b></a>`).join('')}</div>`;
  if (list === 'landed') return `${chips}<div class="rows">${landed.map((pr) => {
    const go = approvedAt(pr.n);
    return `<a class="row approved" href="#/review/${pr.n}"><span class="title">${esc(plainTitle(pr.n))}</span>
      <span class="chips"><span class="pill ok">Landed ${clock(pr.landed_at)}</span>${go ? `<span class="pill">Approved ${clock(go)}</span>` : ''}<span class="pill">#${pr.n}</span></span></a>`;
  }).join('') || '<p class="dim">Nothing from this drive has landed yet.</p>'}</div>`;
  if (list === 'flight') return `${chips}<div class="rows">${flight.map((pr) => {
    const s = prState(pr.n);
    return `<a class="row" href="#/review/${pr.n}"><span class="title">${esc(plainTitle(pr.n))}</span>
      <span class="chips"><span class="pill ${STATE_TONE[s]}">${STATE_WORD[s]}</span>${testPill(pr)}<span class="pill">#${pr.n}</span></span></a>`;
  }).join('') || '<p class="dim">No other pull request is open.</p>'}</div>`;
  const groups = new Map();
  for (const row of state.model.board.rows) {
    if (!groups.has(row.group)) groups.set(row.group, []);
    groups.get(row.group).push(row);
  }
  const order = { you: 0, held: 1, approved: 2 };
  const html = [...groups.entries()].map(([group, rows]) => `<div class="group-h" data-assist-stack="${esc(group)}">${esc(group)}</div><div class="rows">${rows
    .sort((a, b) => order[verdictState(a)] - order[verdictState(b)])
    .map((r) => {
      const v = verdictState(r);
      return `<a class="row ${v}" href="#/review/${r.number}"><span class="title">${esc(r.title)}</span>
        <span class="chips"><span class="pill ${VERDICT_TONE[v]}">${VERDICT_WORD[v]}</span>${testPill(state.model.prs[r.number] ?? {})}<span class="pill">#${r.number}</span></span></a>`;
    }).join('')}</div>`).join('');
  return `${chips}${html || '<p class="dim">Nothing is waiting for you.</p>'}`;
}
function approvedAt(n) {
  return state.model.records.findLast((r) => r.kind === 'go' && r.refs?.prs?.includes(n))?.at;
}
function testPill(pr) {
  if (pr.test_state === 'success') return '<span class="pill ok">Tests pass</span>';
  if (pr.test_state === 'failure') return '<span class="pill bad">Tests failing</span>';
  if (pr.test_state === 'pending') return '<span class="pill warn">Tests running</span>';
  return '';
}
function reviewPR(n, tab) {
  if (n === 'landed' || n === 'flight') return reviewList(n);
  const row = boardRow(n);
  const gh = `https://github.com/${state.model.repo}/pull/${n}`;
  if (!row) {
    if (!state.model.prs[n]) return `<p>#${n} is not part of this drive. <a href="${gh}" target="_blank" rel="noopener">Open it on GitHub</a>.</p>`;
    const s = prState(n);
    const pr = state.model.prs[n];
    const go = approvedAt(Number(n));
    const when = s === 'landed' ? `Landed ${clock(pr.landed_at)}` : STATE_WORD[s];
    return `<div class="pr-head"><div class="kick muted">${s === 'landed' ? 'Landed' : 'Nothing for you to tap'}</div><h1>${esc(plainTitle(n))}</h1>
      <span class="pill ${STATE_TONE[s]}">${when}</span>${go ? `<span class="pill ok">You approved ${clock(go)}</span>` : ''}<a class="pill" href="${gh}" target="_blank" rel="noopener">#${n}</a>${s === 'landed' || s === 'failing' ? '' : testPill(pr)}</div>`;
  }
  const v = verdictState(row);
  const rows = state.model.board.rows.filter((r) => r.group === row.group);
  const index = rows.indexOf(row);
  const next = state.model.board.rows.find((r) => r.number !== row.number && verdictState(r) === 'you');
  const step = (r, label, key) => (r ? `<a class="btn" href="#/review/${r.number}" title="${key}">${label}</a>` : '');
  return `<div class="pr-head" data-assist-pr="${n}"><div class="kick">${esc(row.group)} · ${index + 1} of ${rows.length}</div><h1>${esc(row.title)}</h1>
    <a class="pill" href="${gh}" target="_blank" rel="noopener">#${n} at ${esc(row.head.slice(0, 7))}</a>${testPill(state.model.prs[n] ?? {})}
    <span class="actions">${step(rows[index - 1], '‹', 'K')}${step(rows[index + 1], '›', 'J')}${next ? step(next, 'Next waiting on you ›', 'N') : ''}</span></div>
    <div class="decide">${decideBar(row, v)}</div><div class="bar-bottom decide">${decideBar(row, v)}</div>
    <div class="tiles">${factTiles(row).map(([key, label, short, tone, full]) => `<details class="tile ${tone}" data-id="fact-${n}-${key}"${state.open.has(`fact-${n}-${key}`) ? ' open' : ''}><summary><span class="tile-k">${label}</span><b>${esc(short)}</b></summary><p>${inline(full)}</p></details>`).join('')}</div>
    <nav class="subtabs review-tabs"><a href="#/review/${n}" class="${tab === 'notes' ? '' : 'on'}">Code and review</a><a href="#/review/${n}/notes" class="${tab === 'notes' ? 'on' : ''}">Full text and notes</a></nav>`;
}
function clip(text) {
  const first = String(text ?? '').split(/(?<=[.:;])\s|,\s/)[0].replace(/[.:;]$/, '');
  return first.length > 48 ? `${first.slice(0, first.lastIndexOf(' ', 46))}…` : first;
}
function factTiles(row) {
  const tests = state.model.prs[row.number]?.test_state;
  const tiles = [
    ['plan', 'Plan', /^matches/i.test(row.plan) ? 'Matches' : clip(row.plan), /^matches/i.test(row.plan) ? 'ok' : 'warn', row.plan],
    ['risk', 'Risk', clip(row.risk), /^low/i.test(row.risk) ? 'ok' : /^high/i.test(row.risk) ? 'bad' : 'warn', row.risk],
    ['review', 'Review and CI', tests === 'success' ? 'Tests pass' : tests === 'failure' ? 'Tests failing' : tests === 'pending' ? 'Tests running' : clip(row.review), tests === 'failure' || /fail|red/i.test(row.review) ? 'bad' : tests === 'pending' ? 'warn' : 'ok', row.review],
    ['blocker', 'Blocks landing', /^nothing/i.test(row.blocker) ? 'Nothing' : clip(row.blocker), /^nothing/i.test(row.blocker) ? 'ok' : 'bad', row.blocker],
  ];
  if (row.carried?.length) tiles.push(['carried', 'Earlier tap', `Carries from ${row.carried[0].slice(0, 7)}`, 'ok', `Your verdict at ${row.carried.map((h) => h.slice(0, 7)).join(', ')} still stands for this commit.`]);
  return tiles;
}
function decideBar(row, v) {
  const head = esc(row.head.slice(0, 7));
  const busy = pending.number === row.number && !pending.holding && !pending.error;
  if (pending.number === row.number && pending.holding) {
    return `<form class="hold-form" data-hold="${row.number}"><input name="note" placeholder="Why hold? (optional)" value="${esc(pending.note)}"><button class="btn hold">Hold at ${head}</button><button type="button" class="btn" data-decide="cancel">Cancel</button></form>`;
  }
  if (busy) return `<span class="decided sending">${DECIDE_WORD[pending.verdict]} ${head}…</span>`;
  const error = pending.number === row.number && pending.error ? `<span class="pill bad">Not recorded: ${esc(pending.error)}</span><button class="btn" data-decide="${pending.verdict}">Retry</button>` : '';
  if (v === 'approved' || v === 'held') {
    return `<span class="decided ${v}">${v === 'approved' ? '✓ You approved' : '‖ You held'} ${head}${row.verdict.at ? ` at ${clock(row.verdict.at)}` : ''}${row.verdict.note ? `: ${esc(row.verdict.note)}` : ''}</span><button class="btn" data-decide="cleared">Undo</button>${error}`;
  }
  return `<button class="btn pri" data-decide="approved" title="A">✓ Approve ${head}</button><button class="btn hold" data-decide="hold" title="H">‖ Hold</button>${error}`;
}
const DECIDE_WORD = { approved: 'Approving', rejected: 'Holding', cleared: 'Undoing' };
async function decide(row, verdict, note, advance = true) {
  if (pending.number === row.number && !pending.holding && !pending.error) return;
  clearTimeout(pending.advance);
  Object.assign(pending, { number: row.number, holding: false, error: '', note: '', verdict, retryNote: note });
  render();
  const error = await fetch('/api/verdict', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ block: row.block, number: row.number, verdict, head: row.head, ...(note ? { note } : {}) }) })
    .then((res) => res.json())
    .then((body) => body.error, (failure) => `could not reach the dashboard server: ${failure.message}`);
  if (error) {
    pending.error = error;
    return render();
  }
  row.verdict = verdict === 'cleared' ? null : { verdict, head: row.head, note, at: new Date().toISOString() };
  row.state = verdict === 'cleared' ? 'you' : verdict === 'approved' ? 'approved' : 'held';
  pending.number = null;
  const next = verdict === 'cleared' || !advance ? null : state.model.board.rows.find((r) => r.number !== row.number && verdictState(r) === 'you');
  const from = location.hash;
  if (next) pending.advance = setTimeout(() => { if (location.hash === from) location.hash = `#/review/${next.number}`; }, 700);
  render();
}
function onDecide(e) {
  const button = e.target.closest('[data-decide]');
  const r = route();
  const row = r.screen === 'review' && r.id ? boardRow(r.id) : null;
  if (!button || !row) return;
  const verdict = button.dataset.decide;
  if (verdict === 'hold' || verdict === 'cancel') {
    Object.assign(pending, { number: verdict === 'hold' ? row.number : null, holding: verdict === 'hold', error: '' });
    return render();
  }
  decide(row, verdict, verdict === 'rejected' ? pending.retryNote : undefined);
}
document.addEventListener('click', onDecide);
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
  else if (key === 'f') location.hash = `#/review/${row.number}${r.tab === 'notes' ? '' : '/notes'}`;
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
  const number = r.screen === 'review' && r.id ? Number(r.id) : null;
  const row = number && boardRow(number);
  const head = row ? row.head : state.model.prs[number]?.head ?? '';
  el.hidden = !row && !state.model.prs[number];
  if (el.hidden) {
    embed.number = null;
    el.replaceChildren();
    return;
  }
  el.dataset.tab = row && r.tab === 'notes' ? 'notes' : 'code';
  if (embed.number !== number || embed.row !== Boolean(row)) el.innerHTML = '<div class="pane code"></div>';
  if (el.dataset.tab === 'notes' && !el.querySelector('.pane.verdict')) {
    const theme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    const verdict = `${state.model.board.url}?block=${encodeURIComponent(row.block)}&row=${number}&theme=${theme}`;
    el.insertAdjacentHTML('afterbegin', `<div class="pane verdict"><iframe title="Verdict on #${number}" src="${esc(verdict)}"></iframe></div>`);
  }
  if (embed.number === number && embed.head === head && embed.row === Boolean(row)) return;
  Object.assign(embed, { number, head, row: Boolean(row) });
  const code = el.querySelector('.pane.code');
  code.innerHTML = `<p class="dim pane-wait">Opening the review of #${number}…</p>`;
  fetch(`/api/review/${number}`, { cache: 'no-store' })
    .then((res) => res.json())
    .then(({ url, error }) => {
      if (embed.number !== number) return;
      code.innerHTML = url
        ? `<iframe title="Code and review of #${number}" src="${esc(url)}" allow="clipboard-write"></iframe>`
        : `<p class="pane-wait bad">cc-review could not open #${number}: ${esc(error)}</p>`;
    });
}
window.addEventListener('message', (e) => {
  if (e.data?.type !== 'cc-present.height') return;
  const frame = $('#embed .verdict iframe');
  if (frame && e.source === frame.contentWindow) frame.style.height = `${e.data.px}px`;
});
setInterval(() => {
  const r = route();
  if (r.screen === 'review' && embed.number && !document.hidden) fetch(`/api/review/${embed.number}`, { cache: 'no-store' });
}, 5 * 60 * 1000);
