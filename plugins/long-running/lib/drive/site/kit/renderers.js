const SLACK = /^https:\/\/[\w-]+\.slack\.com\//;
export const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
export const counted = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const VISUAL_KINDS = new Set(['arch', 'mermaid', 'chart', 'compare', 'dot']);
const WORD_FENCES = new Set(['', 'text', 'txt', 'plain']);
export const fenceKind = (language) => VISUAL_KINDS.has(language) ? language : `code:${WORD_FENCES.has(language) ? 'text' : language}`;
export const refusedVisual = (kind, source, failure) => `<p class="refused">This ${esc(kind.split(':')[0])} did not draw (${esc(failure.message)}), so here is what it is drawn from.</p><pre><code>${esc(source)}</code></pre>`;
export const link = (text, url) => (url && !SLACK.test(url) ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text));
const URL_TEXT = /https?:\/\/[^\s<>|]*[^\s<>|.,;:!?)\]'"`]/g;
const URL_NAMES = [
  [/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/(\d+)/, (number) => `#${number}`],
  [/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/commit\/(\w{7})/, (sha) => `commit ${sha}`],
  [/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/releases\/tag\/(?:[\w-]+\/)*([\w.-]+)/, (tag) => `release ${tag}`],
  [/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/actions\/runs\//, () => 'Actions run'],
  [/^https:\/\/buildkite\.com\/[\w-]+\/([\w-]+)\/builds\/(\d+)/, (pipeline, number) => `${pipeline} build ${number}`],
  [/^https:\/\/[\w-]+\.slack\.com\/archives\//, () => 'Slack message'],
  [/^https:\/\/app\.datadoghq\.com\/monitors\//, () => 'Datadog monitor'],
];
const SOURCE_CITE = /(^|[^\w/.@`-])`?((?:[\w.@-]+\/)+[\w.@-]+\.[A-Za-z][A-Za-z0-9]*):(\d+)(?:-(\d+))?`?(?![\w-]|[./]\w)/g;
const PICTURE_LINE = /^\s*!\[([^\]]*)\]\((\/[^)\s]+)\)\s*$/;
const QUOTE_LINE = /^\s*>\s?/;
const TABLE_ROW = /^\s*\|/;
const LIST_ITEM = /^\s*(?:[-*•]|\d+[.)])\s+/;
const KEY_LINE = /^([A-Z][A-Za-z0-9 ()'\/-]{0,38}):(?:\s+(.*))?$/;
const HEADING_LINE = /^\s*(?:#{1,4}\s+(.+?)|\*\*([^*\n]+)\*\*|\*([^*\n]+)\*):?\s*$/;
const CHECK_FACTS = { pass: ['Tests pass', 'good'], fail: ['Tests fail', 'bad'], pending: ['Tests running', 'warn'], none: ['No CI', ''] };
const REVIEW_FACTS = { approved: ['Review approved', 'good'], stale: ['Approved on an older head', ''], changes: ['Changes requested', 'bad'] };
const TAP_KEYS = { Approve: 'a', Hold: 'h' };
export const DECIDED = { tap: 'You tapped', answered: 'You picked', undone: 'You took back' };
export const HAPPENED = { order: 'You asked', answer: 'You answered', live: 'Live', merged: 'Merged', failed: 'Failed', refused: 'No card drawn', said: 'Sent you' };
const FEATURE_KINDS = new Set(['order', 'live', 'failed']);

export function urlName(url) {
  const found = URL_NAMES.find(([shape]) => shape.test(url));
  return found ? found[1](...url.match(found[0]).slice(1)) : new URL(url).hostname;
}

export function named(text) {
  return String(text).replace(URL_TEXT, urlName);
}

export function optionParts(option) {
  const cut = option.search(/:\s|\.\s/);
  if (cut < 0) return [option, ''];
  return [option.slice(0, option[cut] === '.' ? cut + 1 : cut), option.slice(cut + 1).trim()];
}

export function cells(line) {
  const held = [];
  return line.trim().replace(/^\||\|$/g, '').replace(/<https?:\/\/[^>]+>|`[^`]+`|\\\|/g, (value) => `\u0000${held.push(value) - 1}\u0000`).split('|').map((value) => value.trim().replace(/\u0000(\d+)\u0000/g, (_, n) => held[Number(n)]).replace(/\\\|/g, '|'));
}

function decisionTable(text) {
  const lines = String(text ?? '').split('\n');
  const start = lines.findIndex((line, i) => /^\s*\|/.test(line) && /^\s*\|[\s:|\-]+\|\s*$/.test(lines[i + 1] ?? ''));
  if (start < 0) return [];
  const end = lines.findIndex((line, i) => i > start && !/^\s*\|/.test(line));
  return lines.slice(start, end < 0 ? undefined : end).filter((line) => !/^\s*\|[\s:|\-]+\|\s*$/.test(line)).map(cells);
}

export function decisionPlain(text) {
  return String(text ?? '').replace(/<https?:\/\/[^|>]+\|([^>]+)>|\[([^\]]+)\]\(https?:\/\/[^)]+\)/g, (_, slack, markdown) => slack ?? markdown).replace(/https?:\/\/[^\s>]+/g, urlName).replace(/[*`]/g, '').replace(/^\s*(?:New|Fix|Cleanup|Port):\s*/i, '').trim();
}

export function decisionStory(card) {
  const body = (card.body ?? '').replace(/^\s*\[table block\]\s*$/gm, '').trim();
  const [head, ...rows] = decisionTable(body);
  const compared = rows.length === card.options.length;
  const format = compared ? 'lab' : card.options.length > 3 ? 'interview' : 'cockpit';
  return {
    format,
    headline: decisionPlain(card.title),
    context: decisionPlain(body.split(/\n|(?<=[.!?])\s/)[0]),
    options: card.options.map((label, i) => {
      const [title, consequence] = optionParts(label);
      return { label, title: decisionPlain(title), consequence: decisionPlain(consequence).replace(/^./, (c) => c.toUpperCase()), metrics: compared ? head.slice(1).map((column, n) => ({ label: decisionPlain(column), value: decisionPlain(rows[i][n + 1]) })) : [] };
    }),
  };
}

export function sourceCites(text) {
  return [...String(text).matchAll(SOURCE_CITE)].map(([, , path, from]) => ({ path, line: from }));
}

export function fact(html, tip = '', tone = '') {
  return `<span class="fact${tone ? ` ${tone}` : ''}"${tip ? ` tabindex="0" data-tip="${esc(tip)}"` : ''}>${html}</span>`;
}

export function stateFacts(row) {
  const [checks, checksTone] = CHECK_FACTS[row.checks] ?? CHECK_FACTS.none;
  const [review, reviewTone] = REVIEW_FACTS[row.review] ?? ['No review yet', ''];
  return fact(esc(checks), '', checksTone) + fact(esc(review), '', reviewTone);
}

export function createRenderers(context) {
  function inline(text, cite = context.repo()) {
    const spans = [];
    const keep = (html) => `\u0000${spans.push(html) - 1}\u0000`;
    let out = cite ? String(text).replace(SOURCE_CITE, (_, pre, path, from, to) => `${pre}${keep(context.sourceLink(cite, path, from, to))}`) : String(text);
    out = out
      .replace(/`([^`]+)`/g, (_, code) => keep(`<code>${esc(code)}</code>`))
      .replace(/<(https?:\/\/[^|>\s]+)\|([^>]+)>/g, (_, url, label) => keep(link(label, url)))
      .replace(/<(https?:\/\/[^|>\s]+)>/g, (_, url) => keep(link(urlName(url), url)))
      .replace(/!\[([^\]]*)\]\((https?:\/\/[^)\s]+)\)/g, (_, alt, url) => keep(context.picture(url, alt)))
      .replace(/\[([^\]]+)\]\((#\/[^)\s]+)\)/g, (_, label, route) => keep(`<a class="ref" href="${esc(route)}">${esc(label)}</a>`))
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_, label, url) => keep(link(label, url)))
      .replace(URL_TEXT, (url) => keep(link(urlName(url), url)));
    out = esc(out)
      .replace(/&lt;br\s*\/?&gt;/gi, '<br>')
      .replace(/\*\*((?:[^*\n]|\*[^*\n]+\*)+)\*\*/g, (_, bold) => `<b>${bold.replace(/\*([^*\n]+)\*/g, '<i>$1</i>')}</b>`)
      .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, '$1<b>$2</b>')
      .replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,;:!?]|$)/g, '$1<i>$2</i>');
    return out.replace(/\u0000(\d+)\u0000/g, (_, n) => spans[Number(n)]);
  }

  function prose(text, cite) {
    const blocks = [];
    const parts = String(text ?? '').replace(/^\s*\[table block\]\s*$/gm, '').split(/^```[ \t]*([\w+-]*)[^\n]*\n([\s\S]*?)^```\s*$/m);
    parts.forEach((part, i) => {
      if (i % 3 === 1) return;
      if (i % 3 === 2) {
        blocks.push(context.fenced(fenceKind(parts[i - 1].toLowerCase()), part));
        return;
      }
      for (const para of part.split(/\n\s*\n/)) {
        const lines = para.split('\n').filter((line) => line.trim());
        if (!lines.length) continue;
        while (lines.length) {
          const heading = lines[0].match(HEADING_LINE);
          if (heading) {
            blocks.push(`<h4 class="hd">${inline(heading[1] ?? heading[2] ?? heading[3], cite)}</h4>`);
            lines.shift();
            continue;
          }
          const row = TABLE_ROW.test(lines[0]);
          const end = lines.findIndex((line) => TABLE_ROW.test(line) !== row || HEADING_LINE.test(line));
          const run = lines.splice(0, end < 0 ? lines.length : end);
          if (row) {
            blocks.push(table(run, cite));
          } else if (run.some((line) => PICTURE_LINE.test(line) || QUOTE_LINE.test(line))) {
            blocks.push(...lineBlocks(run, cite));
          } else if (run.every((line) => LIST_ITEM.test(line))) {
            blocks.push(listOf(run, cite));
          } else if (run.filter((line) => KEY_LINE.test(line)).length > 1) {
            const at = run.findIndex((line) => KEY_LINE.test(line));
            if (at) blocks.push(`<p>${run.slice(0, at).map((line) => inline(line, cite)).join('<br>')}</p>`);
            blocks.push(factList(run.slice(at), cite));
          } else {
            blocks.push(`<p>${run.map((line) => inline(line, cite)).join('<br>')}</p>`);
          }
        }
      }
    });
    return blocks.join('');
  }

  function listOf(lines, cite) {
    return `<ul>${lines.map((line) => `<li>${inline(line.replace(LIST_ITEM, ''), cite)}</li>`).join('')}</ul>`;
  }

  function factList(lines, cite) {
    const facts = [];
    for (const line of lines) {
      const key = line.match(KEY_LINE);
      if (key) facts.push([key[1], key[2] ? [key[2]] : []]);
      else facts.at(-1)[1].push(line);
    }
    const said = (rest) => {
      const items = rest.filter((line) => LIST_ITEM.test(line));
      return `${rest.filter((line) => !LIST_ITEM.test(line)).map((line) => inline(line, cite)).join('<br>')}${items.length ? listOf(items, cite) : ''}`;
    };
    return `<dl class="keyed">${facts.map(([key, rest]) => `<dt>${esc(key)}</dt><dd>${said(rest)}</dd>`).join('')}</dl>`;
  }

  function lineBlocks(lines, cite) {
    const blocks = [];
    let quote = [];
    const flush = () => {
      if (quote.length) blocks.push(`<blockquote class="quote">${quote.map((line) => inline(line, cite)).join('<br>')}</blockquote>`);
      quote = [];
    };
    for (const line of lines) {
      if (QUOTE_LINE.test(line)) {
        quote.push(line.replace(QUOTE_LINE, ''));
        continue;
      }
      flush();
      const shot = line.match(PICTURE_LINE);
      blocks.push(shot ? context.shown(shot[2], shot[1]) : `<p>${inline(line, cite)}</p>`);
    }
    flush();
    return blocks;
  }

  function table(lines, cite) {
    const rows = lines.filter((line) => !/^\s*\|[\s:|-]+$/.test(line)).map((line) => (Array.isArray(line) ? line : cells(line)));
    const [head, ...body] = rows;
    return `<div class="table-scroll-hint">Scroll to see every column ↔</div><div class="tablewrap" tabindex="0" role="region" aria-label="Comparison table; scroll for all columns"><table><thead><tr>${head.map((cell) => `<th>${inline(cell, cite)}</th>`).join('')}</tr></thead><tbody>${body
      .map((row) => `<tr>${row.map((cell) => `<td>${inline(cell, cite)}</td>`).join('')}</tr>`)
      .join('')}</tbody></table></div>`;
  }

  function choices(item, { given, keyed = false, shown = -1, restored = false } = {}) {
    const answered = item.answer;
    if (given?.done) return `<div class="answered">✓ <b>${esc(given.label)}</b> · answered ${context.time(given.at)}</div>`;
    const held = given && !given.error;
    const failed = given?.error ? `<p class="refused">${esc(given.error)}</p>` : '';
    const buttons = (item.card?.options ?? ['Approve', 'Hold']).map((option, i) => {
      const attrs = `data-card="${esc(answered)}" data-label="${esc(option)}"${held ? ' disabled' : ''}`;
      if (item.kind === 'tap') return `<button class="${option === 'Approve' ? 'primary' : 'secondary'}" ${attrs}>${keyed ? `<kbd>${TAP_KEYS[option] ?? i + 1}</kbd>` : ''}${esc(option)}</button>`;
      const [title, detail] = optionParts(option);
      const recommended = option === item.card.recommended;
      return `<button class="choice${recommended ? ' recommended' : ''}${i === shown ? ' on' : ''}" ${attrs} data-preview="${i}">
        <span class="key">${keyed ? i + 1 : ''}</span>
        <span><span class="choice-title">${esc(title)}${recommended ? '<em>Recommended</em>' : ''}</span>${detail ? `<span class="choice-detail">${esc(detail)}</span>` : ''}</span>
        <span class="arrow" aria-hidden="true">›</span>
      </button>`;
    });
    const back = restored ? '<p class="restored" role="status">Restored. This is being asked again.</p>' : '';
    return `${back}<div class="${item.kind === 'tap' ? 'pr-picks' : 'choices'}">${buttons.join('')}</div>${failed}`;
  }

  function momentText(entry) {
    if (entry.kind === 'heard') return `<b class="what">${esc(entry.detail)}</b> ${context.reportLine(entry.text)}`;
    const text = FEATURE_KINDS.has(entry.kind) ? context.featureLink(entry.feature, entry.text) : link(entry.text, entry.url);
    return `<b class="what">${DECIDED[entry.kind] ? `${DECIDED[entry.kind]} ${esc(entry.detail)} on` : HAPPENED[entry.kind]}</b> ${text}`;
  }

  return { inline, prose, table, choices, momentText };
}
