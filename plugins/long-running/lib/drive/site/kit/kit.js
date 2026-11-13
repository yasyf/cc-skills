import { counted, createRenderers, esc, link, prName, refusedVisual, stateFacts } from './renderers.js';
export { esc } from './renderers.js';

const drawers = new Set();
const requests = new Map();
const diagrams = new WeakMap();
const renders = createRenderers({
  repo: () => null,
  sourceLink: (repo, path, line) => link(`${path}:${line}`, `https://github.com/${repo}/blob/HEAD/${path}#L${line}`),
  picture: (src, caption) => `<figure><img src="${esc(src)}" alt="${esc(caption)}"></figure>`,
  shown: (path, caption) => `<figure><img src="/api/picture?path=${encodeURIComponent(path)}" alt="${esc(caption)}"></figure>`,
  fenced: (kind, source) => `<div data-visual="${esc(kind)}" data-source="${esc(source)}"></div>`,
  featureLink: (id, text) => `<button class="linkish" data-kit-open="#/feature/${esc(id)}">${esc(text)}</button>`,
  time: (at) => time(at),
});
let shown = null;
let drawing = Promise.resolve();

window.addEventListener('message', (event) => {
  if (event.source !== window.parent) return;
  if (event.data?.type === 'drive:completion') {
    const request = requests.get(event.data.id);
    if (!request) return;
    if (event.data.error) { requests.delete(event.data.id); request.reject(new Error(event.data.error)); }
    else if (event.data.done) { requests.delete(event.data.id); request.resolve(request.text); }
    else { request.text += event.data.delta; request.onToken?.(event.data.delta); }
    return;
  }
  if (event.data?.type !== 'drive:model') return;
  shown = event.data.slice;
  const slice = shown;
  drawing = drawing.then(async () => {
    document.documentElement.dataset.kitState = 'drawing';
    await Promise.all([...drawers].map((draw) => draw(slice)));
    for (const host of document.querySelectorAll('[data-visual]')) {
      if (host.dataset.drawn === host.dataset.source) continue;
      await graph(host, host.dataset.source, host.dataset.visual).catch((failure) => { host.innerHTML = refusedVisual(host.dataset.visual, host.dataset.source, failure); });
      host.dataset.drawn = host.dataset.source;
    }
    document.documentElement.dataset.kitState = 'ready';
  }).catch((error) => {
    document.documentElement.dataset.kitState = 'failed';
    document.documentElement.dataset.kitError = error.message;
    console.error(error);
  });
});

document.addEventListener('auxclick', (event) => event.button === 1 && outward(event));

document.addEventListener('click', (event) => {
  if (outward(event)) return;
  const route = event.target.closest('a[href^="#/feature/"]');
  if (route) { event.preventDefault(); return open(route.getAttribute('href')); }
  const button = event.target.closest('button[data-card], button[data-kit-answer], button[data-kit-group], [data-kit-open]');
  if (!button) return;
  if (button.dataset.kitOpen) return open(button.dataset.kitOpen);
  if (button.dataset.kitGroup) {
    const selected = button.closest('[data-kit-component]').querySelectorAll('input[data-kit-pick]:checked:not(:disabled)');
    return answers([...selected].map((input) => ({ card: input.dataset.card, label: input.dataset.label })));
  }
  answer(button.dataset.card ?? button.dataset.kitAnswer, button.dataset.label);
});

document.addEventListener('change', (event) => {
  if (!event.target.matches('input[data-kit-pick]')) return;
  selection(event.target.closest('[data-kit-component]'));
});

function outward(event) {
  const away = event.target.closest('a[href^="https://"]');
  if (!away) return false;
  event.preventDefault();
  open(away.href);
  return true;
}

function selection(group) {
  const count = group.querySelectorAll('input[data-kit-pick]:checked:not(:disabled)').length;
  const button = group.querySelector('[data-kit-group]');
  button.disabled = !count;
  button.textContent = `${button.dataset.verb} ${count}`;
}

export function live(draw) {
  drawers.add(draw);
  if (shown) drawing = drawing.then(() => draw(shown));
  return () => drawers.delete(draw);
}

