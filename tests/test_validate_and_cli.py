import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agora.cli import main
from agora.registry import CatalogError, load_catalog

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "agents.example.toml"
VALIDATE = ROOT / "scripts" / "validate.py"


class ValidateScriptTests(unittest.TestCase):
    def test_example_catalog_is_valid(self):
        out = subprocess.run([sys.executable, str(VALIDATE), str(EXAMPLE)], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("ok (", out.stdout)
        cat = load_catalog(EXAMPLE)
        for required in ("claude-code", "codex", "gemini-cli", "freellmapi"):
            self.assertIn(required, cat)

    def test_bad_catalog_fails(self):
        tmp = Path(tempfile.mkdtemp()) / "bad.toml"
        tmp.write_text('[[agents]]\nid = "x"\nprovider = "p"\nargv = ["x"]\n'
                       '[agents.capabilities]\nplan = 7\n', encoding="utf-8")
        out = subprocess.run([sys.executable, str(VALIDATE), str(tmp)], capture_output=True, text=True)
        self.assertEqual(out.returncode, 1)
        self.assertIn("{prompt}", out.stdout)
        self.assertIn("[0, 1]", out.stdout)
        with self.assertRaises(CatalogError):
            load_catalog(tmp)

    def test_duplicate_ids_rejected(self):
        tmp = Path(tempfile.mkdtemp()) / "dup.toml"
        tmp.write_text('[[agents]]\nid = "x"\nprovider = "p"\nargv = ["x", "{prompt}"]\n'
                       '[[agents]]\nid = "x"\nprovider = "p"\nargv = ["x", "{prompt}"]\n', encoding="utf-8")
        with self.assertRaisesRegex(CatalogError, "duplicate"):
            load_catalog(tmp)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ledger = str(self.tmp / "ledger.sqlite")

    def _run(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            # CI runners have none of the CLIs installed; dry runs may still plan with the example catalog
            code = main([*args, "--assume-installed"])
        return code, buf.getvalue()

    def test_dry_run_prints_allocation_and_spends_nothing(self):
        code, out = self._run("run", "task", "--catalog", str(EXAMPLE), "--ledger", self.ledger,
                              "--dry-run", "--seed", "1", "--strategy", "hybrid")
        self.assertEqual(code, 0, out)
        self.assertIn("no quota spent", out)
        for role in ("role=plan", "role=review", "role=judge", "role=build", "role=inspect"):
            self.assertIn(role, out)
        code, out = self._run("quota", "--catalog", str(EXAMPLE), "--ledger", self.ledger, "--json")
        self.assertEqual(code, 0)
        self.assertNotIn('"requests": 1', out)
        code, out = self._run("quota", "--catalog", str(EXAMPLE), "--ledger", self.ledger, "--forget", "codex")
        self.assertEqual(code, 0, out)
        self.assertIn("forgot learned caps and cooldown for codex", out)
        code, _ = self._run("quota", "--catalog", str(EXAMPLE), "--ledger", self.ledger, "--forget", "nope")
        self.assertEqual(code, 2)

    def test_roles_and_agents_list(self):
        code, out = self._run("roles", "--catalog", str(EXAMPLE), "--ledger", self.ledger, "--seed", "2",
                              "--strategy", "adversarial")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("role="), 5)
        code, out = self._run("roles", "--catalog", str(EXAMPLE), "--ledger", self.ledger, "--seed", "2",
                              "--strategy", "adversarial", "--judge", "proof-cmd")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("role="), 4)
        self.assertNotIn("role=judge", out)
        code, out = self._run("agents", "list", "--catalog", str(EXAMPLE))
        self.assertEqual(code, 0)
        self.assertIn("freellmapi", out)

    def test_cheap_allocation_prefers_free_tiers(self):
        code, out = self._run("run", "task", "--catalog", str(EXAMPLE), "--ledger", self.ledger,
                              "--dry-run", "--seed", "3", "--allocation", "auto:cheap")
        self.assertEqual(code, 0, out)
        self.assertIn("auto:cheap bandit", out)


if __name__ == "__main__":
    unittest.main()