#!/usr/bin/env python3
"""Materialise the shared partials into every plugin that hosts them.

  build.py build    write every generated file
  build.py check    exit 1 naming each generated file that is stale

A host is plugins/<plugin>/skills/<skill>/templates/src/<name>.html. Its
`<!-- @include html/x.js -->` and `/* @include html/x.css */` markers are
replaced by the partial's text and the result is written to
templates/<name>.html, stamped on line 2. The host plugin also receives
scripts/ddshared.py and scripts/build-pdf.py from py/, and a copy of every
components/dd.*.json under reference/components/. Each JS partial opens with
`// @requires a,b` and `// @defines x,y`; check verifies that every required
name is declared by the host source or by a partial the host includes before
it, and that no generated file outlives the source that produced it.
"""
import hashlib, re, sys
from pathlib import Path

SHARED = Path(__file__).resolve().parent
ROOT = SHARED.parents[1]
HOST_GLOB = "plugins/*/skills/*/templates/src/*.html"
INCLUDE = re.compile(r"^(?:<!-- @include (html/[\w.-]+) -->|/\* @include (html/[\w.-]+) \*/)$", re.M)
HTML_STAMP = "<!-- built by plugins/_shared/build.py from templates/src/{name} + {n} partials sha256:{digest} — do not edit; edit the source and rerun -->"
PY_STAMP = "# built by plugins/_shared/build.py from py/{name} sha256:{digest} — do not edit"
GENERATED_DIRS = ("templates", "scripts", "reference/components")
GENERATED_MARK = re.compile(r'built by plugins/_shared/build\.py|"_built":')
REQUIRES = re.compile(r"^// @requires ?(.*)$", re.M)
DEFINES = re.compile(r"^// @defines ?(.*)$", re.M)
DECLARED = re.compile(r"^(?:const|let|function|async function)\s+([A-Za-z_$][\w$]*)", re.M)
DECLARED_MORE = re.compile(r"^(?:const|let)\s+([^;\n]*)", re.M)
NAME_INIT = re.compile(r"(?:^|,)\s*([A-Za-z_$][\w$]*)\s*=")
DESTRUCTURED = re.compile(r"^const \{(.*)\}=R;", re.M)
DESTRUCTURED_NAME = re.compile(r":([A-Za-z_$][\w$]*)=")
JS_TOKEN = re.compile(
    r"//[^\n]*|/\*.*?\*/"
    r"|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`"
    r"|/(?![/*])(?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\n])+/[a-z]*"
    r"|[{}]|[A-Za-z_$][\w$]*",
    re.S,
)
PY_OUTPUTS = {"ddshared.py": "ddshared.py", "build_pdf.py": "build-pdf.py"}


def digest(*parts: bytes) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p)
    return h.hexdigest()[:12]


def partial_names(source: str) -> list[str]:
    return [a or b for a, b in INCLUDE.findall(source)]


def render_host(src: Path) -> str:
    source = src.read_text()
    names = partial_names(source)
    bodies = {n: (SHARED / n).read_text() for n in names}
    stamp = HTML_STAMP.format(name=src.name, n=len(names), digest=digest(source.encode(), *(bodies[n].encode() for n in names)))
    body = INCLUDE.sub(lambda m: bodies[m.group(1) or m.group(2)].rstrip("\n"), source)
    first, rest = body.split("\n", 1)
    return f"{first}\n{stamp}\n{rest}"


def render_py(name: str) -> str:
    source = (SHARED / "py" / name).read_text()
    return PY_STAMP.format(name=name, digest=digest(source.encode())) + "\n" + source


def render_component(path: Path) -> str:
    source = path.read_text()
    first, rest = source.split("\n", 1)
    assert first == "{", f"{path} must open with a bare {{"
    return f'{{\n  "_built": "sha256:{digest(source.encode())}",\n{rest}'


def declared_names(text: str) -> set[str]:
    names = set(DECLARED.findall(text))
    for decl in DECLARED_MORE.findall(text):
        names.update(NAME_INIT.findall(decl))
    for group in DESTRUCTURED.findall(text):
        names.update(DESTRUCTURED_NAME.findall(group))
    return names


