import { mount as mountMap, validate as validateMap } from "./system-map.js";

const KINDS = new Set(["actor", "app", "service", "worker", "store", "log", "external", "artifact", "doc"]);
const SHAPES = new Set(["api", "relay", "gate", "router", "guard", "queue", "pool", "log", "recovery", "handoff", "watcher", "keys", "budget", "timer", "uploader", "codec", "compactor", "sweeper", "cache", "ui", "tool", "docs", "sdk", "module", "store"]);
const MEDIA = new Set(["memory", "disk", "bucket", "keys", "pages"]);
const EDGES = new Set(["sync", "async", "copy", "control"]);
const STATUS = new Set(["live", "planned", "retired"]);
const GROUPS = new Set(["zone", "machine", "process"]);
const STORES = new Set(["store", "log"]);
const SHAPE_OF = { actor: "ui", app: "ui", service: "module", worker: "module", store: "store", log: "log", external: "sdk", artifact: "module", doc: "docs" };
const EDGE_WORDS = { sync: "calls and waits", async: "hands off without waiting", copy: "copies data", control: "controls or checks" };
const STATUS_WORDS = { planned: "planned", retired: "retired" };

const kids = (n) => n.nodes || [];

function index(doc) {
  const node = {};
  const parent = {};
  const depth = {};
  const walk = (list, up, d) => {
    for (const n of list) {
      node[n.id] = n;
      parent[n.id] = up;
      depth[n.id] = d;
      walk(kids(n), n.id, d + 1);
    }
  };
  walk(doc.nodes || [], null, 1);
  const chain = (id) => {
    const out = [];
    for (let at = id; at != null; at = parent[at]) out.unshift(at);
    return out;
  };
  return { node, parent, depth, chain };
}

