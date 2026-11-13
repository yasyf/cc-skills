import html
import io
import json
import re
import subprocess
from pathlib import Path

import pytest
from drive import card, github, limits, picture, state

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
<html><body><div id="visual"></div><script type="module">import { draw } from "./visuals.js";
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
NOTED = """sequenceDiagram
  participant B as builder
  participant D as drive job ask
  participant S as cc-slack
  B->>D: the ASK block
  D->>S: its card
  Note over D: draws each picture; refuses one that fails
  S-->>B: ready
"""


@pytest.fixture(autouse=True)
def chrome() -> None:
    """Each test here fakes the drawing it needs or draws in real Chrome, so none takes the fake every other test draws with."""


def test_a_mermaid_picture_draws_rounded_unshadowed_nodes_in_a_legible_sans_with_quiet_edge_labels_and_no_margin_past_the_diagram(tmp_path) -> None:
    with picture.visual_site(tmp_path, MERMAID_LOOK) as page:
        dumped = picture.headless(page, tmp_path / "profile", "--window-size=1200,1200", "--no-sandbox", "--dump-dom")
    assert json.loads(html.unescape(LOOK.search(dumped)[1])) == {
        "node": {"fill": "rgb(246, 248, 250)", "border": "rgb(175, 184, 193)", "corner": "6px", "shadow": "none"},
        "text": {"size": "15px", "font": "-apple-system"},
        "edge": {"size": "13px", "color": "rgb(82, 81, 78)", "behind": "rgb(255, 255, 255)"},
        "margin": 4,
    }


def test_a_mermaid_diagram_mermaid_cannot_parse_raises_its_parse_error_and_draws_once_its_semicolon_is_written_as_its_entity_code(tmp_path, monkeypatch) -> None:
    headless = picture.headless
    monkeypatch.setattr(picture, "headless", lambda url, profile, *args: headless(url, profile, "--no-sandbox", *args))
    with pytest.raises(picture.PictureError, match=r"Parse error on line \d"):
        picture.visual_png("mermaid", NOTED, tmp_path / "noted.png")
    picture.visual_png("mermaid", NOTED.replace("picture; refuses", "picture#59; refuses"), tmp_path / "noted.png")
    assert (tmp_path / "noted.png").is_file()


def test_an_arch_doc_in_the_shape_the_drive_teaches_draws_and_a_store_without_its_medium_raises_arch_maps_problem(tmp_path, monkeypatch) -> None:
    headless = picture.headless
    monkeypatch.setattr(picture, "headless", lambda url, profile, *args: headless(url, profile, "--no-sandbox", *args))
    taught = {
        "arch": 1, "name": "drive", "title": "The dashboard reads the drive",
        "nodes": [{"id": "page", "kind": "app", "name": "Dashboard", "role": "draws cards"}, {"id": "state", "kind": "store", "medium": "disk", "name": "drive.json", "role": "keeps state"}],
        "edges": [{"from": "page", "to": "state", "label": "reads", "kind": "sync"}],
    }
    picture.visual_png("arch", json.dumps(taught), tmp_path / "taught.png")
    assert (tmp_path / "taught.png").is_file()
    unstored = taught | {"nodes": [taught["nodes"][0], {key: value for key, value in taught["nodes"][1].items() if key != "medium"}]}
    with pytest.raises(picture.PictureError, match='node "state": a store node needs "medium"'):
        picture.visual_png("arch", json.dumps(unstored), tmp_path / "unstored.png")


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
    first = picture.pictures(tmp_path / "pictures", show_me, set())
    assert picture.pictures(tmp_path / "pictures", show_me, set()) == first
    renderer = tmp_path / "visuals.js"
    renderer.write_text("export async function draw() {}")
    monkeypatch.setattr(picture, "VISUALS", renderer)
    assert picture.pictures(tmp_path / "pictures", show_me, set()) != first
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


