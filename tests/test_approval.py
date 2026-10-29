import tempfile
import unittest
from pathlib import Path

from agent_commons.approval import (bind_approval, check_approval, current_commit, inspection_fingerprint,
                                    plan_sha256)

from .helpers import git, make_repo


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.plan = self.tmp / "PLAN.md"
        self.plan.write_text("# plan\nstep 1\n", encoding="utf-8")

    def test_bound_to_bytes(self):
        a = bind_approval(self.plan, "beta", 1, "/art")
        self.assertEqual(a.plan_sha256, plan_sha256(self.plan))
        self.assertTrue(check_approval(self.plan, a))
        self.plan.write_text("# plan\nstep 1!\n", encoding="utf-8")  # one byte
        self.assertFalse(check_approval(self.plan, a))

    def test_bound_to_path(self):
        a = bind_approval(self.plan, "beta", 1, "/art")
        other = self.tmp / "OTHER.md"
        other.write_bytes(self.plan.read_bytes())
        self.assertFalse(check_approval(other, a))
        self.assertFalse(check_approval(self.plan, None))


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = make_repo(self.tmp)

    def test_untracked_file_changes_fingerprint(self):
        f0 = inspection_fingerprint(self.repo)
        (self.repo / "new.py").write_text("x = 1\n", encoding="utf-8")
        f1 = inspection_fingerprint(self.repo)
        self.assertNotEqual(f0, f1)
        (self.repo / "new.py").write_text("x = 2\n", encoding="utf-8")
        self.assertNotEqual(f1, inspection_fingerprint(self.repo))

    def test_tracked_edit_changes_fingerprint(self):
        f0 = inspection_fingerprint(self.repo)
        (self.repo / "README.md").write_text("toy changed\n", encoding="utf-8")
        self.assertNotEqual(f0, inspection_fingerprint(self.repo))
        git(self.repo, "add", ".")
        self.assertNotEqual(f0, inspection_fingerprint(self.repo))  # staged still differs from clean

    def test_commit_and_non_git_fallback(self):
        self.assertEqual(len(current_commit(self.repo)), 40)
        plain = self.tmp / "plain"
        plain.mkdir()
        (plain / "a.txt").write_text("a", encoding="utf-8")
        self.assertIsNone(current_commit(plain))
        f0 = inspection_fingerprint(plain)
        (plain / "a.txt").write_text("b", encoding="utf-8")
        self.assertNotEqual(f0, inspection_fingerprint(plain))


if __name__ == "__main__":
    unittest.main()