from drive import diff

PATCH = """@@ -10,3 +10,4 @@ func Open() {
 \ta := 1
-\tb := 2
+\tb := 3
+\tc := 4
 \treturn
\\ No newline at end of file
@@ -40,2 +41,2 @@
-old
+new
"""


def test_a_patch_parses_into_hunks_numbering_each_line_on_the_side_it_lives_on() -> None:
    first, second = diff.parse(PATCH)
    assert first["section"] == "func Open() {"
    assert [(line["kind"], line["old"], line["new"], line["text"]) for line in first["lines"]] == [
        ("ctx", 10, 10, "\ta := 1"),
        ("del", 11, None, "\tb := 2"),
        ("add", None, 11, "\tb := 3"),
        ("add", None, 12, "\tc := 4"),
        ("ctx", 12, 13, "\treturn"),
    ]
    assert [(line["kind"], line["old"], line["new"]) for line in second["lines"]] == [("del", 40, None), ("add", None, 41)]


def test_a_review_comment_tells_a_bot_and_his_own_apart() -> None:
    bot = diff.comment({"id": 1, "path": "a.go", "line": 11, "side": "RIGHT", "user": {"login": "reviewer[bot]", "type": "Bot"}, "body": "nil check", "html_url": "u", "in_reply_to_id": None}, "owner")
    his = diff.comment({"id": 2, "path": "a.go", "line": None, "user": {"login": "owner", "type": "User"}, "body": "no", "in_reply_to_id": 1}, "owner")
    assert (bot["bot"], bot["his"], bot["line"], bot["reply_to"]) == (True, False, 11, None)
    assert (his["bot"], his["his"], his["side"], his["reply_to"]) == (False, True, "RIGHT", 1)
