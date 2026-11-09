import hashlib
import html
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

FENCE = re.compile(r"^[ \t]*```[ \t]*([A-Za-z0-9_+-]*)[^\n]*\n(.*?)^[ \t]*```[ \t]*$", re.MULTILINE | re.DOTALL)
IMAGE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?[^)]*\)|<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
DIAGRAMS = ("arch", "dot")
SHAPES = {"store": "cylinder", "log": "cylinder", "actor": "oval"}
PATH_COLOR = "#2563eb"
GRAPH_STYLE = 'rankdir=LR compound=true labelloc=t fontsize=18 fontname="Helvetica" pad=0.3 nodesep=0.4 ranksep=0.6'
DOT_SECONDS = 30
DOWNLOAD_SECONDS = 20


class PictureError(Exception):
    pass


def quoted(text: str) -> str:
    return json.dumps(text)


def html_label(name: str, role: str = "") -> str:
    lines = [f"<b>{html.escape(name)}</b>"]
    if role:
        lines.append(f'<font point-size="10">{html.escape(role)}</font>')
    return "<" + "<br/>".join(lines) + ">"


def first_leaf(node: dict) -> str:
    return first_leaf(node["nodes"][0]) if node.get("nodes") else node["id"]


def arch_dot(doc: dict) -> str:
    """Graphviz source for an arch/v1 diagram: its nodes, nested nodes as boxes, its lines, and its main path in blue."""
    lines = [
        "digraph arch {",
        f"  graph [{GRAPH_STYLE} label={quoted(doc.get('title', ''))}]",
        '  node [fontname="Helvetica" fontsize=12 shape=box style="rounded,filled" fillcolor="#f4f6f8" color="#55606e"]',
        '  edge [color="#55606e" fontname="Helvetica" fontsize=10]',
    ]
    clusters: dict[str, str] = {}

    def emit(nodes: list[dict], indent: str) -> None:
        for node in nodes:
            if node.get("nodes"):
                clusters[node["id"]] = first_leaf(node)
                lines.append(f"{indent}subgraph {quoted('cluster_' + node['id'])} {{")
                lines.append(f'{indent}  label={html_label(node["name"])} style="rounded" color="#9aa5b1"')
                emit(node["nodes"], indent + "  ")
                lines.append(f"{indent}}}")
                continue
            shape = SHAPES.get(node.get("kind", ""), "box")
            lines.append(f"{indent}{quoted(node['id'])} [shape={shape} label={html_label(node['name'], node.get('role', ''))}]")

    def line(link: dict, *extra: str) -> str:
        attrs = list(extra)
        ends = []
        for end, side in ((link["from"], "ltail"), (link["to"], "lhead")):
            if end in clusters:
                attrs.append(f"{side}={quoted('cluster_' + end)}")
            ends.append(quoted(clusters.get(end, end)))
        if link.get("label"):
            attrs.append(f"label={quoted(link['label'])}")
        return f"  {ends[0]} -> {ends[1]}" + (f" [{' '.join(attrs)}]" if attrs else "")

    emit(doc["nodes"], "  ")
    path = [(step["from"], step["to"]) for step in (doc.get("path") or {}).get("steps", [])]
    drawn = set()
    for link in doc.get("edges", []):
        ends = (link["from"], link["to"])
        drawn.add(ends)
        lines.append(line(link, f"color={quoted(PATH_COLOR)} penwidth=2") if ends in path else line(link))
    for start, end in path:
        if (start, end) not in drawn:
            lines.append(line({"from": start, "to": end}, f"color={quoted(PATH_COLOR)} penwidth=2"))
    lines.append("}")
    return "\n".join(lines)


def dot_png(source: str, out: Path) -> None:
    try:
        result = subprocess.run(
            ["dot", "-Tpng", "-Gdpi=144", "-o", str(out)],
            input=source,
            check=False,
            capture_output=True,
            text=True,
            timeout=DOT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PictureError(f"Graphviz dot did not run: {error}") from None
    if result.returncode:
        raise PictureError(" ".join(result.stderr.split()) or f"dot exited {result.returncode}")


def downloaded(url: str, out: Path) -> None:
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_SECONDS) as reply:
            out.write_bytes(reply.read())
    except OSError as error:
        raise PictureError(f"{url} did not download: {error}") from None


def sources(show_me: str) -> list[tuple[str, str]]:
    """Each picture a Show me section holds, in order, as (kind, source): an arch or dot fence, or an image link."""
    found = [(match.start(), match[1].lower(), match[2]) for match in FENCE.finditer(show_me) if match[1].lower() in DIAGRAMS]
    found += [(match.start(), "image", match[1] or match[2]) for match in IMAGE.finditer(show_me)]
    return [(kind, source) for _, kind, source in sorted(found)]


def draw(kind: str, source: str, out: Path) -> Path:
    match kind:
        case "arch":
            dot_png(arch_dot(json.loads(source)), out)
        case "dot":
            dot_png(source, out)
        case _:
            out = out.with_suffix(Path(source.split("?")[0]).suffix or ".png")
            downloaded(source, out)
    return out


def pictures(folder: Path, label: str, show_me: str) -> list[str]:
    """Local image files for a PR's Show me, drawn once per source under folder and named after the PR for Slack's title."""
    drawn = []
    for index, (kind, source) in enumerate(sources(show_me), start=1):
        if kind == "image" and not source.startswith(("http://", "https://")):
            if (local := Path(source).expanduser()).is_absolute() and local.is_file():
                drawn.append(str(local))
            else:
                print(f"drive card: {label}: its Show me links {source}, neither a link nor a file here", file=sys.stderr)
            continue
        name = label if index == 1 else f"{label} ({index})"
        home = folder / hashlib.sha256(f"{kind}\n{source}".encode()).hexdigest()[:16]
        if cached := sorted(home.glob(f"{name}.*")):
            drawn.append(str(cached[0]))
            continue
        home.mkdir(parents=True, exist_ok=True)
        try:
            drawn.append(str(draw(kind, source, home / f"{name}.png")))
        except (PictureError, ValueError, KeyError, TypeError) as error:
            print(f"drive card: {label}: its Show me {kind} does not draw: {error}", file=sys.stderr)
    return drawn
