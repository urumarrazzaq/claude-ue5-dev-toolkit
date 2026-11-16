"""Runs the repository self-check so a broken skill/agent/settings file fails the suite."""

from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_toolkit", REPO_ROOT / "scripts" / "check_toolkit.py")
ct = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(ct)


class CheckToolkitTests(unittest.TestCase):

    def test_repository_passes_self_check(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ct.main(["check_toolkit.py", str(REPO_ROOT)])
        self.assertEqual(code, 0, buf.getvalue())

    def test_frontmatter_parser(self):
        meta, body = ct.parse_frontmatter("---\nname: x\ntools: Read, Grep\nskills:\n  - a\n  - b\n---\nBody")
        self.assertEqual(meta, {"name": "x", "tools": "Read, Grep", "skills": ["a", "b"]})
        self.assertEqual(body.strip(), "Body")

    def test_frontmatter_errors(self):
        with self.assertRaises(ValueError):
            ct.parse_frontmatter("no frontmatter")
        with self.assertRaises(ValueError):
            ct.parse_frontmatter("---\nname: x\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
