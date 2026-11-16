"""Tests for scripts/validate_project.py using synthetic projects in a temp dir."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "validate_project.py"

_spec = importlib.util.spec_from_file_location("validate_project", SCRIPT)
vp = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["validate_project"] = vp
_spec.loader.exec_module(vp)


def write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_project(root: Path, *, with_bom: bool = False) -> None:
    uproject = {
        "FileVersion": 3,
        "EngineAssociation": "5.4",
        "Modules": [{"Name": "ShooterGame", "Type": "Runtime", "LoadingPhase": "Default"}],
        "Plugins": [{"Name": "EnhancedInput", "Enabled": True},
                    {"Name": "InventoryKit", "Enabled": True},
                    {"Name": "OnlineSubsystemSteam", "Enabled": False}],
    }
    text = json.dumps(uproject, indent="\t")
    (root / "ShooterGame.uproject").write_text(("﻿" if with_bom else "") + text, encoding="utf-8")
    write(root / "Source/ShooterGame.Target.cs", "// target")
    write(root / "Source/ShooterGameEditor.Target.cs", "// target")
    write(root / "Source/ShooterGame/ShooterGame.Build.cs", "// rules")
    write(root / "Source/ShooterGame/Private/ShooterGame.cpp", "// cpp")
    write(root / "Content/Maps/L_Main.umap")
    write(root / "Content/Characters/BP_Hero.uasset")
    write(root / "Config/DefaultEngine.ini",
          "[/Script/EngineSettings.GameMapsSettings]\nGameDefaultMap=/Game/Maps/L_Main.L_Main\n"
          "+ActiveGameNameRedirects=(OldGameName=\"A\",NewGameName=\"B\")\n")
    write(root / "Config/DefaultGame.ini", "[/Script/EngineSettings.GeneralProjectSettings]\n")
    write(root / "Plugins/InventoryKit/InventoryKit.uplugin",
          json.dumps({"FileVersion": 3, "Modules": [{"Name": "InventoryKit", "Type": "Runtime"}]}))
    write(root / "Plugins/InventoryKit/Source/InventoryKit/InventoryKit.Build.cs", "// rules")
    write(root / ".gitignore", "Binaries/\nIntermediate/\nSaved/\nDerivedDataCache/\n")


class ValidateProjectTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def snapshot(self) -> dict:
        return {str(p): p.stat().st_mtime_ns for p in self.root.rglob("*")}

    def test_healthy_project_has_no_errors(self):
        make_project(self.root)
        report = vp.validate(self.root)
        self.assertEqual(report.error_count, 0, [i.message for i in report.issues])
        self.assertEqual(report.engine_association, "5.4")
        self.assertEqual(report.source_modules, ["ShooterGame"])
        self.assertEqual(report.game_default_map, "/Game/Maps/L_Main.L_Main")
        self.assertEqual(report.asset_counts, {"uasset": 1, "umap": 1})
        sources = {p.name: p.source for p in report.plugins}
        self.assertEqual(sources["InventoryKit"], "project")
        self.assertEqual(sources["EnhancedInput"], "engine/marketplace (not verified)")
        self.assertTrue(all(report.directories[d] for d in ("Source", "Content", "Config", "Plugins")))

    def test_bom_encoded_uproject(self):
        make_project(self.root, with_bom=True)
        self.assertEqual(vp.validate(self.root).error_count, 0)

    def test_uproject_path_argument(self):
        make_project(self.root)
        report = vp.validate(self.root / "ShooterGame.uproject")
        self.assertEqual(report.project_name, "ShooterGame")

    def test_missing_module_source_and_targets(self):
        make_project(self.root)
        (self.root / "Source/ShooterGame/ShooterGame.Build.cs").unlink()
        for t in self.root.glob("Source/*.Target.cs"):
            t.unlink()
        write(self.root / "Source/Extra/Extra.Build.cs")
        messages = [i.message for i in vp.validate(self.root).issues if i.severity == vp.ERROR]
        self.assertTrue(any("ShooterGame" in m and "not found" in m for m in messages), messages)
        self.assertTrue(any("Target.cs" in m for m in messages), messages)

    def test_invalid_uproject_json(self):
        write(self.root / "Broken.uproject", "{ not json")
        report = vp.validate(self.root)
        self.assertTrue(any("not valid JSON" in i.message for i in report.issues))

    def test_no_uproject(self):
        report = vp.validate(self.root)
        self.assertIsNone(report.uproject)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = vp.main([str(self.root)])
        self.assertEqual(code, 2)
        self.assertIn("No .uproject", buf.getvalue())

    def test_gitignore_missing_generated_dirs(self):
        make_project(self.root)
        write(self.root / ".gitignore", "Binaries/\n")
        warnings = [i.message for i in vp.validate(self.root).issues if i.severity == vp.WARNING]
        self.assertTrue(any("Intermediate" in w for w in warnings), warnings)

    def test_plugin_module_without_build_cs(self):
        make_project(self.root)
        (self.root / "Plugins/InventoryKit/Source/InventoryKit/InventoryKit.Build.cs").unlink()
        errors = [i.message for i in vp.validate(self.root).issues if i.severity == vp.ERROR]
        self.assertTrue(any("InventoryKit" in e for e in errors), errors)

    def test_json_output_and_read_only(self):
        make_project(self.root)
        before = self.snapshot()
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = vp.main([str(self.root), "--json"])
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["project_name"], "ShooterGame")
        self.assertEqual(data["error_count"], 0)
        self.assertEqual(before, self.snapshot(), "validator modified the project")

    def test_text_report_renders(self):
        make_project(self.root)
        text = vp.format_report(vp.validate(self.root))
        self.assertIn("Unreal Engine Project Validation Report", text)
        self.assertIn("InventoryKit", text)
        self.assertIn("Result: 0 error(s)", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