def test_a_local_image_is_copied_in_under_its_caption_and_a_missing_one_is_left_out_naming_it(tmp_path) -> None:
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG\r\n")
    [copied], [] = picture.pictures(tmp_path / "pictures", f"![The card after his tap]({shot})\n", set())
    assert Path(copied).name == "The card after his tap"
    assert Path(copied).read_bytes() == shot.read_bytes()
    drawn, undrawn = picture.pictures(tmp_path / "pictures", f"![The card after his tap]({shot})\n![gone](/nowhere/gone.png)\n", set())
    assert ([Path(path).name for path in drawn], undrawn) == (["The card after his tap"], ['the Show me picture "gone": /nowhere/gone.png is neither a link nor a file here'])


def test_a_github_hosted_image_downloads_on_the_gh_token_a_repository_file_through_the_contents_api_and_an_attachment_from_its_signed_address(tmp_path, monkeypatch) -> None:
    signed = "https://private-user-images.githubusercontent.com/1/2-0f8e7a4c.png?jwt=eyJ0&amp;v=1"
    sent: list[list[str]] = []

    def gh(cmd: list[str], **_: object) -> str | bytes:
        sent.append(cmd[2:])
        if cmd[2] == "markdown":
            return f'<p dir="auto"><a target="_blank" href="{signed}"><img src="{signed}" alt="" style="max-width: 100%;"></a></p>\n'
        if cmd[-1].endswith("?ref=gone"):
            raise github.GithubError("gh: Not Found (HTTP 404)")
        return f"the bytes of {cmd[-1]}".encode()

    monkeypatch.setattr(github, "run", gh)
    monkeypatch.setattr(picture.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(f"the bytes of {url}".encode()))
    blob = "https://github.com/acme/app/blob/4b825dc642cb6eb9a060e54bf8d69288fbee4904/before.png?raw=true"
    raw = "https://raw.githubusercontent.com/acme/app/4b825dc6/shots/after%20light.png"
    attached = "https://github.com/user-attachments/assets/0f8e7a4c-1b2d-4e3f-9a8b-7c6d5e4f3a2b"
    show_me = f"![1 · Before, dev]({blob})\n![2 · After, this PR]({raw})\n![Pasted into the PR]({attached})\n"
    drawn, _ = picture.pictures(tmp_path / "pictures", show_me, set())
    assert [(Path(path).name, Path(path).read_bytes()) for path in drawn] == [
        ("1 · Before, dev", b"the bytes of repos/acme/app/contents/before.png?ref=4b825dc642cb6eb9a060e54bf8d69288fbee4904"),
        ("2 · After, this PR", b"the bytes of repos/acme/app/contents/shots/after%20light.png?ref=4b825dc6"),
        ("Pasted into the PR", b"the bytes of https://private-user-images.githubusercontent.com/1/2-0f8e7a4c.png?jwt=eyJ0&v=1"),
    ]
    assert sent[0][:3] == ["-H", "Accept: application/vnd.github.raw", "repos/acme/app/contents/before.png?ref=4b825dc642cb6eb9a060e54bf8d69288fbee4904"]
    assert sent[2] == ["markdown", "-f", f"text=![]({attached})", "-f", "mode=gfm", "-f", f"context={picture.SIGNING_CONTEXT}"]
    gone = "https://github.com/acme/app/blob/gone/before.png?raw=true"
    assert picture.pictures(tmp_path / "pictures", f"![Before]({gone})\n", set()) == (
        [],
        [f"the Show me picture \"Before\" does not draw: {gone} did not download: could not read acme/app's before.png at gone from GitHub: gh: Not Found (HTTP 404)"],
    )


def test_a_pr_cards_diagram_that_does_not_draw_is_left_out_with_mermaids_words_and_the_one_beside_it_still_draws(tmp_path, monkeypatch) -> None:
    def draw(kind: str, source: str, out) -> None:
        if source == NOTED:
            raise picture.PictureError("Parse error on line 7: Expecting 'SOLID_OPEN_ARROW', got 'NEWLINE'")
        out.write_bytes(b"\x89PNG\r\n")

    monkeypatch.setattr(picture, "visual_png", draw)
    drawn, undrawn = picture.pictures(tmp_path / "pictures", f"How a block is asked:\n```mermaid\n{NOTED}```\n\n```mermaid\nflowchart TD\n  a --> b\n```\n", set())
    assert [Path(path).name for path in drawn] == ["Diagram"]
    assert undrawn == ["the Show me picture \"How a block is asked\" does not draw: Parse error on line 7: Expecting 'SOLID_OPEN_ARROW', got 'NEWLINE'"]


