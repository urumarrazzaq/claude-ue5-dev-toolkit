"""Unit tests for .claude/hooks/ue5_safety_guard.py.

Run from the repository root with either:

    python -m unittest discover -s tests -v
    python -m pytest tests          # if pytest is installed

Only the standard library is required.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / ".claude" / "hooks" / "ue5_safety_guard.py"

_spec = importlib.util.spec_from_file_location("ue5_safety_guard", HOOK_PATH)
guard = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["ue5_safety_guard"] = guard  # dataclasses need the module registered
_spec.loader.exec_module(guard)

PROJECT_CWD = "D:/Projects/ShooterGame"


def payload(command: str, tool: str = "Bash", cwd: str = PROJECT_CWD) -> dict:
    """Build a PreToolUse payload shaped like the one Claude Code sends."""
    return {
        "session_id": "test-session",
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": cwd,
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"command": command, "description": "test"},
        "tool_use_id": "toolu_test",
    }


def decision_for(command: str, tool: str = "Bash", cwd: str = PROJECT_CWD):
    """Return 'deny', 'ask' or None for a command."""
    response = guard.decide(payload(command, tool, cwd))
    if response is None:
        return None
    return response["hookSpecificOutput"]["permissionDecision"]


class AllowedCommands(unittest.TestCase):
    """Commands that must produce no decision (normal permissions apply)."""

    def assertNoDecision(self, command: str, tool: str = "Bash") -> None:
        self.assertIsNone(decision_for(command, tool), f"unexpected decision for: {command}")

    def test_git_status(self):
        self.assertNoDecision("git status")

    def test_git_log(self):
        self.assertNoDecision("git log --oneline -20")

    def test_git_diff_and_show(self):
        self.assertNoDecision("git diff HEAD~1 -- Source/")
        self.assertNoDecision("git show HEAD --stat")

    def test_git_soft_reset_and_unstage(self):
        self.assertNoDecision("git reset --soft HEAD~1")
        self.assertNoDecision("git reset HEAD Source/MyActor.cpp")
        self.assertNoDecision("git restore --staged Source/MyActor.cpp")

    def test_git_clean_dry_run(self):
        self.assertNoDecision("git clean -nd")
        self.assertNoDecision("git clean -fdn")

    def test_git_checkout_branch(self):
        self.assertNoDecision("git checkout -b feature/inventory")
        self.assertNoDecision("git switch main")

    def test_normal_push(self):
        self.assertNoDecision("git push origin feature/inventory")

    def test_harmless_python(self):
        self.assertNoDecision("python scripts/validate_project.py D:/Projects/ShooterGame")
        self.assertNoDecision("python -c \"print('hello')\"")
        self.assertNoDecision("py -3 -m unittest discover -s tests")

    def test_regenerable_ue_folders_are_not_blocked(self):
        # Binaries/Intermediate/DerivedDataCache are rebuilt by UBT and the editor.
        self.assertNoDecision("rm -rf Binaries Intermediate DerivedDataCache")
        self.assertNoDecision("rm -rf Plugins/MyPlugin/Intermediate")
        self.assertNoDecision("Remove-Item -Recurse -Force .\\Intermediate", tool="PowerShell")

    def test_quoted_text_is_not_executed(self):
        self.assertNoDecision('git commit -m "Docs: never run git reset --hard; use stash"')
        self.assertNoDecision("echo 'rm -rf /'")

    def test_non_recursive_rm_of_ordinary_file(self):
        self.assertNoDecision("rm Saved/Logs/ShooterGame.log")

    def test_build_command(self):
        self.assertNoDecision(
            '"C:/Program Files/Epic Games/UE_5.4/Engine/Build/BatchFiles/Build.bat" '
            'ShooterGameEditor Win64 Development -Project="D:/Projects/ShooterGame/ShooterGame.uproject"')

    def test_other_tools_are_ignored(self):
        self.assertIsNone(guard.decide({"tool_name": "Read", "tool_input": {"file_path": "x"}}))

    def test_missing_command_is_ignored(self):
        self.assertIsNone(guard.decide({"tool_name": "Bash", "tool_input": {}}))
        self.assertIsNone(guard.decide({"tool_name": "Bash", "tool_input": "oops"}))


class BlockedGitCommands(unittest.TestCase):

    def test_reset_hard(self):
        self.assertEqual(decision_for("git reset --hard"), "deny")
        self.assertEqual(decision_for("git reset --hard origin/main"), "deny")

    def test_clean_force(self):
        self.assertEqual(decision_for("git clean -fd"), "deny")
        self.assertEqual(decision_for("git clean -fdx"), "deny")
        self.assertEqual(decision_for("git clean --force -d"), "deny")

    def test_force_push(self):
        self.assertEqual(decision_for("git push --force origin main"), "deny")
        self.assertEqual(decision_for("git push -f"), "deny")
        self.assertEqual(decision_for("git push origin +main"), "deny")

    def test_force_with_lease_asks(self):
        self.assertEqual(decision_for("git push --force-with-lease origin feature"), "ask")

    def test_discard_whole_tree(self):
        self.assertEqual(decision_for("git checkout -- ."), "deny")
        self.assertEqual(decision_for("git restore ."), "deny")
        self.assertEqual(decision_for("git checkout -f main"), "deny")

    def test_discard_single_file_asks(self):
        self.assertEqual(decision_for("git restore Source/MyActor.cpp"), "ask")
        self.assertEqual(decision_for("git checkout -- Config/DefaultGame.ini"), "ask")

    def test_history_rewrites(self):
        self.assertEqual(decision_for("git filter-branch --tree-filter 'rm x' HEAD"), "deny")
        self.assertEqual(decision_for("git reflog expire --expire=now --all"), "deny")
        self.assertEqual(decision_for("git gc --prune=now"), "deny")
        self.assertEqual(decision_for("git stash clear"), "deny")

    def test_branch_force_delete_asks(self):
        self.assertEqual(decision_for("git branch -D old-feature"), "ask")

    def test_git_global_options_are_skipped(self):
        self.assertEqual(decision_for("git -C D:/Projects/ShooterGame reset --hard"), "deny")

    def test_chained_with_harmless_command(self):
        self.assertEqual(decision_for("git status && git reset --hard HEAD"), "deny")
        self.assertEqual(decision_for("git fetch; git clean -fd"), "deny")


class BlockedDeletion(unittest.TestCase):

    def test_rm_rf_root_and_home(self):
        for cmd in ("rm -rf /", "rm -rf /*", "rm -rf ~", "rm -rf $HOME", "sudo rm -rf /",
                    "rm -rf --no-preserve-root /"):
            self.assertEqual(decision_for(cmd), "deny", cmd)

    def test_wrapper_options_with_values(self):
        self.assertEqual(decision_for("sudo -u root rm -rf /"), "deny")
        self.assertEqual(decision_for("env -u FOO rm -rf Content"), "deny")
        self.assertEqual(decision_for("nice -n 10 git clean -fdx"), "deny")

    def test_rm_rf_current_or_parent_dir(self):
        for cmd in ("rm -rf .", "rm -rf ./", "rm -rf *", "rm -rf ..", "rm -r -f ./*"):
            self.assertEqual(decision_for(cmd), "deny", cmd)

    def test_rm_rf_protected_ue_folders(self):
        for cmd in ("rm -rf Content", "rm -rf Source/", "rm -rf ./Config",
                    "rm -rf Plugins", "rm -rf .git", "rm -rf Content/*",
                    "rm -Rf Plugins/MyPlugin/Source"):
            self.assertEqual(decision_for(cmd), "deny", cmd)

    def test_rm_rf_project_by_absolute_path(self):
        self.assertEqual(decision_for("rm -rf D:/Projects/ShooterGame"), "deny")
        self.assertEqual(decision_for("rm -rf /d/Projects/ShooterGame"), "deny")
        self.assertEqual(decision_for("rm -rf D:/Projects"), "deny")
        self.assertEqual(decision_for("rm -rf C:/"), "deny")

    def test_unresolved_variable_asks(self):
        self.assertEqual(decision_for("rm -rf $BUILD_DIR/"), "ask")
        self.assertEqual(decision_for('rm -rf "${STAGE}"/*'), "ask")

    def test_powershell_remove_item(self):
        for cmd in ("Remove-Item -Recurse -Force .\\Content",
                    "Remove-Item -Path Source -Recurse",
                    "rm -r -fo Config",
                    "ri -Recurse C:\\",
                    "Remove-Item -Recurse -Force $env:USERPROFILE",
                    "Remove-Item -LiteralPath 'D:\\Projects\\ShooterGame' -Recurse -Force"):
            self.assertEqual(decision_for(cmd, tool="PowerShell"), "deny", cmd)

    def test_cmd_rmdir(self):
        self.assertEqual(decision_for("cmd /c rmdir /s /q Content"), "deny")
        self.assertEqual(decision_for("rd /s /q Source", tool="PowerShell"), "deny")

    def test_nested_shells(self):
        self.assertEqual(decision_for('bash -c "rm -rf Source"'), "deny")
        self.assertEqual(decision_for('pwsh -Command "git reset --hard"'), "deny")

    def test_find_delete(self):
        self.assertEqual(decision_for("find Content -name '*.uasset' -delete"), "deny")
        self.assertEqual(decision_for("find Saved -name '*.tmp' -delete"), "ask")

    def test_asset_and_descriptor_files_ask(self):
        self.assertEqual(decision_for("rm Content/Characters/BP_Hero.uasset"), "ask")
        self.assertEqual(decision_for("rm ShooterGame.uproject"), "ask")
        self.assertEqual(decision_for("Remove-Item Source\\ShooterGame\\ShooterGame.Build.cs",
                                      tool="PowerShell"), "ask")

    def test_disk_destroyers(self):
        self.assertEqual(decision_for("mkfs.ext4 /dev/sdb1"), "deny")
        self.assertEqual(decision_for("dd if=/dev/zero of=/dev/sda"), "deny")
        self.assertEqual(decision_for("Format-Volume -DriveLetter D", tool="PowerShell"), "deny")

    def test_deny_reason_is_explanatory(self):
        response = guard.decide(payload("git reset --hard"))
        reason = response["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("git reset --hard", reason)
        self.assertIn("stash", reason)

    def test_deny_wins_over_ask(self):
        self.assertEqual(decision_for("git branch -D x && git reset --hard"), "deny")


class DocumentedExamples(unittest.TestCase):
    """Every row of the table in docs/usage-examples.md, so the docs stay true."""

    def test_usage_examples_table(self):
        cases = [
            ("rm -rf Intermediate Binaries", "Bash", None),
            ("rm -rf Content/Characters", "Bash", None),
            ("rm -rf Content", "Bash", "deny"),
            ("Remove-Item -Recurse -Force .\\Source", "PowerShell", "deny"),
            ("rm Content/Maps/L_Old.umap", "Bash", "ask"),
            ("rm -rf $STAGING_DIR/", "Bash", "ask"),
            ("git push --force-with-lease", "Bash", "ask"),
            ("git push --force", "Bash", "deny"),
            ("rmdir /s Config", "PowerShell", "deny"),
            ("git reset --soft HEAD~1", "Bash", None),
        ]
        for command, tool, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(decision_for(command, tool), expected)


class HookProcessContract(unittest.TestCase):
    """Run the real script as Claude Code would: JSON on stdin, JSON on stdout."""

    def run_hook(self, stdin_text: str, env_extra: dict | None = None):
        env = dict(os.environ)
        env.pop("UE5_GUARD_FAIL_CLOSED", None)
        env.update(env_extra or {})
        return subprocess.run([sys.executable, str(HOOK_PATH)], input=stdin_text,
                              capture_output=True, text=True, env=env, timeout=30)

    def test_deny_output_matches_spec(self):
        result = self.run_hook(json.dumps(payload("git reset --hard")))
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout)
        self.assertEqual(set(out), {"hookSpecificOutput"})
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PreToolUse")
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertTrue(hso["permissionDecisionReason"])

    def test_allowed_command_prints_nothing(self):
        result = self.run_hook(json.dumps(payload("git status")))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_invalid_json_does_not_crash(self):
        result = self.run_hook("{not valid json")
        self.assertEqual(result.returncode, 1)  # non-blocking hook error
        self.assertIn("could not parse", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_empty_and_non_object_input(self):
        self.assertEqual(self.run_hook("").returncode, 1)
        self.assertEqual(self.run_hook("[1, 2, 3]").returncode, 1)

    def test_invalid_json_fail_closed(self):
        result = self.run_hook("garbage", {"UE5_GUARD_FAIL_CLOSED": "1"})
        self.assertEqual(result.returncode, 2)  # blocking error

    def test_main_in_process(self):
        out, err = io.StringIO(), io.StringIO()
        code = guard.main(io.StringIO(json.dumps(payload("git clean -fd"))), out, err)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")


class Tokenizer(unittest.TestCase):

    def test_split_respects_quotes(self):
        self.assertEqual(guard.split_segments('echo "a;b" && ls | wc'), ['echo "a;b"', "ls", "wc"])

    def test_windows_paths_survive_tokenizing(self):
        self.assertEqual(guard.tokenize("rmdir /s C:\\Work\\Game"), ["rmdir", "/s", "C:\\Work\\Game"])

    def test_unbalanced_quotes_do_not_raise(self):
        self.assertIsInstance(guard.tokenize('echo "unterminated'), list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