export function validate(doc) {
  if (!doc || typeof doc !== "object") return ["an architecture doc is a JSON object"];
  const out = [];
  if (doc.arch !== 1) out.push(`"arch" must be 1, the format version`);
  for (const f of ["name", "title"]) if (typeof doc[f] !== "string" || !doc[f].trim()) out.push(`"${f}" is required`);
  if (!Array.isArray(doc.nodes) || !doc.nodes.length) { out.push(`"nodes" needs at least one node`); return out; }
  const seen = {};
  const claim = (id, what) => {
    if (typeof id !== "string" || !/^[a-z][a-z0-9-]{0,39}$/.test(id)) out.push(`${what}: id "${id}" must start with a letter and use only lowercase letters, digits and dashes, 40 at most`);
    else if (seen[id]) out.push(`id "${id}" is used by a ${seen[id]} and a ${what}; ids are unique across the doc`);
    else seen[id] = what;
  };
  for (const g of doc.groups || []) claim(g.id, "group");
  const walk = (list) => {
    for (const n of list) {
      claim(n.id, "node");
      const at = `node "${n.id}"`;
      if (!n.name) out.push(`${at}: "name" is required`);
      if (!n.role) out.push(`${at}: "role" is required`);
      if (!KINDS.has(n.kind)) out.push(`${at}: "kind" is "${n.kind}", not one of ${[...KINDS].join(", ")}`);
      if (n.shape != null && !SHAPES.has(n.shape)) out.push(`${at}: "shape" is "${n.shape}", not one of ${[...SHAPES].join(", ")}`);
      if (n.status != null && !STATUS.has(n.status)) out.push(`${at}: "status" is "${n.status}", not live, planned or retired`);
      if (STORES.has(n.kind) && !MEDIA.has(n.medium)) out.push(`${at}: a ${n.kind} node needs "medium", one of ${[...MEDIA].join(", ")}`);
      if (!STORES.has(n.kind)) for (const f of ["medium", "tag", "holds"]) if (n[f] != null) out.push(`${at}: only store and log nodes take "${f}"`);
      if (n.shape === "store" && !STORES.has(n.kind)) out.push(`${at}: the store shape belongs to store and log nodes`);
      walk(kids(n));
    }
  };
  walk(doc.nodes);
  for (const s of doc.stories || []) claim(s.id, "story");
  const { node } = index(doc);
  const G = Object.fromEntries((doc.groups || []).map((g) => [g.id, g]));
  for (const g of doc.groups || []) {
    if (!GROUPS.has(g.kind || "zone")) out.push(`group "${g.id}": "kind" is "${g.kind}", not zone, machine or process`);
    if ((g.kind || "zone") === "process" && (!G[g.in] || G[g.in].kind !== "machine")) out.push(`group "${g.id}": a process group needs "in" naming a machine group`);
    if ((g.kind || "zone") !== "process" && g.in != null) out.push(`group "${g.id}": only a process group takes "in"`);
  }
  for (const n of Object.values(node)) if (n.group != null && !G[n.group]) out.push(`node "${n.id}": "group" names "${n.group}", which is not a group`);
  const ref = (where, id) => { if (!node[id]) out.push(`${where} names "${id}", which is not a node`); };
  (doc.edges || []).forEach((e, i) => {
    ref(`edge ${i + 1} "from"`, e.from);
    ref(`edge ${i + 1} "to"`, e.to);
    if (e.from === e.to) out.push(`edge ${i + 1} joins "${e.from}" to itself`);
    if (e.kind != null && !EDGES.has(e.kind)) out.push(`edge ${i + 1}: "kind" is "${e.kind}", not one of ${[...EDGES].join(", ")}`);
  });
  const steps = (where, list) => list.forEach((s, i) => {
    ref(`${where} step ${i + 1} "from"`, s.from);
    ref(`${where} step ${i + 1} "to"`, s.to);
    if (!s.say) out.push(`${where} step ${i + 1}: "say" is required`);
  });
  if (doc.path) {
    if (!doc.path.title) out.push(`path: "title" is required`);
    steps("path", doc.path.steps || []);
    (doc.path.steps || []).forEach((s, i) => { if (s.waitsFor != null && !(Number.isInteger(s.waitsFor) && s.waitsFor >= 1 && s.waitsFor <= i)) out.push(`path step ${i + 1}: "waitsFor" is ${s.waitsFor}, which is not an earlier step`); });
  }
  const labels = new Set();
  for (const s of doc.stories || []) {
    if (!s.label) out.push(`story "${s.id}": "label" is required`);
    else if (labels.has(s.label)) out.push(`story label "${s.label}" is used twice`);
    labels.add(s.label);
    if (s.tone != null && !["ok", "fail", "pending"].includes(s.tone)) out.push(`story "${s.id}": "tone" is "${s.tone}", not ok, fail or pending`);
    steps(`story "${s.id}"`, s.steps || []);
    (s.steps || []).forEach((x, i) => {
      if (x.mark != null && !["risk", "stop"].includes(x.mark)) out.push(`story "${s.id}" step ${i + 1}: "mark" is "${x.mark}", not risk or stop`);
      if (x.waitsFor != null) out.push(`story "${s.id}" step ${i + 1}: "waitsFor" belongs only on the path`);
    });
  }
  (doc.notes || []).forEach((n, i) => {
    if ((n.on == null) === (n.edge == null)) out.push(`note ${i + 1}: give "on" (a node) or "edge" (its from and to), exactly one`);
    if (n.on != null) ref(`note ${i + 1} "on"`, n.on);
    if (n.edge != null && !(doc.edges || []).some((e) => (e.from === n.edge.from && e.to === n.edge.to) || (e.from === n.edge.to && e.to === n.edge.from))) out.push(`note ${i + 1}: no edge joins "${n.edge.from}" and "${n.edge.to}"`);
  });
  if (doc.start && doc.start.node != null) ref(`start "node"`, doc.start.node);
  if (doc.start && doc.start.story != null && !(doc.stories || []).some((s) => s.id === doc.start.story)) out.push(`start "story" names "${doc.start.story}", which is not a story`);
  return out;
}

