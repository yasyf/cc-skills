import json

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


def test_sources_lists_diagrams_and_images_in_the_order_they_appear() -> None:
    show_me = (
        "![before](https://example.com/before.png)\n\n```arch\n"
        + json.dumps(DOC)
        + "\n```\n\n```mermaid\nflowchart LR\n```\n\n```dot\ndigraph { a -> b }\n```\n"
    )
    assert [kind for kind, _ in picture.sources(show_me)] == ["image", "arch", "mermaid", "dot"]


def test_a_picture_is_drawn_once_and_again_when_the_renderer_changes(tmp_path, monkeypatch) -> None:
    drawn = []

    def draw(kind: str, source: str, out) -> None:
        drawn.append(kind)
        out.write_bytes(b"\x89PNG\r\n")

    monkeypatch.setattr(picture, "visual_png", draw)
    show_me = "```arch\n" + json.dumps(DOC) + "\n```\n"
    first = picture.pictures(tmp_path / "pictures", "app PR 7", show_me)
    assert picture.pictures(tmp_path / "pictures", "app PR 7", show_me) == first
    renderer = tmp_path / "visuals.js"
    renderer.write_text("export async function draw() {}")
    monkeypatch.setattr(picture, "VISUALS", renderer)
    assert picture.pictures(tmp_path / "pictures", "app PR 7", show_me) != first
    assert drawn == ["arch", "arch"]
    assert json.loads((tmp_path / "pictures").glob("*/source.json").__next__().read_text())["kind"] == "arch"


def test_a_local_image_is_used_where_it_is_and_a_missing_one_is_skipped(tmp_path) -> None:
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG\r\n")
    show_me = f"![shot]({shot})\n![gone](/nowhere/gone.png)\n"
    assert picture.pictures(tmp_path / "pictures", "app PR 7", show_me) == [str(shot)]


def test_a_home_relative_image_is_used_from_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    shot = tmp_path / "shots" / "s106.png"
    shot.parent.mkdir()
    shot.write_bytes(b"\x89PNG\r\n")
    assert picture.pictures(tmp_path / "pictures", "monorepo PR 53064", "![the api](~/shots/s106.png)\n") == [str(shot)]


def test_a_question_cards_show_me_draws_each_fence_titled_by_its_caption_and_leaves_out_words_and_what_does_not_draw(tmp_path, monkeypatch) -> None:
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
        "After.",
    ]
    shown = picture.drawn(tmp_path / "pictures", show_me)
    assert [line.split("](")[0] for line in shown] == ["![The call today", "![Show me", "", "", "", "", "![The flow", "After."]
    assert tried == ["code:sand", "code:proto", "code:go", "mermaid"]


def test_a_card_whose_show_me_leaves_nothing_to_show_has_no_show_me() -> None:
    assert limits.card_body(["One snapshot file per run."], ["", ""]) == "One snapshot file per run."
    assert limits.card_body(["One snapshot file per run."], ["![Show me](/p.png)"]) == "One snapshot file per run.\n*Show me*\n![Show me](/p.png)"
