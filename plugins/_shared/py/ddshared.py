"""Names design.py and retro.py share: the check report, link normalisation and GitHub state,
the ai.json validator, the component schema validator, the plain-twin and capitalisation lints,
the summary-fragment text extractor, and the file digest. Lifted verbatim from design.py.
"""
import hashlib, json, os, re, shutil, subprocess, urllib.error, urllib.parse, urllib.request
from html.parser import HTMLParser
from pathlib import Path

AI_CONFIG_KEYS = ("endpoint", "model", "key")
SITE_CONFIG_KEYS = ("github", "comments")
AI_CONFIG_OPTIONAL = ("reasoning",)
AI_REASONING = ("low", "medium", "high", "none")
LINK_KINDS = ("pr", "issue", "commit", "doc")
LINK_FIELDS = {"url", "kind", "label", "closes"}
GITHUB_LINK = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(pull|issues|commit)/([A-Za-z0-9]+)/?(?:[?#].*)?$")
GITHUB_KIND = {"pull": "pr", "issues": "issue", "commit": "commit"}
GITHUB_API = "https://api.github.com"
GITHUB_STATE_CLOSED = {"merged", "closed"}
TWIN_WORDS = 30
REPO_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GIT_REF = re.compile(r"(?!.*\.\.)(?!.*\.lock$)[A-Za-z0-9][\w./-]*(?<![./])")
HANDLE_RANGE = (2, 5)
FINDING_BY_NUMBER = re.compile(r"finding \d+", re.I)
PASS_TOKEN = re.compile(r"\bpass-\d+\b", re.I)
FILE_PATH = re.compile(r"(?<![\w:])(?:~|\.{1,2})?/[\w.@-]+(?:/[\w.@-]+)+"
                       r"|(?<![\w/])[\w.-]+/[\w.-]+/[\w./-]+"
                       r"|\b[\w-]+\.(?:py|json|md|html|go|rs|sh|yml|yaml|toml|svg|css)\b"
                       r"|\b[a-z0-9_][\w-]*\.(?:ts|tsx|js|mjs)\b")
URL_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")
COMMENT_CONFIG_KEYS = {"repo", "forbiddenTerms"}
COMMENT_REPO = re.compile(r"^[\w.-]+/[\w.-]+$")
LIB_URL = re.compile(r"cdn\.jsdelivr\.net/npm/((?:@[\w.-]+/)?[\w.-]+)@(\d+\.\d+\.\d+)")
PINNED_LIB = re.compile(r"(?<![\w/])((?:@[\w.-]+/)?[A-Za-z][\w.-]*)@(\d+\.\d+\.\d+)")
ACRONYMS = ("API", "SSO", "JWT", "DPoP", "TLS", "mTLS", "HTTP", "HTTPS", "gRPC", "k8s", "S3", "R2", "IAM", "SQL",
            "DB", "ID", "URL", "JSON", "YAML", "CLI", "UI", "UX", "CI", "CD", "PR", "RPM", "TPM", "QPS", "CPU",
            "GPU", "RAM", "AWS", "GCP", "OIDC", "OAuth", "SAML", "DNS", "CDN", "VPC", "RDS", "KMS", "ELK", "SVG",
            "PDF", "HTML", "CSS", "JS", "TSX", "LLM", "AI", "FDE", "SLA", "SLO", "P95", "P99", "RLS", "NLB",
            "EBS", "SNI", "WAF", "DDoS", "GraphQL", "SDK", "WAL", "Postgres", "SQLite", "GitHub", "Kubernetes",
            "Pulumi", "Datadog", "Cloudflare", "WorkOS", "SandSQL", "SandDB")
AMBIGUOUS_PRODUCTS = ("Envoy", "Restate", "Valkey", "Iris", "Sand")
LABEL_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[_./-][A-Za-z0-9]+)*")
IDENTIFIER_HEAD = re.compile(r"[A-Za-z0-9]+[_./-]")
SCHEMA_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool,
                "number": (int, float), "integer": int}
AI_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def foreign_scheme(url: str):
    m = URL_SCHEME.match(re.sub(r"[\s\x00-\x1f]", "", url))
    return m.group(1) if m and m.group(1).lower() not in ("http", "https") else None


def words(s: str) -> int:
    return len(s.split())


def classes_of(attrs) -> set:
    return set((dict(attrs).get("class") or "").split())


