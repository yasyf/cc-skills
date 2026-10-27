import pytest
from server import Drive, parse_plan, plan_prs

DOC = """id: a5262e7
updated: 2026-10-27T06:30:00Z

# Progress

## Ordered plan of action (what lands, in what order)
1. **Now, no owner action needed.** The gateway batch (#42712, #42714) lands, then #43200.
2. **Taps he still owes:** the credential stack (#42788-#42790, #42737).
3. Step 11 cutover.

## Lanes live
- #49999 is not a plan step.
"""


@pytest.mark.parametrize(
    ("text", "numbers"),
    [
        ("#42712 and #42714", [42712, 42714]),
        ("(#42788-#42790, #42737)", [42788, 42789, 42790, 42737]),
        ("#42712 again #42712", [42712]),
        ("no pull requests", []),
    ],
)
def test_plan_prs_expands_ranges_once(text, numbers):
    assert plan_prs(text) == numbers


def test_parse_plan_reads_only_the_ordered_section():
    updated, steps = parse_plan(DOC)
    assert updated == "2026-10-27T06:30:00Z"
    assert [step["title"] for step in steps] == ["Now, no owner action needed", "Taps he still owes", "Step 11 cutover."]
    assert steps[0]["prs"] == [42712, 42714, 43200]
    assert steps[1]["prs"] == [42788, 42789, 42790, 42737]
    assert steps[2]["text"] == ""


def test_parse_plan_skips_a_doc_without_the_section():
    assert parse_plan("id: x\nupdated: now\n\n## Lanes live\n1. **A.** b\n") is None


def test_read_board_finds_a_verdict_keyed_by_the_bare_pr_number():
    row = {"number": 43211, "head": "8242887", "title": "T", "change": "C.", "plan": "P.", "risk": "R.", "review": "V.", "blocker": "B."}
    board = {
        "doc": {"blocks": [{"id": "card-merge", "type": "card", "children": [{"id": "merge-prs", "type": "pr-groups", "groups": [{"id": "g", "title": "G", "summary": "S.", "prs": [row]}]}]}]},
        "interactions": {"triage": {"merge-prs": {"43211": {"verdict": "approved", "head": "8242887"}}}, "choices": {}},
    }
    rows = Drive({"board_url": "http://board/p/x"}, None).read_board(board)["rows"]
    assert rows[0]["block"] == "merge-prs"
    assert rows[0]["verdict"] == {"verdict": "approved", "head": "8242887"}


def card(card_id, *children):
    return {"id": card_id, "type": "card", "title": card_id, "children": list(children)}


def board(blocks, **interactions):
    return {"doc": {"blocks": blocks}, "interactions": {"triage": {}, "choices": {}, "decisions": {}, "inputs": {}, **interactions}}


def test_read_board_counts_only_unanswered_questions():
    blocks = [
        card("answered-approval", {"id": "a1", "type": "approval"}),
        card("open-choice", {"id": "c1", "type": "choice", "options": [{"label": "Yes", "recommended": True}]}),
        card("screenshots-only", {"id": "i1", "type": "image"}),
    ]
    cards = read_board(board(blocks, decisions={"a1": {"verdict": "approved"}}), "url", {})["cards"]
    assert [(c["id"], c["recommended"]) for c in cards] == [("open-choice", "Yes")]


def test_read_board_reads_verdicts_by_number_and_root_gos():
    groups = {"id": "merge-prs", "type": "pr-groups", "groups": [{"title": "G", "prs": [{"number": 41, "head": "abc1234"}, {"number": 42, "head": "def5678"}]}]}
    rows = read_board(board([card("merge", groups)], triage={"merge-prs": {"41": {"verdict": "approved", "head": "abc1234"}}}), "url", {42: "def5678"})["rows"]
    assert [(r["number"], (r["verdict"] or {}).get("verdict"), r["go_head"]) for r in rows] == [(41, "approved", None), (42, None, "def5678")]


OVERVIEW = """
const vm = require('vm');
const fs = require('fs');
const noop = () => {};
const ctx = { console, Intl, Date, Math, JSON, Set, Map, Object, Array, String, Number, RegExp, Boolean, Promise, setInterval: noop,
  document: { addEventListener: noop, querySelector: () => ({}) }, window: { addEventListener: noop, scrollY: 0, scrollTo: noop }, location: { hash: '' } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8') + '\n;globalThis.state = state; globalThis.overview = overview;', ctx);
ctx.state.model = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
ctx.state.version = 1;
const html = ctx.overview();
const landed = Number(/data-landed="(\\d+)"/.exec(html)[1]);
const total = Number(/data-total="(\\d+)"/.exec(html)[1]);
console.log(JSON.stringify({ landed, total, green: (html.match(/<a class="sq landed"/g) || []).length, squares: (html.match(/<a class="sq /g) || []).length }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_overview_ring_counts_the_green_squares(tmp_path):
    prs = {
        "1": {"title": "sand: a", "state": "landed", "head": "a"},
        "2": {"title": "sand: b", "state": "landed", "head": "b"},
        "3": {"title": "api: c", "state": "open", "head": "c"},
        "4": {"title": "api: d", "state": "landed", "head": "d"},
        "5": {"title": "ci: tooling", "state": "landed", "head": "e"},
    }
    trees = {"1": ["New tree", "Old tree"], "2": ["Old tree"], "3": ["Shared"], "4": ["Shared"], "5": ["Shared"]}
    model = {"prs": prs, "trees": trees, "tree_names": ["New tree", "Old tree", "Shared"], "plan": [], "records": [], "lanes": [], "repo": "o/r",
             "board": {"rows": [], "cards": [], "url": "u"}, "releases": {"markdown": ""}, "story": {}}
    (tmp_path / "model.json").write_text(json.dumps(model))
    (tmp_path / "run.js").write_text(OVERVIEW)
    out = json.loads(subprocess.run(["node", str(tmp_path / "run.js"), str(APP / "app.js"), str(tmp_path / "model.json")], capture_output=True, text=True, check=True).stdout)
    assert out == {"landed": 3, "total": 4, "green": 3, "squares": 4}
