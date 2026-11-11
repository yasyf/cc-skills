import hljs from "https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.12.0/es/highlight.min.js";

const HLJS = "https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.12.0";
const ARCH_MAP = "https://cdn.jsdelivr.net/gh/yasyf/cc-present@v0.60.2/plugin/.claude/components/display/src/arch-map.min.js";
const MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@12.3.0/dist/mermaid.esm.min.mjs";
const VEGA_EMBED = "https://cdn.jsdelivr.net/npm/vega-embed@7.3.0/+esm";
const VIZ = "https://cdn.jsdelivr.net/npm/@viz-js/viz@3.31.0/dist/viz.js";
const GUESSED = new Set(["", "text", "txt", "plain", "plaintext"]);
const RAW = new Set(["json", "jsonc", "json5", "jsonl", "ndjson", "yaml", "yml", "csv", "tsv", "log", "logs", "console", "terminal", "shell-session", "sh-session", "pwsh-session", "output", "stdout", "stderr", "traceback", "pytb", "stacktrace"]);
const GRAMMAR_FILES = { proto: "protobuf" };
const SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif";
const MONO = "Menlo, ui-monospace, SFMono-Regular, monospace";
const CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
const INK = "#0b0b0b";
const MUTED = "#52514e";
const WIDEST = 960;
const CHART = {
  background: "#fcfcfb",
  font: SANS,
  padding: 8,
  view: { stroke: null },
  range: { category: CATEGORICAL },
  axis: { domainColor: "#c9c8c4", gridColor: "#ecebe8", tickColor: "#c9c8c4", labelColor: MUTED, titleColor: MUTED, labelFontSize: 11, titleFontSize: 11, titleFontWeight: 500 },
  axisX: { labelAngle: 0 },
  legend: { labelColor: MUTED, titleColor: MUTED, labelFontSize: 11, titleFontSize: 11 },
  title: { color: INK, fontSize: 13, fontWeight: 600, anchor: "start" },
  mark: { tooltip: true, color: CATEGORICAL[0] },
  bar: { cornerRadiusEnd: 4 },
  line: { strokeWidth: 2 },
  point: { size: 64, filled: true },
};
const MERMAID_THEME = {
  fontFamily: SANS,
  fontSize: "15px",
  primaryColor: "#f6f8fa",
  primaryBorderColor: "#afb8c1",
  primaryTextColor: INK,
  secondaryColor: "#ffffff",
  tertiaryColor: "#fbfbfa",
  edgeLabelBackground: "#ffffff",
  clusterBkg: "#fbfbfa",
  clusterBorder: "#d1d9e0",
  lineColor: MUTED,
  textColor: INK,
  useGradient: false,
  dropShadow: "none",
};
const MERMAID_CSS = `.node rect{rx:6px;ry:6px}.edgeLabel,.edgeLabel p{color:${MUTED};font-size:13px}.labelBkg{background:#fff}`;
const DOT_FONT = { fontname: "Helvetica,Arial,sans-serif", fontsize: 12 };
const CSS = `
.visual-code{margin:0;padding:8px 12px;width:max-content;max-width:760px;box-sizing:border-box;font:12.5px/1.5 ${MONO};color:#1f2328;background:#f6f8fa;border:1px solid #d1d9e0;border-radius:6px;white-space:pre-wrap;overflow-wrap:anywhere;tab-size:4}
.visual-code>code.hljs{display:block;padding:0;background:none;font:inherit;overflow:visible}
.visual-compare{display:flex;flex-wrap:wrap;gap:10px;align-items:stretch;max-width:1180px;font:13px/1.45 ${SANS};color:#1f2328}
.visual-option{flex:0 1 auto;min-width:200px;max-width:560px;display:flex;flex-direction:column;border:1px solid #d1d9e0;border-radius:8px;background:#fff;overflow:hidden}
.visual-option.recommended{border:2px solid #1a7f37}
.visual-option>header{font-weight:600;padding:6px 10px;background:#f6f8fa;border-bottom:1px solid #d1d9e0}
.visual-option.recommended>header{background:#dafbe1}
.visual-option>header>span{margin-left:8px;font-weight:500;font-size:11px;color:#1a7f37}
.visual-option .visual-code{width:auto;max-width:540px;border:0;border-radius:0;background:#f6f8fa}
.visual-option>ul{margin:0;padding:6px 10px 8px 24px}
.visual-option>ul>li.plus::marker{content:"+  ";color:#1a7f37;font-weight:700}
.visual-option>ul>li.minus::marker{content:"\\2212  ";color:#cf222e;font-weight:700}
`;

