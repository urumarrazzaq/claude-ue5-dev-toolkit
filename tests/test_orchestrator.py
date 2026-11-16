"""Tests for scripts/orchestrator.py: plan validation, scheduling safety and status transitions."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("orchestrator", REPO_ROOT / "scripts" / "orchestrator.py")
orch = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(orch)

AGENTS_DIR = REPO_ROOT / ".claude" / "agents"
EXAMPLE = REPO_ROOT / "examples" / "orchestration" / "combat-ability.tasks.json"
HAS_GIT = shutil.which("git") is not None


def task(tid, agent="ue5-cpp-developer", deps=(), files=None, status="pending", **extra):
    if files is None:
        files = [f"Source/Game/{tid}.cpp"] if orch.AGENTS[agent]["writes"] else []
    t = {"id": tid, "description": f"do {tid}", "agent": agent, "priority": "medium",
         "depends_on": list(deps), "status": status, "expected_output": "code",
         "validation": ["compiles"], "files": files, "context_files": [], "history": []}
    t.update(extra)
    return t


def plan_of(*tasks):
    p = orch.new_plan("test")
    p["tasks"] = [copy.deepcopy(t) for t in tasks]
    return p


def errors_of(plan):
    return orch.validate_plan(plan, AGENTS_DIR)[0]


def advance(plan, tid, *statuses, note="n"):
    for s in statuses:
        orch.apply_transition(plan, tid, s, note)


class DependencyValidationTests(unittest.TestCase):

    def test_valid_chain(self):
        p = plan_of(task("T1"), task("T2", deps=["T1"]), task("R", "ue5-code-reviewer", deps=["T1", "T2"]))
        self.assertEqual(errors_of(p), [])

    def test_unknown_dependency(self):
        errs = errors_of(plan_of(task("T1", deps=["T9"])))
        self.assertTrue(any("unknown task 'T9'" in e for e in errs), errs)

    def test_self_dependency(self):
        errs = errors_of(plan_of(task("T1", deps=["T1"])))
        self.assertTrue(any("depends on itself" in e for e in errs), errs)

    def test_cycle_detected(self):
        p = plan_of(task("A", deps=["C"]), task("B", deps=["A"]), task("C", deps=["B"]))
        errs = errors_of(p)
        self.assertTrue(any(e.startswith("dependency cycle") for e in errs), errs)

    def test_example_plan_is_valid(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        p = plan_of(*[orch.normalise_task(t) for t in data["tasks"]])
        self.assertEqual(orch.validate_plan(p, AGENTS_DIR), ([], []))


class AgentAssignmentTests(unittest.TestCase):

    def test_unknown_agent(self):
        t = task("T1")
        t["agent"] = "ue5-wizard"
        errs = errors_of(plan_of(t))
        self.assertTrue(any("unknown agent 'ue5-wizard'" in e for e in errs), errs)

    def test_writer_must_own_files(self):
        errs = errors_of(plan_of(task("T1", files=[])))
        self.assertTrue(any("must declare owned 'files'" in e for e in errs), errs)

    def test_read_only_agents_cannot_own_files(self):
        for agent in ("ue5-architect", "ue5-code-reviewer", "ue5-build-engineer",
                      "ue5-blueprint-specialist", "user"):
            errs = errors_of(plan_of(task("T1", agent, files=["Source/A.cpp"])))
            self.assertTrue(any("read-only and cannot own files" in e for e in errs), (agent, errs))

    def test_every_registered_agent_has_a_matching_definition(self):
        used = [a for a in orch.AGENTS if a != "user"]
        errs, warns = orch.agent_tool_mismatches(AGENTS_DIR, used)
        self.assertEqual((errs, warns), ([], []))

    def test_registry_tool_mismatch_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "ue5-code-reviewer.md").write_text(
                "---\nname: ue5-code-reviewer\ntools: Read, Edit\n---\nbody", encoding="utf-8")
            errs, _ = orch.agent_tool_mismatches(Path(tmp), ["ue5-code-reviewer", "ue5-architect"])
        self.assertTrue(any("can write files" in e for e in errs), errs)
        self.assertTrue(any("ue5-architect" in e and "no definition" in e for e in errs), errs)

    def test_missing_review_coverage_is_warned(self):
        _, warns = orch.validate_plan(plan_of(task("T1")), AGENTS_DIR)
        self.assertTrue(any("no ue5-code-reviewer task depends on it" in w for w in warns), warns)


class FileOwnershipTests(unittest.TestCase):

    def test_unordered_tasks_sharing_a_file_conflict(self):
        p = plan_of(task("T1", files=["Source/Game/Game.Build.cs"]),
                    task("T2", files=["Source/Game/Game.Build.cs"]))
        errs = errors_of(p)
        self.assertTrue(any("file ownership conflict: tasks T1 and T2" in e for e in errs), errs)

    def test_ordered_tasks_may_share_a_file(self):
        p = plan_of(task("T1", files=["Source/Game/Game.Build.cs"]),
                    task("T2", deps=["T1"], files=["Source/Game/Game.Build.cs"]))
        self.assertEqual(errors_of(p), [])

    def test_transitive_ordering_counts(self):
        p = plan_of(task("T1", files=["a.cpp"]), task("T2", deps=["T1"], files=["b.cpp"]),
                    task("T3", deps=["T2"], files=["a.cpp"]))
        self.assertEqual(errors_of(p), [])

    def test_directory_ownership_overlaps_files(self):
        p = plan_of(task("T1", files=["Source/Game/Abilities/"]),
                    task("T2", files=["Source/Game/Abilities/Dash.cpp"]))
        self.assertTrue(any("ownership conflict" in e for e in errors_of(p)))

    def test_paths_normalised_for_case_and_separators(self):
        self.assertTrue(orch.paths_overlap("Source\\Game\\A.cpp", "./source/game/a.cpp"))
        self.assertFalse(orch.paths_overlap("Source/Game/A.cpp", "Source/Game/AB.cpp"))
        self.assertFalse(orch.paths_overlap("Source/Game/A", "Source/Game/A.cpp"))

    def test_duplicate_entry_in_one_task_is_warned(self):
        _, warns = orch.validate_plan(plan_of(task("T1", files=["a.cpp", "A.cpp"])), AGENTS_DIR)
        self.assertTrue(any("duplicate entries" in w for w in warns), warns)


class InvalidDefinitionTests(unittest.TestCase):

    def test_missing_required_fields(self):
        errs = errors_of(plan_of({"id": "T1", "agent": "ue5-architect"}))
        for field in ("description", "priority", "depends_on", "status", "expected_output", "validation"):
            self.assertTrue(any(f"missing required field '{field}'" in e for e in errs), (field, errs))

    def test_bad_values(self):
        cases = [
            ({"id": "1bad"}, "'id' must match"),
            ({"priority": "urgent"}, "priority must be one of"),
            ({"status": "done"}, "status must be one of"),
            ({"validation": []}, "'validation' must be a non-empty list"),
            ({"description": "  "}, "'description' must be a non-empty string"),
            ({"depends_on": "T0"}, "'depends_on' must be a list"),
            ({"files": "a.cpp"}, "'files' must be a list"),
        ]
        for override, message in cases:
            t = task("T1")
            t.update(override)
            errs = errors_of(plan_of(t))
            self.assertTrue(any(message in e for e in errs), (override, errs))

    def test_duplicate_ids(self):
        errs = errors_of(plan_of(task("T1"), task("T1", files=["other.cpp"])))
        self.assertTrue(any("duplicate task id" in e for e in errs), errs)

    def test_forbidden_owned_paths(self):
        cases = {"C:/Proj/Source/A.cpp": "relative", "/abs/A.cpp": "relative",
                 "Source/../../A.cpp": "'..'", "Content/Maps/L_Main.umap": "binary asset",
                 "Content/BP_Hero.uasset": "binary asset", "Intermediate/Build/x.h": "generated folder",
                 "Plugins/Kit/Binaries/x.dll": "generated folder"}
        for path, message in cases.items():
            errs = errors_of(plan_of(task("T1", files=[path])))
            self.assertTrue(any(message in e for e in errs), (path, errs))

    def test_non_object_task_and_missing_task_list(self):
        self.assertTrue(any("must be an object" in e for e in errors_of(plan_of("oops"))))
        self.assertEqual(orch.validate_plan({"tasks": None})[0], ["plan has no 'tasks' list"])

    def test_impossible_status_is_an_error(self):
        p = plan_of(task("T1"), task("T2", deps=["T1"], status="in_progress"))
        self.assertTrue(any("never delivered" in e for e in errors_of(p)))


class StatusTransitionTests(unittest.TestCase):

    def setUp(self):
        self.p = plan_of(task("T1"), task("T2", deps=["T1"]),
                         task("R", "ue5-code-reviewer", deps=["T1", "T2"]))

    def test_illegal_transitions_refused(self):
        for target in ("review", "completed"):
            with self.assertRaisesRegex(orch.OrchestratorError, "cannot go from 'pending'"):
                orch.apply_transition(self.p, "T1", target)
        advance(self.p, "T1", "failed")
        with self.assertRaisesRegex(orch.OrchestratorError, "cannot go from 'failed' to 'completed'"):
            orch.apply_transition(self.p, "T1", "completed")

    def test_blocked_and_failed_require_note(self):
        for target in ("blocked", "failed"):
            with self.assertRaisesRegex(orch.OrchestratorError, "requires --note"):
                orch.apply_transition(self.p, "T1", target, "")

    def test_cannot_start_before_prerequisites(self):
        with self.assertRaisesRegex(orch.OrchestratorError, r"prerequisites not done: \['T1'\]"):
            orch.apply_transition(self.p, "T2", "in_progress")

    def test_review_status_unblocks_dependents(self):
        advance(self.p, "T1", "in_progress", "review")
        advance(self.p, "T2", "in_progress")  # builds on T1's delivered output
        self.assertEqual(self.p["tasks"][1]["status"], "in_progress")

    def test_writer_cannot_skip_review(self):
        advance(self.p, "T1", "in_progress")
        with self.assertRaisesRegex(orch.OrchestratorError, "must pass through 'review'"):
            orch.apply_transition(self.p, "T1", "completed")

    def test_completion_requires_approving_review(self):
        advance(self.p, "T1", "in_progress", "review")
        advance(self.p, "T2", "in_progress", "review")
        with self.assertRaisesRegex(orch.OrchestratorError, "no completed ue5-code-reviewer"):
            orch.apply_transition(self.p, "T1", "completed")
        advance(self.p, "R", "in_progress")
        with self.assertRaisesRegex(orch.OrchestratorError, "record the review"):
            orch.apply_transition(self.p, "R", "completed")
        orch.record_handoff(self.p, "R", {"summary": "ok", "verdict": "approve"})
        advance(self.p, "R", "completed")
        advance(self.p, "T1", "completed")
        advance(self.p, "T2", "completed")
        self.assertEqual(errors_of(self.p), [])

    def test_changes_requested_blocks_completion_and_old_approval_is_stale(self):
        advance(self.p, "T1", "in_progress", "review")
        advance(self.p, "T2", "in_progress", "review")
        advance(self.p, "R", "in_progress")
        orch.record_handoff(self.p, "R", {"summary": "bad", "verdict": "changes_requested"})
        advance(self.p, "R", "completed")
        with self.assertRaises(orch.OrchestratorError):
            orch.apply_transition(self.p, "T1", "completed")
        # Rework, then a second reviewer approves.
        advance(self.p, "T1", "in_progress", "review")
        self.p["tasks"].append(task("R2", "ue5-code-reviewer", deps=["T1"]))
        advance(self.p, "R2", "in_progress")
        orch.record_handoff(self.p, "R2", {"summary": "ok", "verdict": "approve_with_comments"})
        advance(self.p, "R2", "completed")
        advance(self.p, "T1", "completed")

    def test_approval_before_rework_does_not_count(self):
        advance(self.p, "T1", "in_progress", "review")
        advance(self.p, "T2", "in_progress", "review")
        advance(self.p, "R", "in_progress")
        orch.record_handoff(self.p, "R", {"summary": "ok", "verdict": "approve"})
        advance(self.p, "R", "completed")
        advance(self.p, "T1", "in_progress", "review")  # changed after approval
        with self.assertRaisesRegex(orch.OrchestratorError, "since it entered review"):
            orch.apply_transition(self.p, "T1", "completed")

    def test_retry_counts_attempts(self):
        advance(self.p, "T1", "in_progress", "failed", "pending", "in_progress")
        self.assertEqual(self.p["tasks"][0]["attempts"], 2)
        self.assertEqual([h["status"] for h in self.p["tasks"][0]["history"]],
                         ["in_progress", "failed", "pending", "in_progress"])

    def test_read_only_task_completes_directly(self):
        p = plan_of(task("A", "ue5-architect"))
        advance(p, "A", "in_progress", "completed")
        self.assertEqual(p["tasks"][0]["status"], "completed")

    def test_manual_task_waits_for_completed_prerequisites(self):
        p = plan_of(task("T1"), task("U", "user", deps=["T1"]), task("R", "ue5-code-reviewer", deps=["T1"]))
        advance(p, "T1", "in_progress", "review")
        with self.assertRaises(orch.OrchestratorError):
            orch.apply_transition(p, "U", "in_progress")

    def test_verdict_only_on_reviewer_tasks(self):
        with self.assertRaisesRegex(orch.OrchestratorError, "only reviewer tasks"):
            orch.record_handoff(self.p, "T1", {"summary": "x", "verdict": "approve"})
        with self.assertRaisesRegex(orch.OrchestratorError, "verdict must be one of"):
            orch.record_handoff(self.p, "R", {"summary": "x", "verdict": "lgtm"})


class ConflictingTaskTests(unittest.TestCase):

    def test_ready_batch_picks_independent_tasks_by_priority(self):
        p = plan_of(task("T1", priority="low"), task("T2", priority="critical"),
                    task("T3", deps=["T1"]), task("R", "ue5-code-reviewer", deps=["T1", "T2", "T3"]))
        batch, waiting = orch.ready_batch(p)
        self.assertEqual([t["id"] for t in batch], ["T2", "T1"])
        self.assertIn(("T3", "waiting for ['T1']"), waiting)

    def test_running_task_locks_its_files(self):
        p = plan_of(task("T1", files=["a.cpp"]), task("T2", deps=["T1"], files=["a.cpp"]),
                    task("T3", files=["b.cpp"]))
        advance(p, "T1", "in_progress")
        conflicts = orch.active_conflicts(p, p["tasks"][1])
        self.assertTrue(any("T1 is modifying ['a.cpp']" in c for c in conflicts), conflicts)

    def test_parallel_limit(self):
        p = plan_of(*[task(f"T{i}") for i in range(5)])
        batch, waiting = orch.ready_batch(p, max_parallel=2)
        self.assertEqual(len(batch), 2)
        self.assertEqual(sum("parallel limit" in r for _, r in waiting), 3)

    def test_build_never_runs_alongside_writers(self):
        p = plan_of(task("T1"), task("T2"), task("B", "ue5-build-engineer", deps=["T1"]))
        advance(p, "T1", "in_progress", "review")
        advance(p, "T2", "in_progress")
        batch, waiting = orch.ready_batch(p)
        self.assertNotIn("B", [t["id"] for t in batch])
        self.assertTrue(any(tid == "B" and "must not run while T2" in r for tid, r in waiting), waiting)
        advance(p, "T2", "review")
        advance(p, "B", "in_progress")
        p["tasks"].append(task("W"))  # a new writer must wait for the running build
        self.assertTrue(any("is compiling" in c for c in orch.active_conflicts(p, p["tasks"][-1])))

    def test_failed_prerequisite_reported(self):
        p = plan_of(task("T1"), task("T2", deps=["T1"]))
        advance(p, "T1", "in_progress", "failed")
        _, waiting = orch.ready_batch(p)
        self.assertIn(("T2", "prerequisite failed: ['T1']"), waiting)

    def test_dirty_user_files_need_approval(self):
        p = plan_of(task("T1", files=["Source/A.cpp"]), task("T2", files=["Source/B.cpp"]))
        p["git_baseline"] = {"captured_at": "x", "dirty": ["Source/A.cpp"]}
        batch, waiting = orch.ready_batch(p)
        self.assertEqual([t["id"] for t in batch], ["T2"])
        with self.assertRaisesRegex(orch.OrchestratorError, "uncommitted user changes"):
            orch.apply_transition(p, "T1", "in_progress")
        p["tasks"][0]["allow_dirty"] = True
        advance(p, "T1", "in_progress")

    def test_handoff_outside_ownership_flagged_and_audited(self):
        p = plan_of(task("T1", files=["Source/A.cpp"]))
        p["git_baseline"] = {"captured_at": "x", "dirty": ["Source/UserWip.cpp"]}
        advance(p, "T1", "in_progress")
        h = orch.record_handoff(p, "T1", {"summary": "s", "files_changed": ["Source/A.cpp", "Game.Build.cs"]})
        self.assertEqual(h["ownership_violations"], ["Game.Build.cs"])
        problems = orch.audit_changes(p, ["Source/A.cpp", "Source/UserWip.cpp", "Source/Stray.cpp",
                                          ".claude/orchestration/plan.json"])
        self.assertEqual(problems, ["Source/Stray.cpp: changed but no task owns it",
                                    "Game.Build.cs: reported changed by T1, which does not own it"])

    def test_change_before_owner_started_is_flagged(self):
        p = plan_of(task("T1", files=["Source/A.cpp"]))
        p["git_baseline"] = {"captured_at": "x", "dirty": []}
        self.assertIn("has not started", orch.audit_changes(p, ["Source/A.cpp"])[0])


class BriefAndReportTests(unittest.TestCase):

    def test_brief_contains_only_task_context(self):
        p = plan_of(task("T1", files=["Source/A.cpp"]), task("T2", deps=["T1"], context_files=["Source/A.h"]),
                    task("T9", files=["Source/Unrelated.cpp"]))
        p["decisions"] = [{"text": "Use GAS"}]
        orch.record_handoff(p, "T1", {"summary": "Added UFoo", "files_changed": ["Source/A.cpp"]})
        brief = orch.build_brief(p, "T2")
        for expected in ("# Task T2", "Source/Game/T2.cpp", "Source/A.h", "Added UFoo", "Use GAS",
                         '"files_changed"'):
            self.assertIn(expected, brief)
        self.assertNotIn("Unrelated", brief)
        self.assertNotIn('"verdict"', brief)
        self.assertIn('"verdict"', orch.build_brief(plan_of(task("R", "ue5-code-reviewer")), "R"))

    def test_report_flags_stale_build_and_keeps_rework_files(self):
        p = plan_of(task("T1", files=["a.cpp", "a.h"]), task("B", "ue5-build-engineer", deps=["T1"]),
                    task("R", "ue5-code-reviewer", deps=["T1"]))
        advance(p, "T1", "in_progress")
        orch.record_handoff(p, "T1", {"summary": "v1", "files_changed": ["a.cpp", "a.h"]})
        advance(p, "T1", "review")
        advance(p, "B", "in_progress")
        orch.record_handoff(p, "B", {"summary": "ok", "validation_result": "Build.bat ... exit 0"})
        advance(p, "B", "completed")
        advance(p, "T1", "in_progress")
        orch.record_handoff(p, "T1", {"summary": "v2", "files_changed": ["a.cpp"]})
        advance(p, "T1", "review")
        report = orch.build_report(p)
        self.assertIn("- a.h", report)
        self.assertIn("STALE: ['T1']", report)
        self.assertIn("Build.bat ... exit 0", report)
        self.assertIn("NOT compiled", orch.build_report(plan_of(task("T1"))))


class CliTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.plan = str(Path(self.tmp, "plan.json"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = orch.main(["--plan", self.plan, "--agents-dir", str(AGENTS_DIR), *args])
        return code, out.getvalue() + err.getvalue()

    def test_import_validate_ready_start(self):
        self.assertEqual(self.run_cli("init", "--title", "demo")[0], 0)
        self.assertEqual(self.run_cli("init", "--title", "demo")[0], 1)  # no silent overwrite
        self.assertEqual(self.run_cli("import-tasks", str(EXAMPLE))[0], 0)
        code, out = self.run_cli("validate", "--json")
        self.assertEqual((code, json.loads(out)["valid"]), (0, True))
        code, out = self.run_cli("ready", "--json")
        self.assertEqual(json.loads(out)["ready"], ["T1", "T2"])
        self.assertEqual(self.run_cli("start", "T1", "T3")[0], 1)  # T3 depends on T1
        self.assertEqual(json.loads(Path(self.plan).read_text())["tasks"][0]["status"], "pending")
        self.assertEqual(self.run_cli("start", "T1", "T2")[0], 0)

    def test_invalid_import_is_not_saved(self):
        self.run_cli("init", "--title", "demo")
        bad = Path(self.tmp, "bad.json")
        bad.write_text(json.dumps([task("T1", files=["x.cpp"]), task("T2", files=["x.cpp"])]))
        code, out = self.run_cli("import-tasks", str(bad))
        self.assertEqual(code, 1)
        self.assertIn("file ownership conflict", out)
        self.assertEqual(json.loads(Path(self.plan).read_text())["tasks"], [])

    def test_missing_plan_is_reported(self):
        code, out = self.run_cli("status")
        self.assertEqual(code, 1)
        self.assertIn("plan not found", out)


@unittest.skipUnless(HAS_GIT, "git not installed")
class GitIntegrationTests(unittest.TestCase):

    def test_git_dirty_files_and_demo(self):
        with tempfile.TemporaryDirectory() as tmp:
            git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", tmp]
            subprocess.run(git + ["init", "-q"], check=True)
            Path(tmp, "a.txt").write_text("1")
            Path(tmp, "b.txt").write_text("1")
            subprocess.run(git + ["add", "-A"], check=True)
            subprocess.run(git + ["commit", "-q", "-m", "x"], check=True)
            Path(tmp, "a.txt").write_text("2")
            Path(tmp, "dir").mkdir()
            Path(tmp, "dir", "new file.txt").write_text("n")
            subprocess.run(git + ["mv", "b.txt", "c.txt"], check=True)
            self.assertEqual(orch.git_dirty_files(Path(tmp)),
                             ["a.txt", "b.txt", "c.txt", "dir/new file.txt"])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(orch.OrchestratorError):
                orch.git_dirty_files(Path(tmp))

    def test_demo_session_runs(self):
        proc = subprocess.run([sys.executable, str(REPO_ROOT / "examples" / "orchestration" / "demo_session.py")],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout[-3000:] + proc.stderr)
        self.assertIn("Demo completed successfully", proc.stdout)
        self.assertIn("file ownership conflict", proc.stdout)
        self.assertIn("changed but no task owns it", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
