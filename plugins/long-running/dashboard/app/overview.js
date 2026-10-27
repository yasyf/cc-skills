const TONE_GLYPH = { you: '★', failing: '✕', held: '⏸', moving: '▶', queued: '⧗', waiting: '○', done: '✓', later: '⚑' };
const TONE_SAY = { you: 'Needs you', failing: 'Tests failing', held: 'Held', moving: 'Being worked on', queued: 'Approved, waiting to land', waiting: 'Waiting its turn', done: 'Done', later: 'Later' };
const LIGHT_ORDER = ['landed', 'queued', 'doing', 'you', 'failing', 'held', 'waiting'];
const ov = { page: 0, noting: null, note: '', planTab: 'steps', only: null, was: new Map(), scrub: 0, dragX: null, drafts: {} };
const PAST_HOURS = [1, 3, 6, 12, 24, 48, 96];
const RELEASED = /^(released|done|live)/i;

function scrubAt() {
  return ov.scrub < 0 ? new Date(Date.now() - PAST_HOURS[-ov.scrub - 1] * 3600 * 1000) : null;
}
function seen(e) {
  if (ov.scrub < 0) {
    const landed = state.model.prs[e.number]?.landed_at;
    return e.state === 'landed' && landed && new Date(landed) > scrubAt() ? 'waiting' : e.state;
  }
  if (ov.scrub > 0 && state.model.steps.slice(0, ov.scrub).some((s) => s.id === driveEntry(e.number)?.step)) return 'landed';
  return e.state;
}
function tally(list) {
  const out = { total: list.length };
  for (const s of LIGHT_ORDER) out[s] = 0;
  for (const e of list) out[seen(e)] += 1;
  return out;
}
function scrubChip() {
  if (!ov.scrub) return '';
  const p = pct(tally(state.model.drive.prs));
  const at = scrubAt();
  return `<button class="only scrubchip" data-scrub-clear>${at ? `As of ${clock(at.toISOString())}` : `If steps 1–${ov.scrub} land`} · ${p}% ✕</button>`;
}
function setScrub(v) {
  const next = Math.max(-PAST_HOURS.length, Math.min(state.model.steps.length, v));
  if (next !== ov.scrub) { ov.scrub = next; render(); }
}
function pct(t) {
  return t.total ? Math.round((100 * t.landed) / t.total) : 0;
}
function light(n) {
  const entry = driveEntry(n);
  const s = entry ? seen(entry) : prState(n);
  const flash = s === 'landed' && ov.was.size && ov.was.get(Number(n)) && ov.was.get(Number(n)) !== 'landed' ? ' flash' : '';
  const dim = ov.only && !ov.only.numbers.has(Number(n)) ? ' out' : '';
  return `<a class="lt ${s}${flash}${dim}" href="${prHref(n)}" data-n="${n}" aria-label="#${n} ${esc(STATE_WORD[s])}"></a>`;
}
function lights(list) {
  return [...list].sort((a, b) => LIGHT_ORDER.indexOf(seen(a)) - LIGHT_ORDER.indexOf(seen(b))).map((e) => light(e.number)).join('');
}
function workerStrip(w) {
  const pr = w.prs[0];
  return `<span class="ws" data-lane="${esc(w.lane)}"><span class="bx">${w.boxes.map((b) => `<i class="${b}"></i>`).join('')}</span>${pr ? `<b>#${pr}</b>` : ''}</span>`;
}
function liveWorkers(stepId) {
  return state.model.workers.filter((w) => w.live && w.step === stepId);
}
const TONE_WORD = { you: 'Needs you', failing: 'Tests failing', held: 'Held', moving: 'Being worked on', queued: 'Approved, landing', waiting: 'Waiting', done: 'Done', later: 'Later' };
function bar(t) {
  return `<span class="sbar">${LIGHT_ORDER.filter((k) => t[k]).map((k) => `<i class="sw ${k}" style="flex:${t[k]}"></i>`).join('')}</span>`;
}
function stepRow(s) {
  const t = tally(s.prs);
  const ws = liveWorkers(s.id);
  const blocker = s.blocker && !/^(none|nothing)$/i.test(s.blocker) ? s.blocker : '';
  const count = (v, cls) => `<span class="r-n${v ? ` ${cls}` : ''}">${s.prs.length ? v : ''}</span>`;
  return `<li class="srow tn-${s.tone}" data-track="${esc(s.id)}" tabindex="0">
    <span class="r-step">${esc(s.label ?? s.title)}${ws.length ? `<i class="pulse" title="${plural(ws.length, 'worker')} on it now"></i>` : ''}</span>
    <span class="r-prog">${s.prs.length ? `<b>${t.landed}<small>/${t.total}</small></b>${bar(t)}` : ''}</span>
    ${count(t.queued, 'ok')}${count(t.you, 'you')}
    <span class="r-state">${TONE_WORD[s.tone] ?? esc(s.tone)}</span>
    <span class="r-block">${esc(blocker)}</span>
    <span class="r-next">${esc(s.next)}</span></li>`;
}
function unplanned() {
  const list = state.model.drive.prs.filter((e) => !e.step && e.state !== 'landed');
  if (!list.length) return '';
  const t = tally(list);
  return `<li class="srow tn-waiting unplanned" data-track="" tabindex="0"><span class="r-step">Other work</span>
    <span class="r-prog"><b>${list.length}<small> open</small></b>${bar(t)}</span>
    <span class="r-n${t.queued ? ' ok' : ''}">${t.queued}</span><span class="r-n${t.you ? ' you' : ''}">${t.you}</span>
    <span class="r-state">${t.failing ? `${t.failing} failing` : 'Open'}</span><span class="r-block"></span><span class="r-next">Fixes and tooling outside the plan steps</span></li>`;
}
function steps() {
  return `<section class="steps2"><header class="srow head"><span>Step</span><span>Landed</span><span title="Approved, waiting to land">Appr.</span><span title="Waiting on your tap">You</span><span>State</span><span>What blocks it</span><span>Next</span></header>
    <ol>${state.model.steps.map(stepRow).join('')}${unplanned()}</ol>
    <footer><span class="legend">${['landed', 'queued', 'doing', 'you', 'failing', 'waiting'].map((k) => `<span><i class="sw ${k}"></i>${{ landed: 'landed', queued: 'approved', doing: 'being worked on', you: 'needs you', failing: 'tests failing', waiting: 'waiting' }[k]}</span>`).join('')}</span></footer></section>`;
}
function lastHour() {
  const m = state.model;
  return m.drive.prs.filter((e) => e.state === 'landed' && Date.now() - new Date(m.prs[e.number]?.landed_at ?? 0) < 3600 * 1000);
}
function brokeTonight() {
  const m = state.model;
  const recs = m.records.filter((r) => ['incident', 'fix-live'].includes(r.kind) && Date.now() - new Date(r.at) < 12 * 3600 * 1000).sort((a, b) => new Date(a.at) - new Date(b.at));
  const last = recs[recs.length - 1];
  const failing = tally(m.drive.prs).failing;
  const red = failing ? ` · ${plural(failing, 'pull request')} failing tests` : '';
  if (!last) return `Nothing broke${red}`;
  if (last.kind === 'incident') return `${esc(firstSentences(last.text, 70))}, open since ${clock(last.at)}${red}`;
  return `${esc(last.text.match(/^FIX-LIVE\s+([^:]+):/)?.[1] ?? firstSentences(last.text, 50))}, fixed ${clock(last.at)}${red}`;
}
function proof(p) {
  return p ? esc(p.state.split(':')[0]) : 'Not checked yet';
}
function tile(label, value, sub, cls, href, fill) {
  return `<a class="tile2 ${cls}" href="${href}"><small>${label}</small><b>${value}</b>${fill === undefined ? '' : `<span class="tbar"><i style="width:${fill}%"></i></span>`}<span class="sub">${sub}</span></a>`;
}
function totals() {
  const m = state.model;
  const all = tally(m.drive.prs);
  const t = tally(m.drive.prs.filter((e) => e.tree === m.tree_names[0]));
  const rels = (m.now.releases ?? []).filter((r) => r.tonight);
  const out = rels.filter((r) => RELEASED.test(r.state)).length;
  const next = rels.find((r) => !RELEASED.test(r.state));
  const hour = lastHour().length;
  const g = m.now.goal ?? {};
  return `<section class="totals">
    ${tile('New app merged', `${t.landed}<span>/${t.total}</span>`, `${hour ? `+${hour}` : 'None'} landed in the last hour`, 'ok', '#/timeline', pct(t))}
    ${tile('Approved, landing next', all.queued, 'pull requests across the drive', '', '#/review')}
    ${tile('Need your tap', all.you, all.you ? 'Open the review list' : 'Nothing waiting on you', all.you ? 'you' : '', '#/review')}
    ${tile('Released tonight', `${out}<span>/${rels.length}</span>`, next ? `Next: ${esc(next.id)}, ${esc(next.name)}` : 'All out', '', '#/releases', rels.length ? (100 * out) / rels.length : 0)}
    <a class="tile2 tonight" href="#/releases"><small>Tonight: flag holders get the new app, everyone else keeps the old</small>
      <ul><li title="${esc(g.old_app?.state ?? '')}"><b>Old app</b><span>${proof(g.old_app)}</span></li><li title="${esc(g.enabled?.state ?? '')}"><b>Flag</b><span>${proof(g.enabled)}</span></li><li><b>Broke</b><span>${brokeTonight()}</span></li></ul></a>
  </section>`;
}
function track() {
  const m = state.model;
  const rels = m.now.releases ?? [];
  const next = rels.findIndex((r) => !RELEASED.test(r.state));
  const cur = rels[next];
  return `<section class="track">
    <div class="stops">${rels.map((r, i) => `<span class="rstop${i < next || next < 0 ? ' done' : i === next ? ' next' : ''}${r.tonight ? '' : ' after'}" data-release="${esc(r.id)}"><i></i><b>${esc(r.id)}</b><small>${esc(r.name)}</small></span>`).join('')}</div>
    <p class="at">${cur ? `<b>${esc(cur.id)}, ${esc(cur.name)}:</b> ${esc(cur.state)}` : 'Every release is out'}</p>
    <button class="planthumb" data-plan><span class="mini">${m.steps.map((s) => `<i class="tn-${s.tone}" style="--w:${Math.max(6, pct(tally(s.prs)))}%"></i>`).join('')}</span><span class="pl"><b>Exact plan</b><small>revised ${m.plan_at ? clock(m.plan_at) : ''}</small></span></button>
  </section>`;
}
function working() {
  const m = state.model;
  const live = m.workers.filter((w) => w.live);
  if (!live.length) return '';
  const box = (w) => {
    const step = m.steps.find((s) => s.id === w.step);
    const pr = w.prs[0];
    return `<a class="wbox2" href="${pr ? prHref(pr) : '#/workers'}" data-lane="${esc(w.lane)}"><b>${esc(step?.label ?? 'Tooling')}</b><em>${agoWords(w.at)}</em><span>${pr ? `#${pr} ${esc(shortName(pr) ?? plainTitle(pr))}` : esc(w.lane)}</span><span class="bx">${w.boxes.map((b) => `<i class="${b}"></i>`).join('')}</span></a>`;
  };
  return `<section class="working"><h4>Working now <span>${plural(live.length, 'worker')}</span></h4><div class="wboxes">${live.slice(0, 6).map(box).join('')}${live.length > 6 ? `<a class="wbox2 more" href="#/workers">+${live.length - 6} more</a>` : ''}</div></section>`;
}
function waitingOn() {
  const items = state.model.now.waiting ?? [];
  if (!items.length) return '';
  return `<section class="waiting"><h4>Waiting on</h4><ul>${items.map((w) => `<li><b>${esc(w.what)}</b><span>${[w.owed_by && `owed by ${esc(w.owed_by)}`, w.since && `since ${esc(w.since)}`, w.expected && `expected ${esc(w.expected)}`].filter(Boolean).join(' · ')}</span>${w.unblocks ? `<em>unblocks ${esc(w.unblocks)}</em>` : ''}</li>`).join('')}</ul></section>`;
}
function why(item) {
  if (!item.why) return '';
  const src = item.source?.href ? ` <a href="${esc(item.source.href)}" target="_blank" rel="noopener">${esc(item.source.label)} ↗</a>` : item.source ? ` <span>${esc(item.source.label)}</span>` : '';
  return `<p class="why">${esc(item.why)}${src}</p>`;
}
function needItem(item) {
  if (!item) return '<div class="act calm"><span class="what">✓ Nothing needs you right now</span></div>';
  if (item.kind === 'prs' && item.prs.length === 1) {
    const r = item.prs[0];
    const row = boardRow(r.number);
    const busy = pending.number === r.number && !pending.error;
    const failed = pending.number === r.number && pending.error ? `<em class="err">${esc(pending.error)}</em>` : '';
    return `<div class="act"><a class="what" href="#/review/${r.number}"><span class="star">★</span><b>#${r.number}</b> ${esc(shortName(r.number) ?? r.title)}</a>${why(item)}
      ${ov.noting === `${r.number}@${r.head}` ? `<form class="note" data-note="${r.number}" data-head="${esc(r.head)}"><input name="note" placeholder="Note" value="${esc(ov.note)}"><button class="btn pri">✓ Approve</button><button type="button" class="btn hold" data-note-hold="${r.number}">‖ Hold</button></form>`
        : `<div class="acts"><button class="btn pri" data-approve="${r.number}"${busy || !row ? ' disabled' : ''}>${busy ? 'Sending…' : `✓ Approve ${esc(r.head.slice(0, 7))}`}</button><button class="btn" data-noting="${r.number}@${esc(r.head)}">✎ Note</button>${failed}</div>`}</div>`;
  }
  if (item.kind === 'prs') return `<div class="act"><a class="what" href="#/review"><span class="star">★</span><b>${item.prs.length}</b> pull requests${item.group ? ` in ${esc(item.group)}` : ''} need your approval</a>${why(item)}<div class="acts"><a class="btn pri" href="#/review">Review ${item.prs.length}</a></div></div>`;
  if (item.kind === 'card') {
    const c = item.card;
    const options = [...c.options].sort((a, b) => b.recommended - a.recommended);
    const failed = ov.answerError ? `<em class="err">${esc(ov.answerError)}</em>` : '';
    return `<div class="act"><span class="what"><span class="star">?</span>${esc(c.title)}</span>${why(item)}
      <div class="acts">${options.map((o) => `<button class="btn${o.recommended ? ' pri' : ''}" data-choose="${esc(o.id)}" data-card="${esc(c.id)}" data-choice="${esc(c.choice)}"${ov.answering ? ' disabled' : ''}>${esc(o.label)}</button>`).join('')}</div>
      <form class="note" data-answer="${esc(c.id)}" data-choice="${esc(c.choice ?? '')}"><input name="text" data-draft="${esc(c.id)}" value="${esc(ov.drafts[c.id] ?? '')}" placeholder="${c.choice ? 'Other answer or a note' : 'Your note'}"><button class="btn">${c.choice ? 'Send' : 'Send note'}</button></form>${failed}</div>`;
  }
  return `<div class="act"><small class="kick">Reminder</small><span class="what">${esc(item.ask.what)}</span>${why(item)}<div class="acts">${item.ask.link ? `<a class="btn pri" href="${esc(item.ask.link)}" target="_blank" rel="noopener">${esc(item.ask.link_text ?? 'Open')}</a>` : ''}</div></div>`;
}
function actionCard() {
  const needs = state.model.needs;
  ov.page = Math.min(ov.page, Math.max(0, needs.length - 1));
  const pager = needs.length > 1 ? `<span class="pager">${needs.map((_, i) => `<i class="${i === ov.page ? 'on' : ''}" data-page="${i}"></i>`).join('')}<span>${ov.page + 1}/${needs.length}</span></span>` : '';
  return `<section class="actcard${needs.length ? '' : ' empty'}${needs[ov.page]?.kind === 'ask' ? ' later' : ''}" tabindex="-1">${needItem(needs[ov.page])}${pager}</section>`;
}
function machine(home, title) {
  const parts = state.model.parts.filter((p) => p.home === home);
  const list = state.model.drive.prs.filter((e) => parts.some((p) => p.key === e.part));
  const target = state.model.tree_names[0];
  const kept = list.filter((e) => e.tree === state.model.tree_names[1]);
  const t = tally(list);
  const rows = parts.map((p) => {
    const mine = list.filter((e) => e.part === p.key && !kept.includes(e));
    if (!mine.length) return '';
    const pt = tally(mine);
    return `<div class="part" data-part="${p.key}"><span class="pn">${esc(p.name)}</span><span class="pls" style="--u:${pct(pt)}%">${lights(mine)}</span></div>`;
  }).join('');
  return `<figure class="mach ${home}" data-home="${home}" data-pct="${pct(t)}"><svg class="ol" aria-hidden="true"></svg>
    <figcaption><b>${title}</b><em>${pct(t)}%</em></figcaption>
    <div class="parts">${rows}</div>
    ${kept.length ? `<div class="kept"><span>${home === 'mac' ? 'Old, kept' : ''}</span><span class="pls">${lights(kept)}</span></div>` : ''}</figure>`;
}
function releaseTrunk() {
  const rels = state.model.now.releases ?? [];
  if (!rels.length) return '';
  const next = rels.findIndex((r) => !/^(released|done|live)/i.test(r.state));
  return `<div class="rtrunk">${rels.map((r, i) => `<span class="rd ${i < next || next < 0 ? 'done' : i === next ? 'next' : ''}" data-release="${esc(r.id)}" title="${esc(r.name)}: ${esc(r.state)}"><i></i>${esc(r.id)}</span>`).join('<span class="rl"></span>')}<span class="rl"></span><span class="rd flag">⚑</span></div>`;
}
function destination() {
  const t = tally(state.model.drive.prs);
  const tools = state.model.drive.prs.filter((e) => e.part === 'tools');
  return `<section class="dest">
    ${machine('cloud', 'Cloud')}
    <div class="ring-row"><button class="ring" data-key style="--p:${pct(t)}%"><b>${pct(t)}<small>%</small></b></button>
      <span class="counts"><span><b>${t.landed}</b> landed <b>${t.queued}</b> approved <b class="${t.you ? 'you' : ''}">${t.you}</b> your tap</span><small>of all ${t.total} drive pull requests</small></span></div>
    ${machine('mac', 'Mac')}
    <div class="toolsband" data-part="tools"><span class="pn">Tools</span><span class="pls">${lights(tools)}</span></div>
    <p class="endstate">End state: Mac and cloud both run the new tree, chats follow live between them, and the old tree stays until cutover.</p>
  </section>`;
}
function overview() {
  const m = state.model;
  const stop = m.now.blocker;
  return `<div class="cp">
    ${stop ? `<button class="stopband" data-stop><i>■</i><b>Stopped since ${clock(stop.since)}</b><span>${esc(stop.text)}</span><em>›</em></button>` : ''}
    ${ov.only || ov.scrub ? `<div class="cp-top">${ov.only ? `<button class="only" data-only-clear>Only ${esc(ov.only.label)} ✕</button>` : ''}${scrubChip()}</div>` : ''}
    ${totals()}
    ${track()}
    <div class="cp-grid2">
      <div class="cp-main">${steps()}</div>
      <aside class="rail">${actionCard()}${destination()}</aside>
    </div>
    ${working()}
    ${waitingOn()}
    <div id="tip" class="tip" hidden></div>
  </div>
  ${state.overlay === 'track' ? trackSheet() : ''}
  ${state.overlay === 'stop' && stop ? stopSheet() : ''}
  ${state.overlay === 'key' ? keySheet() : ''}
  ${state.overlay === 'plan' ? planSheet() : ''}`;
}
function planSheet() {
  const m = state.model;
  const tabs = [['steps', 'Steps'], ['releases', 'Releases'], ['text', 'Plan text']];
  const body = {
    steps: () => m.steps.map((s) => stepCard(s)).join(''),
    releases: () => `${releaseTrunk()}<div class="prose">${markdown(m.releases.markdown)}</div>`,
    text: () => `<p class="dim">The progress doc's ordered plan, written ${m.plan_at ? agoWords(m.plan_at) : ''}. Where it and the release plan differ, the release plan is newer.</p><ol class="psteps">${planSteps(true)}</ol>`,
  }[ov.planTab]();
  return `<div class="overlay" data-close="1"><div class="sheet plan"><button class="btn close" data-close="1">Close</button>
    <nav class="ptabs">${tabs.map(([k, l]) => `<button class="${ov.planTab === k ? 'on' : ''}" data-ptab="${k}">${l}</button>`).join('')}</nav>${body}</div></div>`;
}
async function answer(body) {
  ov.answering = true;
  ov.answerError = '';
  render();
  ov.answerError = await fetch('/api/answer', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then((res) => res.json())
    .then((out) => out.error ?? '', (failure) => `could not reach the dashboard server: ${failure.message}`);
  ov.answering = false;
  render();
}
function noteDecide(form, verdict) {
  const row = boardRow(form.dataset.note);
  ov.noting = null;
  if (row?.head !== form.dataset.head) return render();
  decide(row, verdict, form.note.value.trim(), false);
}
function showOnly(label, numbers) {
  ov.only = numbers ? { label, numbers: new Set(numbers.map(Number)) } : null;
  if (route().screen !== 'overview') location.hash = '#/';
  render();
}
const SAID = { head: 'Pushed a new version', report: 'Reported', state: 'Posted its status', blocker: 'Reported a blocker', ask: 'Asked a question', review: 'Reviewed', go: 'Got the go to land', opened: 'Opened a pull request', landed: 'Landed', defect: 'Reported a tool problem', answer: 'Answered', decide: 'Decided', incident: 'Raised an incident', 'fix-live': 'Put a fix live' };
function lastWord(w) {
  const r = state.model.records.filter((x) => x.lane === w.lane).reduce((a, b) => (!a || new Date(b.at) > new Date(a.at) ? b : a), null);
  const prs = w.prs.slice(0, 2).map((n) => `<a href="${prHref(n)}">#${n} ${esc(shortName(n) ?? plainTitle(n))}</a>`).join(', ');
  return `<li><span class="ws"><span class="bx">${w.boxes.map((b) => `<i class="${b}"></i>`).join('')}</span></span><div><b>${r ? SAID[r.kind] ?? KIND_WORD[r.kind] ?? r.kind : 'At work'}</b>${prs ? ` on ${prs}` : ''}, ${agoWords(r?.at ?? w.at)}<small>worker ${esc(w.lane)}${r ? ` · <a href="/record/${r.seq}" target="_blank" rel="noopener">its last word, #${r.seq} ↗</a>` : ''}</small></div></li>`;
}
function stepCard(s) {
  const t = tally(s.prs);
  const ws = state.model.workers.filter((w) => w.step === s.id && w.live);
  return `<section class="stepcard tn-${s.tone}"><div class="kick">${TONE_GLYPH[s.tone] ?? ''} ${TONE_SAY[s.tone] ?? ''}</div><h3>${esc(s.title)}</h3>
    <dl class="facts"><dt>Where it stands</dt><dd>${inline(s.state)}</dd><dt>What blocks it</dt><dd>${s.blocker ? inline(s.blocker) : 'Nothing'}</dd><dt>Next</dt><dd>${inline(s.next)}</dd></dl>
    ${ws.length ? `<h4 class="sc-h">Who is on it</h4><ul class="wlist">${ws.map(lastWord).join('')}</ul>` : '<p class="dim">No worker is on this step right now.</p>'}
    ${s.prs.length ? `<h4 class="sc-h">Pull requests <span><b>${t.landed}</b> landed · <b>${t.queued}</b> approved · <b>${t.you}</b> your tap · ${t.total} in all</span></h4><ul class="prlist">${[...s.prs].sort((a, b) => LIGHT_ORDER.indexOf(a.state) - LIGHT_ORDER.indexOf(b.state)).map((p) => `<li><a href="${prHref(p.number)}"><i class="lt ${p.state}"></i><b>#${p.number}</b><span>${esc(shortName(p.number) ?? plainTitle(p.number))}</span><em>${STATE_WORD[p.state]}</em></a></li>`).join('')}</ul>` : ''}
    <p class="dim">Desk note written ${clock(state.model.steps_at)}.</p></section>`;
}
function trackSheet() {
  const s = state.model.steps.find((x) => x.id === ov.track);
  if (s) return `<div class="overlay" data-close="1"><div class="sheet"><button class="btn close" data-close="1">Close</button>${stepCard(s)}</div></div>`;
  const list = state.model.drive.prs.filter((e) => !e.step && e.state !== 'landed');
  return `<div class="overlay" data-close="1"><div class="sheet"><button class="btn close" data-close="1">Close</button><div class="kick">No plan step names these</div><h3>Unplanned: ${list.length} open pull requests</h3>
    <ul class="prlist">${list.map((p) => `<li><a href="${prHref(p.number)}"><i class="lt ${p.state}"></i><b>#${p.number}</b><span>${esc(plainTitle(p.number))}</span><em>${STATE_WORD[p.state]}</em></a></li>`).join('')}</ul></div></div>`;
}
function stopSheet() {
  const stop = state.model.now.blocker;
  const events = timeline().filter((e) => ['incident', 'mechanism', 'fix-live', 'blocker'].includes(e.kind) && new Date(e.at) >= new Date(stop.since)).slice(0, 12);
  return `<div class="overlay" data-close="1"><div class="sheet"><button class="btn close" data-close="1">Close</button><div class="kick">Stopped since ${clock(stop.since)}</div><h3>${esc(stop.text)}</h3>
    <ul class="tl">${timelineRows(events, false)}</ul></div></div>`;
}
function keySheet() {
  const say = { landed: 'Landed', queued: 'Approved, waiting to land', doing: 'Being worked on now', you: 'Waiting on your tap', failing: 'Tests failing', held: 'Held', waiting: 'Waiting its turn' };
  return `<div class="overlay" data-close="1"><div class="sheet small"><button class="btn close" data-close="1">Close</button><h3>One light is one pull request</h3>
    <ul class="keylist">${LIGHT_ORDER.map((s) => `<li><i class="lt ${s}"></i>${say[s]}</li>`).join('')}</ul>
    <ul class="keylist">${Object.entries(TONE_GLYPH).map(([k, g]) => `<li><span class="g">${g}</span>${TONE_SAY[k]}</li>`).join('')}</ul></div></div>`;
}
function tipFor(el) {
  if (el.dataset.n) {
    const n = el.dataset.n;
    const e = driveEntry(n);
    const step = state.model.steps.find((s) => s.id === e?.step);
    const part = state.model.parts.find((p) => p.key === e?.part);
    const pr = state.model.prs[n] ?? {};
    return `<div class="tip-h"><i class="lt ${prState(n)}"></i><b>#${n}</b><span>${STATE_WORD[prState(n)]}</span></div><div>${esc(shortName(n) ?? plainTitle(n))}</div>
      <div class="dim">${[step?.label ?? step?.title, part?.name, e?.worker?.lane, pr.landed_at ? `landed ${clock(pr.landed_at)}` : ''].filter(Boolean).map(esc).join(' · ')}</div>`;
  }
  if (el.dataset.part) {
    const p = state.model.parts.find((x) => x.key === el.dataset.part);
    const t = tally(state.model.drive.prs.filter((e) => e.part === p.key));
    return `<div class="tip-h"><b>${esc(p.name)}</b><span>${pct(t)}%</span></div><div>${esc(p.becomes)}</div><div class="dim">${t.landed} landed · ${t.queued} queued · ${t.total - t.landed - t.queued} to go</div>`;
  }
  if (el.dataset.lane) {
    const w = state.model.workers.find((x) => x.lane === el.dataset.lane);
    return `<div class="tip-h"><b>${esc(w.lane)}</b><span>${agoWords(w.at)}</span></div>${w.prs.map((n) => `<div>#${n} ${esc(plainTitle(n))}</div>`).join('')}`;
  }
  if (el.dataset.release) {
    const r = state.model.now.releases.find((x) => x.id === el.dataset.release);
    return `<div class="tip-h"><b>${esc(r.name)}</b><span>${esc(r.state)}</span></div><div class="dim">${r.parts.map((k) => esc(state.model.parts.find((p) => p.key === k)?.name ?? k)).join(' · ')}</div>`;
  }
  return '';
}
function highlight(numbers) {
  const cp = $('.cp');
  if (!cp) return;
  cp.classList.toggle('focus', Boolean(numbers));
  for (const el of cp.querySelectorAll('.lt.lit')) el.classList.remove('lit');
  if (!numbers) return;
  for (const n of numbers) for (const el of cp.querySelectorAll(`.lt[data-n="${n}"]`)) el.classList.add('lit');
}
function numbersFor(el) {
  if (el.dataset.track !== undefined) {
    const s = state.model.steps.find((x) => x.id === el.dataset.track);
    return s ? s.prs.map((p) => p.number) : state.model.drive.prs.filter((e) => !e.step && e.state !== 'landed').map((e) => e.number);
  }
  if (el.dataset.part) return state.model.drive.prs.filter((e) => e.part === el.dataset.part).map((e) => e.number);
  if (el.dataset.release) {
    const r = state.model.now.releases.find((x) => x.id === el.dataset.release);
    return state.model.drive.prs.filter((e) => r.parts.includes(e.part)).map((e) => e.number);
  }
  if (el.dataset.lane) return state.model.workers.find((w) => w.lane === el.dataset.lane)?.prs ?? [];
  return null;
}
function mountOverview() {
  const input = $('.note input');
  if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
  ov.was = new Map(state.model.drive.prs.map((e) => [e.number, e.state]));
  for (const fig of document.querySelectorAll('.mach')) {
    const svg = fig.querySelector('svg');
    const W = fig.offsetWidth, H = fig.offsetHeight, r = 16, e = 1;
    const b = fig.classList.contains('cloud') ? 22 : 0;
    const top = fig.classList.contains('cloud')
      ? `L${W * 0.46} ${b} A${W * 0.07} ${b * 0.8} 0 0 1 ${W * 0.6} ${b} A${W * 0.11} ${b} 0 0 1 ${W * 0.82} ${b} A${W * 0.05} ${b * 0.7} 0 0 1 ${W * 0.92} ${b}`
      : '';
    const d = `M${e} ${H - e - r} L${e} ${b + r} Q${e} ${b + e} ${r} ${b + e} ${top} L${W - r} ${b + e} Q${W - e} ${b + e} ${W - e} ${b + r} L${W - e} ${H - e - r} Q${W - e} ${H - e} ${W - r} ${H - e} L${r} ${H - e} Q${e} ${H - e} ${e} ${H - e - r} Z`;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.innerHTML = `<path class="base" d="${d}"/><path class="prog" d="${d}" pathLength="100" stroke-dasharray="${fig.dataset.pct} 100"/>`;
  }
}
document.addEventListener('mouseover', (e) => {
  const tip = $('#tip');
  if (!tip) return;
  if (!e.target.closest('.cp')) { tip.hidden = true; return highlight(null); }
  const el = e.target.closest('[data-n], [data-part], [data-lane], [data-release]');
  if (el) {
    tip.innerHTML = tipFor(el);
    tip.hidden = false;
    const box = el.getBoundingClientRect();
    tip.style.left = `${Math.min(window.innerWidth - 300, Math.max(8, box.left + box.width / 2 - 140))}px`;
    tip.style.top = `${box.bottom + 8 + window.scrollY}px`;
  } else {
    tip.hidden = true;
  }
  const area = e.target.closest('[data-track], [data-part], [data-release], [data-lane]');
  highlight(area ? numbersFor(area) : null);
});
document.addEventListener('click', (e) => {
  if (!e.target.closest('.cp, .overlay')) return;
  const trackEl = e.target.closest('[data-track]');
  if (trackEl && !e.target.closest('a')) { ov.track = trackEl.dataset.track; state.overlay = 'track'; return render(); }
  if (e.target.closest('[data-plan]')) { state.overlay = 'plan'; ov.planTab = 'text'; return render(); }
  if (e.target.closest('[data-stop]')) { state.overlay = 'stop'; return render(); }
  if (e.target.closest('[data-key]')) { state.overlay = 'key'; return render(); }
  if (e.target.closest('[data-only-clear]')) return showOnly(null);
  const rel = e.target.closest('[data-release]');
  if (rel) {
    const r = state.model.now.releases.find((x) => x.id === rel.dataset.release);
    state.overlay = 'plan';
    ov.planTab = 'releases';
    render();
    const target = [...document.querySelectorAll('.sheet h2, .sheet h3, .sheet h4, .sheet h5')].find((h) => h.textContent.toLowerCase().startsWith(r.name.toLowerCase()));
    target?.scrollIntoView({ block: 'start' });
    return;
  }
  if (e.target.closest('[data-scrub-clear]')) return setScrub(0);
  const ptab = e.target.closest('[data-ptab]');
  if (ptab) { ov.planTab = ptab.dataset.ptab; return render(); }
  const page = e.target.closest('[data-page]');
  if (page) { ov.page = Number(page.dataset.page); ov.noting = null; return render(); }
  const noting = e.target.closest('[data-noting]');
  if (noting) { ov.noting = noting.dataset.noting; ov.note = ''; return render(); }
  const choose = e.target.closest('[data-choose]');
  if (choose) return answer({ kind: 'choice', block: choose.dataset.choice, options: [choose.dataset.choose] });
  const approve = e.target.closest('[data-approve]');
  if (approve) return decide(boardRow(approve.dataset.approve), 'approved', '', false);
  const hold = e.target.closest('[data-note-hold]');
  if (hold) return noteDecide(hold.closest('[data-note]'), 'rejected');
});
document.addEventListener('submit', (e) => {
  const reply = e.target.closest('[data-answer]');
  if (reply) {
    e.preventDefault();
    const text = reply.text.value.trim();
    if (!text) return;
    return answer(reply.dataset.choice ? { kind: 'choice', block: reply.dataset.choice, other: text } : { kind: 'note', block: reply.dataset.answer, text });
  }
  const form = e.target.closest('[data-note]');
  if (!form) return;
  e.preventDefault();
  noteDecide(form, 'approved');
});
document.addEventListener('input', (e) => {
  if (e.target.dataset.draft) ov.drafts[e.target.dataset.draft] = e.target.value;
  if (e.target.closest('[data-note]')) ov.note = e.target.value;
});
document.addEventListener('keydown', (e) => {
  if (route().screen !== 'overview' || e.target.closest('input, textarea') || e.metaKey || e.ctrlKey) return;
  const needs = state.model?.needs ?? [];
  if (e.key === 'Escape' && ov.only && !state.overlay) { showOnly(null); return; }
  const paging = e.target.closest('.actcard') && needs.length > 1;
  if (paging && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) {
    ov.page = (ov.page + (e.key === 'ArrowRight' ? 1 : needs.length - 1)) % needs.length;
    render();
    $('.actcard')?.focus();
  }
  else if (e.key === 'ArrowRight' && !state.overlay) setScrub(ov.scrub + 1);
  else if (e.key === 'ArrowLeft' && !state.overlay) setScrub(ov.scrub - 1);
  else if (e.key === 'n' && ov.scrub) setScrub(0);
  else if (e.key === 'p' && !state.overlay) { state.overlay = 'plan'; render(); }
  else if (e.key === 'Enter' && e.target.dataset?.track !== undefined) { ov.track = e.target.dataset.track; state.overlay = 'track'; render(); }
});
window.addEventListener('resize', () => { if (route().screen === 'overview' && state.model) mountOverview(); });
Object.assign(window, { showOnly, tally });
document.addEventListener('pointerdown', (e) => {
  if (!e.target.closest('.dest .mach, .dest .ring')) return;
  ov.dragX = e.clientX;
  ov.dragFrom = ov.scrub;
});
document.addEventListener('pointermove', (e) => {
  if (ov.dragX === null) return;
  setScrub(ov.dragFrom + Math.round((e.clientX - ov.dragX) / 48));
});
document.addEventListener('pointerup', () => { ov.dragX = null; });
document.addEventListener('pointercancel', () => { ov.dragX = null; });
document.addEventListener('visibilitychange', () => { if (document.hidden) localStorage.setItem('drive-last-look', String(Date.now())); });
window.addEventListener('pagehide', () => localStorage.setItem('drive-last-look', String(Date.now())));