hljs.registerLanguage("sand", (hljs) => ({
  name: "Sand",
  keywords: {
    keyword:
      "fn page_fn external react test endpoint abstract for pfor while page_while if page_if else return break continue loop emit emit_each async throw try module type masked resource shared exclusive consume in to from open queue render exit exit_error exit_cancel assert assert_with_code assert_with_page throws nothrow batch table foreign retired",
    type: "text bytes prompt prompt_block bool int64 int decimal float json any file interface date datetime run_id",
    literal: "true false null",
    built_in:
      "click navigate set_input set_dropdown key_press click_download gen gen_with_code gen_table gen_text gen_text_with_code gen_bool page_gen js js_text js_table js_bool page_script cast log sleep identity optional required is_null run_ctx action_ctx join contains first json_parse json_try_parse json_stringify datetime_now datetime_parse datetime_iso date_parse date_iso read_file read_dir write_file text_to_file range get_secret_text",
  },
  contains: [
    hljs.C_LINE_COMMENT_MODE,
    { scope: "string", begin: /\{\|/, end: /\|\}/ },
    { scope: "string", begin: /%\{/, end: /\}%/ },
    { scope: "string", begin: /`/, end: /`/, contains: [hljs.BACKSLASH_ESCAPE, { scope: "subst", begin: /\{/, end: /\}/ }] },
    hljs.QUOTE_STRING_MODE,
    { scope: "variable", match: /@[A-Za-z_]\w*/ },
    { match: [/\b(?:fn|page_fn|endpoint|test|external|react|abstract)\b/, /\s+/, /[A-Za-z_]\w*/], scope: { 1: "keyword", 3: "title.function" } },
    { scope: "title.class", match: /\b[A-Z]\w*/ },
    { scope: "number", match: /\b\d[\d_]*(?:\.[\d_]+f?)?\b/ },
    { scope: "operator", match: /->|::|\.\.|&&|\|\||[=!<>]=?/ },
  ],
}));

const styled = new WeakMap();
let mermaidLoaded;
let vizLoaded;
let mermaidCount = 0;

function style(document) {
  if (!styled.has(document)) {
    const sheet = Object.assign(document.createElement("link"), { rel: "stylesheet", href: `${HLJS}/styles/github.min.css` });
    const loaded = new Promise((resolve) => sheet.addEventListener("load", resolve, { once: true }));
    document.head.append(sheet, Object.assign(document.createElement("style"), { textContent: CSS }));
    styled.set(document, loaded);
  }
  return styled.get(document);
}

async function grammar(name) {
  if (GUESSED.has(name)) return null;
  if (hljs.getLanguage(name)) return name;
  try {
    hljs.registerLanguage(name, (await import(`${HLJS}/es/languages/${GRAMMAR_FILES[name] ?? name}.min.js`)).default);
    return name;
  } catch {
    return null;
  }
}

async function code(document, name, source) {
  const text = source.replace(/\n+$/, "");
  const language = await grammar(name.toLowerCase());
  const pre = Object.assign(document.createElement("pre"), { className: "visual-code" });
  const lit = language ? hljs.highlight(text, { language }) : hljs.highlightAuto(text);
  pre.innerHTML = `<code class="hljs">${lit.value}</code>`;
  return pre;
}