class FragmentText(HTMLParser):
    HEADINGS = {f"h{n}": "#" * n for n in range(1, 7)}
    PANEL_HEADINGS = {f"h{n}": "#" * min(6, max(3, n + 1)) for n in range(1, 7)}
    BLOCKS = {"p", "li", "div", "section", "figure", "figcaption", "blockquote", "pre", "tr", "td", "th"} | set(HEADINGS)
    SPACED = {"br", "b", "span", "small"}
    SKIPPED = {"svg", "script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines, self.buf, self.cells, self.muted, self.panel = [], [], [], [], 0

    def _take(self) -> str:
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        return text

    def _emit(self, text: str):
        if text:
            self.lines.append(text)

    def handle_starttag(self, tag, attrs):
        classes = classes_of(attrs)
        if tag in self.SKIPPED or (tag == "pre" and "mermaid" in classes):
            self.muted.append(tag)
        if self.muted:
            return
        if tag in self.SPACED:
            self.buf.append(" ")
        elif tag in self.BLOCKS:
            self._emit(self._take())
            if tag == "section" and self.panel:
                self.panel += 1
            elif tag == "section" and "xs-panel" in classes:
                self.panel = 1
                self._emit(f"## {dict(attrs).get('data-kind') or 'panel'}")
            if tag == "tr":
                self.cells = []

    def handle_endtag(self, tag):
        if self.muted:
            if tag == self.muted[-1]:
                self.muted.pop()
            return
        if tag in self.SPACED:
            self.buf.append(" ")
        elif tag in ("td", "th"):
            self.cells.append(self._take())
        elif tag == "tr":
            self.cells.append(self._take())
            row = [c for c in self.cells if c]
            self.cells = []
            if row:
                self._emit("| " + " | ".join(row) + " |")
        elif tag in self.HEADINGS:
            text = self._take()
            marks = (self.PANEL_HEADINGS if self.panel else self.HEADINGS)[tag]
            self._emit(f"{marks} {text}" if text else "")
        elif tag == "li":
            text = self._take()
            self._emit(f"- {text}" if text else "")
        elif tag in self.BLOCKS:
            if tag == "section" and self.panel:
                self.panel -= 1
            self._emit(self._take())

    def handle_data(self, data):
        if not self.muted:
            self.buf.append(data)

    def close(self):
        super().close()
        self._emit(self._take())


def digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.exists() else None


def slugify(s: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-") or "design"


def twin_issues(twin: str, original: str, ids: re.Pattern):
    issues = []
    n = words(twin)
    if n > TWIN_WORDS and n * 3 > words(original):
        issues.append(f"{n} words; keep it under {TWIN_WORDS} or a third of the original")
    ids = sorted(set(ids.findall(twin)))
    if ids:
        issues.append("names register ids " + ", ".join(ids))
    paths = sorted(set(FILE_PATH.findall(twin)))
    if paths:
        issues.append("names file paths " + ", ".join(paths))
    if FINDING_BY_NUMBER.search(twin) or PASS_TOKEN.search(twin):
        issues.append("names a review finding or pass by number")
    return issues


def first_line(s: str, width: int = 72) -> str:
    line = s.strip().splitlines()[0] if s.strip() else ""
    return line if len(line) <= width else line[:width - 1].rstrip() + "…"


class Report:
    def __init__(self, strict=False):
        self.errors, self.warnings, self.strict = [], [], strict

    def err(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def strict_warn(self, msg):
        (self.errors if self.strict else self.warnings).append(msg)

    def finish(self) -> int:
        for m in self.errors:
            print(f"ERROR: {m}")
        for m in self.warnings:
            print(f"warn:  {m}")
        print(f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)")
        return 1 if self.errors else 0


def github_ref(url: str):
    m = GITHUB_LINK.match(url)
    if not m:
        return None
    owner, repo, path, ref = m.groups()
    kind = GITHUB_KIND[path]
    if kind == "commit":
        if not re.fullmatch(r"[0-9a-f]{7,40}", ref):
            return None
        return {"owner": owner, "repo": repo, "kind": kind, "sha": ref, "key": f"{owner}/{repo}@{ref[:7]}"}
    if not ref.isdigit():
        return None
    return {"owner": owner, "repo": repo, "kind": kind, "n": int(ref), "key": f"{owner}/{repo}#{ref}"}


def normalise_link(link):
    if isinstance(link, str):
        link = {"url": link}
    if not isinstance(link, dict):
        return None, f"{link!r} is neither a URL string nor an object with 'url'"
    url = link.get("url")
    if not (isinstance(url, str) and url.strip()):
        return None, f"{link!r} has no 'url'"
    url = url.strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return None, f"{url} is not an https:// URL"
    extra = sorted(set(link) - LINK_FIELDS)
    if extra:
        return None, f"{url} carries {', '.join(map(repr, extra))}; a link is {{url, kind?, label?, closes?}}"
    gh = github_ref(url)
    kind = link.get("kind")
    if kind is not None and kind not in LINK_KINDS:
        return None, f"{url}: kind {kind!r} not in {', '.join(LINK_KINDS)}"
    if gh and kind and kind != gh["kind"]:
        return None, f"{url}: kind {kind!r} disagrees with the URL, which is a GitHub {gh['kind']}"
    if not gh and kind in ("pr", "issue", "commit"):
        return None, f"{url}: kind {kind!r} needs a github.com pull, issues or commit URL"
    kind = kind or (gh["kind"] if gh else "doc")
    label = link.get("label")
    if label is not None and not (isinstance(label, str) and label.strip()):
        return None, f"{url}: label must be a non-empty string"
    closes = link.get("closes")
    if closes is not None and not isinstance(closes, bool):
        return None, f"{url}: closes must be true or false"
    if "closes" in link and kind not in ("pr", "issue"):
        return None, f"{url}: closes belongs on a pull request or an issue, not a {kind}"
    out = {"url": url, "kind": kind, "closes": bool(closes)}
    if label:
        out["label"] = label.strip()
    if gh:
        out["gh"] = gh
    return out, None


def entry_links(rep, where, entry, closes_ok: bool) -> list:
    raw = entry.get("links") if isinstance(entry, dict) else None
    if raw is None:
        return []
    if not isinstance(raw, list):
        rep.err(f"{where}: links must be a list of URLs or {{url, kind?, label?, closes?}} objects")
        return []
    out = []
    for link in raw:
        norm, problem = normalise_link(link)
        if problem:
            rep.err(f"{where}: link {problem}")
            continue
        if not closes_ok and isinstance(link, dict) and "closes" in link:
            rep.err(f"{where}: link {norm['url']} carries closes; it belongs on the open item the change retires")
            continue
        out.append(norm)
    return out


def acronym_map(meta: dict) -> dict:
    raw = meta.get("acronyms")
    extra = [a.strip() for a in raw if isinstance(a, str) and a.strip()] if isinstance(raw, list) else []
    named = {a.lower() for a in extra}
    names = list(ACRONYMS) + [p for p in AMBIGUOUS_PRODUCTS if p.lower() in named] + extra
    return {a.lower(): a for a in names}


def check_case(rep, where, label, acronyms, sentence_case=True):
    head = label.strip()
    if sentence_case and head[:1].isalpha() and head[:1].islower() and not IDENTIFIER_HEAD.match(head):
        rep.warn(f"{where}: {first_line(label, 48)!r} opens lower-case; a label is sentence case unless it opens on "
                 "an identifier in backticks")
    for word in LABEL_WORD.findall(re.sub(r"`[^`]*`", " ", label)):
        if any(c in word for c in "_-./"):
            continue
        canon = acronyms.get(word.lower())
        if canon and canon != word:
            rep.warn(f"{where}: {word!r} should read {canon!r}; acronyms and product names keep their own "
                     "capitalisation (name a product that is also an ordinary word in meta.acronyms to lint it)")


def schema_errors(value, schema, where: str) -> list:
    if "const" in schema:
        return [] if value == schema["const"] else [f"{where} must be {schema['const']!r}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{where} must be one of {', '.join(map(str, schema['enum']))}"]
    kind = schema.get("type")
    want = SCHEMA_TYPES.get(kind)
    if want and (not isinstance(value, want) or (kind != "boolean" and isinstance(value, bool))):
        return [f"{where} must be {'an' if kind[0] in 'aoi' else 'a'} {kind}"]
    out = []
    if kind == "object":
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                out.append(f"{where} is missing {key!r}")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    out.append(f"{where} has an unknown property {key!r}")
        for key, sub in props.items():
            if key in value:
                out.extend(schema_errors(value[key], sub, f"{where}.{key}"))
    elif kind == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append(f"{where} needs at least {schema['minItems']} entries")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(f"{where} takes at most {schema['maxItems']} entries")
        if "items" in schema:
            for i, item in enumerate(value):
                out.extend(schema_errors(item, schema["items"], f"{where}[{i}]"))
    elif kind == "string":
        if len(value.strip()) < schema.get("minLength", 0):
            out.append(f"{where} must not be empty")
        if "pattern" in schema and not re.fullmatch(schema["pattern"].strip("^$"), value):
            out.append(f"{where} must match {schema['pattern']}")
    elif kind in ("number", "integer"):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{where} must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{where} must be at most {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            out.append(f"{where} must be greater than {schema['exclusiveMinimum']}")
    return out


def comment_config(rep, cfg):
    comments = cfg.get("comments")
    if not isinstance(comments, dict):
        rep.err('ai.json: "comments" must be an object carrying "repo", and "forbiddenTerms" when it has any')
        return
    for k in sorted(set(comments) - COMMENT_CONFIG_KEYS):
        rep.warn(f"ai.json carries comments.{k}, which the page ignores")
    repo = comments.get("repo")
    if repo is not None and not (isinstance(repo, str) and COMMENT_REPO.match(repo)):
        rep.err(f"ai.json: comments.repo is {repo!r}; it must read owner/repo")
    terms = comments.get("forbiddenTerms")
    if terms is not None and not (isinstance(terms, list) and all(isinstance(t, str) and t.strip() for t in terms)):
        rep.err("ai.json: comments.forbiddenTerms must be a list of non-empty strings")


def check_ai_config(rep, root):
    path = root / "ai.json"
    if not path.exists():
        return
    try:
        cfg = json.loads(path.read_text())
    except ValueError as e:
        rep.err(f"ai.json does not parse: {e}")
        return
    if not isinstance(cfg, dict):
        rep.err('ai.json must be a JSON object: {"endpoint", "model", "key"} for the assistant, {"github": {"token"}} for '
                'link states, {"comments": {"repo"}} for reader comments, or {"disabled": true} to turn the assistant off')
        return
    if "github" in cfg:
        github = cfg["github"]
        if not (isinstance(github, dict) and isinstance(github.get("token"), str) and github["token"].strip()):
            rep.err('ai.json: "github" must be {"token": "<read-only fine-grained PAT>"}')
        else:
            for k in sorted(set(github) - {"token"}):
                rep.warn(f"ai.json carries github.{k}, which the page ignores")
    if "comments" in cfg:
        comment_config(rep, cfg)
    if cfg.get("disabled") is True:
        for k in sorted(set(cfg) - {"disabled"} - set(SITE_CONFIG_KEYS)):
            rep.warn(f"ai.json disables the assistant, so {k!r} beside it does nothing")
        return
    if not (set(cfg) & set(AI_CONFIG_KEYS)) and not (set(cfg) & set(SITE_CONFIG_KEYS)):
        rep.err("ai.json carries none of the assistant keys (endpoint, model, key), a github block, or a comments block")
        return
    if set(cfg) & set(AI_CONFIG_KEYS):
        for k in AI_CONFIG_KEYS:
            v = cfg.get(k)
            if not (isinstance(v, str) and v.strip()):
                rep.err(f"ai.json: {k!r} must be a non-empty string")
        endpoint = cfg.get("endpoint")
        if isinstance(endpoint, str) and endpoint.strip():
            problem = ai_endpoint_problem(endpoint.strip())
            if problem:
                rep.err(f"ai.json: endpoint {problem}")
        reasoning = cfg.get("reasoning")
        if reasoning is not None and reasoning not in AI_REASONING:
            rep.err(f"ai.json: 'reasoning' must be one of {', '.join(AI_REASONING)}")
        elif reasoning in AI_REASONING[:3] and str(cfg.get("model", "")).startswith("gemma"):
            rep.warn(f"ai.json: reasoning {reasoning!r} on a gemma model only switches reasoning on; low, medium and high behave the same")
    for k in sorted(set(cfg) - set(AI_CONFIG_KEYS) - set(AI_CONFIG_OPTIONAL) - set(SITE_CONFIG_KEYS)):
        rep.warn(f"ai.json carries {k!r}, which the page ignores")


def github_get(path: str):
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req = urllib.request.Request(GITHUB_API + path, headers={
            "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
    if not shutil.which("gh"):
        raise RuntimeError("set GITHUB_TOKEN or install the gh CLI to resolve GitHub links")
    p = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if p.returncode:
        if "404" in p.stderr or "Not Found" in p.stderr:
            return None
        raise RuntimeError(p.stderr.strip() or f"gh api {path} failed")
    return json.loads(p.stdout)


def github_state(gh: dict):
    base = f"/repos/{gh['owner']}/{gh['repo']}"
    if gh["kind"] == "commit":
        c = github_get(f"{base}/commits/{gh['sha']}")
        if not c:
            return {"state": "unknown"}
        return {"state": "commit", "title": c["commit"]["message"].split("\n", 1)[0],
                "author": (c.get("author") or {}).get("login") or c["commit"]["author"]["name"],
                "date": c["commit"]["author"]["date"][:10]}
    if gh["kind"] == "pr":
        pr = github_get(f"{base}/pulls/{gh['n']}")
        if not pr:
            return {"state": "unknown"}
        state = "merged" if pr.get("merged_at") else "draft" if pr.get("draft") else pr["state"]
        return {"state": state, "title": pr["title"], "author": pr["user"]["login"],
                "date": (pr.get("merged_at") or pr.get("closed_at") or pr["updated_at"])[:10]}
    issue = github_get(f"{base}/issues/{gh['n']}")
    if not issue:
        return {"state": "unknown"}
    return {"state": issue["state"], "title": issue["title"], "author": issue["user"]["login"],
            "date": (issue.get("closed_at") or issue["updated_at"])[:10]}


def link_line(link: dict, repo, state) -> str:
    gh = link.get("gh")
    if gh:
        prefix = "" if repo == f"{gh['owner']}/{gh['repo']}" else f"{gh['owner']}/{gh['repo']}"
        label = link.get("label") or (prefix + ("@" + gh["sha"][:7] if gh["kind"] == "commit" else f"#{gh['n']}"))
    else:
        label = link.get("label") or link["url"]
    bits = [f"{link['kind']} {label}"]
    if state:
        bits.append(state["state"] + (f" {state['date']}" if state.get("date") else "")
                    + (f" by {state['author']}" if state.get("author") else ""))
        if state.get("title"):
            bits.append(f"\"{state['title']}\"")
    if link["closes"]:
        bits.append("closes")
    return " · ".join(bits)


def link_drift(row: dict, states: dict) -> str:
    closers = [(l, states.get(l["url"])) for l in row["links"] if l["closes"]]
    if not closers:
        return ""
    ref = lambda l: f"{l['kind']} {l['gh']['key']}"
    landed = [(l, st) for l, st in closers if st and st["state"] in GITHUB_STATE_CLOSED]
    if row["s"] != "closed" and landed:
        l, st = landed[0]
        return f"{row['id']} is still open but {ref(l)} was {st['state']} on {st['date']}; set s: \"closed\""
    if row["s"] == "closed" and all(st and st["state"] not in GITHUB_STATE_CLOSED and st["state"] != "unknown" for _, st in closers):
        l, st = closers[0]
        return f"{row['id']} is marked closed but {ref(l)} is still {st['state']}"
    return ""


def ai_endpoint_problem(endpoint: str):
    parts = urllib.parse.urlsplit(endpoint)
    if not parts.scheme or not parts.netloc:
        return "must be an absolute URL; the page calls it with no page-relative base to resolve against"
    if parts.scheme == "http" and (parts.hostname or "") in AI_LOOPBACK:
        return None
    if parts.scheme != "https":
        return (f"uses the {parts.scheme}: scheme; a page served over https can only call an https endpoint "
                "(http is allowed on localhost)")
    return None


def handle_range_issue(handle: str):
    n = words(handle)
    if not HANDLE_RANGE[0] <= n <= HANDLE_RANGE[1]:
        return f"is {n} word(s); a handle is {HANDLE_RANGE[0]}–{HANDLE_RANGE[1]} words a reader would say out loud"
    return None


def handle_issues(handle: str, ids: re.Pattern) -> list:
    issues = []
    range_issue = handle_range_issue(handle)
    if range_issue:
        issues.append(range_issue)
    named = sorted(set(ids.findall(handle)))
    if named:
        issues.append("names register ids " + ", ".join(named))
    return issues
