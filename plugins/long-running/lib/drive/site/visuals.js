import hljs from "https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.12.0/es/highlight.min.js";

const HLJS = "https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.12.0";
const GUESSED = new Set(["", "text", "txt", "plain", "plaintext"]);
const GRAMMAR_FILES = { proto: "protobuf" };
const CSS = `
.visual-code{margin:0;padding:8px 12px;width:max-content;max-width:760px;box-sizing:border-box;font:12.5px/1.5 Menlo,ui-monospace,SFMono-Regular,monospace;color:#1f2328;background:#f6f8fa;border:1px solid #d1d9e0;border-radius:6px;white-space:pre-wrap;overflow-wrap:anywhere;tab-size:4}
.visual-code>code.hljs{display:block;padding:0;background:none;font:inherit;overflow:visible}
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
  const language = await grammar(name);
  const pre = Object.assign(document.createElement("pre"), { className: "visual-code" });
  const lit = language ? hljs.highlight(text, { language }) : hljs.highlightAuto(text);
  pre.innerHTML = `<code class="hljs">${lit.value}</code>`;
  return pre;
}

export async function draw(host, kind, source) {
  const document = host.ownerDocument;
  await style(document);
  host.replaceChildren(await code(document, kind.replace(/^code:/, ""), source));
}