export function project(doc, focus) {
  const ix = index(doc);
  const top = focus == null ? doc.nodes : kids(ix.node[focus]);
  const fchain = focus == null ? [] : ix.chain(focus);
  const vis = {};
  const blockOf = {};
  for (const b of top) {
    vis[b.id] = b.id;
    blockOf[b.id] = b.id;
    for (const p of kids(b)) { vis[p.id] = p.id; blockOf[p.id] = b.id; }
  }
  const ctx = {};
  const see = (id) => {
    if (Object.hasOwn(vis, id)) return [vis[id], false];
    const c = ix.chain(id);
    if (fchain.every((x, i) => c[i] === x)) return [c.length > fchain.length + 1 ? vis[c[fchain.length + 1]] : null, false];
    let i = 0;
    while (i < c.length && c[i] === fchain[i]) i += 1;
    return [i === c.length ? null : c[i], true];
  };
  const pair = (u, v) => {
    let [a, ao] = see(u);
    let [z, zo] = see(v);
    if (a == null || z == null || (ao && zo)) return null;
    for (const [id, out] of [[a, ao], [z, zo]]) if (out) { ctx[id] = true; blockOf[id] = id; }
    if (blockOf[a] === blockOf[z] && (a === blockOf[a]) !== (z === blockOf[z])) { a = a === blockOf[a] ? z : a; z = a; }
    return [a, z];
  };
  const links = [];
  const seenLink = new Set();
  for (const e of doc.edges || []) {
    const p = pair(e.from, e.to);
    if (!p || p[0] === p[1]) continue;
    const style = e.kind || "sync";
    const key = `${p[0]}|${p[1]}|${style}`;
    if (seenLink.has(key)) continue;
    seenLink.add(key);
    links.push({ from: p[0], to: p[1], ...(e.label ? { label: e.label } : {}), style });
  }
  const walk = (list, keep) => {
    const out = [];
    const at = [];
    let run = null;
    list.forEach((s) => {
      const p = pair(s.from, s.to);
      if (!p) { run = null; at.push(null); return; }
      const box = blockOf[p[0]] === blockOf[p[1]] ? blockOf[p[0]] : null;
      if (box && run && run.box === box) {
        run.n += 1;
        Object.assign(run.step, { from: box, to: box, say: `${run.say} (and ${run.n - 1} more)` });
        at.push(out.length);
        return;
      }
      const step = { from: p[0], to: p[1], say: s.say, ...keep(s) };
      out.push(step);
      at.push(out.length);
      run = box ? { box, step, say: s.say, n: 1 } : null;
    });
    return { out, at };
  };
  const path = doc.path && walk(doc.path.steps || [], () => ({}));
  if (path) {
    path.out.forEach((s, i) => {
      const w = doc.path.steps.find((x, j) => path.at[j] === i + 1 && x.waitsFor != null);
      const to = w && path.at[w.waitsFor - 1];
      if (to && to <= i && blockOf[s.from] !== blockOf[s.to]) s.waitsFor = to;
    });
  }
  const stories = (doc.stories || []).map((st) => ({ st, w: walk(st.steps || [], (s) => (s.mark ? { mark: s.mark } : {})) })).filter((x) => x.w.out.length);
  const blockNode = (n) => {
    const own = kids(n);
    const out = { id: n.id, kind: n.kind, name: n.name, role: n.role };
    if (n.group && !ctx[n.id]) out.group = n.group;
    if (STORES.has(n.kind)) { out.medium = n.medium; if (n.tag) out.tag = n.tag; if (n.holds) out.contents = n.holds; }
    for (const f of ["about", "critical", "status", "badges", "lane"]) if (n[f] != null) out[f] = n[f];
    if (own.length) out.parts = own.map(partNode);
    return out;
  };
  const partNode = (n) => {
    const shape = n.shape || SHAPE_OF[n.kind];
    const out = { id: n.id, shape, name: n.name, role: n.role };
    if (shape === "store") { out.medium = n.medium; if (n.tag) out.tag = n.tag; }
    for (const f of ["about", "status", "badges"]) if (n[f] != null) out[f] = n[f];
    return out;
  };
  const outside = (id) => {
    const n = ix.node[id];
    return { id, kind: "external", name: n.name, role: n.role, ...(n.about ? { about: n.about } : {}), ...(n.status ? { status: n.status } : {}) };
  };
  const blocks = [...top.map(blockNode), ...Object.keys(ctx).map(outside)];
  const used = new Set(blocks.map((b) => b.group).filter(Boolean));
  for (const g of doc.groups || []) if (used.has(g.id) && g.kind === "process") used.add(g.in);
  const groups = (doc.groups || []).filter((g) => used.has(g.id)).map((g) => ({ id: g.id, label: g.label, kind: g.kind || "zone", ...(g.in ? { in: g.in } : {}) }));
  const notes = [];
  for (const n of doc.notes || []) {
    if (n.on != null) { if (vis[n.on] === n.on) notes.push({ on: n.on, text: n.text }); continue; }
    const p = pair(n.edge.from, n.edge.to);
    if (p && p[0] !== p[1] && links.some((l) => (l.from === p[0] && l.to === p[1]) || (l.from === p[1] && l.to === p[0]))) notes.push({ link: { from: p[0], to: p[1] }, text: n.text });
  }
  const here = focus == null ? null : ix.node[focus];
  const map = { title: here ? here.claim || `${here.name}: ${here.role}` : doc.title, blocks };
  if (groups.length) map.groups = groups;
  if (links.length) map.links = links;
  if (path && path.out.length) map.path = { title: doc.path.title, steps: path.out };
  if (stories.length) map.stories = stories.map(({ st, w }) => ({ id: st.id, label: st.label, ...(st.title ? { title: st.title } : {}), ...(st.tone ? { tone: st.tone } : {}), steps: w.out }));
  if (notes.length) map.notes = notes;
  if (doc.lanes) map.lanes = doc.lanes;
  return map;
}