def test_a_pr_cards_arch_whose_store_node_names_a_medium_the_drawing_does_not_know_is_left_out_naming_the_node_and_the_rest_still_draws(tmp_path, monkeypatch) -> None:
    headless = picture.headless
    monkeypatch.setattr(picture, "headless", lambda url, profile, *args: headless(url, profile, "--no-sandbox", *args))
    queued = DOC | {
        "nodes": [*DOC["nodes"], {"id": "stream", "kind": "store", "medium": "queue", "name": "Valkey stream", "role": "carries each fork"}],
        "path": {"title": "A fence reaches the card", "steps": [{"from": "body", "to": "drive", "say": "the card reads the fence"}]},
    }
    show_me = f"The fork snapshot share:\n```arch\n{json.dumps(queued)}\n```\n\nThe share, step by step:\n```mermaid\nflowchart TD\n  fork --> share\n```\n"
    drawn, undrawn = picture.pictures(tmp_path / "pictures", show_me, set())
    assert [Path(path).name for path in drawn] == ["The share, step by step"]
    assert undrawn == ['the Show me picture "The fork snapshot share" does not draw: node "stream": a store node needs "medium", one of memory, disk, bucket, keys, pages']


def test_a_home_relative_image_is_used_from_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    shot = tmp_path / "shots" / "s106.png"
    shot.parent.mkdir()
    shot.write_bytes(b"\x89PNG\r\n")
    [copied], [] = picture.pictures(tmp_path / "pictures", "![the api](~/shots/s106.png)\n", set())
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
        f"{long}\n```sand\nlet diff = lane::diff(@tree)\nlane::post(diff)\n```\n"
    )
    named = [Path(path).name for path in picture.pictures(tmp_path / "pictures", show_me, set())[0]]
    assert named == ["1 · Feed first then read today", "go code", " ".join(long.split())[: picture.TITLE_CHARS].rstrip()]
    assert drawn == ["mermaid", "code:go", "code:sand"]
    assert picture.pictures(tmp_path / "pictures", show_me, set())[0][0].endswith("/1 · Feed first then read today")
    assert drawn == ["mermaid", "code:go", "code:sand"]


