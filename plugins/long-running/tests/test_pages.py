import http.client
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from drive import cli, dashboard, pages, state
from test_dashboard import RAIL, board, browse, post, serving

Run = Callable[..., tuple[int, str]]
PAGE = {"index.html": "<main></main>", "page.json": json.dumps({"title": "Run page lane", "goal": "how far the run page is from live"}), "app.js": "export {};"}
BRIDGED = """<script type="module">
import { live, answer } from '/kit/kit.js';
live((slice) => parent.postMessage({ type: 'seen', features: slice.features.map((entry) => entry.id), prs: slice.prs.map((entry) => [entry.answer, entry.options]) }, '*'));
addEventListener('message', (event) => event.source === parent && event.data.type === 'try' && (answer(event.data.answer, event.data.label), parent.postMessage({ type: 'tried' }, '*')));
</script>"""


def folder(tmp_path: Path, files: dict[str, str]) -> Path:
    built = tmp_path / "built"
    built.mkdir(exist_ok=True)
    for name, text in files.items():
        (built / name).parent.mkdir(parents=True, exist_ok=True)
        (built / name).write_text(text)
    return built


@pytest.fixture
def ordered(run: Run, demo: str) -> str:
    run("order", "add", "--title", "Run page", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    return demo


def test_a_page_goes_in_whole_under_its_slug_filed_by_its_order_and_a_second_add_replaces_it(run: Run, ordered: str, tmp_path: Path) -> None:
    code, said = run("page", "add", "run-lane", "--order", "o1", "--dir", str(folder(tmp_path, PAGE)))
    home = pages.pages_dir(ordered) / "run-lane"
    assert (code, said) == (0, "page run-lane for o1: /pages/run-lane/ on the dashboard, listed under Pages as 'Run page lane'")
    assert sorted(path.name for path in home.iterdir()) == ["app.js", "index.html", "page.json"]
    kept = json.loads((home / "page.json").read_text())
    assert {key: kept[key] for key in ("title", "goal", "orders", "prs", "project", "workstream")} == {
        "title": "Run page lane", "goal": "how far the run page is from live", "orders": ["o1"], "prs": [], "project": "Iris", "workstream": "Run page",
    }
    (tmp_path / "built" / "app.js").unlink()
    assert run("page", "add", "run-lane", "--order", "o1", "--dir", str(tmp_path / "built"))[0] == 0
    assert sorted(path.name for path in home.iterdir()) == ["index.html", "page.json"]
    assert [page["slug"] for page in pages.listed(ordered)] == ["run-lane"]


@pytest.mark.parametrize(
    ("slug", "files", "refused"),
    [
        ("Run Lane", PAGE, "'Run Lane' is no page slug"),
        ("run-lane", {key: value for key, value in PAGE.items() if key != "index.html"}, "holds no index.html"),
        ("run-lane", PAGE | {"page.json": json.dumps({"title": "Run page lane"})}, "names no title or no goal"),
        ("run-lane", PAGE | {"build.py": "print()"}, "not build.py"),
        ("run-lane", PAGE | {"page.json": json.dumps({"title": "t", "goal": "g", "orders": ["o9"]})}, "names o9, which the demo drive does not hold"),
        ("run-lane", PAGE | {"page.json": json.dumps({"title": "t", "goal": "g", "prs": ["a/b#1"]})}, "names a/b#1, which the demo drive does not hold"),
    ],
)
def test_page_add_refuses_a_folder_the_dashboard_could_not_serve_or_naming_what_the_drive_does_not_hold(
    ordered: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], slug: str, files: dict[str, str], refused: str
) -> None:
    assert cli.main(["page", "add", slug, "--order", "o1", "--dir", str(folder(tmp_path, files))]) == 2
    assert refused in capsys.readouterr().err
    assert not pages.pages_dir(ordered).exists()


def test_a_page_is_live_while_any_order_it_shows_is_open(run: Run, ordered: str, tmp_path: Path) -> None:
    run("order", "add", "--title", "Run page docs", "--project", "Iris", "--workstream", "Run page", "document the run page")
    run("page", "add", "run-lane", "--order", "o1", "--dir", str(folder(tmp_path, PAGE | {"page.json": json.dumps({"title": "t", "goal": "g", "orders": ["o2"]})})))
    with state.edit(ordered) as data:
        data["orders"]["o1"]["state"] = "done"
    [page] = pages.shown(state.load(ordered), pages.listed(ordered))
    assert (page["live"], page["order_title"]) == (True, "Run page")
    with state.edit(ordered) as data:
        data["orders"]["o2"]["state"] = "dropped"
    assert [page["live"] for page in pages.shown(state.load(ordered), pages.listed(ordered))] == [False]


