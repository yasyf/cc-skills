from __future__ import annotations

from pathlib import Path

BODIES = Path(__file__).parent / "fixtures" / "refusal-kinds"
CITED = BODIES / "cited.md"
UNCITED = BODIES / "uncited.md"
TREE = BODIES / "tree"
HELPER_TRANSCRIPT = BODIES.parent / "launched-460k.jsonl"
NO_DRIVE = {"CLAUDE_LONG_RUNNING_LANE": "p1-hooks", "CLAUDE_CONFIG_DIR": str(BODIES)}

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

HOOK_CAP = """\
diff --git a/plugins/long-running/capt-hook/hooks/lane_reports.py b/plugins/long-running/capt-hook/hooks/lane_reports.py
--- a/plugins/long-running/capt-hook/hooks/lane_reports.py
+++ b/plugins/long-running/capt-hook/hooks/lane_reports.py
@@ -30,0 +31 @@ def cap_lane_final_text(evt):
+    max_fires=1,
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

NEW_SENTINEL = """\
diff --git a/service/store/view.go b/service/store/view.go
--- a/service/store/view.go
+++ b/service/store/view.go
@@ -3,0 +4 @@ import (
+var errReadOnly = errors.New("store: the view is read-only")
"""

DURATION_FIELDS = """\
diff --git a/service/blob/rules.go b/service/blob/rules.go
--- a/service/blob/rules.go
+++ b/service/blob/rules.go
@@ -10,0 +11,4 @@ import (
+type FaultRule struct {
+	Delay time.Duration
+	Took  time.Duration `json:"took"`
+}
"""

HELPER_PACKAGE_CLOCK = """\
diff --git a/service/blob/blobtest/faults.go b/service/blob/blobtest/faults.go
--- a/service/blob/blobtest/faults.go
+++ b/service/blob/blobtest/faults.go
@@ -20,0 +21,3 @@ func (b *Bucket) apply(rule FaultRule) {
+	select {
+	case <-time.After(rule.Delay):
+	}
"""

RELISTED = """\
diff --git a/plugins/long-running/skills/long-running/scripts/desk-runner.py b/plugins/long-running/skills/long-running/scripts/desk-runner.py
--- a/plugins/long-running/skills/long-running/scripts/desk-runner.py
+++ b/plugins/long-running/skills/long-running/scripts/desk-runner.py
@@ -368 +368 @@ ESCALATION_KINDS = {
-ESCALATION_KINDS = {"UNBOUND": "blocker", "LAUNCH-STALLED": "blocker"}
+ESCALATION_KINDS = {"UNBOUND": "blocker", "LAUNCH-STALLED": "blocker", "LAUNCH-DUPLICATE": "blocker"}
"""

CARD_TERMS = """\
diff --git a/hooks/card_terms.py b/hooks/card_terms.py
--- a/hooks/card_terms.py
+++ b/hooks/card_terms.py
@@ -12,0 +13,2 @@ def card(verdict: dict, **fields: object) -> Input:
+        card({"terms": ["FINDER-PENDING", "rule 23"]}, title="Send the list to the team?", options=["Send", "Hold"]): Allow(),
+        Input(command="cc-slack dm-status --title 'Merge the fix?'"): Block(pattern=r'"FINDER-PENDING" defined'),
"""

CARD_TERMS_KIND = """\
diff --git a/hooks/card_terms.py b/hooks/card_terms.py
--- a/hooks/card_terms.py
+++ b/hooks/card_terms.py
@@ -2,0 +3 @@ from captain_hook import Allow, Block, Event, Input, on
+ESCALATIONS = {"CARD-STALE": "blocker"}
@@ -11,0 +13,2 @@ def card(verdict: dict, **fields: object) -> Input:
+        card({"terms": ["FINDER-PENDING", "rule 23"]}, title="Send the list to the team?", options=["Send", "Hold"]): Allow(),
+        Input(command="cc-slack dm-status --title 'Merge the fix?'"): Block(pattern=r'"FINDER-PENDING" defined'),
"""

HEREDOC_BODY = """ccx vcs ship -m 'long-running: page a stalled launch' --pr-body-file - <<'EOF'
## Rulings

- 317cfd6, the owner's approval of the plan; plan 1.1 names `LAUNCH-STALLED` and `STALL_SECONDS`.
EOF"""


ON_BASE = """\
diff --git a/plugins/long-running/skills/long-running/scripts/desk-runner.py b/plugins/long-running/skills/long-running/scripts/desk-runner.py
--- a/plugins/long-running/skills/long-running/scripts/desk-runner.py
+++ b/plugins/long-running/skills/long-running/scripts/desk-runner.py
@@ -4901,0 +4902 @@ class Landing:
+        \"\"\"Hold a PR whose `MERGE-ASK`, `RULING-ASK` lines the owner has not tapped.\"\"\"
"""

E2E_AND_HARNESS = """\
diff --git a/dashboard/e2e/irisServe.e2e.ts b/dashboard/e2e/irisServe.e2e.ts
--- a/dashboard/e2e/irisServe.e2e.ts
+++ b/dashboard/e2e/irisServe.e2e.ts
@@ -200,0 +201,2 @@ test("serve", async () => {
+  const refused = new Set<string>();
+  await new Promise((resolve) => setTimeout(resolve, 5000));
diff --git a/go/sand/internal/macharness/run.go b/go/sand/internal/macharness/run.go
--- a/go/sand/internal/macharness/run.go
+++ b/go/sand/internal/macharness/run.go
@@ -10,0 +11 @@ package macharness
+var errStalled = errors.New("stalled")
"""

REFUSED_IDENTIFIERS = """\
diff --git a/dashboard/src/iris/chat/send.ts b/dashboard/src/iris/chat/send.ts
--- a/dashboard/src/iris/chat/send.ts
+++ b/dashboard/src/iris/chat/send.ts
@@ -40,0 +41,2 @@ export function send() {
+  const refusedChat = response.status === 409;
+  let refuseNext = false;
"""

RUST_TESTS_RS = """\
diff --git a/rust/crates/sandsql-api/src/admin/tests.rs b/rust/crates/sandsql-api/src/admin/tests.rs
--- a/rust/crates/sandsql-api/src/admin/tests.rs
+++ b/rust/crates/sandsql-api/src/admin/tests.rs
@@ -10168,0 +10169,2 @@ mod admin {
+    let max_attempts = 3;
+    assert_eq!(retries, max_attempts);
"""

DESK_RUNNER_ON_BASE = 'ESCALATION_KINDS = {"MERGE-ASK": "report", "RULING-ASK": "report"}\n'


def sources(diff: str, *, pr: str | tuple[int, str] = "", base: str = "") -> dict[str, str | tuple[int, str]]:
    return {
        "git symbolic-ref": "origin/main\n",
        "git rev-parse": f"{TREE}\n",
        "git diff": diff,
        "git show": base,
        "git ls-files": "",
        "gh pr view": pr,
        "gh pr diff": diff,
    }
