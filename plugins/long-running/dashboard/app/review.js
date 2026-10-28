const VERDICT_TONE = { you: 'bad', approved: 'ok', held: 'warn' };
const VERDICT_WORD = { you: 'Waiting on you', approved: 'Approved', held: 'Held' };
const embed = { number: null, head: null, row: false };
const pending = { number: null, holding: false, error: '', note: '' };

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
  const row = boardRow(n);
  const gh = `https://github.com/${state.model.repo}/pull/${n}`;
  if (!row) {
    if (!state.model.prs[n]) return `<p>#${n} is not part of this drive. <a href="${gh}" target="_blank" rel="noopener">Open it on GitHub</a>.</p>`;
    const s = prState(n);
    const pr = state.model.prs[n];
    const go = approvedAt(Number(n));
    const when = s === 'landed' ? `Landed ${clock(pr.landed_at)}` : STATE_WORD[s];
    return `<div class="pr-head"><div class="kick muted">${s === 'landed' ? 'Landed' : 'Nothing for you to tap'}</div><h1>${esc(plainTitle(n))}</h1>
      <span class="pill ${STATE_TONE[s]}">${when}</span>${go ? `<span class="pill ok">You approved ${clock(go)}</span>` : ''}<a class="pill" href="${gh}" target="_blank" rel="noopener">#${n}</a>${s === 'landed' || s === 'failing' ? '' : testPill(pr)}</div>
      ${reviewTabs(n, tab)}${tab === 'code' ? '' : `${prFacts(n)}${prText(n)}`}`;
  }
  const v = verdictState(row);
  const rows = state.model.board.rows.filter((r) => r.group === row.group);
  const index = rows.indexOf(row);
  const next = state.model.board.rows.find((r) => r.number !== row.number && verdictState(r) === 'you');
  const step = (r, label, key) => (r ? `<a class="btn" href="#/review/${r.number}" title="${key}">${label}</a>` : '');
  const group = state.model.needs.find((item) => item.kind === 'prs' && [...item.prs, ...item.others].some((r) => r.number === row.number));
  const w = rowWords(row);
  const waits = waitLine(row, w);
  const carried = row.carried?.length ? '<p class="mr-waits">Your earlier approval carries to this version; the review line names the fix.</p>' : '';
  return `<div class="pr-head" data-assist-pr="${n}"><div class="kick">${esc(rewritten('groups', group?.key)?.title ?? row.group)} · ${index + 1} of ${rows.length}</div><h1>${esc(w.title)}</h1>
    <a class="pill" href="${gh}" target="_blank" rel="noopener">Open on GitHub ›</a>${testPill(state.model.prs[n] ?? {})}
    <span class="actions">${step(rows[index - 1], '‹', 'K')}${step(rows[index + 1], '›', 'J')}${next ? step(next, 'Next waiting on you ›', 'N') : ''}</span></div>
    <div class="decide">${decideBar(row, v)}</div><div class="bar-bottom decide">${decideBar(row, v)}</div>
    ${reviewTabs(n, tab)}${tab === 'code' ? '' : `<div class="pr-facts">${waits}${carried}${rowFacts(row)}</div>${prText(n)}`}`;
}
function reviewTabs(n, tab) {
  return `<nav class="subtabs review-tabs"><a href="#/review/${n}" class="${tab === 'code' ? '' : 'on'}">What it does</a><a href="#/review/${n}/code" class="${tab === 'code' ? 'on' : ''}">Code and review</a></nav>`;
}
const described = {};
function prText(n) {
  const head = boardRow(n)?.head ?? state.model.prs[n]?.head ?? '';
  const key = `${n}@${head}`;
  if (!(key in described)) {
    described[key] = null;
    fetch(`/api/pr/${n}`, { cache: 'no-store' })
      .then((res) => res.json())
      .catch((failure) => ({ error: failure.message }))
      .then((out) => { described[key] = out; render(); });
  }
  const pr = described[key];
  if (!pr) return '<p class="dim pr-wait">Reading the description and the reviewer bot’s summary…</p>';
  if (pr.error) return `<p class="pr-wait">The description is unavailable right now${/rate limit/i.test(pr.error) ? ': GitHub is limiting requests' : ''}. <a href="https://github.com/${esc(state.model.repo)}/pull/${n}" target="_blank" rel="noopener">Open it on GitHub ↗</a></p>`;
  const bot = pr.eli5 ?? pr.summary;
  const body = (pr.body ?? '').replace(/<!--[\s\S]*?-->/g, '').trim();
  return `${bot ? `<section class="pr-read pr-eli5"><div class="kick muted">${pr.eli5 ? 'In plain words, from the reviewer bot' : 'The reviewer bot’s summary'}</div><div class="md">${markdown(bot)}</div></section>` : ''}
    <section class="pr-read"><div class="kick muted">The pull request’s description</div><div class="md">${body ? markdown(body) : '<p class="dim">Its author wrote no description.</p>'}</div></section>`;
}
function decideBar(row, v) {
  const busy = pending.number === row.number && !pending.holding && !pending.error;
  if (pending.number === row.number && pending.holding) {
    return `<form class="hold-form" data-hold="${row.number}"><input name="note" placeholder="Why hold? (optional)" value="${esc(pending.note)}"><button class="btn hold">Hold</button><button type="button" class="btn" data-decide="cancel">Cancel</button></form>`;
  }
  if (busy) return `<span class="decided sending">${DECIDE_WORD[pending.verdict]}…</span>`;
  const error = pending.number === row.number && pending.error ? `<span class="pill bad">Not recorded: ${esc(pending.error)}</span><button class="btn" data-decide="${pending.verdict}">Retry</button>` : '';
  if (v === 'approved' || v === 'held') {
    return `<span class="decided ${v}">${v === 'held' ? '‖ You held' : row.ci_fix_go ? '✓ CI fix, landing without your tap' : '✓ You approved'}${row.verdict?.at ? ` at ${clock(row.verdict.at)}` : ''}${row.verdict.note ? `: ${esc(row.verdict.note)}` : ''}</span><button class="btn" data-decide="cleared">Undo</button>${error}`;
  }
  return `<button class="btn pri" data-decide="approved" data-head="${esc(row.head)}" title="A">✓ Approve</button><button class="btn hold" data-decide="hold" title="H">‖ Hold</button>${error}`;
}
const DECIDE_WORD = { approved: 'Approving', rejected: 'Holding', cleared: 'Undoing' };
async function decide(row, verdict, note, advance = true) {
  if (pending.number === row.number && !pending.holding && !pending.error) return;
  clearTimeout(pending.advance);
  Object.assign(pending, { number: row.number, holding: false, error: '', note: '', verdict, retryNote: note });
  render();
  const out = await fetch('/api/verdict', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ block: row.block, number: row.number, verdict, head: row.head, ...(note ? { note } : {}) }) })
    .then((res) => res.json())
    .catch((failure) => ({ error: `could not reach the dashboard server: ${failure.message}` }));
  if (out.error) {
    pending.error = out.error;
    return render();
  }
  pending.number = null;
  const next = verdict === 'cleared' || !advance ? null : state.model.board.rows.find((r) => r.number !== row.number && verdictState(r) === 'you');
  const from = location.hash;
  if (next) pending.advance = setTimeout(() => { if (location.hash === from) location.hash = `#/review/${next.number}`; }, 700);
  tapped({ seq: out.seq, block: row.block, number: row.number, head: row.head, verdict: verdict === 'cleared' ? null : { verdict, head: row.head, note, at: new Date().toISOString() }, state: verdict === 'cleared' ? 'you' : verdict === 'approved' ? 'approved' : 'held' });
}
function onDecide(e) {
  const button = e.target.closest('[data-decide]');
  const r = route();
  const row = r.screen === 'review' && r.id ? boardRow(r.id) : null;
  if (!button || !row) return;
  if (button.dataset.head && button.dataset.head !== row.head) return render();
  const verdict = button.dataset.decide;
  if (verdict === 'hold' || verdict === 'cancel') {
    Object.assign(pending, { number: verdict === 'hold' ? row.number : null, holding: verdict === 'hold', error: '' });
    return render();
  }
  decide(row, verdict, verdict === 'rejected' ? pending.retryNote : undefined);
}
document.addEventListener('click', onDecide);
document.addEventListener('submit', (e) => {
  const asking = e.target.closest('[data-ask-pr]');
  if (asking) {
    e.preventDefault();
    const n = asking.dataset.askPr;
    const question = asking.q.value.trim();
    if (!question) return;
    const answer = asking.nextElementSibling;
    answer.innerHTML = '<p class="dim">Thinking…</p>';
    fetch('/api/asked', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: `${question} (on #${n})` }) });
    assistAsk(`About pull request #${n}, ${plainTitle(n)}: ${question}`, [], (text) => (answer.innerHTML = `<p>${inline(text).replace(/\n/g, '<br>')}</p>`))
      .catch((failure) => (answer.innerHTML = `<p class="bad">The answer stopped: ${esc(failure.message)}</p>`));
    return;
  }
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
  else if (key === 'f') location.hash = `#/review/${row.number}${r.tab === 'code' ? '' : '/code'}`;
  else return;
  e.preventDefault();
});
const STALE_LINE = '<p class="assist-line stale">Showing the last copy of this review: GitHub is refusing requests for now, so new pushes and comments show up once it answers again.</p>';
const ASSISTANT_LINE = `<p class="assist-line"><i class="on"></i><b>Assistant connected.</b> Ask in the bar under the code; it answers from<span class="pill" title="The files and lines this pull request changes">the diff</span><span class="pill" title="This pull request's row on the merge board: change, plan, risk, review, blocker">its board row</span><span class="pill" title="Your recorded rulings that match the question">your rulings</span><span class="pill" title="The drive's records that name this pull request">the drive's records</span></p>`;
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
  el.dataset.tab = r.tab === 'code' ? 'code' : 'text';
  if (embed.number !== number || embed.row !== Boolean(row)) el.innerHTML = '<div class="pane code"></div>';
  if (embed.number === number && embed.head === head && embed.row === Boolean(row)) return;
  Object.assign(embed, { number, head, row: Boolean(row) });
  const code = el.querySelector('.pane.code');
  code.innerHTML = '<p class="dim pane-wait">Opening the code and review…</p>';
  fetch(`/api/review/${number}`, { cache: 'no-store' })
    .then((res) => res.json())
    .then(({ url, assistant, stale, error }) => {
      if (embed.number !== number) return;
      code.innerHTML = url
        ? `${stale ? STALE_LINE : ''}${assistant ? ASSISTANT_LINE : ''}<iframe title="Code and review of #${number}" src="${esc(url)}" allow="clipboard-write"></iframe>`
        : `<div class="pane-wait"><b>The code view is unavailable right now${/rate limit/i.test(error) ? ': GitHub is limiting requests' : ''}.</b> What this pull request changes and where it stands are above. <a href="https://github.com/${esc(state.model.repo)}/pull/${number}" target="_blank" rel="noopener">Open it on GitHub ↗</a></div>
          <form class="assist-line pr-ask" data-ask-pr="${number}"><input name="q" placeholder="Ask about #${number}: it answers from its facts, your rulings and the drive's records"><button class="btn pri">Ask</button></form><div class="pr-answer"></div>`;
    });
}
setInterval(() => {
  const r = route();
  if (r.screen === 'review' && embed.number && !document.hidden) fetch(`/api/review/${embed.number}`, { cache: 'no-store' });
}, 5 * 60 * 1000);