export function toMarkdown(doc) {
  const { node } = index(doc);
  const name = (id) => (node[id] ? node[id].name : id);
  const L = [`# ${doc.name}`, "", doc.title];
  if (doc.summary) L.push("", doc.summary);
  const G = Object.fromEntries((doc.groups || []).map((g) => [g.id, g.label]));
  L.push("", "## Systems");
  const walk = (list, pad) => list.forEach((n) => {
    const bits = [`${pad}- **${n.name}** (${n.kind}${n.medium ? `, ${n.medium}` : ""}${n.status && n.status !== "live" ? `, ${STATUS_WORDS[n.status]}` : ""}${n.group ? `, in ${G[n.group]}` : ""}): ${n.role}`];
    if (n.about) bits.push(`. ${n.about}`);
    L.push(bits.join(""));
    for (const h of n.holds || []) L.push(`${pad}  - Holds: ${h.text}${h.note ? `, ${h.note}` : ""}`);
    walk(kids(n), `${pad}  `);
  });
  walk(doc.nodes, "");
  if ((doc.edges || []).length) {
    L.push("", "## Connections");
    for (const e of doc.edges) L.push(`- ${name(e.from)} to ${name(e.to)}${e.label ? `: ${e.label}` : ""} (${EDGE_WORDS[e.kind || "sync"]})`);
  }
  const steps = (list) => list.forEach((s, i) => L.push(`${i + 1}. ${s.say} (${s.from === s.to ? `in ${name(s.from)}` : `${name(s.from)} to ${name(s.to)}`})${s.waitsFor ? `, waits for step ${s.waitsFor}` : ""}${s.mark === "risk" ? ", a risk" : s.mark === "stop" ? ", refused on purpose" : ""}`));
  if (doc.path) { L.push("", `## ${doc.path.title}`); steps(doc.path.steps || []); }
  for (const st of doc.stories || []) { L.push("", `## ${st.title || st.label}`); steps(st.steps || []); }
  if ((doc.notes || []).length) {
    L.push("", "## Notes");
    for (const n of doc.notes) L.push(`- ${n.on != null ? name(n.on) : `${name(n.edge.from)} to ${name(n.edge.to)}`}: ${n.text}`);
  }
  return `${L.join("\n")}\n`;
}

const CSS = String.raw`
.arch { display: grid; border: 1px solid #e2e5eb; border-radius: 12px; overflow: hidden; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .arch { border-color: #30353d; } }
:root[data-theme="dark"] .arch { border-color: #30353d; }
.arch > .arch-map > .smap { border-radius: 0; }
.arch-key { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 16px; padding: 8px 12px 10px; border-top: 1px solid color-mix(in srgb, currentColor 14%, transparent); font: 12px/1.4 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; color: color-mix(in srgb, currentColor 70%, transparent); }
.arch-key span { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.arch-key svg { flex: none; }
.arch-key b { font-weight: 650; color: inherit; }
.arch-problems { padding: 12px 14px; color: #c9302a; font: 13px/1.5 ui-sans-serif, system-ui, sans-serif; }
.arch-problems ul { margin: 6px 0 0; padding-left: 18px; }
`;

const KEY = [
  ["sync", "Calls and waits", "", "M2 6 H34"],
  ["async", "Hands off", "6 4", "M2 6 H34"],
  ["copy", "Copies data", "9 3", "M2 6 H34"],
  ["control", "Controls or checks", "1.5 4", "M2 6 H34"],
];

