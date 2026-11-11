import html
import json
import re
from pathlib import Path

from drive import limits, picture

DOC = {
    "arch": 1,
    "name": "tap-picture",
    "title": "A tap card's show-me becomes a picture",
    "nodes": [
        {"id": "body", "kind": "store", "name": "PR body", "role": "carries the fence", "medium": "pages"},
        {
            "id": "drive",
            "kind": "service",
            "name": "drive card",
            "role": "draws the card",
            "nodes": [{"id": "draw", "kind": "service", "name": "picture", "role": "renders a PNG"}],
        },
        {"id": "him", "kind": "actor", "name": "his Slack card", "role": "shows the picture"},
    ],
    "edges": [{"from": "body", "to": "drive", "label": "Show me"}, {"from": "drive", "to": "him"}],
    "path": {"steps": [{"from": "body", "to": "drive"}]},
}
MERMAID_LOOK = r"""<!doctype html>
<html><body><div id="visual"></div><script type="module">VISUALS
const host = document.getElementById("visual");
await draw(host, "mermaid", "flowchart LR\n  boot[first boot: instance store blank] -->|now: up waits| job[AWS job formats it]");
await document.fonts.ready;
const look = (selector) => getComputedStyle(host.querySelector(selector));
const svg = host.querySelector("svg");
const box = svg.viewBox.baseVal;
const drawn = svg.getBBox();
document.documentElement.dataset.look = JSON.stringify({
  node: { fill: look(".node rect").fill, border: look(".node rect").stroke, corner: look(".node rect").rx, shadow: look(".node rect").filter },
  text: { size: look(".nodeLabel").fontSize, font: look(".nodeLabel").fontFamily.split(",")[0] },
  edge: { size: look(".edgeLabel p").fontSize, color: look(".edgeLabel p").color, behind: look(".labelBkg").backgroundColor },
  margin: Math.round(Math.max(drawn.x - box.x, box.x + box.width - drawn.x - drawn.width, drawn.y - box.y, box.y + box.height - drawn.y - drawn.height)),
});
</script></body></html>
"""
LOOK = re.compile(r'<html[^>]*\bdata-look="([^"]*)"')


def test_a_mermaid_picture_draws_rounded_unshadowed_nodes_in_a_legible_sans_with_quiet_edge_labels_and_no_margin_past_the_diagram(tmp_path) -> None:
    page = tmp_path / "look.html"
    page.write_text(MERMAID_LOOK.replace("VISUALS", picture.VISUALS.read_text(), 1))
    dumped = picture.headless(page, "--window-size=1200,1200", "--no-sandbox", "--dump-dom")
    assert json.loads(html.unescape(LOOK.search(dumped)[1])) == {
        "node": {"fill": "rgb(246, 248, 250)", "border": "rgb(175, 184, 193)", "corner": "6px", "shadow": "none"},
        "text": {"size": "15px", "font": "-apple-system"},
        "edge": {"size": "13px", "color": "rgb(82, 81, 78)", "behind": "rgb(255, 255, 255)"},
        "margin": 4,
    }


def test_sources_lists_diagrams_and_images_in_the_order_they_appear_each_with_its_caption_and_no_chart() -> None:
    show_me = (
        "![before](https://example.com/before.png)\n\n```arch\n"
        + json.dumps(DOC)
        + "\n```\n\nThe wake, step by step:\n```mermaid\nflowchart LR\n```\n\nSeconds per wake\n```chart\n{\"mark\": \"bar\"}\n```\n\n```dot\ndigraph { a -> b }\n```\n"
    )
    assert [(kind, caption) for kind, _, caption in picture.sources(show_me)] == [
        ("image", "before"),
        ("arch", ""),
        ("mermaid", "The wake, step by step"),
        ("dot", ""),
    ]


def test_a_picture_is_drawn_once_and_again_when_the_renderer_changes(tmp_path, monkeypatch) -> None:
    drawn = []

    def draw(kind: str, source: str, out) -> None:
        drawn.append(kind)
        out.write_bytes(b"\x89PNG\r\n")

    monkeypatch.setattr(picture, "visual_png", draw)
    show_me = "```arch\n" + json.dumps(DOC) + "\n```\n"
    first = picture.pictures(tmp_path / "pictures", "app PR 7", show_me, set())
    assert picture.pictures(tmp_path / "pictures", "app PR 7", show_me, set()) == first
    renderer = tmp_path / "visuals.js"
    renderer.write_text("export async function draw() {}")
    monkeypatch.setattr(picture, "VISUALS", renderer)
    assert picture.pictures(tmp_path / "pictures", "app PR 7", show_me, set()) != first
    assert drawn == ["arch", "arch"]
    assert json.loads((tmp_path / "pictures").glob("*/source.json").__next__().read_text())["kind"] == "arch"