export function answer(card, label) { window.parent.postMessage({ type: 'drive:answer', answer: card, label }, '*'); }
export function answers(picks) { if (picks.length) window.parent.postMessage({ type: 'drive:answers', picks }, '*'); }
export function ask(text) { window.parent.postMessage({ type: 'drive:ask', text }, '*'); }
export function open(route) { window.parent.postMessage({ type: 'drive:open', route }, '*'); }
export function model(messages, onToken) {
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    requests.set(id, { resolve, reject, onToken, text: '' });
    window.parent.postMessage({ type: 'drive:complete', id, messages }, '*');
  });
}
export const time = (at) => new Date(at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/Los_Angeles' });
export const prose = renders.prose;

export function patch(host, html) {
  const made = document.createElement('template');
  made.innerHTML = html;
  const reconcile = (old, next) => {
    const keys = new Map([...old.children].filter((node) => node.dataset.key).map((node) => [node.dataset.key, node]));
    let cursor = old.firstChild;
    for (const node of [...next.childNodes]) {
      const kept = node.nodeType === 1 && node.dataset.key ? keys.get(node.dataset.key) : cursor && cursor.nodeType === node.nodeType && cursor.nodeName === node.nodeName && !cursor.dataset?.key ? cursor : null;
      if (!kept) { old.insertBefore(node, cursor); continue; }
      if (kept !== cursor) old.insertBefore(kept, cursor);
      if (kept.nodeType === 3) { if (kept.textContent !== node.textContent) kept.textContent = node.textContent; }
      else {
        for (const attr of [...kept.attributes]) if (!node.hasAttribute(attr.name) && !(attr.name === 'open' && kept.matches('details'))) kept.removeAttribute(attr.name);
        for (const attr of node.attributes) if (kept.getAttribute(attr.name) !== attr.value) kept.setAttribute(attr.name, attr.value);
        reconcile(kept, node);
      }
      cursor = kept.nextSibling;
    }
    while (cursor) { const next = cursor.nextSibling; cursor.remove(); cursor = next; }
  };
  reconcile(host, made.content);
  for (const button of host.querySelectorAll('[data-kit-group]')) selection(button.closest('[data-kit-component]'));
}

export function ring(done, total, label = '') {
  const around = 2 * Math.PI * 26;
  return `<figure class="kit-ring" data-kit-component="progress" role="img" aria-label="${esc(`${done} of ${total} ${label}`)}"><svg viewBox="0 0 64 64"><circle r="26" cx="32" cy="32"/><circle class="done" r="26" cx="32" cy="32" stroke-dasharray="${total ? done / total * around : 0} ${around}"/></svg><figcaption><b>${done}</b>/${total} ${esc(label)}</figcaption></figure>`;
}

export function counter(element, to) {
  const from = Number(element.dataset.kitCount ?? 0);
  element.dataset.kitCount = to;
  if (matchMedia('(prefers-reduced-motion: reduce)').matches || from === to) return void (element.textContent = to);
  const start = performance.now();
  const step = (now) => {
    if (Number(element.dataset.kitCount) !== to) return;
    const t = Math.min(1, (now - start) / 600);
    element.textContent = Math.round(from + (to - from) * (1 - (1 - t) ** 3));
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

export function decision(question) {
  const card = question.card;
  const body = decisionBody(card);
  const hints = Object.entries(card.hints ?? {}).map(([option, text]) => `<dt>${esc(option)}</dt><dd>${renders.prose(text)}</dd>`).join('');
  const links = [...(card.links ?? []), ...(card.refs ?? [])].map((ref) => `<li>${link(ref.label, ref.url)}</li>`).join('');
  const pictures = (card.pictures ?? []).map((path) => `<figure><img src="/api/picture?path=${encodeURIComponent(path)}" alt="Decision evidence"></figure>`).join('');
  const facts = `${question.blocks?.length ? `<div class="kit-tags">${question.blocks.map((item) => `<button data-kit-open="#/feature/${esc(item.id)}">${esc(item.title)}</button>`).join('')}</div>` : ''}${(card.facts ?? []).map((fact) => `<span class="fact">${esc(fact.label)}: ${renders.inline(fact.value)}</span>`).join('')}`;
  return decisionCard(question, {
    body: body || hints || links || pictures ? `<details data-key="context"><summary>Explain more</summary>${body}${hints ? `<dl class="keyed">${hints}</dl>` : ''}${links ? `<ul>${links}</ul>` : ''}${pictures}</details>` : '',
    facts, picks: renders.choices(question, { given: question.answered }),
  });
}

export function prLane(prs) {
  return `<ol class="kit-lane" data-kit-component="prs">${prs.map((pr) => {
    const row = pr.card?.prs?.find((row) => row.url === pr.url) ?? pr;
    const facts = stateFacts(pr);
    const caption = pr.approve_waits === '' ? 'Approve queues it for merge at once' : pr.approve_waits ? `After Approve: ${pr.approve_waits}` : pr.stage?.why ?? '';
    const failed = pr.failed?.length ? `<p class="refused">Failing: ${pr.failed.map(esc).join(', ')}</p>` : '';
    const rail = prRail({ head: pr.head, facts, lands: `<li>${esc(caption)}</li>`, state: failed, serves: pr.feature ? `<p><button class="linkish" data-kit-open="#/feature/${esc(pr.feature)}">${esc(pr.feature_title)}</button></p>` : '' });
    const panes = `${row.show_me ? `<details data-key="show"><summary>Show me</summary>${renders.prose(row.show_me, pr.key)}</details>` : ''}<details data-key="landing"><summary>Landing details</summary>${rail}</details>`;
    return `<li data-key="${esc(pr.key)}">${prCard({ key: pr.key, row: pr, title: pr.title, facts, summary: row.summary || row.refresher ? `<div class="card-summary">${renders.prose(row.summary || row.refresher, pr.key)}</div>` : '', panes, picks: renders.choices({ ...pr, kind: 'tap' }, { given: pr.answered }), caption })}</li>`;
  }).join('')}</ol>`;
}

export function prGroups(prs, by = (pr) => pr.feature_title || 'Changes') {
  const groups = Map.groupBy(prs, by);
  return [...groups].map(([title, members]) => {
    const eligible = [...new Map(members.filter((pr) => (!pr.answered || pr.answered.error) && pr.options.includes('Approve') && pr.checks !== 'fail' && pr.stage?.name === 'card').map((pr) => [pr.answer, pr])).values()];
    const decided = [...new Map(members.filter((pr) => pr.answered?.done).map((pr) => [pr.answer, pr])).values()];
    return `<section class="kit-group" data-key="pr:${esc(title)}" data-kit-component="pr-group"><header><div><h3>${esc(title)}</h3><small>${counted(members.length, 'pull request')} · ${counted(eligible.length, 'approval')} available${decided.length ? ` · ${decided.length} answered` : ''}</small></div><button class="kit-btn primary" data-kit-group="approve" data-verb="Approve"${eligible.length ? '' : ' disabled'}>Approve ${eligible.length}</button></header>
      <div class="kit-selection">${eligible.map((pr) => `<label data-key="${esc(pr.answer)}"><input type="checkbox" data-kit-pick data-card="${esc(pr.answer)}" data-label="Approve" checked><span>${esc(prName(pr.key))} · ${esc(pr.title)}</span></label>`).join('')}${decided.map((pr) => decidedRow(pr, `${prName(pr.key)} · ${pr.title}`)).join('')}</div><details data-key="members"><summary>Review ${counted(members.length, 'change')}</summary>${prLane(members)}</details></section>`;
  }).join('');
}

export function decisionGroups(questions, by = (question) => question.blocks?.[0]?.title || 'Decisions') {
  return [...Map.groupBy(questions, by)].map(([title, members]) => {
    const eligible = members.filter((q) => q.card.recommended && q.options.includes(q.card.recommended) && (!q.answered || q.answered.error));
    const decided = members.filter((q) => q.answered?.done);
    const blocks = new Set(members.flatMap((q) => q.tasks));
    return `<section class="kit-group" data-key="decisions:${esc(title)}" data-kit-component="decision-group"><header><div><h3>${esc(title)}</h3><small>${counted(members.length, 'decision')} · ${blocks.size === 1 ? '1 task depends' : `${blocks.size} tasks depend`} on ${members.length === 1 ? 'it' : 'these'}${decided.length ? ` · ${decided.length} answered` : ''}</small></div><button class="kit-btn" data-kit-group="recommendations" data-verb="Use recommendations ·"${eligible.length ? '' : ' disabled'}>Use recommendations · ${eligible.length}</button></header><details data-key="recommendations"><summary>Review recommendations before answering</summary><div class="kit-selection">${eligible.map((q) => `<label data-key="${esc(q.answer)}"><input type="checkbox" data-kit-pick data-card="${esc(q.answer)}" data-label="${esc(q.card.recommended)}" checked><span><b>${esc(q.title)}</b><small>${esc(q.card.recommended)}</small></span></label>`).join('')}${decided.map((q) => decidedRow(q, q.title)).join('')}</div></details><details data-key="questions"><summary>Review ${counted(members.length, 'decision')}</summary><div class="kit-decisions">${members.map(decision).join('')}</div></details></section>`;
  }).join('');
}

export function timeline(moments) {
  return `<ol class="kit-timeline" data-kit-component="timeline">${moments.filter((moment) => moment.kind !== 'heard').slice(0, 12).map((moment) => `<li data-key="${esc(`${moment.at}:${moment.kind}:${moment.feature ?? moment.url ?? ''}`)}"><time>${esc(time(moment.at))}</time><span>${renders.momentText(moment)}</span></li>`).join('')}</ol>`;
}

function decidedRow(item, title) {
  return `<div class="kit-decided" data-key="done:${esc(item.answer)}"><span>${esc(title)}</span>${renders.choices(item, { given: item.answered })}</div>`;
}

function decisionBody(card) {
  const rows = (card.rows ?? []).map((row) => `<li>${renders.inline(row.text)}</li>`).join('');
  return [card.body ? renders.prose(card.body) : '', rows ? `<ul class="rows">${rows}</ul>` : ''].join('');
}

function decisionCard(item, { body, facts, picks }) {
  const id = `decision-${encodeURIComponent(item.answer)}`;
  return `<div class="queue-layout" data-key="${esc(item.answer)}" data-kit-component="decision">
    <article class="focus-card" aria-labelledby="${esc(id)}" tabindex="-1">
      <div class="card-content">
        <div class="card-top"><span class="type-label">Decision</span>${item.answered?.done ? '' : `<button class="linkish" data-kit-open="${esc(item.href)}">Answer it in your queue ›</button>`}</div>
        <h2 class="card-title" id="${esc(id)}">${renders.inline(item.card.title || item.title)}</h2>
        ${body ? `<div class="card-summary">${body}</div>` : ''}
        ${facts ? `<div class="facts">${facts}</div>` : ''}
        ${picks}
      </div>
    </article>
  </div>`;
}

function prRail({ head, facts, state, lands, serves }) {
  return `<aside class="context-rail pr-rail" aria-label="About this change">
    <div class="rail-block"><div class="eyebrow">Attached to this change <code>${esc(head.slice(0, 7))}</code></div><div class="facts">${facts}</div>${state}<p class="spot-head">What Approve lands</p><ul class="rail-list">${lands}</ul></div>
    ${serves}
  </aside>`;
}

function prCard({ key, row, title, summary, facts, panes, picks, caption }) {
  const id = `pr-${encodeURIComponent(key)}`;
  return `<div class="pr-layout" data-read="${esc(key)}" data-head="${esc(row.head)}" data-key="${esc(key)}" data-kit-component="pr">
    <article class="focus-card pr-card" aria-labelledby="${esc(id)}" tabindex="-1">
      <div class="card-content">
        <div class="card-top"><span class="type-label">Pull request <a href="${esc(row.url)}" target="_blank" rel="noopener">${esc(key)}</a></span></div>
        <h2 class="card-title" id="${esc(id)}">${esc(title)}</h2>
        ${summary}<div class="facts">${facts}</div>
      </div>
      ${panes}
      <div class="pr-actions">${picks}<span class="meta">${esc(caption)}</span></div>
    </article>
  </div>`;
}

export async function diagram(host, doc, options = {}) {
  host.dataset.kitComponent = 'diagram';
  let map = diagrams.get(host);
  if (!map) {
    const { mount } = await import('/vendor/cc-present@v0.60.2/arch-map.js?drive=keyed-1');
    map = mount(host, doc, { fill: true, key: false, ...options });
    diagrams.set(host, map);
    if (options.onNode) {
      host.addEventListener('dblclick', (event) => {
        const node = event.target.closest('[data-block], [data-part]');
        if (node) options.onNode(node.dataset.part ?? node.dataset.block);
      });
    }
  } else map.update(doc);
  await map.ready;
  if (map.problems.length) throw new Error(map.problems.join('; '));
  return map;
}

export async function graph(host, source, kind = 'mermaid') {
  const { draw } = await import('/visuals.js');
  await draw(host, kind, source);
}

export async function burndown(host, points, field = 'open') {
  return graph(host, JSON.stringify({ data: { values: points }, mark: { type: 'area', line: true, interpolate: 'monotone' }, encoding: { x: { field: 'at', type: 'temporal', title: null }, y: { field, type: 'quantitative', title: field } } }), 'chart');
}
