import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "app"))

import artifacts


def code(text, **fields):
    return artifacts.page({"title": "The file", "kind": "code", "language": "go", "size": "3 lines", **fields}, text)


def transcript(*events):
    return artifacts.page({"title": "The session", "kind": "transcript", "size": "2 turns"}, "\n".join(json.dumps(event) for event in events) + "\n")


def turn(kind, *blocks, **fields):
    return {"kind": kind, "blocks": list(blocks), **fields}


def test_code_keeps_every_line_and_its_indentation():
    page = code("func main() {\n\tprintln()\n\n}\n")
    assert re.findall(r"<span>(.*?)</span>", page) == ["func main() {", "\tprintln()", "", "}"]
    assert "white-space: pre;" in page


def test_a_title_and_code_that_hold_markup_stay_text():
    page = code("<script>alert(1)</script>", title="<img src=x onerror=alert(1)>")
    assert "<script>" not in page
    assert "<img" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_a_transcript_keeps_its_turns_in_order_with_who_said_each():
    page = transcript(
        {"kind": "user", "text": "why did it stop?", "blocks": []},
        turn("assistant", {"type": "text", "text": "The load gate."}),
    )
    assert re.findall(r'<article class="turn (\w+)"', page) == ["user", "assistant"]
    assert page.index("why did it stop?") < page.index("The load gate.")
    assert "<h2>User" in page and "<h2>Claude" in page


def test_a_turn_time_reads_in_pacific():
    page = transcript(turn("assistant", {"type": "text", "text": "ok"}, meta={"timestamp": "2026-11-04T21:20:49+00:00"}))
    assert "<time>Nov 4 1:20:49 PM</time>" in page


def test_thinking_and_tool_calls_fold_and_keep_their_text():
    page = transcript(
        turn(
            "assistant",
            {"type": "thinking", "thinking": "read the log first"},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/a.go"}},
            {"type": "text", "text": "Done."},
        ),
        turn("user", {"type": "tool_result", "content": [{"type": "text", "text": "line one"}]}),
    )
    assert "<details><summary>Thinking</summary>" in page and "read the log first" in page
    assert "<details><summary>Read</summary>" in page and "file_path" in page
    assert "<details><summary>Result</summary>" in page and "line one" in page
    assert page.index("Done.") > page.index("read the log first")
    assert re.findall(r'<article class="turn (\w+)"', page) == ["assistant", "tool"]


def test_thinking_the_session_did_not_keep_draws_no_empty_fold():
    assert "Thinking" not in transcript(turn("assistant", {"type": "thinking", "thinking": ""}, {"type": "text", "text": "ok"}))


def test_what_is_not_a_turn_is_not_drawn():
    page = transcript(
        {"kind": "attachment", "text": "hook output"},
        {"kind": "system", "text": "compacted"},
        turn("assistant", {"type": "text", "text": "ok"}),
    )
    assert "hook output" not in page
    assert "compacted" not in page


def test_a_block_this_page_does_not_know_still_shows_its_content():
    assert "a new kind of thing" in transcript(turn("assistant", {"type": "future", "data": "a new kind of thing"}))