def test_a_page_serves_only_its_own_files_of_the_kinds_it_may_hold(run: Run, ordered: str, tmp_path: Path) -> None:
    run("page", "add", "run-lane", "--order", "o1", "--dir", str(folder(tmp_path, PAGE)))
    (pages.pages_dir(ordered) / "secret.json").write_text("{}")
    home = (pages.pages_dir(ordered) / "run-lane").resolve()
    assert [pages.file(ordered, "run-lane", path) for path in ("", "app.js", "../secret.json", "../../drive.db", "missing.js")] == [home / "index.html", home / "app.js", None, None, None]
    assert pages.file(ordered, "..", "secret.json") is None


def test_the_dashboard_serves_a_page_only_sandboxed_and_its_kit_to_any_origin_and_takes_no_post_from_a_sandboxed_origin(run: Run, ordered: str, tmp_path: Path) -> None:
    shown = dashboard.Board(ordered, [])
    before = shown.stamp()
    run("page", "add", "run-lane", "--order", "o1", "--dir", str(folder(tmp_path, PAGE)))
    (pages.pages_dir(ordered) / "secret.json").write_text("{}")
    server, port = serving(shown)

    def get(path: str) -> tuple[int, str | None, str | None]:
        connection = http.client.HTTPConnection(dashboard.LOOPBACK, port, timeout=5)
        connection.request("GET", path, headers={"Host": f"box:{port}"})
        reply = connection.getresponse()
        reply.read()
        connection.close()
        return reply.status, reply.getheader("Content-Security-Policy"), reply.getheader("Access-Control-Allow-Origin")

    got = [get(path) for path in ("/pages/run-lane/", "/pages/run-lane/app.js", "/pages/run-lane/%2e%2e/secret.json", "/kit/kit.js")]
    refused = post(port, "null", "/api/tap", {"pr": "a/b#7", "label": "Approve", "head": "abc"})[0]
    server.shutdown()
    assert shown.stamp() != before
    assert got == [(200, "sandbox allow-scripts", None), (200, "sandbox allow-scripts", None), (404, None, None), (200, None, "*")]
    assert refused == 403


def test_a_page_gets_its_slice_and_its_answer_counts_only_from_his_click_on_one_of_its_prs_with_one_of_its_options(run: Run, ordered: str, tmp_path: Path) -> None:
    run("page", "add", "run-lane", "--order", "o1", "--dir", str(folder(tmp_path, PAGE | {"index.html": BRIDGED})))
    prs = [RAIL | {"key": f"a/b#{n}", "title": f"change {n}", "url": f"https://github.com/a/b/pull/{n}", "head": "abc", "card": None, "feature": feature} for n, feature in ((7, "o1"), (8, "o2"))]
    seen = browse(tmp_path, """
const posted = [];
const fetched = window.fetch;
window.fetch = (url, options) => options?.method === 'POST' ? (posted.push([url, JSON.parse(options.body)]), new Promise(() => {})) : fetched(url, options);
const activation = { isActive: false };
Object.defineProperty(navigator, 'userActivation', { value: activation });
const heard = [];
let acks = 0;
addEventListener('message', (event) => event.data?.type === 'seen' ? heard.push(event.data) : event.data?.type === 'tried' && acks++);
location.hash = '#/pages/run-lane';
await until(() => heard.length);
const frame = document.querySelector('#main iframe.page-frame');
const tried = async (answer, label) => { const before = acks; frame.contentWindow.postMessage({ type: 'try', answer, label }, '*'); await until(() => acks > before); return posted.length; };
const unprompted = await tried('tap:a/b#7', 'Approve');
activation.isActive = true;
const outside = await tried('tap:a/b#8', 'Approve');
const unoffered = await tried('tap:a/b#7', 'Merge');
await tried('tap:a/b#7', 'Approve');
done({ sandbox: frame.getAttribute('sandbox'), slice: heard[0], counts: [unprompted, outside, unoffered], posted, toast: document.querySelector('#toasts .toast')?.className });
""", board(review=prs, pages=pages.shown(state.load(ordered), pages.listed(ordered))))
    assert seen == {
        "sandbox": "allow-scripts",
        "slice": {"type": "seen", "features": ["o1"], "prs": [["tap:a/b#7", ["Approve", "Hold"]]]},
        "counts": [0, 0, 0],
        "posted": [["/api/tap", {"pr": "a/b#7", "label": "Approve", "head": "abc"}]],
        "toast": "toast sending",
    }
