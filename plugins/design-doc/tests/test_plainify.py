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
        return design.ask_plain("Rewrite this.", "decisions", {"t": "One database"}, "One database per tenant (DQ2).",
                                {"DQ2": "Per-tenant isolation"})

    def opus_reply(self, cmd, **kwargs):
        self.assertEqual(cmd[:6], ["env", "-u", "CLAUDECODE", "claude", "-p", "--model"])
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-opus-5-5")
        return subprocess.CompletedProcess(cmd, 0, stdout="Each tenant has its own database.")

    def test_opus_writes_the_twin_in_one_call(self):
        with patch.object(design.subprocess, "run", side_effect=self.opus_reply) as run:
            self.assertEqual(self.ask(), "Each tenant has its own database.")
        self.assertEqual(run.call_count, 1)
        self.assertIn('"DQ2": "Per-tenant isolation"', run.call_args.args[0][-1])

    def test_opus_failures_propagate_without_a_fallback(self):
        errors = [FileNotFoundError("claude"), subprocess.TimeoutExpired("claude", 180),
                  subprocess.CalledProcessError(1, "claude", stderr="unavailable")]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                with patch.object(design.subprocess, "run", side_effect=error) as run, self.assertRaises(type(error)):
                    self.ask()
                self.assertEqual(run.call_count, 1)

    def test_empty_opus_reply_raises(self):
        with patch.object(design.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="  ")):
            with self.assertRaises(ValueError):
                self.ask()

    def run_cli(self, root, *extra):
        with patch("sys.argv", [str(SCRIPT), "plainify", str(root), *extra]):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exit:
                design.main()
        return exit.exception.code

    def test_default_writes_opus_twin(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "registers.json").write_text(json.dumps({"tldr": [{"md": "One database per tenant."}]}))
            with patch.object(design.subprocess, "run", side_effect=self.opus_reply):
                self.assertEqual(self.run_cli(root), 0)
            self.assertEqual(json.loads((root / "registers.json").read_text())["tldr"][0]["p"],
                             "Each tenant has its own database.")

    def test_dry_run_generates_without_writing(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            original = json.dumps({"tldr": [{"md": "One database per tenant."}]})
            (root / "registers.json").write_text(original)
            with patch.object(design.subprocess, "run", side_effect=self.opus_reply) as run:
                self.assertEqual(self.run_cli(root, "--dry-run"), 0)
            self.assertEqual(run.call_count, 1)
            self.assertEqual((root / "registers.json").read_text(), original)

    def test_tldr_uses_shorter_budget_and_reports_overlong_reply(self):
        def reply(cmd, **kwargs):
            self.assertIn("At most 20 words", cmd[-1])
            self.assertNotIn("At most 30 words", cmd[-1])
            return subprocess.CompletedProcess(cmd, 0, stdout=" ".join(["word"] * 21))
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "registers.json").write_text(json.dumps({"tldr": [{"md": "One database per tenant."}]}))
            args = design.argparse.Namespace(dir=str(root), only=None, provider="claude", dry_run=True)
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
                    return self.opus_reply(cmd, **kwargs)
                raise subprocess.CalledProcessError(1, cmd, stderr="unavailable")
            with patch.object(design.subprocess, "run", side_effect=reply):
                self.assertEqual(self.run_cli(root), 1)
            self.assertEqual((root / "registers.json").read_text(), original)


if __name__ == "__main__":
    unittest.main()
