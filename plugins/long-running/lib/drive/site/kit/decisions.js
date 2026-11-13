function decisionCells(line) {
  const held = [];
  return line.trim().replace(/^\||\|$/g, '').replace(/<https?:\/\/[^>]+>|`[^`]+`|\\\|/g, (value) => `\u0000${held.push(value) - 1}\u0000`).split('|').map((value) => value.trim().replace(/\u0000(\d+)\u0000/g, (_, n) => held[Number(n)]).replace(/\\\|/g, '|'));
}

function decisionTable(text) {
  const lines = String(text ?? '').split('\n');
  const start = lines.findIndex((line, i) => /^\s*\|/.test(line) && /^\s*\|[\s:|\-]+\|\s*$/.test(lines[i + 1] ?? ''));
  if (start < 0) return [];
  const end = lines.findIndex((line, i) => i > start && !/^\s*\|/.test(line));
  return lines.slice(start, end < 0 ? undefined : end).filter((line) => !/^\s*\|[\s:|\-]+\|\s*$/.test(line)).map(decisionCells);
}

function decisionPlain(text) {
  return String(text ?? '').replace(/<https?:\/\/[^|>]+\|([^>]+)>|\[([^\]]+)\]\(https?:\/\/[^)]+\)/g, (_, slack, markdown) => slack ?? markdown).replace(/https?:\/\/[^\s>]+/g, urlName).replace(/[*`]/g, '').replace(/^\s*(?:New|Fix|Cleanup|Port):\s*/i, '').trim();
}

function decisionStory(card) {
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
