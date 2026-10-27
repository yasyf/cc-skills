const TONE_GLYPH = { you: '★', failing: '✕', held: '⏸', moving: '▶', queued: '⧗', waiting: '○', done: '✓', later: '⚑' };
const TONE_SAY = { you: 'Needs you', failing: 'Tests failing', held: 'Held', moving: 'Being worked on', queued: 'Approved, waiting to land', waiting: 'Waiting its turn', done: 'Done', later: 'Later' };
const LIGHT_ORDER = ['landed', 'queued', 'doing', 'you', 'failing', 'held', 'waiting'];
const ATTENTION = ['failing', 'you', 'held', 'doing', 'queued', 'waiting', 'landed'];
const ov = { page: 0, noting: null, note: '', planTab: 'steps', only: null, was: new Map(), scrub: 0, dragX: null, drafts: {}, embedCard: null, sent: new Map() };
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
function lastHour() {
  const m = state.model;
  return m.drive.prs.filter((e) => e.state === 'landed' && Date.now() - new Date(m.prs[e.number]?.landed_at ?? 0) < 3600 * 1000);
}
function brokeTonight() {
  const recs = state.model.records.filter((r) => ['incident', 'fix-live'].includes(r.kind) && Date.now() - new Date(r.at) < 12 * 3600 * 1000).sort((a, b) => new Date(a.at) - new Date(b.at));
  const last = recs[recs.length - 1];
  if (!last) return state.model.records.length ? `no outage since ${clock(state.model.records.reduce((a, b) => (new Date(b.at) < new Date(a.at) ? b : a)).at)}` : 'no outage';
  if (last.kind === 'incident') return `<b class="bad">${esc(firstSentences(last.text, 70))}</b>, open since ${clock(last.at)}`;
  const name = last.text.match(/^FIX-LIVE\s+([^:]+):/)?.[1] ?? firstSentences(last.text, 50);
  return `${esc(name)}, fixed ${clock(last.at)}`;
}
function releaseWord(rels, i) {
  const r = rels[i];
  const held = rels.find((x) => x.word === 'held');
  return {
    out: ['out', 'Out'], held: ['held', 'Held'], blocked: ['blocked', 'Blocked'], now: ['next', 'Now'],
    after: ['', `After ${rels[i - 1]?.id}`], unreachable: ['unreach', `Waits on ${held?.id}`],
  }[r.word];
}
function clause(text) {
  const t = String(text ?? '');
  const i = t.search(/:\s/);
  return i > 0 ? [t.slice(0, i), t.slice(i + 1).trim()] : [t, ''];
}
function minutesPast(when) {
  const hm = /^(\d{1,2}):(\d{2})\s*([AP]M)(?:\s+(\w{3} \d{1,2}))?/i.exec(String(when ?? '').trim());
  if (!hm) return null;
  const now = new Date(new Date().toLocaleString('en-US', { timeZone: PACIFIC }));
  const late = now.getHours() * 60 + now.getMinutes() - ((Number(hm[1]) % 12) + (/p/i.test(hm[3]) ? 12 : 0)) * 60 - Number(hm[2]);
  if (!hm[4]) return late < 0 ? late + 24 * 60 : late;
  const day = new Date(`${hm[4]} ${now.getFullYear()}`);
  return Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()) - day) / 60000) + late;
}
function planRows() {
  const md = state.model.releases.markdown ?? '';
  const sec = md.split(/^## /m).find((x) => /^The releases at a glance/.test(x)) ?? '';
  return sec.split('\n').filter((l) => /^\|\s*\d+\s*\|/.test(l)).map((l) => l.split('|').slice(1, -1).map((c) => c.trim()));
}
function tonight() {
  const m = state.model;
  const t = tally(m.drive.prs.filter((e) => e.tree === m.tree_names[0]));
  const rels = m.now.releases ?? [];
  const tonightRels = rels.filter((r) => r.tonight);
  const out = tonightRels.filter((r) => RELEASED.test(r.state)).length;
  const at = rels.findIndex((r) => !RELEASED.test(r.state));
  const next = rels[at];
  const word = next ? releaseWord(rels, at) : ['out', 'Out'];
  const stop = rels.find((r) => r.word === 'held');
  const verdict = !next ? ['ok', 'All released'] : stop ? ['bad', `Tonight stops at ${stop.id}`] : word[0] === 'blocked' ? ['bad', `Blocked at ${next.id}`] : ['', `${next.id} now`];
  const g = m.now.goal ?? {};
  const fact = (f, okWord) => {
    if (!f) return '<span class="warn">not checked yet</span>';
    const [head, why] = clause(f.state);
    const age = minutesPast(f.at);
    if (age !== null && age > 30) return `<span class="warn">not rechecked since ${esc(f.at.replace(/ Oct \d+$/, ''))}</span>`;
    return `<em class="${okWord.test(head) && !/not/i.test(head) ? 'ok' : 'warn'}">${esc(head)}</em> <small title="${esc(why)}">${esc(firstSentences(why, 90))}</small>`;
  };
  const [flagWord] = clause(g.enabled?.state ?? 'Not checked yet');
  const target = stop ?? next;
  const rail = rels.map((r, i) => `<button class="mr ${releaseWord(rels, i)[0]}" data-release="${esc(r.id)}" aria-label="${esc(r.id)} ${esc(r.name)}: ${esc(releaseWord(rels, i)[1])}"><i></i><span>${esc(r.id)}</span></button>`).join('');
  return `<section class="tonight"><button class="verdict ${verdict[0]}"${target ? ` data-release="${esc(target.id)}"` : ''}>${esc(verdict[1])}${stop ? ` <small>${esc(clause(stop.state)[0].split(/\.\s/)[0])}</small>` : ''}</button><span class="minirail">${rail}</span>
    <span class="tn-facts"><span class="ok" title="Pull requests in the new app's code that have merged, of all the drive's new-app pull requests"><b>${t.landed}<small>/${t.total}</small></b>new-app changes merged</span><span><b>${out}<small>/${tonightRels.length}</small></b>released tonight</span><span title="${esc(g.enabled?.state ?? '')}"><b>${esc(flagWord)}</b>on the new Mac build${(minutesPast(g.enabled?.at) ?? 0) > 60 ? ` <span class="warn">as of ${esc(g.enabled.at.replace(/ Oct \d+$/, ''))}</span>` : ''}</span><a class="tn-oldapp" title="${esc(g.old_app?.state ?? '')}"${/#(\d{6})/.test(g.old_app?.source ?? '') ? ` href="/record/${/#(\d{6})/.exec(g.old_app.source)[1]}" target="_blank" rel="noopener"` : ''}>Everyone else: ${fact(g.old_app, /unchanged|kept|proven/i)}</a></span></section>`;
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
    <div class="link"><i class="stream a"></i><i class="stream b"></i><button class="gauge" data-key style="--p:${pct(t)}"><b>${pct(t)}<small>%</small></b><span>of the end state landed</span></button>${back.length ? `<button class="plusn" data-back title="Landed since you last looked">+${back.length}</button>` : ''}</div>
    ${machine('mac', 'Your Mac')}</div>
    <div class="runway" data-part="tools"><span class="rl pl">Tools</span><div class="lights">${byLight(tools).map((e) => dot(e.number)).join('')}</div></div>
    ${replay()}
    <div class="foot"><p class="endline"><b>End state.</b> Mac and cloud both run the new code, chats follow live between them, and the old code stays until cutover.</p>
    <div class="legend"><span><i class="dot s-done"></i>landed</span><span><i class="dot s-queued"></i>approved</span><span><i class="dot s-doing"></i>moving now</span><span><i class="dot s-failing"></i>own tests failing</span><span><i class="dot s-waiting"></i>waiting</span><span><i class="dot s-you"></i>you</span></div></div>
  </section>`;
}
function replay() {
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
function strip(w) {
  return `<button class="wst" data-worker="${esc(w.lane)}" data-lane="${esc(w.lane)}"><span class="boxes">${w.boxes.map((b) => `<i class="${b}"></i>`).join('')}</span></button>`;
}
function track(s) {
  const m = state.model;
  const t = tally(s.prs);
  const ws = m.workers.filter((w) => w.live && w.step === s.id);
  const tone = ov.scrub > 0 && m.steps.indexOf(s) < ov.scrub ? 'done' : s.tone;
  return `<div class="trk tn-${tone}" data-track="${esc(s.id)}" tabindex="0"><span class="no">${m.steps.indexOf(s) + 1}</span><span class="nm">${esc(s.label)}</span>
    <span class="tdots">${byLight(s.prs).map((e) => dot(e.number)).join('')}${s.untracked.map(dot).join('')}</span>
    <span class="tws">${ws.slice(0, 2).map(strip).join('')}${ws.length > 2 ? `<em>+${ws.length - 2}</em>` : ''}${ws.length ? `<button class="badge" data-worker="${esc(ws[0].lane)}" aria-label="${esc(ws[0].lane)}'s last hour">▶${ws.length}</button>` : ''}</span>
    <span class="ct" title="${t.landed} of ${plural(t.total, 'pull request')} landed${s.untracked.length ? `; ${s.untracked.length} more the merge ledger does not track yet` : ''}">${t.landed}/${t.total}</span><span class="g" title="${TONE_SAY[tone] ?? ''}" aria-label="${TONE_SAY[tone] ?? ''}">${TONE_GLYPH[tone] ?? ''}</span><i class="into"></i></div>`;
}
function tracks() {
  const m = state.model;
  const shown = m.steps.filter((s) => ov.unfold || !['done', 'later'].includes(s.tone));
  const done = m.steps.filter((s) => s.tone === 'done').length;
  const later = m.steps.filter((s) => s.tone === 'later').length;
  const loose = m.drive.prs.filter((e) => !e.step && e.state !== 'landed');
  const looseWs = m.workers.filter((w) => w.live && !w.step);
  return `<section class="tracks"><div class="th"><b>The plan</b><span>each line is a step; its lights run into the release gate</span><button class="linkish" data-plan>Open the plan ⤢</button></div>
    ${shown.map(track).join('')}
    ${loose.length ? `<div class="trk loose" data-track="" tabindex="0"><span class="no">┄</span><span class="nm">Unplanned</span><span class="tdots">${byLight(loose).map((e) => dot(e.number)).join('')}</span><span class="tws">${looseWs.slice(0, 2).map(strip).join('')}${looseWs.length ? `<b class="badge">▶${looseWs.length}</b>` : ''}</span><span class="ct">${loose.length}</span><span class="g"></span><i class="into"></i></div>` : ''}
    ${done || later ? `<button class="fold" data-unfold>${ov.unfold ? 'Fold done and later steps' : `${done ? `✓ ${done} done` : ''}${done && later ? ' · ' : ''}${later ? `⚑ ${later} later` : ''}`}</button>` : ''}</section>`;
}
function gateTone(r) {
  if (!r) return ['done', 'all out'];
  const waits = [...r.state.matchAll(/#(\d{5})\b/g)].map((x) => prState(x[1]));
  if (state.model.now.blocker || /\b(blocked|stopped|rolled back)\b/i.test(r.state)) return ['bad', 'blocked'];
  if (waits.includes('failing')) return ['bad', 'waits on red'];
  if (waits.includes('you')) return ['you', 'needs you'];
  if (/\b(live|cutting|rolling|running|passed|complete)\b/i.test(r.state)) return ['ok', 'moving'];
  return ['wait', 'waiting'];
}
function gate() {
  const rels = state.model.now.releases ?? [];
  const r = rels.find((x) => !RELEASED.test(x.state));
  const open = r ? [...new Set([...r.state.matchAll(/#(\d{5})\b/g)].map((x) => Number(x[1])))].filter((n) => prState(n) !== 'landed') : [];
  const [tone, word] = open.length && gateTone(r)[0] === 'ok' ? ['stop', 'part out'] : gateTone(r);
  return `<div class="gate g-${tone}"><button class="gb"${r ? ` data-release="${esc(r.id)}"` : ''} aria-label="${r ? `${esc(r.id)} ${esc(r.name)}: ${word}` : 'Every release is out'}"><b>${r ? esc(r.id) : '⚑'}</b><small>${word}</small></button>${open.length ? `<span class="gw"><i class="bar"></i><small>stopped: waits on</small>${open.map(dot).join('')}</span>` : ''}<i class="trunk"></i></div>`;
}
function dock() {
  const need = state.model.needs.length;
  return `<nav class="dock">${need ? cta(state.model.needs[ov.page]) : '<span class="dcalm">✓ Nothing needs you</span>'}<span class="dbtns"><button class="dbtn" data-plan>⤢ Plan</button><button class="dbtn" data-jump>⌕ Jump</button></span></nav>`;
}
function cta(item) {
  const pager = `<span class="nx">${state.model.needs.length > 1 ? `<button class="pg" data-page="${(ov.page + 1) % state.model.needs.length}" aria-label="Next item">${ov.page + 1} of ${state.model.needs.length} ›</button><span class="pips">${state.model.needs.map((_, i) => `<i data-page="${i}"${i === ov.page ? ' class="on"' : ''}></i>`).join('')}</span>` : ''}<a class="all" href="#/now">All in Needs you ›</a></span>`;
  const shell = (icon, kick, head, meta, acts) => `<div class="cta" tabindex="-1"><span class="ic">${icon}</span><span class="tx"><small>${kick}</small><b>${head}</b>${meta ? `<span>${meta}</span>` : ''}<span class="acts">${acts}</span></span>${pager}</div>`;
  const meta = item.why ? `${esc(item.why)}${item.source?.href ? ` · <a href="${esc(item.source.href)}" target="_blank" rel="noopener">${esc(item.source.label)} ↗</a>` : ''}` : '';
  if (item.kind === 'prs' && item.prs.length === 1) {
    const r = item.prs[0];
    const busy = pending.number === r.number && !pending.error;
    const failed = pending.number === r.number && pending.error ? ` <em>${esc(pending.error)}</em>` : '';
    const acts = ov.noting === `${r.number}@${r.head}`
      ? `<form class="note" data-note="${r.number}" data-head="${esc(r.head)}"><input name="note" placeholder="Note" value="${esc(ov.note)}"><button class="btn">✓ Approve</button><button type="button" class="btn ghost" data-note-hold="${r.number}">‖ Hold</button></form>`
      : `<button class="btn" data-approve="${r.number}" data-head="${esc(r.head)}"${busy || !boardRow(r.number) ? ' disabled' : ''}>${busy ? 'Sending…' : `✓ Approve ${esc(r.head.slice(0, 7))}`}</button><button class="btn ghost" data-noting="${r.number}@${esc(r.head)}">✎ Note</button><a class="btn ghost" href="#/review/${r.number}">Open ›</a>${failed}`;
    const stuck = r.blocker && !/^nothing/i.test(r.blocker) ? `<span class="stuck">⚠ ${esc(firstSentences(r.blocker, 80))}</span>` : '';
    return shell('★', stuck ? 'Your tap, then it still waits' : 'Tap to merge', `#${r.number} ${esc(shortName(r.number) ?? r.title)}`, `${stuck}${meta}`, acts);
  }
  if (item.kind === 'prs') return shell('★', `${item.prs.length} pull requests need your approval`, esc(item.group ?? 'Review them together'), meta, `<a class="btn" href="#/now">Review ${item.prs.length} ›</a>`);
  if (item.kind === 'card') {
    const c = item.card;
    const options = [...c.options].sort((a, b) => b.recommended - a.recommended);
    const block = c.choice ?? c.id;
    const busy = sending(block) ? ' disabled' : '';
    const acts = `${options.map((o) => `<button class="btn${o.recommended ? '' : ' ghost'}" data-choose="${esc(o.id)}" data-card="${esc(c.id)}" data-choice="${esc(c.choice)}"${busy}>${o.recommended ? '★ ' : ''}${esc(o.label.replace(/\s*\(Recommended\)\s*$/i, ''))}</button>`).join('')}<form class="note" data-answer="${esc(c.id)}" data-choice="${esc(c.choice ?? '')}"><input name="text" data-draft="${esc(c.id)}" value="${esc(ov.drafts[c.id] ?? '')}" placeholder="${c.choice ? 'Other answer or a note' : 'Your note'}"><button class="btn ghost"${busy}>Send</button></form>${sentNote(block)}`;
    return shell('?', 'A question for you', esc(c.title), meta, acts);
  }
  return shell('!', 'Reminder', esc(item.ask.what), meta, item.ask.link ? `<a class="btn" href="${esc(item.ask.link)}" target="_blank" rel="noopener">${esc(item.ask.link_text ?? 'Open')} ↗</a>` : `<a class="btn" href="#/now">Open in Needs you ›</a>`);
}
function deck() {
  const m = state.model;
  const t = tally(m.drive.prs);
  const hour = lastHour().length;
  const need = needsTotal();
  ov.page = Math.min(ov.page, Math.max(0, m.needs.length - 1));
  return `<div class="deck"><div class="readouts">
    <button class="ro ro-done" title="Every pull request of the drive that has merged: new app, old app and shared code" data-only="landed" aria-pressed="${ov.only?.key === 'landed'}"><b>${t.landed}</b><span><i></i>landed</span><em>${hour ? `+${hour} in the last hour` : 'none in the last hour'}</em></button>
    <button class="ro ro-fly" data-only="flight" aria-pressed="${ov.only?.key === 'flight'}"><b>${t.total - t.landed}</b><span><i></i>in flight</span><em>${t.doing} moving now · ${t.queued} approved</em></button>
    <button class="ro ro-you${need ? '' : ' zero'}" data-only="you" aria-pressed="${ov.only?.key === 'you'}"><b>${need}</b><span><i></i>need${need === 1 ? 's' : ''} you</span><em>${need ? 'tap to see them all' : 'nothing to approve'}</em></button></div>
    ${m.needs.length ? cta(m.needs[ov.page]) : `<a class="calm" href="#/review"><span class="ic">✓</span><span><b>Nothing needs you right now</b><span>Approvals and questions land here with their buttons. Review ›</span></span></a>`}</div>`;
}
function routeStrip() {
  const m = state.model;
  const rels = m.now.releases ?? [];
  const at = rels.findIndex((r) => !RELEASED.test(r.state));
  const cur = rels[at];
  const rel = (r, i) => `<span class="wp rel ${releaseWord(rels, i)[0]}${i === at ? ' here' : ''}" data-release="${esc(r.id)}" title="${esc(r.id)} ${esc(r.name)}: ${esc(r.state)}"><i class="n"></i><span class="lb">${esc(r.id)}</span><span class="ct">${esc(releaseWord(rels, i)[1])}</span></span>`;
  return `<section class="route"><div class="track"><span class="rk">Releases</span>${rels.map(rel).join('')}<span class="wp end"><i class="n"></i><span class="lb">Cutover</span></span></div>
    <div class="route-now">${cur ? `<b>${esc(cur.id)} ${esc(cur.name)}:</b> ${inline(cur.state)}` : 'Every release is out'}</div>
    <button class="planlink" data-plan="releases">Release plan${m.releases.updated ? `<small>revised ${clock(m.releases.updated)}</small>` : ''}<span>Open ›</span></button></section>`;
}
function workerTile(w, title) {
  const m = state.model;
  const r = m.records.filter((x) => x.lane === w.lane).reduce((a, b) => (!a || b.seq > a.seq ? b : a), null);
  const pr = w.prs.find((n) => driveEntry(n)?.state !== 'landed') ?? w.prs[0];
  const where = m.steps.find((s) => s.id === (w.step ?? driveEntry(pr)?.step))?.label ?? m.parts.find((p) => p.key === driveEntry(pr)?.part)?.name ?? 'Other work';
  const first = w.boxes.findIndex(Boolean);
  const st = ['blocker', 'defect', 'incident'].includes(r?.kind) ? 'blocked' : r?.kind === 'ask' ? 'asking' : 'live';
  const doing = `${r ? SAID[r.kind] ?? 'Working' : 'Working'}${pr ? ` on ${shortName(pr) ?? plainTitle(pr)}` : ''}`;
  return `<a class="wt st-${st}" href="#/workers" data-lane="${esc(w.lane)}" data-worker="${esc(w.lane)}"><span class="wt-h"><i class="lt"></i><b>${esc(title ?? where)}</b><time>${agoWords(w.at)}</time></span><span class="wt-on"><span>${esc(doing)}</span></span><span class="wt-for">on it ${first < 0 ? 'a few' : (w.boxes.length - first) * 5} min</span></a>`;
}
function deskName(lane) {
  return ['root', 'main'].includes(lane) ? 'Coordinator' : lane;
}
function desksTile() {
  const ds = state.model.desks;
  if (!ds.length) return '';
  return `<a class="wt desks" href="#/workers" data-desks><span class="wt-h"><i class="lt"></i><b>Coordinators</b><time>${agoWords(ds[0].at)}</time></span><span class="wt-on">${ds.map((d) => `<i${d.live ? ' class="on"' : ''} title="${esc(deskName(d.lane))}"></i>`).join('')}</span><span class="wt-for">${ds.filter((d) => d.live).length} of ${plural(ds.length, 'desk')} active</span></a>`;
}
function crew() {
  const order = (w) => state.model.steps.findIndex((s) => s.id === w.step) >>> 0;
  const live = state.model.workers.filter((w) => w.live && w.prs.length).sort((a, b) => order(a) - order(b) || a.lane.localeCompare(b.lane));
  if (!live.length && !state.model.desks.length) return '';
  return `<section class="crew"><div class="crew-h"><b>Working now</b><span>${plural(live.length, 'worker')} active</span><a href="#/workers">all workers ›</a></div><div class="strip" data-keep-scroll="crew">${desksTile()}${live.map((w) => workerTile(w)).join('')}</div></section>`;
}
function desksSheet() {
  const m = state.model;
  const last = (lane) => m.records.filter((x) => x.lane === lane).reduce((a, b) => (!a || b.seq > a.seq ? b : a), null);
  return sheet(`<div class="kick">Coordinators · the desks that land, grade and report</div><h3>${plural(m.desks.length, 'desk')} in the last hour</h3>
    <ul class="dlist">${m.desks.map((d) => {
      const r = last(d.lane);
      return `<li><button class="linkish" data-worker="${esc(d.lane)}">${esc(deskName(d.lane))}</button><span class="bx">${d.boxes.map((b) => `<i class="${b}"></i>`).join('')}</span><span class="dsaid">${r ? `<b>${SAID[r.kind] ?? KIND_WORD[r.kind] ?? r.kind}</b> ${inline(firstSentences(r.text, 110))}` : ''}</span><time>${agoWords(d.at)}</time></li>`;
    }).join('')}</ul>`);
}
function answer(key, keep = () => true) {
  const j = state.model.journeys[key];
  const who = (w) => !w ? '' : w === 'you' ? '<b>You</b>' : `<button class="linkish" data-worker="${esc(w)}">${esc(w)}</button>`;
  const src = (x) => x ? ` · <a href="${esc(x.href)}"${x.href.startsWith('#') ? '' : ' target="_blank" rel="noopener"'}>${esc(x.label)}</a>` : '';
  const lines = j.lines.filter(keep);
  return `<ul class="answer">${lines.map((l) => `<li><span class="who">${who(l.who)}</span><span class="what">${inline(l.text)}${l.next ? `<small>${inline(l.next)}</small>` : ''}</span><em>${l.since ? `last word ${agoWords(l.since)}` : ''}${src(l.source)}</em></li>`).join('')}</ul>${lines.length ? '' : '<p class="dim">Nothing.</p>'}${j.missing.length ? `<p class="dim">Not known yet: ${j.missing.map(inline).join('; ')}</p>` : ''}`;
}
function waitingOn() {
  return `<section class="waiting"><h4>Waiting on</h4>${answer('waiting')}</section>`;
}
function releaseTrunk() {
  const rels = state.model.now.releases ?? [];
  if (!rels.length) return '';
  const next = rels.findIndex((r) => !RELEASED.test(r.state));
  return `<div class="rtrunk">${rels.map((r, i) => `<span class="rd ${i < next || next < 0 ? 'done' : i === next ? 'next' : ''}" data-release="${esc(r.id)}" title="${esc(r.name)}: ${esc(r.state)}"><i></i>${esc(r.id)}</span>`).join('<span class="rl"></span>')}<span class="rl"></span><span class="rd flag">⚑</span></div>`;
}
function overview() {
  const m = state.model;
  const stop = m.now.blocker;
  return `<div class="ck ov">
    ${stop ? `<button class="stopband" data-stop><i>■</i><b>Stopped since ${clock(stop.since)}</b><span>${esc(stop.text)}</span><em>›</em></button>` : ''}
    ${ov.only || ov.scrub ? `<div class="cp-top">${ov.only ? `<button class="only" data-only-clear>Only ${esc(ov.only.label)} ✕</button>` : ''}${scrubChip()}</div>` : ''}
    ${tonight()}
    <div class="body"><div class="left">${deck()}${tracks()}${waitingOn()}</div>${gate()}${destination()}</div>
    ${routeStrip()}${crew()}${dock()}
    <div id="tip" class="tip" hidden></div>
  </div>
  ${state.overlay === 'track' ? trackSheet() : ''}
  ${state.overlay === 'stop' && stop ? stopSheet() : ''}
  ${state.overlay === 'key' ? keySheet() : ''}
  ${state.overlay === 'plan' ? planSheet() : ''}
  ${state.overlay === 'release' ? releaseSheet() : ''}
  ${state.overlay === 'pr' ? prSheet() : ''}
  ${state.overlay === 'part' ? partSheet() : ''}
  ${state.overlay === 'worker' ? workerSheet() : ''}
  ${state.overlay === 'desks' ? desksSheet() : ''}`;
}
const PART_WORD = { landed: 'landed', queued: 'approved', doing: 'moving', you: 'yours', failing: 'red', held: 'held', waiting: 'waiting' };
const HOME = { mac: 'Your Mac', cloud: 'The cloud', tools: 'Release and tools' };
function sheet(body, small = '') {
  return `<div class="overlay" data-close="1"><div class="sheet${small}"><button class="btn close" data-close="1">Close</button>${body}</div></div>`;
}
function prFacts(n) {
  const m = state.model;
  const t = truth(n);
  const pr = m.prs[n] ?? {};
  const step = m.steps.find((s) => s.id === t.step) ?? m.steps.find((s) => s.untracked.includes(Number(n)));
  const part = m.parts.find((p) => p.key === t.part);
  const row = boardRow(n);
  const w = m.workers.find((x) => x.lane === t.owner);
  const word = t.word;
  const stands = `${pr.landed_at ? `Landed ${clock(pr.landed_at)}` : STATE_WORD[t.state]}${t.base ? ` · stacked on #${t.base}${t.base_failing ? ', whose tests fail' : ''}` : ''}`;
  return `<dl class="facts pr-facts"><dt>Where it stands</dt><dd>${stands}${t.head ? ` <small class="dim">at ${esc(t.head)}${t.read_at ? `, read ${agoWords(t.read_at)}` : ''}</small>` : ''}</dd>
    ${t.blocker ? `<dt>What blocks it</dt><dd class="${/^GitHub has not/.test(t.blocker) ? 'warn' : 'bad'}">${inline(t.blocker)}</dd>` : ''}
    ${t.checks.length ? `<dt>Which check failed</dt><dd>${t.checks.map((c) => `<a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.name)} ↗</a>`).join(', ')}</dd>` : ''}
    ${t.next ? `<dt>What happens next</dt><dd>${inline(t.next)}</dd>` : ''}
    <dt>Who is on it</dt><dd>${t.owner ? `<button class="linkish" data-worker="${esc(t.owner)}">${esc(t.owner)}</button>, ${w?.live ? 'working now' : t.word ? `last word ${agoWords(t.word.at)}` : 'no word from it on this pull request'}` : 'No worker named'}</dd>
    ${word ? `<dt>Its latest word on this</dt><dd>${said(word, 260)} <small class="dim">${clock(word.at)} · <a href="/record/${word.seq}" target="_blank" rel="noopener">record ↗</a></small></dd>` : ''}
    ${row?.change ? `<dt>What changes for a user</dt><dd>${inline(row.change)}</dd>` : ''}
    <dt>Part</dt><dd>${part ? `<button class="linkish" data-part-open="${esc(part.key)}">${esc(part.name)}</button>: ${esc(part.becomes)}` : 'Not in a part of the end state'}</dd>
    <dt>Plan step</dt><dd>${step ? `<button class="linkish" data-track="${esc(step.id)}">${esc(step.label ?? step.title)}</button>` : 'No plan step names it'}</dd></dl>`;
}
function prSheet() {
  const m = state.model;
  const n = ov.pr;
  const e = driveEntry(n) ?? { number: n, state: prState(n) };
  const row = boardRow(n);
  const approve = e.state === 'you' && row && !/^conflicts/i.test(row.blocker ?? '') ? `<button class="btn" data-approve="${n}" data-head="${esc(row.head)}">✓ Approve ${esc(row.head.slice(0, 7))}</button>` : '';
  return sheet(`<div class="kick"><i class="lt ${e.state}"></i> ${STATE_WORD[e.state]}</div><h3>#${n} ${esc(shortName(n) ?? plainTitle(n))}</h3>
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
function workerSheet() {
  const m = state.model;
  const w = m.workers.find((x) => x.lane === ov.lane) ?? m.desks.find((x) => x.lane === ov.lane);
  if (!w) return sheet(`<div class="kick">Worker</div><h3>${esc(ov.lane)}</h3><p class="relnow">No word from this worker in the last hour.</p>`, ' small');
  const last = m.records.filter((r) => r.lane === w.lane).reduce((a, b) => (!a || b.seq > a.seq ? b : a), null);
  const events = timeline().filter((e) => e.who === w.lane && e.id !== `r${last?.seq}`).slice(0, 13);
  const where = m.steps.find((s) => s.id === w.step)?.label;
  return sheet(`<div class="kick">Worker · ${w.live ? 'active' : 'quiet'} · last word ${agoWords(w.at)}</div><h3>${esc(w.lane)}</h3>
    ${where ? `<p class="relnow">On plan step <button class="linkish" data-track="${esc(w.step)}">${esc(where)}</button></p>` : ''}
    ${last ? `<div class="lastsaid"><small>Its last word, ${clock(last.at)}</small><p><b>${SAID[last.kind] ?? KIND_WORD[last.kind] ?? ''}</b> ${said(last, 240)}</p><a href="/record/${last.seq}" target="_blank" rel="noopener">the record ↗</a></div>` : ''}
    <div class="wbox"><span class="bx">${w.boxes.map((b) => `<i class="${b}"></i>`).join('')}</span><small>each box is five minutes, newest on the right</small></div>
    <h4 class="sc-h">Before that <span>newest first</span></h4><ul class="tl">${events.length ? timelineRows(events, false) : '<li class="dim">Nothing else in the last day.</li>'}</ul>
    ${w.prs.length ? `<details data-id="worker-prs-${esc(w.lane)}"${state.open.has(`worker-prs-${w.lane}`) ? ' open' : ''}><summary class="sc-h">Pull requests it named <span>${w.prs.length}</span></summary><ul class="prlist">${w.prs.map((n) => prRow(driveEntry(n) ?? { number: n, state: prState(n) })).join('')}</ul></details>` : ''}`);
}
function planSheet() {
  const m = state.model;
  const rels = m.now.releases ?? [];
  const at = rels.findIndex((r) => !RELEASED.test(r.state));
  const share = (s) => { const t = tally(s.prs); return t.total ? Math.round((100 * t.landed) / t.total) : s.tone === 'done' ? 100 : 0; };
  const route = [
    ...m.steps.map((s) => `<div class="bw step tn-${s.tone}${s.tone === 'done' ? ' past' : ''}" data-track="${esc(s.id)}" style="--p:${share(s)}"><span class="bn"></span><span class="bl">${esc(s.label)}</span></div>`),
    ...rels.map((r, i) => `<div class="bw rel ${releaseWord(rels, i)[0]}${i === at ? ' here' : ''}${i < at ? ' past' : ''}" data-release="${esc(r.id)}"><span class="bn">${i === at ? '✈' : esc(r.id.slice(1))}</span><span class="bl">${esc(r.name.split(',')[0])}</span></div>`),
  ].join('');
  const stepCards = m.steps.map((s, i) => {
    const t = tally(s.prs);
    return `<article class="stc tn-${s.tone}" data-track="${esc(s.id)}" style="--p:${share(s)}"><header><span class="si">${i + 1}</span><b>${esc(s.title)}</b><em>${t.total ? `${t.landed}/${t.total} landed` : ''}</em></header>
      ${s.prs.length + s.untracked.length ? `<div class="sd">${byLight(s.prs).map((e) => dot(e.number)).join('')}${s.untracked.map(dot).join('')}</div>` : ''}
      <p><em class="tone">${esc(TONE_SAY[s.tone] ?? s.tone)}.</em> ${inline(s.state)}</p>${s.blocker || s.next ? `<p class="dim">${s.blocker ? `Waits on: ${inline(s.blocker)}. ` : ''}${s.next ? `Next: ${inline(s.next)}` : ''}</p>` : ''}</article>`;
  }).join('');
  const relCards = planRows().map(([n, name, reach, targets, who]) => {
    const r = rels.find((x) => x.id === `R${n}`);
    const i = rels.indexOf(r);
    return `<article class="rc ${r ? releaseWord(rels, i)[0] : ''}"${r ? ` data-release="${esc(r.id)}"` : ''}><span class="rn">${esc(n)}</span><div><b>${esc(name)}</b><p>${inline(reach)}</p><small>${r ? `<b>${esc(releaseWord(rels, i)[1])}:</b> ${inline(r.state)}` : ''}</small><small>${esc(who)} · ${esc(targets)}</small></div></article>`;
  }).join('');
  const doc = (m.releases.markdown ?? '').split('\n## ').slice(1).map((sec, i) => {
    const [h, ...body] = sec.split('\n');
    return `<details data-id="plan-doc-${i}"${state.open.has(`plan-doc-${i}`) ? ' open' : ''}><summary>${esc(h)}</summary><div class="md">${markdown(body.join('\n'))}</div></details>`;
  }).join('');
  const tab = { steps: `<div class="steps">${stepCards}</div>`, releases: `<div class="rels">${relCards}</div>`,
    text: `<h3>The full release plan</h3><div class="doc">${doc}</div>
    <h3>The progress doc's ordered plan</h3><div class="doc"><details data-id="plan-progress"${state.open.has('plan-progress') ? ' open' : ''}><summary>Written ${m.plan_at ? agoWords(m.plan_at) : ''}</summary><ol class="psteps">${planSteps(true)}</ol></details></div>` };
  return `<div class="overlay" data-close="1"><div class="sheet plan ck"><button class="btn close" data-close="1">Close</button><div class="pgrid"><div class="pv">
    <div class="ph"><h2>The plan</h2><span>release plan revised ${m.releases.updated ? clock(m.releases.updated) : ''} · steps as of ${clock(m.steps_at)}</span></div>
    <div class="bigroute">${route}</div>
    <nav class="ptabs">${[['steps', 'Steps'], ['releases', 'Releases'], ['text', 'Plan text']].map(([k, label]) => `<button data-ptab="${k}"${ov.planTab === k ? ' class="on"' : ''}>${label}</button>`).join('')}</nav>
    ${tab[ov.planTab] ?? tab.steps}
  </div><aside class="mini">${machine('cloud', 'The cloud')}${machine('mac', 'Your Mac')}<p>Hover a step or a release to light what it changes.</p></aside></div></div></div>`;
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
async function answer(body, draft) {
  if (sending(body.block)) return;
  ov.sent.set(body.block, {});
  render();
  const error = await fetch('/api/answer', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then((res) => res.json())
    .then((out) => out.error ?? '', (failure) => `could not reach the dashboard server (${failure.message})`);
  ov.sent.set(body.block, error ? { error } : { recorded: true });
  if (!error && draft) delete ov.drafts[draft];
  render();
  if (!error) setTimeout(() => { ov.sent.delete(body.block); render(); }, body.kind === 'note' ? 4000 : 30000);
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
  return `<li><span class="ws"><span class="bx">${w.boxes.map((b) => `<i class="${b}"></i>`).join('')}</span></span><div><button class="linkish" data-worker="${esc(w.lane)}">${esc(w.lane)}</button> <b>${r ? SAID[r.kind] ?? KIND_WORD[r.kind] ?? r.kind : 'At work'}</b>${prs ? ` on ${prs}` : ''}, ${agoWords(r?.at ?? w.at)}<small>${r ? `<a href="/record/${r.seq}" target="_blank" rel="noopener">its last word, #${r.seq} ↗</a>` : ''}</small></div></li>`;
}
function stepCard(s) {
  const t = tally(s.prs);
  const red = s.prs.filter((p) => p.state === 'failing');
  const ws = state.model.workers.filter((w) => w.step === s.id).sort((a, b) => new Date(b.at) - new Date(a.at)).slice(0, 5);
  return `<section class="stepcard tn-${s.tone}"><div class="kick">${TONE_GLYPH[s.tone] ?? ''} ${TONE_SAY[s.tone] ?? ''}</div><h3>${esc(s.title)}</h3>
    <dl class="facts">${red.length ? `<dt>Red now</dt><dd class="bad">${red.map((p) => `${prLink(p.number)} ${esc(shortName(p.number) ?? plainTitle(p.number))}${(state.model.prs[p.number]?.failing_checks ?? []).map((c) => ` · <a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.name)} ↗</a>`).join('')}`).join('<br>')}</dd>` : ''}<dt>Where it stands</dt><dd>${inline(s.state)}</dd><dt>What blocks it</dt><dd>${blockerNow(s.blocker)}</dd><dt>Next</dt><dd>${inline(s.next)}</dd></dl>
    ${ws.length ? `<h4 class="sc-h">Who is on it <span>${plural(ws.filter((w) => w.live).length, 'worker')} active in the last 20 minutes</span></h4><ul class="wlist">${ws.map(lastWord).join('')}</ul>` : '<p class="dim">No worker has reported on this step.</p>'}
    ${s.untracked.length ? `<p class="dim">Not tracked by the merge ledger yet: ${s.untracked.map(prLink).join(', ')}</p>` : ''}
    ${s.prs.length ? `<h4 class="sc-h">Pull requests <span><b>${t.landed}</b> landed · <b>${t.queued}</b> approved · <b>${t.you}</b> your tap · ${t.total} in all</span></h4><ul class="prlist">${[...s.prs].sort((a, b) => ATTENTION.indexOf(a.state) - ATTENTION.indexOf(b.state)).map((p) => prRow(p, ['failing', 'held', 'you'].includes(p.state) ? ownerLine(p.number) : '')).join('')}</ul>` : ''}
    <p class="dim">Desk note written ${clock(state.model.steps_at)}.</p></section>`;
}
function blockerNow(text) {
  if (!text) return 'Nothing';
  const named = [...new Set([...text.matchAll(/#(\d{5})\b/g)].map((x) => x[1]))];
  const landedSince = (n) => new Date(state.model.prs[n]?.landed_at ?? 0) > new Date(state.model.steps_at);
  return named.length && named.every((n) => prState(n) === 'landed' && landedSince(n)) ? `Nothing open: ${named.map(prLink).join(', ')} landed after this note. <small class="dim">The step note says ${inline(text)}</small>` : inline(text);
}
function trackSheet() {
  const s = state.model.steps.find((x) => x.id === ov.track);
  const red = state.model.journeys.red;
  if (s) return `<div class="overlay" data-close="1"><div class="sheet"><button class="btn close" data-close="1">Close</button>${stepCard(s)}${red.question.includes(` ${s.label} `) ? `<h4 class="sc-h">${esc(red.question)}</h4>${answer('red')}` : ''}</div></div>`;
  const list = state.model.drive.prs.filter((e) => !e.step && e.state !== 'landed');
  return `<div class="overlay" data-close="1"><div class="sheet"><button class="btn close" data-close="1">Close</button><div class="kick">No plan step names these</div><h3>Other work: ${list.length} open pull requests</h3>
    <ul class="prlist">${list.map((p) => prRow(p)).join('')}</ul></div></div>`;
}
function shortShas(text) {
  return String(text).replace(/\b([0-9a-f]{7})[0-9a-f]{5,33}\b/g, '$1');
}
function truth(n) {
  return driveEntry(n) ?? { number: Number(n), state: prState(n), owner: state.model.prs[n]?.lane, next: null, blocker: null, checks: [] };
}
function said(r, max) {
  return inline(plainWord(r) ?? firstSentences(shortShas(r.text), max));
}
function ownerLine(n) {
  const t = truth(n);
  return `<span class="own">${t.owner ? `<button class="linkish" data-worker="${esc(t.owner)}">${esc(t.owner)}</button>` : 'No worker named'}${t.next ? `: ${inline(t.next)}` : ''}${t.word ? ` <small class="dim">last word ${agoWords(t.word.at)}</small>` : ''}</span>`;
}
function redWhy(n) {
  const checks = truth(n).checks.map((c) => c.name);
  return checks.length ? ` · ${esc(checks.join(', '))} failed` : '';
}
function prRow(p, extra = '') {
  const pr = state.model.prs[p.number] ?? {};
  const why = `${p.state === 'failing' ? redWhy(p.number) : ''}${pr.mergeable_state === 'dirty' ? ' · conflicts with its base' : ''}`;
  return `<li><a href="${prHref(p.number)}" data-pr="${p.number}"><i class="lt ${p.state}"></i><b>#${p.number}</b><span>${esc(shortName(p.number) ?? plainTitle(p.number))}</span><em>${STATE_WORD[p.state]}${why}</em></a>${extra}</li>`;
}
function releaseSheet() {
  const m = state.model;
  const rels = m.now.releases;
  const i = rels.findIndex((r) => r.id === ov.release);
  const r = rels[i];
  const next = rels.findIndex((x) => !RELEASED.test(x.state));
  const parts = r.parts.map((k) => m.parts.find((p) => p.key === k)).filter(Boolean);
  const named = [...new Set([...r.state.matchAll(/#(\d{5})\b/g)].map((x) => Number(x[1])))];
  const blocking = named.filter((n) => prState(n) !== 'landed');
  const cleared = named.filter((n) => prState(n) === 'landed');
  const open = m.drive.prs.filter((e) => r.parts.includes(e.part) && e.state !== 'landed' && e.tree === m.tree_names[0] && !blocking.includes(e.number));
  const row = (n) => prRow(driveEntry(n) ?? { number: n, state: prState(n) }, ownerLine(n));
  return `<div class="overlay" data-close="1"><div class="sheet"><button class="btn close" data-close="1">Close</button><div class="kick">${esc(r.id)} · ${releaseWord(rels, i)[1]}${r.tonight ? ' · tonight' : ' · after tonight'}</div><h3>${esc(r.name)}</h3>
    ${m.journeys.release.question.includes(` ${r.id},`) ? `<h4 class="sc-h">What blocks it</h4>${answer('release', (l) => !l.text.startsWith(`${r.id} `))}` : blocking.length ? `<h4 class="sc-h">What it waits on <span>${blocking.length}</span></h4><ul class="prlist">${blocking.map(row).join('')}</ul>` : ''}
    ${cleared.length ? `<p class="dim">Already landed: ${cleared.map(prLink).join(', ')}</p>` : ''}
    <details class="relstate" data-id="rel-state-${esc(r.id)}"${state.open.has(`rel-state-${r.id}`) ? ' open' : ''}><summary>${inline(firstSentences(r.state, 140))}</summary><p class="relnow">${inline(r.state)}</p></details>
    <dl class="facts"><dt>What reaches people</dt><dd>${esc(planRows().find(([n]) => `R${n}` === r.id)?.[2] ?? '')}</dd><dt>Parts it ships</dt><dd>${parts.map((p) => esc(p.name)).join(' · ')}</dd></dl>
    ${open.length ? `<details data-id="rel-open-${esc(r.id)}"${state.open.has(`rel-open-${r.id}`) ? ' open' : ''}><summary class="sc-h">Other new-app pull requests open in these parts <span>${open.length}</span></summary><ul class="prlist">${[...open].sort((a, b) => LIGHT_ORDER.indexOf(a.state) - LIGHT_ORDER.indexOf(b.state)).map((p) => prRow(p)).join('')}</ul></details>` : ''}
    <p><button class="linkish" data-plan>Open the full release plan ›</button></p></div></div>`;
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
    return `<div class="tip-h"><b>${esc(r.id)} ${esc(r.name)}</b></div><div>${esc(r.state)}</div><div class="dim">${r.parts.map((k) => esc(state.model.parts.find((p) => p.key === k)?.name ?? k)).join(' · ')}</div>`;
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
  const input = ov.focusNote && $('.note input');
  if (input) { ov.focusNote = false; input.focus(); }
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
  if (!e.target.closest('.ck, .overlay, .rellist, .pr-facts, #cardembed')) return;
  const away = () => (route().screen === 'overview' ? render() : (location.hash = '#/'));
  if (e.metaKey || e.ctrlKey) return;
  const light = e.target.closest('.ck .dot[data-n], .overlay [data-pr]');
  if (light) { e.preventDefault(); ov.pr = Number(light.dataset.n ?? light.dataset.pr); state.overlay = 'pr'; return render(); }
  const strip = e.target.closest('.ck .route');
  if (strip && !e.target.closest('.planlink, a')) return (location.hash = `#/status/${e.target.closest('[data-release]')?.dataset.release ?? ''}`);
  const onlyEl = e.target.closest('[data-only]');
  if (onlyEl?.dataset.only === 'you') return (location.hash = '#/now');
  if (onlyEl) return onlyBy(onlyEl.dataset.only);
  const cardEl = e.target.closest('.deck .cta');
  if (cardEl && !e.target.closest('a, button, input, form, [data-page]')) return (location.hash = '#/now');
  const scrubTo = e.target.closest('[data-scrubto]');
  if (scrubTo) return setScrub(Number(scrubTo.dataset.scrubto));
  const partEl = e.target.closest('[data-part-open], .ck .pl');
  if (partEl) { ov.part = partEl.dataset.partOpen ?? partEl.closest('[data-part]')?.dataset.part; if (ov.part) { state.overlay = 'part'; return away(); } }
  const workerEl = e.target.closest('[data-worker]');
  if (workerEl) { e.preventDefault(); ov.lane = workerEl.dataset.worker; state.overlay = 'worker'; return away(); }
  const trackEl = e.target.closest('[data-track]');
  if (trackEl && !e.target.closest('a')) { ov.track = trackEl.dataset.track; state.overlay = 'track'; return away(); }
  const planEl = e.target.closest('[data-plan]');
  if (planEl) { state.overlay = 'plan'; ov.planTab = planEl.dataset.plan || 'steps'; return render(); }
  if (e.target.closest('[data-desks]')) { e.preventDefault(); state.overlay = 'desks'; return render(); }
  if (e.target.closest('[data-back]')) {
    ov.only = ov.only?.key === 'back' ? null : { key: 'back', label: 'landed since you last looked', numbers: new Set(landedSinceLook().map((x) => x.number)) };
    return render();
  }
  if (e.target.closest('[data-unfold]')) { ov.unfold = !ov.unfold; return render(); }
  if (e.target.closest('[data-jump]')) return $('#kbtn')?.click();
  if (e.target.closest('[data-stop]')) { state.overlay = 'stop'; return render(); }
  if (e.target.closest('[data-key]')) { state.overlay = 'key'; return render(); }
  if (e.target.closest('[data-only-clear]')) return showOnly(null);
  const rel = e.target.closest('[data-release]');
  if (rel) { ov.release = rel.dataset.release; state.overlay = 'release'; return route().screen === 'overview' ? render() : (location.hash = '#/'); }
  if (e.target.closest('[data-scrub-clear]')) return setScrub(0);
  const ptab = e.target.closest('[data-ptab]');
  if (ptab) { ov.planTab = ptab.dataset.ptab; return render(); }
  const page = e.target.closest('[data-page]');
  if (page) { ov.page = Number(page.dataset.page); ov.noting = null; return render(); }
  const noting = e.target.closest('[data-noting]');
  if (noting) { ov.noting = noting.dataset.noting; ov.note = ''; ov.focusNote = true; return render(); }
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
  const hold = e.target.closest('[data-note-hold]');
  if (hold) return noteDecide(hold.closest('[data-note]'), 'rejected');
});
document.addEventListener('submit', (e) => {
  const reply = e.target.closest('[data-answer]');
  if (reply) {
    e.preventDefault();
    const text = reply.text.value.trim();
    if (!text) return;
    return answer(reply.dataset.choice ? { kind: 'choice', block: reply.dataset.choice, other: text } : { kind: 'note', block: reply.dataset.answer, text }, reply.text.dataset.draft);
  }
  const approval = e.target.closest('[data-approval]');
  if (approval) {
    e.preventDefault();
    return answer({ kind: 'approval', block: approval.dataset.approval, verdict: e.submitter?.value ?? 'approved', text: approval.text.value.trim() }, approval.text.dataset.draft);
  }
  const input = e.target.closest('[data-input]');
  if (input) {
    e.preventDefault();
    const text = input.text.value.trim();
    return text ? answer({ kind: 'input', block: input.dataset.input, text }, input.text.dataset.draft) : undefined;
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
  const paging = e.target.closest('.cta') && needs.length > 1;
  if (paging && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) {
    ov.page = (ov.page + (e.key === 'ArrowRight' ? 1 : needs.length - 1)) % needs.length;
    render();
    $('.cta')?.focus();
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
