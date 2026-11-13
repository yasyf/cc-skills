const drawers = [];
let shown = null;

window.addEventListener('message', (event) => {
  if (event.source !== window.parent || event.data?.type !== 'drive:model') return;
  shown = event.data.slice;
  for (const draw of drawers) draw(shown);
});

document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-kit-answer]');
  if (button) answer(button.dataset.kitAnswer, button.dataset.label);
});

export function live(draw) {
  drawers.push(draw);
  if (shown) draw(shown);
}

export function answer(card, label) {
  window.parent.postMessage({ type: 'drive:answer', answer: card, label }, '*');
}

export function ask(text) {
  window.parent.postMessage({ type: 'drive:ask', text }, '*');
}

export function esc(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

export function time(at) {
  return new Date(at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/Los_Angeles' });
}

export function ring(done, total, label = '') {
  const share = total ? done / total : 0;
  const around = 2 * Math.PI * 26;
  return `<figure class="kit-ring" role="img" aria-label="${esc(`${done} of ${total} ${label}`)}">
    <svg viewBox="0 0 64 64"><circle r="26" cx="32" cy="32"/>${done ? `<circle class="done" r="26" cx="32" cy="32" stroke-dasharray="${share * around} ${around}"/>` : ''}</svg>
    <figcaption><b>${done}</b>/${total} ${esc(label)}</figcaption>
  </figure>`;
}

export function counter(element, to) {
  const from = Number(element.dataset.kitCount ?? 0);
  element.dataset.kitCount = to;
  if (matchMedia('(prefers-reduced-motion: reduce)').matches || from === to) return void (element.textContent = to);
  const start = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - start) / 600);
    element.textContent = Math.round(from + (to - from) * (1 - (1 - t) ** 3));
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function choices(item) {
  if (item.answered) return `<span class="kit-meta">${esc(item.answered.label)}${item.answered.done ? ' sent' : item.answered.error ? `: ${esc(item.answered.error)}` : ', waiting for Undo'}</span>`;
  return item.options.map((label) => `<button type="button" class="kit-btn${label === item.card?.recommended ? ' primary' : ''}" data-kit-answer="${esc(item.answer)}" data-label="${esc(label)}">${esc(label)}</button>`).join('');
}

export function prLane(prs) {
  if (!prs.length) return '<p class="kit-meta">No open PR.</p>';
  return `<ol class="kit-lane">${prs.map((pr) => `<li class="kit-card">
      <a href="${esc(pr.url)}" target="_blank" rel="noopener">${esc(pr.title)}</a>
      <span class="kit-meta">checks ${esc(pr.checks ?? 'none')} · review ${esc(pr.review)}${pr.landing ? ` · ${esc(pr.landing)}` : ''}</span>
      <span class="kit-actions">${choices(pr)}</span>
    </li>`).join('')}</ol>`;
}

export function decision(question) {
  return `<section class="kit-card kit-decision">
    <h3>${esc(question.title)}</h3>
    ${question.card.body ? `<p>${esc(question.card.body)}</p>` : ''}
    <span class="kit-actions">${choices(question)}</span>
  </section>`;
}

export function timeline(moments) {
  return `<ol class="kit-timeline">${moments.map((moment) => `<li><time>${esc(time(moment.at))}</time> <span>${esc(moment.text)}</span></li>`).join('')}</ol>`;
}

export async function graph(host, source, kind = 'mermaid') {
  const { draw } = await import('/visuals.js');
  await draw(host, kind, source);
}

export async function burndown(host, points, field = 'open') {
  const { draw } = await import('/visuals.js');
  const spec = {
    data: { values: points },
    mark: { type: 'area', line: true, interpolate: 'monotone' },
    encoding: { x: { field: 'at', type: 'temporal', title: null }, y: { field, type: 'quantitative', title: field } },
  };
  await draw(host, 'chart', JSON.stringify(spec));
}