def test_a_caption_naming_the_source_a_picture_keeps_still_draws_the_picture(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    show_me = "source:\n```mermaid\nflowchart TD\n  a --> b\n```\nsource.json\n```mermaid\nflowchart TD\n  c --> d\n```\n"
    drawn, _ = picture.pictures(tmp_path / "pictures", show_me, set())
    assert [(Path(path).name, Path(path).read_bytes()) for path in drawn] == [("source", b"\x89PNG\r\n"), ("source.json (2)", b"\x89PNG\r\n")]
    assert picture.cached(tmp_path / "pictures", "source", "mermaid", "flowchart TD\n  a --> b\n").read_bytes() == b"\x89PNG\r\n"


def test_pictures_titled_alike_on_one_card_are_numbered_apart_and_drawn_once(tmp_path, monkeypatch) -> None:
    drawn = []

    def draw(kind: str, source: str, out) -> None:
        drawn.append(kind)
        out.write_bytes(b"\x89PNG\r\n")

    monkeypatch.setattr(picture, "visual_png", draw)
    taken: set[str] = set()
    first, _ = picture.pictures(tmp_path / "pictures", "```mermaid\nflowchart TD\n  a --> b\n```\n", taken)
    second, _ = picture.pictures(tmp_path / "pictures", "```mermaid\nflowchart TD\n  c --> d\n```\n```mermaid\nflowchart TD\n  a --> b\n```\n", taken)
    assert [Path(path).name for path in [*first, *second]] == ["Diagram", "Diagram (2)", "Diagram (3)"]
    assert drawn == ["mermaid", "mermaid"]


def test_a_question_cards_show_me_draws_each_fence_titled_by_its_caption_or_kind_keeps_words_and_charts_as_written_leaves_out_raw_data_and_refuses_what_does_not_draw(
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
        "}",
        "```",
        "```proto",
        "message Run {",
        "}",
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
        "![The flow",
        "![Graph",
        "Posts against alerts in a day:",
        "```chart",
        '{"mark": "bar"}',
        "```",
        "After.",
    ]
    assert tried == ["code:sand", "code:proto", "mermaid", "dot"]
    with pytest.raises(state.DriveError, match='^the Show me picture "The call that breaks", a ```go fence, does not draw: the code:go did not finish drawing$'):
        picture.drawn(tmp_path / "pictures", ["The call that breaks:", "```go", "broken()", "next()", "```"])


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


TEARDOWN = "[1:2:ERROR:ui/display/mac/cv_display_link_mac.mm:188] CVDisplayLinkCreateWithCGDisplay failed. CVReturn: -6670\nTeardown watchdog expired; recording dump and terminating."


def test_a_visual_chrome_crashed_leaving_is_judged_by_the_verdict_it_printed_and_the_screenshot_it_wrote(tmp_path, monkeypatch) -> None:
    launched = []

    def run(command: list[str], **_) -> subprocess.CompletedProcess:
        launched.append(command)
        if shot := next((arg.removeprefix("--screenshot=") for arg in command if arg.startswith("--screenshot=")), None):
            Path(shot).write_bytes(b"\x89PNG\r\n")
            return subprocess.CompletedProcess(command, 133, "", TEARDOWN)
        return subprocess.CompletedProcess(command, 133, '<html data-print data-drawn="40x20"><body></body></html>', TEARDOWN)

    monkeypatch.setenv("CHROME", "chrome")
    monkeypatch.setattr(subprocess, "run", run)
    out = tmp_path / "flow.png"
    picture.visual_png("mermaid", "flowchart TD\n  a --> b\n", out)
    assert out.is_file()
    assert len(launched) == 2


def test_a_visual_chrome_fails_on_says_chrome_failed_and_never_that_the_picture_does_not_draw(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHROME", "chrome")
    monkeypatch.setattr(subprocess, "run", lambda command, **_: subprocess.CompletedProcess(command, 133, "", TEARDOWN))
    with pytest.raises(picture.ChromeError, match="Teardown watchdog expired"):
        picture.visual_png("mermaid", "flowchart TD\n  a --> b\n", tmp_path / "flow.png")
    with pytest.raises(state.DriveError, match=r'^the Show me picture "The wake", a ```mermaid fence, was not judged: headless Chrome failed, not the picture, so keep the picture and run the same command again: .*Teardown watchdog expired'):
        picture.drawn(tmp_path / "pictures", ["The wake", "```mermaid", "flowchart TD", "  a --> b", "```"])
    monkeypatch.setattr(subprocess, "run", lambda command, **_: subprocess.CompletedProcess(command, 0, "<html><body></body></html>", ""))
    with pytest.raises(picture.ChromeError, match="^Chrome gave no verdict on the dot$"):
        picture.visual_png("dot", "digraph { a -> b }", tmp_path / "dot.png")


def test_headless_chrome_runs_its_command_on_every_launch_of_a_profile_set_to_continue_where_it_left_off(tmp_path) -> None:
    profile = tmp_path / "profile"
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Preferences").write_text(json.dumps({"session": {"restore_on_startup": 1}}))
    page = tmp_path / "page.html"
    page.write_text("<!doctype html><html><body><p>drawn</p></body></html>")
    assert ["<p>drawn</p>" in picture.headless(page.as_uri(), profile, "--no-sandbox", "--dump-dom") for _ in range(3)] == [True, True, True]


def test_a_card_whose_show_me_leaves_nothing_to_show_has_no_show_me() -> None:
    assert limits.card_body(["One snapshot file per run."], ["", ""]) == "One snapshot file per run."
    assert limits.card_body(["One snapshot file per run."], ["![Show me](/p.png)"]) == "One snapshot file per run.\n*Show me*\n![Show me](/p.png)"


VIEWS_PAGE = """<!doctype html>
<html><head><title>Prototype</title><style>main { color: red }</style></head>
<body><main id="view"></main><template><p>a view still to build</p></template><noscript>turn scripts on</noscript>
<script>document.getElementById("view").textContent = new URLSearchParams(location.search).has("slack") ? "Local Block Kit preview Approve Hold" : "Priority queue Decision 1 of 72";</script>
</body></html>
"""


def test_a_pages_text_is_the_words_the_view_its_address_names_shows_once_its_scripts_run_and_a_page_chrome_cannot_load_raises(tmp_path, monkeypatch) -> None:
    headless = picture.headless
    monkeypatch.setattr(picture, "headless", lambda url, profile, *args: headless(url, profile, "--no-sandbox", *args))
    page = tmp_path / "index.html"
    page.write_text(VIEWS_PAGE)
    assert picture.page_text(page.as_uri()) == "Priority queue Decision 1 of 72"
    assert picture.page_text(f"{page.as_uri()}?slack=pr") == "Local Block Kit preview Approve Hold"
    with pytest.raises(picture.ChromeError, match="could not load .*missing.html: net::ERR_FILE_NOT_FOUND"):
        picture.page_text((tmp_path / "missing.html").as_uri())


FLAG_FLIP = '# before\n[target."platform/runs"]\nwrite = 0\n\n# after\n[target."platform/runs"]\nwrite = 1\n'
ARGUMENT_DROPPED = "// before\nclient.PushSchema(ctx, db, []*sandsql.LogicalTable{runClaims, sessionClaims, teamRuns})\n\n// after\nclient.PushSchema(ctx, db, []*sandsql.LogicalTable{runClaims, teamRuns})\n"
ENTRY_BEFORE = 'REVIEWER_TOKEN: {\n  name: "REVIEWER_TOKEN",\n  path: "/ci/review/token",\n},\n'
ENTRY_AFTER = 'REVIEWER_TOKEN: {\n  name: "REVIEWER_TOKEN",\n  path: "/ci/review/app-key",\n  app: "1234",\n},\n'
CLAIM = (
    "func (s *Store) Claim(ctx context.Context, run RunID) (Lease, error) {\n\tlease, err := s.leases.Acquire(ctx, run, s.ttl)\n\tif err != nil {\n"
    '\t\treturn Lease{}, fmt.Errorf("claim %s: %w", run, err)\n\t}\n\ts.journal.Append(ctx, ClaimEvent{Run: run, Lease: lease.ID})\n\treturn lease, nil\n}\n'
)
MIGRATION_SHOW_ME = (
    "The new migration:\n```sql\nDROP TABLE session_claims;\n```\n"
    f"`formats.toml`, before and after:\n```toml\n{FLAG_FLIP}```\n"
    f"The fixture's schema push, before and after:\n```go\n{ARGUMENT_DROPPED}```\n"
    f"The claim it keeps:\n```go\n{CLAIM}```\n"
)
TOKEN_SHOW_ME = (
    "A session step's command before this PR\n```sh\nexec bun run tools/oncall/session.ts --role responder\n```\n"
    "The same step after this PR\n```sh\nexec ci oncall-github bun run tools/oncall/session.ts --role responder\n```\n"
    f"The secret declaration before this PR\n```ts\n{ENTRY_BEFORE}```\n"
    f"The same declaration after this PR\n```ts\n{ENTRY_AFTER}```\n"
)


def test_a_pr_cards_one_line_fence_value_flip_and_dropped_argument_show_as_code_blocks_while_a_function_still_draws(tmp_path, monkeypatch, jev) -> None:
    drawn = []
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: drawn.append(source) or out.write_bytes(b"\x89PNG\r\n"))
    jev.plain = {FLAG_FLIP, ARGUMENT_DROPPED}
    named = [Path(path).name for path in picture.pictures(tmp_path / "pictures", MIGRATION_SHOW_ME, set())[0]]
    assert (named, drawn) == (["The claim it keeps"], [CLAIM])
    assert jev.illustrated == [FLAG_FLIP, ARGUMENT_DROPPED, CLAIM]
    assert card.show_me_words(MIGRATION_SHOW_ME, True, 2) == (
        "The new migration:\n\n```\nDROP TABLE session_claims;\n```\n\n"
        f"`formats.toml`, before and after\n```\n{FLAG_FLIP.rstrip()}\n```\n\n"
        f"The fixture's schema push, before and after\n```\n{ARGUMENT_DROPPED.rstrip()}\n```"
    )
    assert jev.illustrated == [FLAG_FLIP, ARGUMENT_DROPPED, CLAIM]


def test_a_pr_card_of_two_one_line_commands_and_a_map_entry_before_and_after_draws_no_picture(tmp_path, monkeypatch, jev) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: pytest.fail(f"drew a {kind} picture"))
    jev.plain = {ENTRY_BEFORE, ENTRY_AFTER}
    assert picture.pictures(tmp_path / "pictures", TOKEN_SHOW_ME, set()) == ([], [])
    assert card.show_me_words(TOKEN_SHOW_ME, False, 2).split("\n\n") == [
        "A session step's command before this PR",
        "```\nexec bun run tools/oncall/session.ts --role responder\n```",
        "The same step after this PR\n```\nexec ci oncall-github bun run tools/oncall/session.ts --role responder\n```",
        f"The secret declaration before this PR\n```\n{ENTRY_BEFORE.rstrip()}\n```",
        f"The same declaration after this PR\n```\n{ENTRY_AFTER.rstrip()}\n```",
    ]


