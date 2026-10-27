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
      const pr = state.model.prs[r.number] ?? {};
      return `<a class="row ${v}" href="#/review/${r.number}"><span class="title">${esc(r.title)}</span>
        <span class="chips"><span class="pill ${v === 'you' ? 'bad' : v === 'approved' ? 'ok' : 'warn'}">${v === 'you' ? 'Waiting on you' : v === 'approved' ? 'Approved' : 'Held'}</span>${testPill(pr)}<span class="pill">#${r.number} · ${esc(r.head)}</span></span>
        <span class="now">${inline(r.change)}</span></a>`;
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
  const pr = state.model.prs[n] ?? {};
  const rows = state.model.board.rows.filter((r) => r.group === row.group);
  const index = rows.indexOf(row);
  const next = state.model.board.rows.find((r) => r.number !== row.number && verdictState(r) === 'you');
  const given = row.verdict && row.verdict.verdict !== 'cleared' ? `${row.verdict.verdict === 'approved' ? 'Approved' : 'Held'} at ${esc((row.verdict.head ?? '').slice(0, 7))}${row.verdict.at ? ` on ${clock(row.verdict.at)}` : ''}${v === 'you' ? ', before the latest commit' : ''}` : 'No verdict yet';
  const facts = [
    ['Against the plan', row.plan, /^matches/i.test(row.plan) ? 'ok' : 'warn'],
    ['Risk', row.risk, 'warn'],
    ['Review and tests', row.review, /fail|red/i.test(row.review) ? 'bad' : ''],
    ['Blocks landing', row.blocker, /your tap/i.test(row.blocker) ? 'bad' : ''],
    ['Your verdict', given, v === 'approved' ? 'ok' : v === 'held' ? 'warn' : 'bad'],
  ];
  const actions = `<a class="btn hold" href="${esc(state.model.board.url)}" target="_blank" rel="noopener">‖ Hold</a><a class="btn pri" href="${esc(state.model.board.url)}" target="_blank" rel="noopener">✓ Approve at ${esc(row.head)}</a>${next ? `<a class="btn" href="#/review/${next.number}">Next ›</a>` : ''}`;
  const body = tab === 'code'
    ? `<p><a class="btn" href="https://github.com/${state.model.repo}/pull/${n}/files" target="_blank" rel="noopener">Open the changed files on GitHub</a></p>`
    : `<p class="lead">${inline(row.change)}</p><dl class="facts">${facts.map(([k, val, tone]) => `<div class="fact ${tone}"><dt>${k}</dt><dd>${inline(val)}</dd></div>`).join('')}</dl>
       ${row.md ? `<details class="card" style="margin-top:16px" data-id="md-${n}"${state.open.has(`md-${n}`) ? ' open' : ''}><summary><b>More detail</b></summary><div class="prose">${markdown(row.md)}</div></details>` : ''}`;
  return `<div class="pr-head"><div class="kick">${esc(row.group)} · ${index + 1} of ${rows.length}</div><h1>${esc(row.title)}</h1>
    <span class="pill ${v === 'you' ? 'bad' : v === 'approved' ? 'ok' : 'warn'}">${v === 'you' ? 'Waiting on you' : v === 'approved' ? 'Approved' : 'Held'}</span>
    <a class="pill" href="https://github.com/${state.model.repo}/pull/${n}" target="_blank" rel="noopener">#${n} at ${esc(row.head)}</a>${testPill(pr)}
    <span class="actions">${actions}</span></div>
    <nav class="subtabs"><a href="#/review/${n}" class="${tab ? '' : 'on'}">Overview</a><a href="#/review/${n}/code" class="${tab === 'code' ? 'on' : ''}">Code and review</a></nav>
    ${body}<div class="bar-bottom">${actions}</div>`;
}
