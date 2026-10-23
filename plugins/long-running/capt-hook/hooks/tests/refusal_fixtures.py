from __future__ import annotations

from pathlib import Path

BODIES = Path(__file__).parent / "fixtures" / "refusal-kinds"
CITED = BODIES / "cited.md"
UNCITED = BODIES / "uncited.md"
UNNAMED = BODIES / "unnamed.md"
NO_DRIVE = {"CLAUDE_LONG_RUNNING_LANE": "p1-hooks", "CLAUDE_CONFIG_DIR": str(BODIES)}
ANSWER = '{"id": "317cfd653e7c28400242f659a6d609a68bb4a934", "title": "Approve the plan?", "body": "Approved"}'
NAMED_ANSWER = (
    '{"id": "317cfd653e7c28400242f659a6d609a68bb4a934", "title": "Page a stalled launch?",'
    ' "body": "Yes: add LAUNCH-STALLED with a STALL_SECONDS clock."}'
)
SENTINEL_ANSWER = '{"id": "317cfd653e7c28400242f659a6d609a68bb4a934", "title": "Open it read-only?", "body": "Yes, return errReadOnlyView."}'

LAUNCH_STALLED = """\
diff --git a/plugins/long-running/skills/long-running/scripts/desk-runner.py b/plugins/long-running/skills/long-running/scripts/desk-runner.py
--- a/plugins/long-running/skills/long-running/scripts/desk-runner.py
+++ b/plugins/long-running/skills/long-running/scripts/desk-runner.py
@@ -368,0 +369 @@ ESCALATION_KINDS = {
+    "LAUNCH-STALLED": "blocker",
@@ -1744,0 +1746,2 @@ def accept_launch(self, action):
+STALL_SECONDS = 60
+
"""

UPLOAD_BUDGET = """\
diff --git a/service/upload/upload.go b/service/upload/upload.go
--- a/service/upload/upload.go
+++ b/service/upload/upload.go
@@ -40,0 +41,5 @@ func (u *Uploader) Upload(ctx context.Context) error {
+const uploadBudget = 15 * time.Second
+
+func (u *Uploader) waitForProgress(ctx context.Context) error {
+	ticker := time.NewTicker(time.Second)
+	defer ticker.Stop()
"""

RETIRE_AND_RECONVERT = """\
diff --git a/service/store/open.go b/service/store/open.go
--- a/service/store/open.go
+++ b/service/store/open.go
@@ -120,2 +120,9 @@ func openWriter(ctx context.Context, store *Store) (*Writer, error) {
+		done, err := store.Converted(ctx)
+		if err != nil {
+			return nil, err
+		}
+		if !done {
+			stats.Incr("store.open", []string{"cause:unfinished_conversion"}, 1)
+			log.Warn("store holds an unfinished conversion; opened read-only instead of reconverted", "stage", "reconvert")
+			return nil, errReadOnly
+		}
"""

TEST_ONLY = """\
diff --git a/plugins/long-running/tests/test_desk_runner.py b/plugins/long-running/tests/test_desk_runner.py
--- a/plugins/long-running/tests/test_desk_runner.py
+++ b/plugins/long-running/tests/test_desk_runner.py
@@ -900,0 +901,3 @@ def test_unbound_runner_fails_each_launch(shell):
+def test_a_stalled_launch_pages_once(shell):
+    assert shell.escalations == ["LAUNCH-STALLED"]
+    time.sleep(0)
"""

PLAIN = """\
diff --git a/plugins/long-running/skills/long-running/scripts/drive.py b/plugins/long-running/skills/long-running/scripts/drive.py
--- a/plugins/long-running/skills/long-running/scripts/drive.py
+++ b/plugins/long-running/skills/long-running/scripts/drive.py
@@ -543,0 +544,3 @@ def lane_status(args):
+def digest(args):
+    for desk in desks(args.drive):
+        print(desk.name, desk.version)
"""

LOGGED_STATE = """\
diff --git a/plugins/long-running/skills/long-running/scripts/drive.py b/plugins/long-running/skills/long-running/scripts/drive.py
--- a/plugins/long-running/skills/long-running/scripts/drive.py
+++ b/plugins/long-running/skills/long-running/scripts/drive.py
@@ -543,0 +544,2 @@ def lane_status(args):
+def upload(args):
+    log.info("UPLOAD-STARTED")
"""

FAILS_OPEN = """\
diff --git a/plugins/long-running/capt-hook/hooks/register_gate.py b/plugins/long-running/capt-hook/hooks/register_gate.py
--- a/plugins/long-running/capt-hook/hooks/register_gate.py
+++ b/plugins/long-running/capt-hook/hooks/register_gate.py
@@ -80,0 +81,2 @@ def register_gate(evt):
+    except OSError as error:
+        return failed_open(evt, "register_gate", error)
"""

SENTINEL_ONLY = """\
diff --git a/service/store/view.go b/service/store/view.go
--- a/service/store/view.go
+++ b/service/store/view.go
@@ -10,0 +11 @@ func openView(ctx context.Context) (*View, error) {
+	return nil, errReadOnly
"""

SENTINEL_BODY = """ccx vcs ship -m 'store: open views read-only' --pr-body-file - <<'EOF'
## Rulings

- 317cfd6: the owner's answer orders `errReadOnlyView`.
EOF"""

DIGIT_ID_BODY = """ccx vcs ship -m 'long-running: page a stalled launch' --pr-body-file - <<'EOF'
## Rulings

- 1068167: the owner's answer adds `LAUNCH-STALLED` with a `STALL_SECONDS` clock.
EOF"""

HEREDOC_BODY = """ccx vcs ship -m 'long-running: page a stalled launch' --pr-body-file - <<'EOF'
## Rulings

- 317cfd6, the owner's approval of the plan; plan 1.1 names `LAUNCH-STALLED` and `STALL_SECONDS`.
EOF"""


def sources(diff: str, *, answer: str | tuple[int, str] = ANSWER, pr: str | tuple[int, str] = "") -> dict[str, str | tuple[int, str]]:
    return {
        "git symbolic-ref": "origin/main\n",
        "git diff": diff,
        "git ls-files": "",
        "gh pr view": pr,
        "gh pr diff": diff,
        "ccn": answer,
    }