def test_a_question_cards_fence_that_illustrates_nothing_stays_a_captioned_code_block_and_a_diagram_is_never_judged(tmp_path, monkeypatch, jev) -> None:
    drawn = []
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: drawn.append(kind) or out.write_bytes(b"\x89PNG\r\n"))
    jev.plain = {FLAG_FLIP}
    show_me = ["The table it drops:", "```sql", "DROP TABLE session_claims;", "```", "The flag:", "```toml", *FLAG_FLIP.splitlines(), "```"]
    show_me += ["1 · The claim:", "```go", *CLAIM.splitlines(), "```", "The flow:", "```mermaid", "flowchart TD", "  a --> b", "```"]
    shown = picture.drawn(tmp_path / "pictures", show_me)
    assert [line.split("](")[0] for line in shown] == [
        "The table it drops",
        "```",
        "DROP TABLE session_claims;",
        "```",
        "The flag",
        "```",
        *FLAG_FLIP.rstrip("\n").splitlines(),
        "```",
        "![1 · The claim",
        "![The flow",
    ]
    assert drawn == ["code:go", "mermaid"]
    assert jev.illustrated == [FLAG_FLIP, CLAIM]


def test_jevs_verdict_on_a_fence_is_kept_so_every_draw_shows_the_same_pictures_and_a_fence_jev_gives_none_draws(jev, capsys) -> None:
    jev.plain = {FLAG_FLIP}
    assert not picture.illustrative("toml", FLAG_FLIP)
    jev.plain = set()
    assert not picture.illustrative("TOML", FLAG_FLIP)
    assert jev.illustrated == [FLAG_FLIP]
    [kept] = (state.root() / picture.JUDGED).glob("*.json")
    assert json.loads(kept.read_text()) == {"language": "toml", "source": FLAG_FLIP, "p_yes": 0.05, "illustrative": False}
    assert not picture.illustrative("go", "return lease, nil\n") and picture.illustrative("mermaid", "flowchart TD\n")
    jev.answer = None
    assert picture.illustrative("go", CLAIM)
    assert capsys.readouterr().err == "drive card: Jev did not say whether a ```go fence illustrates anything, so it draws as a picture: no jev decision within 20s\n"
    assert len(list((state.root() / picture.JUDGED).glob("*.json"))) == 1
