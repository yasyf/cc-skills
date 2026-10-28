const TONE_GLYPH = { you: '★', failing: '✕', held: '⏸', moving: '▶', queued: '⧗', waiting: '○', done: '✓', later: '⚑' };
const TONE_SAY = { you: 'Needs you', failing: 'Tests failing', held: 'Held', moving: 'Being worked on', queued: 'Approved, waiting to land', waiting: 'Waiting its turn', done: 'Done', later: 'Later' };
const LIGHT_ORDER = ['landed', 'queued', 'doing', 'you', 'failing', 'held', 'waiting'];
const ov = { page: 0, planTab: 'steps', only: null, was: new Map(), scrub: 0, dragX: null, embedCard: null, sent: new Map() };
const PAST_HOURS = [1, 3, 6, 12, 24, 48, 96];

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
function setScrub(v) {
  const next = Math.max(-PAST_HOURS.length, Math.min(state.model.steps.length, v));
  if (next !== ov.scrub) { ov.scrub = next; render(); }
}
function pct(t) {
  return t.total ? Math.round((100 * t.landed) / t.total) : 0;
}
const DOT = { landed: 's-done', doing: 's-doing', you: 's-you', failing: 's-failing', held: 's-held', queued: 's-queued', waiting: 's-waiting' };
function dot(n) {
  const entry = driveEntry(n);
  const s = entry ? seen(entry) : prState(n);
  const pr = state.model.prs[n] ?? {};
  const fresh = s === 'landed' && pr.landed_at && Date.now() - new Date(pr.landed_at) < 3600 * 1000 ? ' recent' : '';
  const flash = s === 'landed' && ov.was.size && ov.was.get(Number(n)) && ov.was.get(Number(n)) !== 'landed' ? ' flash' : '';
  const dim = ov.only && !ov.only.numbers.has(Number(n)) ? ' out' : '';
  return `<a class="dot ${DOT[s] ?? 's-waiting'}${fresh}${flash}${dim}" href="${prHref(n)}" data-n="${n}" aria-label="#${n} ${esc(STATE_WORD[s])}"></a>`;
}
function byLight(list) {
  return [...list].sort((a, b) => LIGHT_ORDER.indexOf(seen(a)) - LIGHT_ORDER.indexOf(seen(b)) || a.number - b.number);
}
function cluster(name, list, key, extra = '') {
  const c = Math.min(13, Math.max(2, Math.ceil(Math.sqrt(list.length * 2.4))));
  return `<div class="cl${extra}"${key ? ` data-part="${esc(key)}"` : ''} style="--c:${c}"><div class="dots">${byLight(list).map((e) => dot(e.number)).join('')}</div><span class="pl">${esc(name)}</span></div>`;
}
function machine(home, title) {
  const m = state.model;
  const parts = m.parts.filter((p) => p.home === home);
  const list = m.drive.prs.filter((e) => parts.some((p) => p.key === e.part));
  const old = list.filter((e) => e.tree === m.tree_names[1]);
  const groups = parts.map((p) => [p, list.filter((e) => e.part === p.key && e.tree !== m.tree_names[1])]).filter(([, l]) => l.length).sort((a, b) => b[1].length - a[1].length);
  const p = pct(tally(list));
  return `<figure class="machine ${home}" data-zone="${home}" data-pct="${p}"><div class="shell"><svg class="ol" aria-hidden="true"></svg><span class="tag">${title} <em>${p}%</em></span>
    <div class="screen">${groups.map(([part, l]) => cluster(part.name, l, part.key)).join('')}${old.length ? cluster('Old, kept', old, '', ' kept') : ''}</div></div>${home === 'mac' ? '<div class="kb"></div>' : ''}</figure>`;
}
function landedSinceLook() {
  const look = Number(localStorage.getItem('drive-last-look') ?? 0);
  return look ? state.model.drive.prs.filter((e) => e.state === 'landed' && new Date(state.model.prs[e.number]?.landed_at ?? 0) > look) : [];
}
function destination() {
  const t = tally(state.model.drive.prs);
  const back = landedSinceLook();
  const tools = state.model.drive.prs.filter((e) => e.part === 'tools');
  return `<section class="hero"><div class="machines">${machine('cloud', 'The cloud')}
    <div class="link"><i class="stream a"></i><i class="stream b"></i><div class="gauge" style="--p:${pct(t)}"><b>${pct(t)}<small>%</small></b><span>of the end state landed</span></div>${back.length ? `<button class="plusn" data-back title="Landed since you last looked">+${back.length}</button>` : ''}</div>
    ${machine('mac', 'Your Mac')}</div>
    <div class="runway" data-part="tools"><span class="rl pl">Tools</span><div class="lights">${byLight(tools).map((e) => dot(e.number)).join('')}</div></div>
    <div class="foot"><p class="endline"><b>End state.</b> Mac and cloud both run the new code, chats follow live between them, and the old code stays until cutover.</p>
    <div class="legend"><span><i class="dot s-done"></i>landed</span><span><i class="dot s-queued"></i>approved</span><span><i class="dot s-doing"></i>moving now</span><span><i class="dot s-failing"></i>own tests failing</span><span><i class="dot s-waiting"></i>waiting</span><span><i class="dot s-held"></i>held</span><span><i class="dot s-you"></i>you</span></div></div>
  </section>`;
}
function replay() {
  if (!ov.replayOpen && !ov.scrub) return '<button class="replay-open" data-replay>Replay the last four days, or see the plan ahead ›</button>';
  const ticks = [...PAST_HOURS.map((h, i) => [-(i + 1), h >= 24 ? `${h / 24}d ago` : `${h}h ago`, '']).reverse(), [0, 'Now', ''], ...state.model.steps.map((s, i) => [i + 1, s.label, `If steps 1–${i + 1} land`])];
  return `<div class="replay"><span class="rl">Replay</span><div class="ticks" data-keep-scroll="replay">${ticks.map(([v, label, hint]) => `<button class="tk ${v < 0 ? 'past' : v > 0 ? 'fut' : 'now'}${v === ov.scrub ? ' on' : ''}" data-scrubto="${v}"${hint ? ` title="${esc(hint)}"` : ''}><i></i>${esc(label)}</button>`).join('')}</div></div>`;
}
const ONLY = {
  landed: ['landed', (e) => seen(e) === 'landed'],
  flight: ['in flight', (e) => seen(e) !== 'landed'],
  you: ['waiting on you', (e) => seen(e) === 'you'],
};
function onlyBy(key) {
  if (ov.only?.key === key) return showOnly(null);
  const [label, test] = ONLY[key];
  ov.only = { key, label, numbers: new Set(state.model.drive.prs.filter(test).map((e) => e.number)) };
  render();
}
function workerName(lane) {
  if (['root', 'main'].includes(lane)) return 'Coordinator';
  const m = state.model;
  const w = m.workers.find((x) => x.lane === lane);
  const step = m.steps.find((s) => s.id === w?.step);
  const peers = step ? m.workers.filter((x) => x.step === step.id).map((x) => x.lane) : [];
  if (step) return `${step.label} worker${peers.length > 1 ? ` ${peers.indexOf(lane) + 1}` : ''}`;
  const pr = (w?.prs ?? []).find((n) => driveEntry(n)?.state !== 'landed') ?? m.drive.prs.find((e) => e.owner === lane && e.state !== 'landed')?.number;
  if (pr) return `Worker on ${prTitle(pr)}`;
  return lane.replace(/^p\d+[a-z]?\d*-/, '').replace(/^r\d+-/, 'release-').replace(/-\d+$/, '').replace(/-/g, ' ').replace(/^./, (c) => c.toUpperCase());
}
const PART_WORD = { landed: 'landed', queued: 'approved', doing: 'moving', you: 'yours', failing: 'red', held: 'held', waiting: 'waiting' };
const HOME = { mac: 'Your Mac', cloud: 'The cloud', tools: 'Release and tools' };
function sheet(body, small = '') {
  return `<div class="overlay" data-close="1"><div class="sheet${small}"><button class="btn close" data-close="1">Close</button>${body}</div></div>`;
}
function namedWorkers(html) {
  const lanes = [...new Set(state.model.records.map((r) => r.lane))].sort((a, b) => b.length - a.length);
  if (!lanes.length) return html;
  return html.replace(new RegExp(`(?<![\\w-])(${lanes.join('|')})(?![\\w-])`, 'g'), (lane) => `<a href="#/worker/${encodeURIComponent(lane)}">${esc(workerName(lane))}</a>`);
}
function prFacts(n) {
  const m = state.model;
  const t = truth(n);
  const pr = m.prs[n] ?? {};
  const step = m.steps.find((s) => s.id === t.step) ?? m.steps.find((s) => s.untracked.includes(Number(n)));
  const part = m.parts.find((p) => p.key === t.part);
  const row = boardRow(n);
  const w = m.workers.find((x) => x.lane === t.owner);
  const stands = `${pr.landed_at ? `Landed ${clock(pr.landed_at)}` : STATE_WORD[t.state]}${t.base ? ` · stacked on #${t.base}${t.base_failing ? ', whose tests fail' : ''}` : ''}`;
  return `<dl class="facts pr-facts"><dt>Where it stands</dt><dd>${stands}${t.read_at ? ` <small class="dim">read ${agoWords(t.read_at)}</small>` : ''}</dd>
    ${truthFacts(n, Boolean(pr.landed_at))}
    <dt>Tests</dt><dd>${TEST_WORD[pr.test_state] ?? 'Not known'}${t.checks.length ? `: ${t.checks.map((c) => `<a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.name)} ↗</a>`).join(', ')}` : ''}</dd>
    <dt>Who is on it</dt><dd>${t.owner ? `<a href="#/worker/${encodeURIComponent(t.owner)}">${esc(workerName(t.owner))}</a>, ${w?.live ? 'working now' : t.word ? `last word ${agoWords(t.word.at)}` : 'no word from it on this pull request'}` : 'No worker named'}</dd>
    ${row?.change ? `<dt>What changes for a user</dt><dd>${inline(row.change)}</dd>` : ''}
    <dt>Part of the plan</dt><dd>${step ? `<a href="#/plan/${encodeURIComponent(step.id)}">${esc(step.label ?? step.title)}</a>` : 'Not in a part of the plan'}</dd>${part ? `<dt>Where it runs</dt><dd>${esc(part.name)}: ${esc(part.becomes)}</dd>` : ''}</dl>`;
}
function prSheet() {
  const m = state.model;
  const n = ov.pr;
  const e = driveEntry(n) ?? { number: n, state: prState(n) };
  const row = boardRow(n);
  const approve = e.state === 'you' && row && !/^conflicts/i.test(row.blocker ?? '') ? `<button class="btn" data-approve="${n}" data-head="${esc(row.head)}">✓ Approve ${esc(row.head.slice(0, 7))}</button>` : '';
  return sheet(`<div class="kick"><i class="lt ${e.state}"></i> ${STATE_WORD[e.state]}</div><h3>#${n} ${esc(prTitle(n))}</h3>
    ${prFacts(n)}
    <p class="acts">${approve}${m.prs[n] ? `<a class="btn${approve ? ' ghost' : ''}" href="#/review/${n}">Open the review ›</a>` : ''}<a class="btn ghost" href="https://github.com/${esc(m.repo)}/pull/${n}" target="_blank" rel="noopener">GitHub ↗</a></p>`, ' small');
}
function partSheet() {
  const m = state.model;
  const p = m.parts.find((x) => x.key === ov.part);
  const list = m.drive.prs.filter((e) => e.part === p.key);
  const t = tally(list);
  const open = list.filter((e) => seen(e) !== 'landed').sort((a, b) => LIGHT_ORDER.indexOf(seen(a)) - LIGHT_ORDER.indexOf(seen(b)));
  const landed = list.filter((e) => seen(e) === 'landed');
  const ws = m.workers.filter((w) => w.live && w.prs.some((n) => driveEntry(n)?.part === p.key));
  return sheet(`<div class="kick">${HOME[p.home] ?? ''} · ${pct(t)}% landed</div><h3>${esc(p.name)}</h3><p class="relnow">${esc(p.becomes)}</p>
    <div class="partbar">${LIGHT_ORDER.filter((k) => t[k]).map((k) => `<span class="pb ${k}" style="flex:${t[k]}" title="${t[k]} ${esc(STATE_WORD[k])}"><i class="lt ${k}"></i>${t[k]} <small>${PART_WORD[k]}</small></span>`).join('')}</div>
    ${ws.length ? `<h4 class="sc-h">Who is on it <span>${plural(ws.length, 'worker')} active</span></h4><ul class="wlist">${ws.map(lastWord).join('')}</ul>` : ''}
    ${open.length ? `<h4 class="sc-h">Still to land <span>${open.length}</span></h4><ul class="prlist">${open.map((p) => prRow(p)).join('')}</ul>` : ''}
    ${landed.length ? `<details data-id="part-landed-${esc(p.key)}"${state.open.has(`part-landed-${p.key}`) ? ' open' : ''}><summary class="sc-h">Landed <span>${landed.length}</span></summary><ul class="prlist">${landed.map((p) => prRow(p)).join('')}</ul></details>` : ''}`);
}
function sending(block) {
  const sent = ov.sent.get(block);
  return Boolean(sent && !sent.error);
}
function sentNote(block) {
  const sent = ov.sent.get(block);
  if (!sent) return '';
  return sent.error ? `<em class="nsent bad">Not recorded: ${esc(sent.error)}. Try again.</em>` : `<em class="nsent">${sent.recorded ? '✓ Recorded on your board' : 'Sending…'}</em>`;
}
async function answer(body) {
  if (sending(body.block)) return;
  ov.sent.set(body.block, {});
  render();
  const out = await fetch('/api/answer', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then((res) => res.json())
    .catch((failure) => ({ error: `could not reach the dashboard server (${failure.message})` }));
  ov.sent.set(body.block, out.error ? { error: out.error } : { recorded: true });
  if (out.error) return render();
  setTimeout(() => { ov.sent.delete(body.block); render(); }, body.kind === 'note' ? 4000 : 30000);
  const answer = { choice: { optionIds: body.options ?? [], other: body.other }, approval: { verdict: body.verdict, note: body.text }, input: { text: body.text } }[body.kind];
  tapped({ seq: out.seq, block: body.block, ...(answer ? { answer } : { noted: true }) });
}
function showOnly(label, numbers) {
  ov.only = numbers ? { label, numbers: new Set(numbers.map(Number)) } : null;
  if (route().screen !== 'overview') location.hash = '#/';
  render();
}
const SAID = { head: 'Pushed a new version', report: 'Reported', state: 'Posted its status', blocker: 'Reported a blocker', ask: 'Asked a question', review: 'Reviewed', go: 'Got the go to land', opened: 'Opened a pull request', landed: 'Landed', defect: 'Reported a tool problem', answer: 'Answered', decide: 'Decided', incident: 'Raised an incident', 'fix-live': 'Put a fix live' };
function lastWord(w) {
  const r = state.model.records.filter((x) => x.lane === w.lane).reduce((a, b) => (!a || new Date(b.at) > new Date(a.at) ? b : a), null);
  const prs = w.prs.slice(0, 2).map((n) => `<a href="${prHref(n)}">#${n} ${esc(prTitle(n))}</a>`).join(', ');
  return `<li><span class="ws"><span class="bx">${w.boxes.map((b) => `<i class="${b}"></i>`).join('')}</span></span><div><button class="linkish" data-worker="${esc(w.lane)}" title="${esc(w.lane)}">${esc(workerName(w.lane))}</button> <b>${r ? SAID[r.kind] ?? KIND_WORD[r.kind] ?? r.kind : 'At work'}</b>${prs ? ` on ${prs}` : ''}, ${agoWords(r?.at ?? w.at)}<small>${r ? `<a href="/record/${r.seq}" target="_blank" rel="noopener">its last word, #${r.seq} ↗</a>` : ''}</small></div></li>`;
}
function truth(n) {
  return driveEntry(n) ?? { number: Number(n), state: prState(n), owner: state.model.prs[n]?.lane, next: null, blocker: null, checks: [] };
}
function redWhy(n) {
  const checks = truth(n).checks.map((c) => c.name);
  return checks.length ? ` · ${esc(checks.join(', '))} failed` : '';
}
function prRow(p, extra = '') {
  const pr = state.model.prs[p.number] ?? {};
  const why = `${p.state === 'failing' ? redWhy(p.number) : ''}${pr.mergeable_state === 'dirty' ? ' · conflicts with its base' : ''}`;
  return `<li><a href="${prHref(p.number)}" data-pr="${p.number}"><i class="lt ${p.state}"></i><b>#${p.number}</b><span>${esc(prTitle(p.number))}</span><em>${STATE_WORD[p.state]}${why}</em></a>${extra}</li>`;
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
    return `<div class="tip-h"><i class="lt ${prState(n)}"></i><b>#${n}</b><span>${STATE_WORD[prState(n)]}</span></div><div>${esc(prTitle(n))}</div>
      <div class="dim">${[step?.label ?? step?.title, part?.name, e?.worker?.lane, pr.landed_at ? `landed ${clock(pr.landed_at)}` : ''].filter(Boolean).map(esc).join(' · ')}</div>`;
  }
  if (el.dataset.part) {
    const p = state.model.parts.find((x) => x.key === el.dataset.part);
    const t = tally(state.model.drive.prs.filter((e) => e.part === p.key));
    return `<div class="tip-h"><b>${esc(p.name)}</b><span>${pct(t)}%</span></div><div>${esc(p.becomes)}</div><div class="dim">${t.landed} landed · ${t.queued} queued · ${t.total - t.landed - t.queued} to go</div>`;
  }
  if (el.dataset.lane) {
    const w = state.model.workers.find((x) => x.lane === el.dataset.lane);
    return `<div class="tip-h"><b>${esc(workerName(w.lane))}</b><span>${agoWords(w.at)}</span></div>${w.prs.map((n) => `<div>#${n} ${esc(prTitle(n))}</div>`).join('')}`;
  }
  if (el.dataset.release) {
    const r = state.model.now.releases.find((x) => x.id === el.dataset.release);
    return `<div class="tip-h"><b>${esc(r.name)}</b></div><div>${inline(r.state)}</div><div class="dim">${r.parts.map((k) => esc(state.model.parts.find((p) => p.key === k)?.name ?? k)).join(' · ')}</div>`;
  }
  return '';
}
function highlight(numbers) {
  for (const cp of document.querySelectorAll('.ck')) {
    cp.classList.toggle('focus', Boolean(numbers));
    for (const el of cp.querySelectorAll('.dot.lit')) el.classList.remove('lit');
    if (numbers) for (const n of numbers) for (const el of cp.querySelectorAll(`.dot[data-n="${n}"]`)) el.classList.add('lit');
  }
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
  ov.was = new Map(state.model.drive.prs.map((e) => [e.number, e.state]));
  for (const fig of document.querySelectorAll('.ck .machine')) {
    const shell = fig.querySelector('.shell');
    const svg = shell.querySelector('svg');
    const W = shell.offsetWidth, H = shell.offsetHeight, r = 14, e = 1;
    const b = W > 500 ? 30 : 24;
    const d = fig.classList.contains('cloud')
      ? `M${e} ${H - e - r} L${e} ${b + r} Q${e} ${b} ${r} ${b} L${W * 0.42} ${b} A${W * 0.06} ${b * 0.62} 0 0 1 ${W * 0.54} ${b} A${W * 0.12} ${b - 2} 0 0 1 ${W * 0.78} ${b} A${W * 0.06} ${b * 0.7} 0 0 1 ${W * 0.9} ${b} L${W - r} ${b} Q${W - e} ${b} ${W - e} ${b + r} L${W - e} ${H - e - r} Q${W - e} ${H - e} ${W - r} ${H - e} L${r} ${H - e} Q${e} ${H - e} ${e} ${H - e - r} Z`
      : `M${e} ${r} Q${e} ${e} ${r} ${e} L${W - r} ${e} Q${W - e} ${e} ${W - e} ${r} L${W - e} ${H - r} Q${W - e} ${H - e} ${W - r} ${H - e} L${r} ${H - e} Q${e} ${H - e} ${e} ${H - r} Z`;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.innerHTML = `<path class="base" d="${d}"/><path class="prog" d="${d}" pathLength="100" stroke-dasharray="${fig.dataset.pct} 100"/>`;
  }
}
document.addEventListener('mouseover', (e) => {
  const tip = $('#tip');
  if (!tip) return;
  if (!e.target.closest('.ck')) { tip.hidden = true; return highlight(null); }
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
  if (!e.target.closest('.ck, .overlay, .pr-facts, #cardembed, .nw-needs, .pg')) return;
  if (e.metaKey || e.ctrlKey) return;
  const light = e.target.closest('.ck .dot[data-n]');
  if (light) { e.preventDefault(); return (location.hash = `#/review/${light.dataset.n}`); }
  const strip = e.target.closest('.ck .route');
  if (strip && !e.target.closest('.planlink, a')) return (location.hash = `#/plan/${e.target.closest('[data-release]')?.dataset.release ?? ''}`);
  const onlyEl = e.target.closest('[data-only]');
  if (onlyEl?.dataset.only === 'you') return (location.hash = '#/review');
  if (onlyEl) return onlyBy(onlyEl.dataset.only);
  const cardEl = e.target.closest('.deck .cta');
  if (cardEl && !e.target.closest('a, button, input, form, [data-page]')) return (location.hash = '#/review');
  const scrubTo = e.target.closest('[data-scrubto]');
  if (scrubTo) return setScrub(Number(scrubTo.dataset.scrubto));
  const partEl = e.target.closest('[data-part-open], .ck .pl');
  if (partEl) return (location.hash = '#/plan');
  const workerEl = e.target.closest('[data-worker]');
  if (workerEl) { e.preventDefault(); return (location.hash = `#/worker/${encodeURIComponent(workerEl.dataset.worker)}`); }
  const trackEl = e.target.closest('[data-track]');
  if (trackEl && !e.target.closest('a')) return (location.hash = `#/plan/${encodeURIComponent(trackEl.dataset.track)}`);
  if (e.target.closest('[data-plan]')) return (location.hash = '#/plan');
  if (e.target.closest('[data-desks]')) { e.preventDefault(); return (location.hash = '#/work'); }
  if (e.target.closest('[data-back]')) {
    ov.only = ov.only?.key === 'back' ? null : { key: 'back', label: 'landed since you last looked', numbers: new Set(landedSinceLook().map((x) => x.number)) };
    return render();
  }
  if (e.target.closest('[data-unfold]')) { ov.unfold = !ov.unfold; return render(); }
  if (e.target.closest('[data-jump]')) return (location.hash = '#/jump');
  if (e.target.closest('[data-stop]')) return (location.hash = '#/plan');
  if (e.target.closest('[data-only-clear]')) return showOnly(null);
  const rel = e.target.closest('[data-release]');
  if (rel) return (location.hash = '#/plan');
  if (e.target.closest('[data-scrub-clear]')) return setScrub(0);
  if (e.target.closest('[data-replay]')) { ov.replayOpen = true; return render(); }
  const ptab = e.target.closest('[data-ptab]');
  if (ptab) { ov.planTab = ptab.dataset.ptab; return render(); }
  const page = e.target.closest('[data-page]');
  if (page) { ov.page = Number(page.dataset.page); return render(); }
  const opening = e.target.closest('[data-embed-card]');
  if (opening) { ov.embedCard = opening.dataset.embedCard; return render(); }
  if (e.target.closest('[data-embed-close]')) { ov.embedCard = null; return render(); }
  const choose = e.target.closest('[data-choose]');
  if (choose) return answer({ kind: 'choice', block: choose.dataset.choice, options: [choose.dataset.choose] });
  const approve = e.target.closest('[data-approve]');
  if (approve) {
    const row = boardRow(approve.dataset.approve);
    return row?.head === approve.dataset.head ? decide(row, 'approved', '', false) : render();
  }
});
document.addEventListener('keydown', (e) => {
  if (route().screen !== 'overview' || e.target.closest('input, textarea') || e.metaKey || e.ctrlKey) return;
  const needs = state.model?.needs ?? [];
  if (e.key === 'Escape' && ov.only && !state.overlay) { showOnly(null); return; }
  const paging = e.target.closest('.cta') && needs.length > 1;
  if (paging && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) {
    ov.page = (ov.page + (e.key === 'ArrowRight' ? 1 : needs.length - 1)) % needs.length;
    render();
    $('.cta')?.focus();
  }
  else if (e.key === 'ArrowRight' && !state.overlay) setScrub(ov.scrub + 1);
  else if (e.key === 'ArrowLeft' && !state.overlay) setScrub(ov.scrub - 1);
  else if (e.key === 'n' && ov.scrub) setScrub(0);
  else if (e.key === 'p' && !state.overlay) location.hash = '#/plan';
  else if (e.key === 'Enter' && e.target.dataset?.track !== undefined) location.hash = `#/plan/${encodeURIComponent(e.target.dataset.track)}`;
});
window.addEventListener('resize', () => { if (route().screen === 'overview' && state.model) mountOverview(); });
Object.assign(window, { showOnly, tally });
document.addEventListener('pointerdown', (e) => {
  if (!e.target.closest('.ck .machine, .ck .gauge')) return;
  ov.dragX = e.clientX;
  ov.dragFrom = ov.scrub;
});
document.addEventListener('pointermove', (e) => {
  if (ov.dragX === null) return;
  setScrub(ov.dragFrom + Math.round((e.clientX - ov.dragX) / 48));
});
document.addEventListener('pointerup', () => { ov.dragX = null; });
document.addEventListener('pointercancel', () => { ov.dragX = null; });
function settleLook() {
  const hidden = Number(localStorage.getItem('drive-hidden-at') ?? 0);
  if (hidden && Date.now() - hidden > 5 * 60 * 1000) localStorage.setItem('drive-last-look', String(hidden));
}
settleLook();
document.addEventListener('visibilitychange', () => {
  if (document.hidden) return localStorage.setItem('drive-hidden-at', String(Date.now()));
  settleLook();
  if (state.model) render();
});
window.addEventListener('pagehide', () => localStorage.setItem('drive-hidden-at', String(Date.now())));