const key = () => `<div class="arch-key" aria-label="Key">${KEY.map(([k, label, dash, d]) => `<span data-k="${k}"><svg width="36" height="12" viewBox="0 0 36 12" aria-hidden="true"><path d="${d}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"${dash ? ` stroke-dasharray="${dash}"` : ""}/></svg>${label}</span>`).join("")}<span><svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><circle cx="7" cy="7" r="6.5" fill="#2f5bd8"/><text x="7" y="10.5" font-size="9" font-weight="700" text-anchor="middle" fill="#fff" font-family="system-ui">1</text></svg>Main path step</span><span><svg width="16" height="12" viewBox="0 0 16 12" aria-hidden="true"><rect x="1" y="1" width="14" height="10" rx="2" fill="#fff4c2" stroke="#e8dca4"/></svg>Note</span></div>`;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export function mount(host, doc, opts = {}) {
  if (!document.getElementById("arch-css")) {
    const st = document.createElement("style");
    st.id = "arch-css";
    st.textContent = CSS;
    document.head.append(st);
  }
  let cur = doc;
  let ix = null;
  let problems = [];
  let map = null;
  const start = opts.state || {};
  let focus = start.focus ?? null;
  const root = document.createElement("div");
  root.className = "arch";
  host.replaceChildren(root);
  const emit = (v) => { if (opts.onState) opts.onState({ ...(focus != null ? { focus } : {}), ...v }); };
  const go = (next, view) => {
    focus = next;
    map.update(project(cur, focus), view || {});
    emit(view || {});
  };
  const nav = {
    trail: () => (focus == null ? [] : [cur.name, ...ix.chain(focus).slice(0, -1).map((id) => ix.node[id].name)]),
    here: () => (focus == null ? cur.name : ix.node[focus].name),
    up: (i) => {
      const chain = ix.chain(focus);
      const next = i === 0 ? null : chain[i - 1];
      go(next, { block: chain[i] });
    },
    deeper: (id) => {
      const n = ix.node[id];
      if (!n) return null;
      const blockLevel = focus == null ? ix.depth[id] === 1 : ix.parent[id] === focus;
      const partLevel = focus == null ? ix.depth[id] === 2 : ix.parent[ix.parent[id]] === focus;
      if (blockLevel && kids(n).some((k) => kids(k).length)) return `Zoom into ${n.name}`;
      if (partLevel && kids(n).length) return `Zoom into ${n.name}`;
      if (!blockLevel && !partLevel) return `Go to ${n.name}`;
      return null;
    },
    down: (id) => {
      const blockLevel = focus == null ? ix.depth[id] === 1 : ix.parent[id] === focus;
      const partLevel = focus == null ? ix.depth[id] === 2 : ix.parent[ix.parent[id]] === focus;
      if (blockLevel) go(id, {});
      else if (partLevel) go(ix.parent[id], { block: id });
      else go(ix.parent[id], { block: id });
    },
  };
  const draw = () => {
    problems = validate(cur);
    if (problems.length) {
      if (map) { map.destroy(); map = null; }
      root.innerHTML = `<div class="arch-problems" role="alert"><b>This architecture doc has ${problems.length === 1 ? "a problem" : `${problems.length} problems`} to fix</b><ul>${problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div>`;
      return;
    }
    ix = index(cur);
    if (focus != null && !ix.node[focus]) focus = null;
    const level = project(cur, focus);
    const extra = validateMap(level, { derived: true });
    if (extra.length) throw new Error(`architecture doc "${cur.name}" projects to an invalid map: ${extra.join("; ")}`);
    if (map) { map.update(level); return; }
    root.innerHTML = `<div class="arch-map"></div>${opts.key === false ? "" : key()}`;
    const view = { story: start.story, block: start.block, part: start.part };
    if (!opts.state && cur.start) {
      if (cur.start.story) view.story = cur.start.story;
      if (cur.start.node) { const p = ix.parent[cur.start.node]; focus = p == null ? null : ix.parent[p]; view.block = p == null ? cur.start.node : p; if (p != null) view.part = cur.start.node; }
    }
    map = mountMap(root.querySelector(".arch-map"), project(cur, focus), { skin: "clean", nav, derived: true, fill: opts.fill, state: view, onState: emit });
  };
  draw();
  return {
    get problems() { return problems; },
    get ready() { return map ? map.ready : Promise.resolve(); },
    update(next) { cur = next; draw(); },
    show(view) {
      if (!map) return;
      const f = view.focus ?? null;
      if (f !== focus) go(f, view);
      else map.show(view);
    },
    destroy() { if (map) map.destroy(); host.replaceChildren(); },
  };
}
