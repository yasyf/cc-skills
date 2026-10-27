const TONE_GLYPH = { you: '★', failing: '✕', held: '⏸', moving: '▶', queued: '⧗', waiting: '○', done: '✓', later: '⚑' };
const TONE_SAY = { you: 'Needs you', failing: 'Tests failing', held: 'Held', moving: 'Being worked on', queued: 'Approved, waiting to land', waiting: 'Waiting its turn', done: 'Done', later: 'Later' };
const LIGHT_ORDER = ['landed', 'queued', 'doing', 'you', 'failing', 'held', 'waiting'];
const ov = { page: 0, unfold: false, noting: null, note: '', planTab: 'steps', only: null, was: new Map() };

function tally(list) {
  const out = { total: list.length };
  for (const s of LIGHT_ORDER) out[s] = 0;
  for (const e of list) out[e.state] += 1;
  return out;
}
function pct(t) {
  return t.total ? Math.round((100 * t.landed) / t.total) : 0;
}
function light(n) {
  const s = prState(n);
  const flash = s === 'landed' && ov.was.size && ov.was.get(Number(n)) && ov.was.get(Number(n)) !== 'landed' ? ' flash' : '';
  const dim = ov.only && !ov.only.numbers.has(Number(n)) ? ' out' : '';
  return `<a class="lt ${s}${flash}${dim}" href="${prHref(n)}" data-n="${n}" aria-label="#${n} ${esc(STATE_WORD[s])}"></a>`;
}
function lights(list) {
  return [...list].sort((a, b) => LIGHT_ORDER.indexOf(a.state) - LIGHT_ORDER.indexOf(b.state)).map((e) => light(e.number)).join('');
}
function workerStrip(w) {
  const pr = w.prs[0];
  return `<span class="ws" data-lane="${esc(w.lane)}"><span class="bx">${w.boxes.map((b) => `<i class="${b}"></i>`).join('')}</span>${pr ? `<b>#${pr}</b>` : ''}</span>`;
}
function liveWorkers(stepId) {
  return state.model.workers.filter((w) => w.live && w.step === stepId);
}
function track(s, i) {
  const t = tally(s.prs);
  const ws = liveWorkers(s.id);
  const strips = ws.slice(0, 2).map(workerStrip).join('') + (ws.length > 2 ? `<span class="more">+${ws.length - 2}</span>` : '');
  return `<li class="trk tn-${s.tone}" data-track="${esc(s.id)}" tabindex="0">
    <span class="tn">${i + 1}</span><span class="tl">${esc(s.label ?? '')}</span>
    <span class="tls">${lights(s.prs)}</span>
    <span class="tws">${strips}</span>${ws.length ? `<span class="badge">▶${ws.length}</span>` : ''}
    <span class="tf">${s.prs.length ? `${t.landed}/${t.total}` : ''}</span>
    <span class="tg" title="${TONE_SAY[s.tone] ?? ''}">${TONE_GLYPH[s.tone] ?? ''}</span><span class="wire"></span></li>`;
}
function unplanned() {
  const list = state.model.drive.prs.filter((e) => !e.step && e.state !== 'landed');
  if (!list.length) return '';
  const ws = state.model.workers.filter((w) => w.live && !w.step);
  return `<li class="trk tn-waiting unplanned" data-track="" tabindex="0"><span class="tn">┄</span><span class="tl">Unplanned</span>
    <span class="tls">${lights(list)}</span><span class="tws">${ws.slice(0, 2).map(workerStrip).join('')}</span>${ws.length ? `<span class="badge">▶${ws.length}</span>` : ''}
    <span class="tf">${list.length}</span><span class="tg"></span><span class="wire"></span></li>`;
}
function tracks() {
  const steps = state.model.steps;
  const active = steps.map((s, i) => [s, i]).filter(([s]) => ov.unfold || !['done', 'later'].includes(s.tone));
  const done = steps.filter((s) => s.tone === 'done').length;
  const later = steps.filter((s) => s.tone === 'later').length;
  return `<ol class="trks">${active.map(([s, i]) => track(s, i)).join('')}${unplanned()}</ol>
    <button class="fold" data-unfold>${ov.unfold ? 'Fold' : `${done ? `✓${done}` : ''}${later ? `  ⚑${later}` : ''}`}</button>`;
}
function needItem(item) {
  if (!item) return '<div class="act calm"><span class="big-glyph">✓</span></div>';
  if (item.kind === 'prs' && item.prs.length === 1) {
    const r = item.prs[0];
    const row = boardRow(r.number);
    const busy = pending.number === r.number && !pending.error;
    const failed = pending.number === r.number && pending.error ? `<em class="err">${esc(pending.error)}</em>` : '';
    return `<div class="act"><a class="what" href="#/review/${r.number}"><span class="star">★</span><b>#${r.number}</b> ${esc(shortName(r.number) ?? r.title)}</a>
      ${ov.noting === `${r.number}@${r.head}` ? `<form class="note" data-note="${r.number}" data-head="${esc(r.head)}"><input name="note" placeholder="Note" value="${esc(ov.note)}"><button class="btn pri">✓ Approve</button><button type="button" class="btn hold" data-note-hold="${r.number}">‖ Hold</button></form>`
        : `<div class="acts"><button class="btn pri" data-approve="${r.number}"${busy || !row ? ' disabled' : ''}>✓ Approve ${esc(r.head.slice(0, 7))}</button><button class="btn" data-noting="${r.number}@${esc(r.head)}">✎ Note</button>${failed}</div>`}</div>`;
  }
  if (item.kind === 'prs') return `<div class="act"><a class="what" href="#/now"><span class="star">★</span><b>${item.prs.length}</b> pull requests need your approval</a><div class="acts"><a class="btn pri" href="#/now">Review ${item.prs.length}</a></div></div>`;
  if (item.kind === 'card') return `<div class="act"><a class="what" href="#/now"><span class="star">?</span>${esc(item.card.title)}</a><div class="acts"><a class="btn pri" href="#/now">${item.card.recommended ? esc(item.card.recommended) : 'Answer'}</a></div></div>`;
  return `<div class="act"><span class="what"><span class="star">!</span>${esc(item.ask.what)}</span><div class="acts">${item.ask.link ? `<a class="btn pri" href="${esc(item.ask.link)}" target="_blank" rel="noopener">${esc(item.ask.link_text ?? 'Open')}</a>` : ''}</div></div>`;
}
function actionCard() {
  const needs = state.model.needs;
  ov.page = Math.min(ov.page, Math.max(0, needs.length - 1));
  const pager = needs.length > 1 ? `<span class="pager">${needs.map((_, i) => `<i class="${i === ov.page ? 'on' : ''}" data-page="${i}"></i>`).join('')}<span>${ov.page + 1}/${needs.length}</span></span>` : '';
  return `<section class="actcard${needs.length ? '' : ' empty'}">${needItem(needs[ov.page])}${pager}</section>`;
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
    <div class="ring-row"><span class="trunk"></span><button class="ring" data-key style="--p:${pct(t)}%"><b>${pct(t)}<small>%</small></b></button>
      <span class="counts"><b>${t.landed}</b> landed <b>${t.queued}</b> queued <b class="${t.you ? 'you' : ''}">${t.you}</b> your tap</span><button class="btn ghost plan-btn" data-overlay="plan">⤢ Plan</button></div>
    ${machine('mac', 'Mac')}
    <div class="toolsband" data-part="tools"><span class="pn">Tools</span><span class="pls">${lights(tools)}</span></div>
    ${releaseTrunk()}
  </section>`;
}
function overview() {
  const m = state.model;
  const stop = m.now.blocker;
  return `<div class="cp">
    <div class="cp-top">${stop ? `<button class="stop" data-stop><i></i>Stopped since ${clock(stop.since)} ›</button>` : ''}${ov.only ? `<button class="only" data-only-clear>Only ${esc(ov.only.label)} ✕</button>` : ''}<span class="sp"></span><button class="btn ghost" data-overlay="plan">⤢ Plan</button></div>
    <div class="cp-grid">
      <div class="cp-left">${actionCard()}${tracks()}</div>
      <button class="gate" data-overlay="plan" title="${esc(m.now.release ? `${m.now.release.name}: ${m.now.release.state}` : 'Release 1')}"><span>R1</span></button>
      ${destination()}
    </div>
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
    text: () => `<p class="dim">Written ${m.plan_at ? agoWords(m.plan_at) : ''}</p><ol class="psteps">${planSteps(true)}</ol>`,
  }[ov.planTab]();
  return `<div class="overlay" data-close="1"><div class="sheet plan"><button class="btn close" data-close="1">Close</button>
    <nav class="ptabs">${tabs.map(([k, l]) => `<button class="${ov.planTab === k ? 'on' : ''}" data-ptab="${k}">${l}</button>`).join('')}</nav>${body}</div></div>`;
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
function stepCard(s) {
  const t = tally(s.prs);
  const ws = state.model.workers.filter((w) => w.step === s.id);
  return `<section class="stepcard tn-${s.tone}"><div class="kick">${TONE_GLYPH[s.tone] ?? ''} ${TONE_SAY[s.tone] ?? ''}</div><h3>${esc(s.title)}</h3>
    <dl class="facts"><dt>Where it is</dt><dd>${inline(s.state)}</dd><dt>What blocks it</dt><dd>${s.blocker ? inline(s.blocker) : 'Nothing'}</dd><dt>Next</dt><dd>${inline(s.next)}</dd></dl>
    ${s.prs.length ? `<div class="sc-counts"><b>${t.landed}</b> landed · <b>${t.queued}</b> queued · <b>${t.you}</b> your tap · ${t.total} in all</div><ul class="prlist">${[...s.prs].sort((a, b) => LIGHT_ORDER.indexOf(a.state) - LIGHT_ORDER.indexOf(b.state)).map((p) => `<li><a href="${prHref(p.number)}"><i class="lt ${p.state}"></i><b>#${p.number}</b><span>${esc(plainTitle(p.number))}</span><em>${STATE_WORD[p.state]}</em></a></li>`).join('')}</ul>` : ''}
    ${ws.length ? `<div class="wlist">${ws.map((w) => `<div class="wrow">${workerStrip(w)}<b>${esc(w.lane)}</b><span class="dim">${agoWords(w.at)}</span>${w.prs.map((n) => prLink(n)).join(' ')}</div>`).join('')}</div>` : ''}</section>`;
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
  if (!localStorage.getItem('drive-key-seen')) {
    localStorage.setItem('drive-key-seen', '1');
    state.overlay = 'key';
    render();
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
  if (e.target.closest('[data-unfold]')) { ov.unfold = !ov.unfold; return render(); }
  if (e.target.closest('[data-stop]')) { state.overlay = 'stop'; return render(); }
  if (e.target.closest('[data-key]')) { state.overlay = 'key'; return render(); }
  if (e.target.closest('[data-only-clear]')) return showOnly(null);
  const ptab = e.target.closest('[data-ptab]');
  if (ptab) { ov.planTab = ptab.dataset.ptab; return render(); }
  const page = e.target.closest('[data-page]');
  if (page) { ov.page = Number(page.dataset.page); ov.noting = null; return render(); }
  const noting = e.target.closest('[data-noting]');
  if (noting) { ov.noting = noting.dataset.noting; ov.note = ''; return render(); }
  const approve = e.target.closest('[data-approve]');
  if (approve) return decide(boardRow(approve.dataset.approve), 'approved', '', false);
  const hold = e.target.closest('[data-note-hold]');
  if (hold) return noteDecide(hold.closest('[data-note]'), 'rejected');
});
document.addEventListener('submit', (e) => {
  const form = e.target.closest('[data-note]');
  if (!form) return;
  e.preventDefault();
  noteDecide(form, 'approved');
});
document.addEventListener('input', (e) => {
  if (e.target.closest('[data-note]')) ov.note = e.target.value;
});
document.addEventListener('keydown', (e) => {
  if (route().screen !== 'overview' || e.target.closest('input, textarea') || e.metaKey || e.ctrlKey) return;
  const needs = state.model?.needs ?? [];
  if (e.key === 'Escape' && ov.only && !state.overlay) { showOnly(null); return; }
  if (e.key === 'ArrowRight' && needs.length > 1) { ov.page = (ov.page + 1) % needs.length; render(); }
  else if (e.key === 'ArrowLeft' && needs.length > 1) { ov.page = (ov.page + needs.length - 1) % needs.length; render(); }
  else if (e.key === 'p' && !state.overlay) { state.overlay = 'plan'; render(); }
  else if (e.key === 'Enter' && e.target.dataset?.track !== undefined) { ov.track = e.target.dataset.track; state.overlay = 'track'; render(); }
});
window.addEventListener('resize', () => { if (route().screen === 'overview' && state.model) mountOverview(); });
Object.assign(window, { showOnly, tally });
