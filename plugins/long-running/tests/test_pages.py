import http.client
import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from drive import cli, dashboard, page_check, pages, state
from test_dashboard import FEATURE, QUESTION, RAIL, board, browse, post, serving

Run = Callable[..., tuple[int, str]]
PAGE = {"index.html": "<main></main>", "page.json": json.dumps({"title": "Run page lane", "goal": "how far the run page is from live"}), "app.js": "export {};"}
BRIDGED = """<script type="module">
import { live, answer } from '/kit/kit.js';
live((slice) => parent.postMessage({ type: 'seen', features: slice.features.map((entry) => entry.id), prs: slice.prs.map((entry) => [entry.answer, entry.options]) }, '*'));
addEventListener('message', (event) => event.source === parent && event.data.type === 'try' && (answer(event.data.answer, event.data.label), parent.postMessage({ type: 'tried' }, '*')));
</script>"""


@pytest.fixture(autouse=True)
def checked_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(page_check, 'check', lambda *args: [])


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


def test_a_page_goes_in_whole_under_its_slug_and_a_second_add_replaces_it(run: Run, ordered: str, tmp_path: Path) -> None:
    code, said = run("page", "add", "run-lane", "--order", "o1", "--dir", str(folder(tmp_path, PAGE)))
    home = pages.pages_dir(ordered) / "run-lane"
    assert (code, said) == (0, "page run-lane for o1: /pages/run-lane/ on the dashboard, listed under Pages as 'Run page lane'")
    assert sorted(path.name for path in home.iterdir()) == ["app.js", "index.html", "page.json"]
    kept = json.loads((home / "page.json").read_text())
    assert {key: kept.get(key) for key in ("title", "goal", "orders", "prs", "project", "workstream")} == {
        "title": "Run page lane", "goal": "how far the run page is from live", "orders": ["o1"], "prs": [], "project": None, "workstream": None,
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


def test_a_page_is_filed_where_its_order_is_filed_now_so_a_refile_moves_it(run: Run, ordered: str, tmp_path: Path) -> None:
    run("page", "add", "run-lane", "--order", "o1", "--dir", str(folder(tmp_path, PAGE)))

    def filed() -> list[tuple[str, str]]:
        return [(page["project"], page["workstream"]) for page in pages.shown(state.load(ordered), pages.listed(ordered))]

    assert filed() == [("Iris", "Run page")]
    assert run("order", "set", "o1", "--project", "Platform", "--workstream", "Runs")[0] == 0
    assert filed() == [("Platform", "Runs")]


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


def test_a_render_refusal_keeps_the_previous_page(run: Run, ordered: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    built = folder(tmp_path, PAGE)
    pages.add(ordered, 'run-lane', 'o1', built)
    home = pages.pages_dir(ordered) / 'run-lane'
    before = (home / 'page.json').read_bytes()
    (built / 'index.html').write_text('<pre>POSTED raw record</pre>')
    def refused(*args: object) -> list[str]:
        raise state.DriveError('page render check refused plain text')
    monkeypatch.setattr(page_check, 'check', refused)
    with pytest.raises(state.DriveError, match='plain text'):
        pages.add(ordered, 'run-lane', 'o1', built)
    assert (home / 'page.json').read_bytes() == before
    assert (home / 'index.html').read_text() == PAGE['index.html']


def test_a_model_call_uses_its_registered_frame_without_requiring_an_answer_tap(run: Run, ordered: str, tmp_path: Path) -> None:
    run('page', 'add', 'run-lane', '--order', 'o1', '--dir', str(folder(tmp_path, PAGE | {'index.html': BRIDGED})))
    seen = browse(tmp_path, """
let seen = false, calls = 0;
addEventListener('message', (event) => { if (event.data?.type === 'seen') seen = true; });
location.hash = '#/pages/run-lane';
await until(() => seen);
const fetched = window.fetch;
window.fetch = (url, options) => url === '/api/complete' ? (calls++, Promise.resolve(new Response('data: [DONE]' + String.fromCharCode(10, 10)))) : fetched(url, options);
Object.defineProperty(navigator, 'userActivation', { value: { isActive: false } });
const data = { type: 'drive:complete', id: 'explain', messages: [{ role: 'user', content: 'What blocks it?' }] };
await heardPage({ source: window, data });
const refused = calls;
await heardPage({ source: document.querySelector('iframe.page-frame').contentWindow, data });
done({ refused, calls });
""", board(pages=pages.shown(state.load(ordered), pages.listed(ordered))))
    assert seen == {'refused': 0, 'calls': 1}


def test_a_group_accepts_only_scoped_options_and_one_undo_restores_all_its_answers(run: Run, ordered: str, tmp_path: Path) -> None:
    bridge = BRIDGED.replace('live, answer', 'live, answer, answers').replace("answer(event.data.answer, event.data.label)", 'answers(event.data.picks)')
    run('page', 'add', 'run-lane', '--order', 'o1', '--dir', str(folder(tmp_path, PAGE | {'index.html': bridge})))
    prs = [RAIL | {'key': f'a/b#{n}', 'title': f'change {n}', 'url': f'https://github.com/a/b/pull/{n}', 'head': 'abc', 'card': None, 'feature': feature} for n, feature in ((7, 'o1'), (8, 'o1'), (9, 'o2'))]
    seen = browse(tmp_path, """
const posted = [];
const fetched = window.fetch;
window.fetch = (url, options) => options?.method === 'POST' ? (posted.push([url, JSON.parse(options.body)]), Promise.resolve({ ok: true, json: async () => ({ restored: true, at: '2026-11-13T00:00:00Z', undo_until: '2099-11-13T00:00:00Z' }) })) : fetched(url, options);
Object.defineProperty(navigator, 'userActivation', { value: { isActive: true } });
let seen = false, acks = 0;
addEventListener('message', (event) => event.data?.type === 'seen' ? seen = true : event.data?.type === 'tried' && acks++);
location.hash = '#/pages/run-lane';
await until(() => seen);
const frame = document.querySelector('iframe.page-frame');
const send = async (picks) => { const before = acks; frame.contentWindow.postMessage({ type: 'try', picks }, '*'); await until(() => acks > before); };
await send([{ card: 'tap:a/b#9', label: 'Approve' }]);
await send([{ card: 'tap:a/b#7', label: 'Merge' }]);
const refused = posted.length;
await send([{ card: 'tap:a/b#7', label: 'Approve' }, { card: 'tap:a/b#8', label: 'Approve' }, { card: 'tap:a/b#7', label: 'Approve' }]);
await until(() => document.querySelector('#toasts .toast.sent'));
const groups = document.querySelectorAll('#toasts .toast').length;
document.querySelector('#toasts button[data-undo]').click();
await until(() => document.querySelector('#toasts .toast.undone'));
done({ refused, groups, posted: posted.map(([url, body]) => [url, body.card ?? body.pr]), restored: answers.size });
""", board(review=prs, pages=pages.shown(state.load(ordered), pages.listed(ordered))))
    assert seen == {'refused': 0, 'groups': 1, 'posted': [['/api/tap', 'a/b#7'], ['/api/tap', 'a/b#8'], ['/api/undo', 'tap:a/b#7'], ['/api/undo', 'tap:a/b#8']], 'restored': 0}


def test_a_live_diagram_preserves_focused_node_and_camera_when_reordered_and_removes_deleted_nodes(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
const { diagram } = await import('/kit/kit.js');
const host = document.createElement('div');
host.style.cssText = 'width:1000px;height:500px';
document.body.append(host);
const doc = { arch: 1, name: 'Work', title: 'Writer and reads', nodes: [{ id: 'writer', name: 'Writer', role: 'Running', kind: 'service', nodes: [{ id: 'change', name: 'Change', role: 'Open', kind: 'artifact' }] }, { id: 'reader', name: 'Reader', role: 'Running', kind: 'service' }] };
const map = await diagram(host, doc);
const node = host.querySelector('[data-block="writer"]');
node.focus();
const svg = host.querySelector('.smap-canvas svg');
svg.dispatchEvent(new WheelEvent('wheel', { deltaY: 50, bubbles: true, cancelable: true, clientX: 250, clientY: 250 }));
const camera = svg.getAttribute('viewBox');
const next = structuredClone(doc); next.nodes.reverse(); next.nodes[1].name = 'Writer landed';
await diagram(host, next);
const kept = { identity: node === host.querySelector('[data-block="writer"]'), focus: document.activeElement === node, camera: camera === svg.getAttribute('viewBox'), title: host.textContent.includes('Writer landed') };
const removed = host.querySelector('[data-block="reader"]');
next.nodes.shift();
await diagram(host, next);
done({ ...kept, removed: !removed.isConnected });
""", board(), drawn=True)
    assert seen == {'identity': True, 'focus': True, 'camera': True, 'title': True, 'removed': True}


def test_a_partial_group_failure_can_undo_successes_and_retry_only_the_failed_answer(tmp_path: Path) -> None:
    questions = [QUESTION | {'answer': f'https://x.slack.com/archives/D1/p179438245385920{n}', 'tasks': ['o1']} for n in (1, 2)]
    seen = browse(tmp_path, """
let calls = 0;
const fetched = window.fetch;
window.fetch = (url, options) => {
  if (url === '/api/answer') {
    const failed = ++calls === 1;
    return Promise.resolve({ ok: !failed, json: async () => failed ? { error: 'Card changed' } : { at: '2026-11-13T00:00:00Z', undo_until: new Date(Date.now() + 10000).toISOString() } });
  }
  if (url === '/api/undo') return Promise.resolve({ ok: true, json: async () => ({ restored: true }) });
  return fetched(url, options);
};
const picks = model.queue.map((q) => ({ card: q.answer, label: 'Raise it' }));
await answerBatch(picks);
const group = [...toasts].find(([key]) => key.startsWith('group:'))[0];
const failed = { error: answers.get(picks[0].card).error, done: answers.get(picks[1].card).done, said: document.querySelector('#toasts .toast').textContent };
await undo(group);
const undone = { remaining: [...answers.keys()], state: toasts.get(group).state };
await answerBatch([picks[0]]);
done({ failed: { error: failed.error, done: failed.done, counted: failed.said.includes('1 sent, 1 failed') }, undone, calls, retried: answers.get(picks[0].card).done });
""", board(queue=questions))
    assert seen == {'failed': {'error': 'Card changed', 'done': True, 'counted': True}, 'undone': {'remaining': [questions[0]['answer']], 'state': 'undone'}, 'calls': 3, 'retried': True}


@pytest.mark.parametrize('count', [1, 1000 if os.environ.get('FORGE_CI_AT_SCALE') == '1' else 100])
def test_page_hydration_uses_one_request_for_its_scope_at_any_order_count(count: int, run: Run, ordered: str, tmp_path: Path) -> None:
    run('page', 'add', 'run-lane', '--order', 'o1', '--dir', str(folder(tmp_path, PAGE | {'index.html': BRIDGED})))
    registered = pages.shown(state.load(ordered), pages.listed(ordered))
    registered[0]['scope'] = {'workstreams': ['SandSQL']}
    features = [FEATURE | {'id': f'o{n + 1}'} for n in range(count)]
    seen = browse(tmp_path, """
let slices = 0;
addEventListener('message', (event) => { if (event.data?.type === 'seen') slices = event.data.features.length; });
location.hash = '#/pages/run-lane';
await until(() => slices && hydration.length === 1);
done({ count: slices, requests: hydration.length });
""", board(status=[{'state': 'running', 'features': features}], pages=registered), before="""
const hydration = [];
const fetchBefore = window.fetch;
window.fetch = (url, options) => { if (url.startsWith('/api/page?')) hydration.push(url); return fetchBefore(url, options); };
""")
    assert seen == {'count': count, 'requests': 1}


def test_adjusting_a_group_selection_sends_nothing_and_survives_a_live_patch(tmp_path: Path) -> None:
    seen = browse(tmp_path, """
const { patch, prGroups } = await import('/kit/kit.js');
const posted = [];
addEventListener('message', (event) => { if (['drive:answer', 'drive:answers'].includes(event.data?.type)) posted.push(event.data); });
const host = document.createElement('div');document.body.append(host);
const prs = [1, 2].map((n) => ({ key: 'a/b#' + n, answer: 'tap:a/b#' + n, url: 'https://github.com/a/b/pull/' + n, title: 'Change ' + n, head: 'abc', review: 'approved', options: ['Approve', 'Hold'], stage: { name: 'card' }, checks: 'pass' }));
patch(host, prGroups(prs));
const input = host.querySelector('input');input.click();
await wait(50);
const unprompted = posted.length;
patch(host, prGroups(prs));
const kept = input === host.querySelector('input') && !input.checked;
const label = host.querySelector('[data-kit-group]').textContent;
host.querySelector('[data-kit-group]').click();
await until(() => posted.length);
done({ unprompted, kept, label, posted });
""", board())
    assert seen == {'unprompted': 0, 'kept': True, 'label': 'Approve 1', 'posted': [{'type': 'drive:answers', 'picks': [{'card': 'tap:a/b#2', 'label': 'Approve'}]}]}


def test_live_scope_covers_new_matching_orders_beyond_the_project() -> None:
    page = {'orders': ['o1'], 'scope': {'projects': ['SandSQL'], 'workstreams': ['SandSQL'], 'words': ['sandsql', 'litestream']}}
    features = [
        {'id': 'o1', 'title': 'The page'},
        {'id': 'o2', 'title': 'Other title', 'project': 'SandSQL'},
        {'id': 'o3', 'title': 'Other title', 'project': 'Platform', 'workstream': 'SandSQL'},
        {'id': 'o4', 'title': 'SandSQL agent inbox', 'project': 'Iris'},
        {'id': 'o5', 'title': 'Unrelated task', 'project': 'Iris'},
    ]
    assert pages.scope_orders(features, page) == {'o1', 'o2', 'o3', 'o4'}
