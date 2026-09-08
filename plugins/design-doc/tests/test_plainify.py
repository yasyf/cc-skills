import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "skills/design-doc/scripts/design.py"
spec = importlib.util.spec_from_file_location("design", SCRIPT)
design = importlib.util.module_from_spec(spec)
spec.loader.exec_module(design)


class PlainifyTests(unittest.TestCase):
    def ask(self):
        return design.ask_plain("codex", "Rewrite this.", "decisions", {"t": "One database"}, "One database per tenant (DQ2).",
                                {"DQ2": "Per-tenant isolation"})

    def astra_reply(self, cmd, **kwargs):
        self.assertEqual(cmd[cmd.index("--model") + 1], "gpt-6-astra")
        self.assertIn("model_reasoning_effort=xhigh", cmd)
        Path(cmd[cmd.index("--output-last-message") + 1]).write_text("Each tenant has its own database.")
        return subprocess.CompletedProcess(cmd, 0)

    def test_astra_success_does_not_call_claude(self):
        with patch.object(design.subprocess, "run", side_effect=self.astra_reply) as run:
            self.assertEqual(self.ask(), "Each tenant has its own database.")
        self.assertEqual(run.call_count, 1)

    def test_astra_failures_call_claude_once(self):
        errors = [FileNotFoundError("codex"), subprocess.TimeoutExpired("codex", 180),
                  subprocess.CalledProcessError(1, "codex", stderr="unavailable")]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                fallback = subprocess.CompletedProcess([], 0, stdout="One database per tenant.")
                with patch.object(design.subprocess, "run", side_effect=[error, fallback]) as run:
                    self.assertEqual(self.ask(), "One database per tenant.")
                self.assertEqual(run.call_count, 2)
                self.assertIn("claude", run.call_args.args[0])
                self.assertIn("claude-haiku-4-5", run.call_args.args[0])
                self.assertIn('"DQ2": "Per-tenant isolation"', run.call_args.args[0][-1])
                self.assertIn('"DQ2": "Per-tenant isolation"', run.call_args_list[0].args[0][-1])

    def test_empty_astra_reply_uses_claude(self):
        def reply(cmd, **kwargs):
            if cmd[0] == "codex":
                Path(cmd[cmd.index("--output-last-message") + 1]).write_text("  ")
                return subprocess.CompletedProcess(cmd, 0)
            return subprocess.CompletedProcess(cmd, 0, stdout="One database per tenant.")
        with patch.object(design.subprocess, "run", side_effect=reply) as run:
            self.assertEqual(self.ask(), "One database per tenant.")
        self.assertEqual(run.call_count, 2)

    def run_cli(self, root, *extra):
        with patch("sys.argv", [str(SCRIPT), "plainify", str(root), *extra]):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exit:
                design.main()
        return exit.exception.code

    def test_default_writes_astra_twin(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "registers.json").write_text(json.dumps({"tldr": [{"md": "One database per tenant."}]}))
            with patch.object(design.subprocess, "run", side_effect=self.astra_reply):
                self.assertEqual(self.run_cli(root), 0)
            self.assertEqual(json.loads((root / "registers.json").read_text())["tldr"][0]["p"],
                             "Each tenant has its own database.")

    def test_dry_run_generates_without_writing(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            original = json.dumps({"tldr": [{"md": "One database per tenant."}]})
            (root / "registers.json").write_text(original)
            with patch.object(design.subprocess, "run", side_effect=self.astra_reply) as run:
                self.assertEqual(self.run_cli(root, "--dry-run"), 0)
            self.assertEqual(run.call_count, 1)
            self.assertEqual((root / "registers.json").read_text(), original)

    def test_tldr_uses_shorter_budget_and_reports_overlong_reply(self):
        def reply(cmd, **kwargs):
            self.assertIn("At most 20 words", cmd[-1])
            self.assertNotIn("At most 30 words", cmd[-1])
            Path(cmd[cmd.index("--output-last-message") + 1]).write_text(" ".join(["word"] * 21))
            return subprocess.CompletedProcess(cmd, 0)
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "registers.json").write_text(json.dumps({"tldr": [{"md": "One database per tenant."}]}))
            args = design.argparse.Namespace(dir=str(root), only=None, provider="codex", dry_run=True)
            out = io.StringIO()
            with patch.object(design.subprocess, "run", side_effect=reply), contextlib.redirect_stdout(out):
                self.assertEqual(design.plainify(args), 0)
            self.assertIn("21 words; keep the tl;dr under 20", out.getvalue())

    def test_none_does_not_call_model_or_write(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            original = json.dumps({"tldr": [{"md": "One database per tenant."}]})
            (root / "registers.json").write_text(original)
            with patch.object(design.subprocess, "run", side_effect=AssertionError("model called")):
                self.assertEqual(self.run_cli(root, "--provider", "none"), 0)
            self.assertEqual((root / "registers.json").read_text(), original)

    def test_handle_failure_exits_without_writing_twins(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            original = json.dumps({"decisions": [{"id": "DQ1", "t": "One database", "r": "One database per tenant."}]})
            (root / "registers.json").write_text(original)
            def reply(cmd, **kwargs):
                if "Kind: decision" in cmd[-1] and "One database per tenant." in cmd[-1]:
                    return self.astra_reply(cmd, **kwargs)
                raise subprocess.CalledProcessError(1, cmd, stderr="unavailable")
            with patch.object(design.subprocess, "run", side_effect=reply):
                self.assertEqual(self.run_cli(root), 1)
            self.assertEqual((root / "registers.json").read_text(), original)


if __name__ == "__main__":
    unittest.main()