def top_level_names(name: str, text: str) -> set[str]:
    names, depth = set(), 0
    for m in JS_TOKEN.finditer(text):
        token = m.group()
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
        elif not depth and (token[0].isalpha() or token[0] in "_$"):
            names.add(token)
    assert not depth, f"{name} leaves the brace scan at depth {depth}; it holds a brace this tokeniser cannot see past"
    return names


def contract_problems(src: Path) -> list[str]:
    source = src.read_text()
    names = [n for n in partial_names(source) if n.endswith(".js")]
    bodies = {n: (SHARED / n).read_text() for n in names}
    decls = {n: declared_names(bodies[n]) for n in names}
    host = src.relative_to(ROOT)
    provided = declared_names(source)
    out = []
    for i, n in enumerate(names):
        m = REQUIRES.search(bodies[n])
        if not m:
            out.append(f"{n} has no // @requires line")
        else:
            eager = top_level_names(n, bodies[n])
            for name in filter(None, m.group(1).split(",")):
                if name in provided:
                    continue
                later = [o for o in names[i + 1:] if name in decls[o]]
                if not later:
                    out.append(f"{n} requires {name}, which neither {host} nor an included partial declares")
                elif name in eager:
                    out.append(f"{n} reads {name} where the page runs it, but {host} includes {later[0]}, which declares {name}, after {n}")
        d = DEFINES.search(bodies[n])
        if not d:
            out.append(f"{n} has no // @defines line")
        else:
            for name in filter(None, d.group(1).split(",")):
                if name not in decls[n]:
                    out.append(f"{n} claims to define {name} but never declares it")
        provided |= decls[n]
    return out


def outputs() -> dict[Path, str]:
    out = {}
    plugins = set()
    for src in sorted(ROOT.glob(HOST_GLOB)):
        out[src.parents[1] / src.name] = render_host(src)
        plugins.add(src.parents[2])
    for skill in sorted(plugins):
        for source, target in PY_OUTPUTS.items():
            out[skill / "scripts" / target] = render_py(source)
        for schema in sorted((SHARED / "components").glob("dd.*.json")):
            out[skill / "reference" / "components" / schema.name] = render_component(schema)
    return out


def generated_files() -> list[Path]:
    out = []
    for directory in GENERATED_DIRS:
        for path in sorted(ROOT.glob(f"plugins/*/skills/*/{directory}/*")):
            if not path.is_file():
                continue
            if GENERATED_MARK.search("\n".join(path.read_text(errors="replace").split("\n", 2)[:2])):
                out.append(path)
    return out


def build() -> int:
    problems = [p for src in sorted(ROOT.glob(HOST_GLOB)) for p in contract_problems(src)]
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    for path, text in outputs().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


def check() -> int:
    expected = outputs()
    problems = [p for src in sorted(ROOT.glob(HOST_GLOB)) for p in contract_problems(src)]
    for path, text in expected.items():
        if not path.exists():
            problems.append(f"{path.relative_to(ROOT)} is missing; run build.py build")
        elif path.read_text() != text:
            problems.append(f"{path.relative_to(ROOT)} is stale; run build.py build")
    for path in generated_files():
        if path not in expected:
            problems.append(f"{path.relative_to(ROOT)} carries a build stamp but nothing generates it; restore its source or delete it")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"{len(expected)} generated files are current")
    return 0


def stale_host(built: Path) -> str:
    src = built.parent / "src" / built.name
    if not src.exists():
        return f"{built} has no source at {src}"
    text = built.read_text()
    lines = text.split("\n", 2)
    if len(lines) < 2 or "built by plugins/_shared/build.py" not in lines[1]:
        return f"{built} carries no build stamp on line 2; run plugins/_shared/build.py build"
    if text != render_host(src):
        return f"{built} differs from templates/src/{built.name} rendered with the shared partials; run plugins/_shared/build.py build"
    return ""


def main():
    cmd = sys.argv[1] if len(sys.argv) == 2 else None
    if cmd == "build":
        sys.exit(build())
    if cmd == "check":
        sys.exit(check())
    print(__doc__, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
