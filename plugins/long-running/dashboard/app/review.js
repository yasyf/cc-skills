const VERDICT_TONE = { you: 'bad', approved: 'ok', held: 'warn' };
const VERDICT_WORD = { you: 'Waiting on you', approved: 'Approved', held: 'Held' };
const embed = { number: null, head: null, row: false, started: null };
const pending = { number: null, holding: false, error: '', note: '' };

function approvedAt(n) {
  return state.model.records.findLast((r) => r.kind === 'go' && r.refs?.prs?.includes(n))?.at;
}
const REVIEW_WORD = { APPROVED: 'The reviewer bot approved it', CHANGES_REQUESTED: 'The reviewer bot asked for changes', DISMISSED: 'The reviewer bot’s review was dismissed' };
const TEST_WORD = { success: 'Pass', failure: 'Failing', pending: 'Running' };
const TEST_TONE = { success: 'ok', failure: 'bad', pending: 'acc' };
function testLight(n) {
  const t = state.model.prs[n]?.test_state;
  return dotted(TEST_WORD[t] ?? 'Not known', TEST_TONE[t]);
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
  const open = `<a class="pill" href="${gh}" target="_blank" rel="noopener" data-key="o">Open on GitHub ›<kbd>O</kbd></a><a class="pill" href="https://app.graphite.com/github/pr/${state.model.repo}/${n}" target="_blank" rel="noopener">Open in Graphite ›</a>`;
  if (!row) {
    if (!state.model.prs[n]) return `<p>#${n} is not part of this drive. <a href="${gh}" target="_blank" rel="noopener">Open it on GitHub</a>.</p>`;
    const s = prState(n);
    const pr = state.model.prs[n];
    const go = approvedAt(Number(n));
    const when = s === 'landed' ? `Landed ${clock(pr.landed_at)}` : STATE_WORD[s];
    return `<div class="pr-head"><div class="kick muted">${s === 'landed' ? 'Landed' : 'Nothing for you to tap'}</div><h1>${esc(prTitle(n))}</h1>
      <span class="pill ${STATE_TONE[s]}">${when}</span>${go ? `<span class="pill ok">Reviewed and checked ${clock(go)}</span>` : ''}${open}${s === 'landed' || s === 'failing' ? '' : testPill(pr)}</div>
      ${s === 'landed' ? '' : changeBox(n, pr.head)}${reviewTabs(n, tab)}${tab === 'code' ? '' : `${prFacts(n)}${prText(n)}`}`;
  }
  const v = verdictState(row);
  const rows = state.model.board.rows.filter((r) => r.group === row.group);
  const index = rows.indexOf(row);
  const next = state.model.board.rows.find((r) => r.number !== row.number && verdictState(r) === 'you');
  for (const r of [rows[index - 1], rows[index + 1], next]) if (r) describe(r.number);
  const step = (r, label, key) => (r ? `<a class="btn" href="#/review/${r.number}" data-key="${key.toLowerCase()}">${label}<kbd>${key}</kbd></a>` : '');
  const group = state.model.needs.find((item) => item.kind === 'prs' && [...item.prs, ...item.others].some((r) => r.number === row.number));
  const w = rowWords(row);
  const carried = row.carried?.length ? '<p class="mr-waits">Your earlier approval carries to this version; the review line names the fix.</p>' : '';
  return `<div class="pr-head"><div class="kick">${esc(rewritten('groups', group?.key)?.title ?? row.group)}</div><h1>${esc(w.title)}</h1>
    ${open}${testPill(state.model.prs[n] ?? {})}
    <span class="actions">${step(rows[index - 1], '‹', 'K')}${step(rows[index + 1], '›', 'J')}${next ? step(next, 'Next waiting on you ›', 'N') : ''}</span></div>
    <div class="decide">${decideBar(row, v)}</div><div class="bar-bottom decide">${decideBar(row, v)}</div>${changeBox(n, row.head)}
    ${reviewTabs(n, tab)}${tab === 'code' ? '' : `<div class="pr-facts">${carried}${rowFacts(row)}<dl class="mr-f"><dt>Tests</dt><dd>${testLight(n)}</dd>${truthFacts(n, false)}</dl></div>${prText(n)}`}`;
}
function reviewTabs(n, tab) {
  return `<nav class="subtabs review-tabs"><a href="#/review/${n}" class="${tab === 'code' ? '' : 'on'}">What it does</a><a href="#/review/${n}/code" class="${tab === 'code' ? 'on' : ''}">Code and review<kbd>C</kbd></a></nav>`;
}
const described = {};
function describedKey(n) {
  return `${n}@${boardRow(n)?.head ?? state.model.prs[n]?.head ?? ''}`;
}
function describe(n) {
  const key = describedKey(n);
  if (!(key in described)) {
    described[key] = null;
    fetch(`/api/pr/${n}`, { cache: 'no-store' })
      .then((res) => res.json())
      .catch((failure) => ({ error: failure.message }))
      .then((out) => { described[key] = out; render(); });
  }
  return described[key];
}
function prBody(pr) {
  return (pr.body ?? '').replace(/<!--[\s\S]*?-->/g, '').trim();
}
function prText(n) {
  const pr = describe(n);
  if (!pr) return '<p class="dim pr-wait">Reading the description and what the reviewer bot says it does…</p>';
  if (pr.error) return `<p class="pr-wait">The description is unavailable right now${/rate limit/i.test(pr.error) ? ': GitHub is limiting requests' : ''}. <a href="https://github.com/${esc(state.model.repo)}/pull/${n}" target="_blank" rel="noopener">Open it on GitHub ↗</a></p>`;
  const reviewed = pr.review && `<p class="pr-reviewed">${REVIEW_WORD[pr.review.state] ?? 'The reviewer bot reviewed it'} at ${clock(pr.review.at)}.</p>`;
  const said = pr.eli5 ?? pr.summary;
  const bot = said || pr.diagram
    ? `<section class="pr-read pr-eli5"><div class="kick muted">What it does, from the reviewer bot</div>${reviewed || ''}${said ? `<div class="md">${markdown(said)}</div>` : ''}${pr.diagram ? diagramFigure(pr.diagram) : ''}</section>`
    : `<section class="pr-read pr-eli5"><div class="kick muted">What it does, from the reviewer bot</div>${reviewed || ''}<p class="dim">The reviewer bot wrote no plain-words explanation of this change, so its author's description below says what it does.</p></section>`;
  const body = prBody(pr);
  return `${bot}
    <section class="pr-read"><div class="kick muted">The pull request’s description</div><div class="md">${body ? markdown(body) : '<p class="dim">Its author wrote no description.</p>'}</div></section>`;
}
const diagrams = new Map();
let mermaidLoad;
function diagramFigure(diagram) {
  if (!diagrams.has(diagram.source)) {
    diagrams.set(diagram.source, null);
    mermaidLoad ??= import('https://cdn.jsdelivr.net/npm/mermaid@12.2.0/dist/mermaid.esm.min.mjs').then(({ default: mermaid }) => {
      mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', suppressErrorRendering: true, theme: 'base', fontFamily: getComputedStyle(document.body).getPropertyValue('--sans'), flowchart: { curve: 'basis', padding: 14, useMaxWidth: false }, themeVariables: { darkMode: true, background: '#0d1015', primaryColor: '#121a24', primaryTextColor: '#eceff3', primaryBorderColor: '#3ddc97', lineColor: '#6b7480', secondaryColor: '#151a21', tertiaryColor: '#11151b', edgeLabelBackground: '#0d1015', fontSize: '13px' } });
      return mermaid;
    });
    const id = `diagram-${diagrams.size}`;
    mermaidLoad
      .then((mermaid) => mermaid.render(id, diagram.source))
      .then(({ svg }) => svg, () => '')
      .then((drawn) => { diagrams.set(diagram.source, drawn); render(); });
  }
  return `<figure class="bot-diagram" data-keep-scroll="diagram"><figcaption>${esc(diagram.title)}</figcaption>${diagrams.get(diagram.source) ?? '<div class="dg-wait">Drawing the diagram…</div>'}</figure>`;
}
function prPeek(n) {
  const pr = described[describedKey(n)];
  if (!pr) return '<div class="mr-peek dim">Reading what it does…</div>';
  if (pr.error) return '<div class="mr-peek dim">What it does is unavailable right now.</div>';
  const body = prBody(pr);
  const said = pr.eli5 ?? pr.summary;
  const [kick, text] = said ? ['What it does, from the reviewer bot', said] : body ? ['From its description', /^#{1,4}\s*Summary\s*\n+([\s\S]*?)(?=^#{1,4}\s|(?![\s\S]))/m.exec(body)?.[1].trim() || body] : ['', ''];
  return text ? `<div class="mr-peek"><div class="kick muted">${kick}</div><div class="md">${markdown(text)}</div></div>` : '<div class="mr-peek dim">Its author wrote no description.</div>';
}
function decideBar(row, v) {
  const busy = pending.number === row.number && !pending.holding && !pending.error;
  if (pending.number === row.number && pending.holding) {
    return `<form class="hold-form" data-hold="${row.number}"><input name="note" placeholder="Why hold? (optional)" value="${esc(pending.note)}"><button class="btn hold">Hold</button><button type="button" class="btn" data-decide="cancel">Cancel</button></form>`;
  }
  if (busy) return `<span class="decided sending">${DECIDE_WORD[pending.verdict]}…</span>`;
  const error = pending.number === row.number && pending.error ? `<span class="pill bad">Not recorded: ${esc(pending.error)}</span><button class="btn" data-decide="${pending.verdict}">Retry</button>` : '';
  if (v === 'approved' || v === 'held') {
    return `<span class="decided ${v}">${v === 'held' ? '‖ You held' : row.ci_fix_go ? '✓ Landing under the CI-fix exemption' : '✓ You approved'}${row.verdict?.at ? ` at ${clock(row.verdict.at)}` : ''}${row.verdict?.note ? `: ${esc(row.verdict.note)}` : ''}</span><button class="btn" data-decide="cleared">Undo</button>${error}`;
  }
  return `<button class="btn pri" data-decide="approved" data-head="${esc(row.head)}">✓ Approve<kbd>A</kbd></button><button class="btn hold" data-decide="hold">‖ Hold<kbd>H</kbd></button>${error}`;
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
  if (next) pending.advance = setTimeout(() => { if (location.hash === from) swapDrawer(`#/review/${next.number}`); }, 300);
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
    assistAsk(`About pull request #${n}, ${prTitle(n)}: ${question}`, [], (text) => (answer.innerHTML = `<p>${inline(text).replace(/\n/g, '<br>')}</p>`))
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
  if (!prOpen(r) || e.metaKey || e.ctrlKey || e.altKey || e.target.closest('input, textarea')) return;
  const row = boardRow(r.id);
  const key = e.key.toLowerCase();
  const go = (to) => to && swapDrawer(`#/review/${to.number}`);
  if (key === 'c') swapDrawer(`#/review/${r.id}${r.tab === 'code' ? '' : '/code'}`);
  else if (key === 'o') $('#main .pr-drawer.front [data-key="o"]').click();
  else if (key === 'a' && row && verdictState(row) === 'you') decide(row, 'approved');
  else if (key === 'h' && row && verdictState(row) === 'you') { Object.assign(pending, { number: row.number, holding: true, error: '' }); render(); }
  else if (key === 'n' && row) go(state.model.board.rows.find((x) => x.number !== row.number && verdictState(x) === 'you'));
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
  if (embed.number !== number || embed.row !== Boolean(row)) {
    Object.assign(embed, { number, head: null, row: Boolean(row) });
    el.innerHTML = '<div class="pane code"></div>';
  }
  const code = el.querySelector('.pane.code');
  if (embed.started !== number) {
    if (!code.querySelector('[data-start-review]')) code.innerHTML = `<div class="pane-wait"><p>The code review opens its own review session for this pull request, so it starts only when you ask.</p><button class="btn pri" data-start-review="${number}">Start the code review</button></div>`;
    return;
  }
  if (embed.head === head) return;
  embed.head = head;
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
document.addEventListener('click', (e) => {
  const start = e.target.closest('[data-start-review]');
  if (!start) return;
  embed.started = Number(start.dataset.startReview);
  syncReviewEmbed(route());
});
setInterval(() => {
  const r = route();
  if (r.screen === 'review' && embed.number && embed.started === embed.number && !document.hidden) fetch(`/api/review/${embed.number}`, { cache: 'no-store' });
}, 5 * 60 * 1000);
const changeDrafts = {};
const sentChanges = [];
const changesSending = new Set();
const changesUnsent = {};
function changeAsks(n) {
  const asks = [...state.model.records, ...sentChanges].filter((r) => r.lane === 'owner-board' && r.kind === 'ask' && r.refs?.prs?.includes(Number(n)));
  return [...new Map(asks.map((r) => [r.seq, r])).values()].sort((a, b) => a.seq - b.seq);
}
function changeBox(n, head) {
  const thread = changeAsks(n).map((ask) => {
    const replies = state.model.records.filter((r) => r.seq > ask.seq && (r.re === ask.seq || r.text.includes(`#${ask.seq}`)));
    return `<div class="fb-said">${dotted('Change requested', 'you')}<span class="fb-words">${esc(ask.text)}</span><small>${clock(ask.at)}</small>${replies.map((r) => `<div class="fb-reply"><b>${esc(workerName(r.lane))}</b> ${esc(r.text.length > 220 ? `${r.text.slice(0, 220)}…` : r.text)} <a href="/record/${r.seq}" target="_blank" rel="noopener">${clock(r.at)} ↗</a></div>`).join('') || '<div class="fb-reply dim">No reply yet.</div>'}</div>`;
  }).join('');
  const busy = changesSending.has(Number(n));
  return `<div class="fb-box">${thread}<form class="fb" data-change="${n}" data-head="${esc(head ?? '')}"><kbd>F</kbd><input name="change" data-draft="${n}" value="${esc(changeDrafts[n] ?? '')}" placeholder="Tell Claude what to change" autocomplete="off"${busy ? ' disabled' : ''}><button class="btn"${busy ? ' disabled' : ''}>${busy ? 'Sending' : 'Send'}</button>${changesUnsent[n] ? `<span class="pill bad">Not sent: ${esc(changesUnsent[n])}</span>` : ''}</form></div>`;
}
document.addEventListener('input', (e) => {
  const form = e.target.closest('[data-change]');
  if (form) changeDrafts[form.dataset.change] = e.target.value;
});
document.addEventListener('submit', async (e) => {
  const form = e.target.closest('[data-change]');
  if (!form) return;
  e.preventDefault();
  const n = Number(form.dataset.change);
  const text = (changeDrafts[n] ?? '').trim();
  if (!text) return;
  changesSending.add(n);
  delete changesUnsent[n];
  render();
  const res = await fetch('/api/change', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ number: n, head: form.dataset.head, text }) });
  const out = await res.json();
  changesSending.delete(n);
  if (res.ok) {
    sentChanges.push({ seq: out.seq, lane: 'owner-board', kind: 'ask', at: new Date().toISOString(), text, refs: { prs: [n] } });
    changeDrafts[n] = '';
  } else changesUnsent[n] = out.error;
  render();
});
document.addEventListener('keydown', (e) => {
  if (e.key.toLowerCase() !== 'f' || e.metaKey || e.ctrlKey || e.altKey || e.target.closest('input, textarea, select, [contenteditable]') || route().screen !== 'review') return;
  const input = $('#main .pr-drawer [data-change] input') ?? $(`#main .mr[data-peek="${peek.n}"] [data-change] input`) ?? document.activeElement.closest?.('.mr')?.querySelector('[data-change] input') ?? $('#main [data-change] input');
  if (!input) return;
  e.preventDefault();
  input.focus();
});
