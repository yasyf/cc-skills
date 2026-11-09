import json

from drive import picture

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


def test_an_arch_diagram_becomes_graphviz_with_its_boxes_lines_and_main_path() -> None:
    source = picture.arch_dot(DOC)
    assert 'label="A tap card\'s show-me becomes a picture"' in source
    assert '"body" [shape=cylinder label=<<b>PR body</b><br/><font point-size="10">carries the fence</font>>]' in source
    assert 'subgraph "cluster_drive" {' in source
    assert '"him" [shape=oval' in source
    assert '"body" -> "draw" [color="#2563eb" penwidth=2 lhead="cluster_drive" label="Show me"]' in source
    assert '"draw" -> "him" [ltail="cluster_drive"]' in source


def test_sources_lists_diagrams_and_images_in_the_order_they_appear() -> None:
    show_me = (
        "![before](https://example.com/before.png)\n\n```arch\n"
        + json.dumps(DOC)
        + "\n```\n\n```mermaid\nflowchart LR\n```\n\n```dot\ndigraph { a -> b }\n```\n"
    )
    assert [kind for kind, _ in picture.sources(show_me)] == ["image", "arch", "dot"]


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