def test_a_picture_drawn_before_pictures_kept_their_source_gets_it_from_the_kept_fence_and_the_rest_are_counted_once(tmp_path, capsys) -> None:
    source = 'export const placer = restate.object({\n  name: "Placer",\n})\n'
    old, today, lost, redrawn = tmp_path / "b1501a9dfc231de0", tmp_path / "0123456789abcdef", tmp_path / "fedcba9876543210", tmp_path / "redraw" / "j1-2"
    for home in (old, today, lost, redrawn):
        home.mkdir(parents=True)
        (home / "show-me.png").write_bytes(b"\x89PNG\r\n")
    (today / "source.json").write_text(json.dumps({"kind": "mermaid", "source": "flowchart LR\n  a --> b"}))
    picture.fill(tmp_path, [f"ASK Which owner?\nShow me:\nOne owner per team:\n```\n{source}```\n- One owner (recommended)\n"])
    assert json.loads((old / "source.json").read_text()) == {"kind": "code:", "source": source}
    assert json.loads((today / "source.json").read_text())["kind"] == "mermaid"
    assert not (lost / "source.json").exists() and not (redrawn / "source.json").exists()
    assert capsys.readouterr().err == "drive pictures: 1 drawn before pictures kept their source now keep it; 2 keep none the drive can find and stay as drawn\n"
    assert (tmp_path / picture.SOURCED).exists()


def test_a_local_image_is_copied_in_under_its_caption_and_a_missing_one_is_skipped(tmp_path) -> None:
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG\r\n")
    show_me = f"![The card after his tap]({shot})\n![gone](/nowhere/gone.png)\n"
    [copied] = picture.pictures(tmp_path / "pictures", "app PR 7", show_me, set())
    assert Path(copied).name == "The card after his tap"
    assert Path(copied).read_bytes() == shot.read_bytes()


def test_a_home_relative_image_is_used_from_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    shot = tmp_path / "shots" / "s106.png"
    shot.parent.mkdir()
    shot.write_bytes(b"\x89PNG\r\n")
    [copied] = picture.pictures(tmp_path / "pictures", "monorepo PR 53064", "![the api](~/shots/s106.png)\n", set())
    assert (Path(copied).name, Path(copied).read_bytes()) == ("the api", shot.read_bytes())


def test_a_pr_cards_pictures_are_each_named_exactly_their_caption_which_slack_titles_them_with_and_its_chart_draws_none(tmp_path, monkeypatch) -> None:
    drawn = []

    def draw(kind: str, source: str, out) -> None:
        drawn.append(kind)
        out.write_bytes(b"\x89PNG\r\n")

    monkeypatch.setattr(picture, "visual_png", draw)
    long = "Each lane's tree diff the coordinator reads on a wake " * 4
    show_me = (
        "Seconds per wake: before and after\n```chart\n{\"mark\": \"bar\"}\n```\n"
        "1 · Feed first / then read [today]:\n```mermaid\nflowchart TD\n  a --> b\n```\n"
        "```go\nfunc wake() {\n\tread()\n}\n```\n"
        f"{long}\n```sand\nlane::diff(@tree) -> @diff\n```\n"
    )
    named = [Path(path).name for path in picture.pictures(tmp_path / "pictures", "monorepo PR 54043", show_me, set())]
    assert named == ["1 · Feed first then read today", "go code", " ".join(long.split())[: picture.TITLE_CHARS].rstrip()]
    assert drawn == ["mermaid", "code:go", "code:sand"]
    assert picture.pictures(tmp_path / "pictures", "monorepo PR 54043", show_me, set())[0].endswith("/1 · Feed first then read today")
    assert drawn == ["mermaid", "code:go", "code:sand"]