async function compare(document, source) {
  const { options } = JSON.parse(source);
  const grid = Object.assign(document.createElement("div"), { className: "visual-compare" });
  for (const option of options) {
    const panel = Object.assign(document.createElement("section"), { className: `visual-option${option.recommended ? " recommended" : ""}` });
    const header = Object.assign(document.createElement("header"), { textContent: option.name });
    if (option.recommended) header.append(Object.assign(document.createElement("span"), { textContent: "recommended" }));
    panel.append(header);
    if (option.code) panel.append(await code(document, option.language ?? "", option.code));
    if (option.points?.length) {
      const list = document.createElement("ul");
      for (const point of option.points) {
        const sign = point.match(/^([+-])\s+/);
        list.append(Object.assign(document.createElement("li"), { className: sign ? (sign[1] === "+" ? "plus" : "minus") : "", textContent: sign ? point.slice(sign[0].length) : point }));
      }
      panel.append(list);
    }
    grid.append(panel);
  }
  return grid;
}

async function chart(host, source) {
  const spec = JSON.parse(source);
  const { default: embed } = await import(VEGA_EMBED);
  const sized = spec.mark || spec.layer ? { width: 420, height: 220, ...spec } : spec;
  await embed(host, sized, { actions: false, renderer: "svg", config: CHART });
}

async function mermaid(host, source) {
  mermaidLoaded ??= import(MERMAID).then(({ default: diagrams }) => {
    diagrams.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "base",
      themeVariables: MERMAID_THEME,
      themeCSS: MERMAID_CSS,
      fontFamily: SANS,
      flowchart: { padding: 12, nodeSpacing: 20, rankSpacing: 28, wrappingWidth: 170, diagramPadding: 4 },
      sequence: { actorMargin: 40, boxMargin: 6, messageMargin: 28, mirrorActors: false },
    });
    return diagrams;
  });
  const { svg } = await (await mermaidLoaded).render(`visual-mermaid-${++mermaidCount}`, source);
  host.innerHTML = svg;
  const drawn = host.querySelector("svg");
  drawn.removeAttribute("width");
  drawn.style.width = `${Math.min(drawn.viewBox.baseVal.width, WIDEST)}px`;
  drawn.style.maxWidth = "100%";
}

async function dot(host, source) {
  vizLoaded ??= import(VIZ).then(({ instance }) => instance());
  const drawn = (await vizLoaded).renderSVGElement(source, { graphAttributes: DOT_FONT, nodeAttributes: DOT_FONT, edgeAttributes: { ...DOT_FONT, fontsize: 11 } });
  drawn.style.maxWidth = "100%";
  drawn.style.height = "auto";
  host.replaceChildren(drawn);
}

async function arch(host, source) {
  const { mount } = await import(ARCH_MAP);
  const drawn = mount(host, JSON.parse(source), { key: false });
  await drawn.ready;
  if (drawn.problems.length) throw new Error(drawn.problems.join("; "));
}

export async function draw(host, kind, source) {
  const document = host.ownerDocument;
  await style(document);
  const [base, language = ""] = kind.split(":");
  host.dataset.kind = base;
  switch (base) {
    case "code":
      if (RAW.has(language.toLowerCase())) throw new Error(`a ${language} fence is raw data or tool output, which a picture shows nothing of: draw a mermaid flow, a compare, a table or a chart, or no picture`);
      host.replaceChildren(await code(document, language, source));
      return;
    case "compare":
      host.replaceChildren(await compare(document, source));
      return;
    case "chart":
      return chart(host, source);
    case "mermaid":
      return mermaid(host, source);
    case "dot":
      return dot(host, source);
    case "arch":
      return arch(host, source);
  }
  throw new Error(`${kind} is no kind of visual: code, arch, mermaid, dot, chart or compare`);
}