def test_a_caption_naming_the_source_a_picture_keeps_still_draws_the_picture(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    show_me = "source:\n```mermaid\nflowchart TD\n  a --> b\n```\nsource.json\n```mermaid\nflowchart TD\n  c --> d\n```\n"
    drawn = picture.pictures(tmp_path / "pictures", "app PR 7", show_me, set())
    assert [(Path(path).name, Path(path).read_bytes()) for path in drawn] == [("source", b"\x89PNG\r\n"), ("source.json (2)", b"\x89PNG\r\n")]
    assert picture.cached(tmp_path / "pictures", "source", "mermaid", "flowchart TD\n  a --> b\n").read_bytes() == b"\x89PNG\r\n"


def test_pictures_titled_alike_on_one_card_are_numbered_apart_and_drawn_once(tmp_path, monkeypatch) -> None:
    drawn = []

    def draw(kind: str, source: str, out) -> None:
        drawn.append(kind)
        out.write_bytes(b"\x89PNG\r\n")

    monkeypatch.setattr(picture, "visual_png", draw)
    taken: set[str] = set()
    first = picture.pictures(tmp_path / "pictures", "app PR 7", "```mermaid\nflowchart TD\n  a --> b\n```\n", taken)
    second = picture.pictures(tmp_path / "pictures", "app PR 8", "```mermaid\nflowchart TD\n  c --> d\n```\n```mermaid\nflowchart TD\n  a --> b\n```\n", taken)
    assert [Path(path).name for path in [*first, *second]] == ["Diagram", "Diagram (2)", "Diagram (3)"]
    assert drawn == ["mermaid", "mermaid"]


def test_a_question_cards_show_me_draws_each_fence_titled_by_its_caption_or_kind_keeps_words_and_charts_as_written_and_leaves_out_what_does_not_draw(
    tmp_path, monkeypatch
) -> None:
    tried = []

    def draw(kind: str, source: str, out) -> None:
        tried.append(kind)
        if source.startswith("broken"):
            raise picture.PictureError("the code:go did not finish drawing")
        out.write_bytes(b"\x89PNG\r\n")

    monkeypatch.setattr(picture, "visual_png", draw)
    show_me = [
        "The call today:",
        "```sand",
        "fn note() -> int {",
        "```",
        "```proto",
        "message Run {}",
        "```",
        "```",
        "GET journal/0001.ltx",
        "```",
        "The tag the coordinator read:",
        "```json",
        '{"type": "slack.message"}',
        "```",
        "```console",
        "$ cc-slack subscribe --kind alerts",
        "ALERT open monitor 315028134",
        "```",
        "```go",
        "broken",
        "```",
        "The flow:",
        "```mermaid",
        "flowchart LR",
        "```",
        "```dot",
        "digraph { a -> b }",
        "```",
        "Posts against alerts in a day:",
        "```chart",
        '{"mark": "bar"}',
        "```",
        "After.",
    ]
    shown = picture.drawn(tmp_path / "pictures", show_me)
    assert [line.split("](")[0] for line in shown] == [
        "![The call today",
        "![proto code",
        "```",
        "GET journal/0001.ltx",
        "```",
        "",
        "",
        "",
        "![The flow",
        "![Graph",
        "Posts against alerts in a day:",
        "```chart",
        '{"mark": "bar"}',
        "```",
        "After.",
    ]
    assert tried == ["code:sand", "code:proto", "code:go", "mermaid", "dot"]


def test_a_fence_of_sentences_shows_as_a_quote_of_its_text_and_a_bare_fence_draws_as_a_code_picture_only_when_it_holds_code(
    tmp_path, monkeypatch
) -> None:
    tried = []

    def draw(kind: str, source: str, out) -> None:
        tried.append(kind)
        out.write_bytes(b"\x89PNG\r\n")

    monkeypatch.setattr(picture, "visual_png", draw)
    show_me = [
        "The DM as drafted:",
        "```",
        "Hey Rowan! The copy for the acme team can't read acme's files: its grant throws <https://sentry.io/1|this error> at 9:29 PM.",
        "",
        "Can you take it? You'll know whether acme's files live in R2 or in its own bucket.",
        "```",
        "```markdown",
        "Thanks for the quick fix to the copy, it works on every team I tried tonight.",
        "```",
        "```",
        "func grant(team string) error {",
        '\treturn fmt.Errorf("no bucket for %s", team)',
        "}",
        "```",
        "```",
        "GET journal/0001.ltx",
        "```",
        "```",
        "api -> broker -> bucket",
        "```",
    ]
    shown = picture.drawn(tmp_path / "pictures", show_me)
    assert [line.split("](")[0] for line in shown] == [
        "The DM as drafted",
        "> Hey Rowan! The copy for the acme team can't read acme's files: its grant throws <https://sentry.io/1|this error> at 9:29 PM.",
        ">",
        "> Can you take it? You'll know whether acme's files live in R2 or in its own bucket.",
        "> Thanks for the quick fix to the copy, it works on every team I tried tonight.",
        "![Code",
        "```",
        "GET journal/0001.ltx",
        "```",
        "```",
        "api -> broker -> bucket",
        "```",
    ]
    assert tried == ["code:"]
    assert picture.sources("\n".join(show_me[6:9])) == []


def test_a_visual_chrome_dumped_before_it_finished_drawing_is_measured_again(tmp_path, monkeypatch) -> None:
    dumps = iter(["<html><body></body></html>", '<html data-print data-drawn="40x20"><body></body></html>'])
    sizes = []

    def headless(page, *args: str) -> str:
        sizes.append(args[0])
        if shot := next((arg.removeprefix("--screenshot=") for arg in args if arg.startswith("--screenshot=")), None):
            Path(shot).write_bytes(b"\x89PNG\r\n")
            return ""
        return next(dumps)

    monkeypatch.setattr(picture, "headless", headless)
    out = tmp_path / "dot.png"
    picture.visual_png("dot", "digraph { a -> b }", out)
    assert out.is_file()
    assert sizes == ["--window-size=1200,1200", "--window-size=1200,1200", "--window-size=40,20"]


def test_a_card_whose_show_me_leaves_nothing_to_show_has_no_show_me() -> None:
    assert limits.card_body(["One snapshot file per run."], ["", ""]) == "One snapshot file per run."
    assert limits.card_body(["One snapshot file per run."], ["![Show me](/p.png)"]) == "One snapshot file per run.\n*Show me*\n![Show me](/p.png)"
